#!/usr/bin/env python3
"""INP-aware parameter recommender for Monte Carlo priors (issue #52).

The recommender answers: "given this INP, which parameters should I put
in my prior parameter_space.json?". The answer has three parts:

* ``core_required`` — a hardcoded 6-element list of the SWMM-sensitive
  parameters that should always be perturbed regardless of what the INP
  contains (N-Imperv, S-Imperv, Pct-Imperv, Width, MaxRate, MinRate).
  Per #52 this is the lock-in list; do not change it without revisiting
  the PRD.
* ``recommended`` — ``core_required`` plus extras detected from the INP
  (e.g. Decay if HORTON, Suction/K/IMD if GREEN_AMPT, Slope if there's
  any non-trivial spread). Always a superset of ``core_required``.
* ``rationale`` — a ``{param: prose}`` map. Every parameter in
  ``recommended`` that is **not** in ``core_required`` carries a
  non-empty rationale string so the modeller knows why each extra was
  added. Core parameters get a rationale too when there's a useful
  evidence boundary to call out (e.g. baseline range derived from
  default ±20%), but the contract only requires non-empty rationale
  for extras.

Detection is intentionally simple and authoritative: the
``[OPTIONS]`` block carries an ``INFILTRATION`` keyword which is the
SWMM5-defined source of truth. We honor that keyword verbatim and only
fall back to the column count of the ``[INFILTRATION]`` body when
``[OPTIONS]`` is missing or unparseable — that fallback is enough for
the unit tests but is not a substitute for a well-formed INP.

CLI::

    python parameter_recommender.py --inp <path>

emits the structured object on stdout as JSON. Stdout is the contract;
stderr is reserved for warnings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# The six-parameter SWMM-sensitive core. Per the issue spec this list
# is fixed regardless of the INP — even for Green-Ampt INPs, the
# Horton-tinted parameters MaxRate/MinRate stay in the core. The
# infiltration-method detection drives the *extras* in `recommended`,
# not the contents of `core_required`.
CORE_REQUIRED: tuple[str, ...] = (
    "N-Imperv",
    "S-Imperv",
    "Pct-Imperv",
    "Width",
    "MaxRate",
    "MinRate",
)


# Per-method extras. The intersection between these and CORE_REQUIRED
# (MaxRate / MinRate for Horton) is allowed and silently deduplicated
# when we compose `recommended`.
INFILTRATION_EXTRAS: dict[str, tuple[str, ...]] = {
    "horton": ("MaxRate", "MinRate", "Decay"),
    "modified_horton": ("MaxRate", "MinRate", "Decay"),
    "green_ampt": ("Suction", "K", "IMD"),
    "modified_green_ampt": ("Suction", "K", "IMD"),
    "curve_number": ("CurveNum", "Ksat", "DryTime"),
}


# Static rationale fragments. These describe *why* each extra is in the
# recommended set; the prose stays terse because the broader hydrology
# rationale lives in docs/hitl-thresholds.md and PRD-Z.
#
# PRD-GF-CORE: as of the gap-fill refactor, citations on a per-parameter
# basis are loaded from ``defaults_table.yaml`` (single source of truth
# across the runtime). The static templates below remain as fallbacks
# for parameters that do not have a defaults_table entry yet.
RATIONALE_TEMPLATES: dict[str, str] = {
    "Decay": "Horton infiltration detected in [INFILTRATION] section.",
    "Suction": "Green-Ampt infiltration detected in [INFILTRATION] section.",
    "K": "Green-Ampt infiltration detected in [INFILTRATION] section.",
    "IMD": "Green-Ampt infiltration detected in [INFILTRATION] section.",
    "CurveNum": "SCS Curve-Number infiltration detected in [INFILTRATION] section.",
    "Ksat": "Curve-Number infiltration detected in [INFILTRATION] section.",
    "DryTime": "Curve-Number infiltration detected in [INFILTRATION] section.",
    "Slope": "Default range [-20%, +20%] of measured value.",
    "MaxRate": "Horton infiltration detected in [INFILTRATION] section.",
    "MinRate": "Horton infiltration detected in [INFILTRATION] section.",
}


# PRD-GF-CORE: alias map from SWMM-canonical parameter names used in
# this recommender to the registry entry names in
# ``defaults_table.yaml``. Parallel to the alias map in
# ``agentic_swmm.gap_fill.proposer`` but local to this script — the
# recommender is shipped as a standalone CLI under skills/, so we
# cannot import the agentic_swmm package.
_DEFAULTS_TABLE_ALIASES: dict[str, str] = {
    "MaxRate": "horton_max_infiltration_rate",
    "MinRate": "horton_min_infiltration_rate",
    "Decay": "horton_decay_constant",
}


def _defaults_table_path() -> Path:
    """Resolve the project-root ``defaults_table.yaml``.

    The recommender script lives at
    ``skills/swmm-uncertainty/scripts/parameter_recommender.py``;
    the table is at the repo root three levels up. Tests can
    override via ``AISWMM_DEFAULTS_TABLE``.
    """
    import os

    override = os.environ.get("AISWMM_DEFAULTS_TABLE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "defaults_table.yaml"


def _load_defaults_table() -> dict[str, dict[str, Any]]:
    """Return the ``entries`` map of the defaults table, or empty.

    Missing file / missing PyYAML / malformed YAML all map to an
    empty dict. The recommender then falls back to the static
    ``RATIONALE_TEMPLATES`` so behaviour is preserved when the table
    is unavailable.
    """
    path = _defaults_table_path()
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:  # pragma: no cover - defensive
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {str(k): dict(v) for k, v in entries.items() if isinstance(v, dict)}


def _rationale_for(param: str, method: str, table: dict[str, dict[str, Any]]) -> str:
    """Return the rationale string for ``param``.

    Lookup order:

    1. ``defaults_table.yaml`` entry pointed at by the alias map —
       returns ``"<reason from static template> Source: <citation>"``
       so the modeller sees both the trigger and the literature ref.
    2. Static ``RATIONALE_TEMPLATES`` entry — preserves the pre-PRD
       behaviour for parameters we do not have a defaults entry for.
    3. Generic ``f"Detected from [INFILTRATION] section ({method})"``
       fallback.
    """
    base = RATIONALE_TEMPLATES.get(param, "")
    entry_name = _DEFAULTS_TABLE_ALIASES.get(param)
    if entry_name and entry_name in table:
        citation = table[entry_name].get("source")
        if citation:
            if base:
                return f"{base} Source: {citation}"
            return f"Source: {citation}"
    if base:
        return base
    return f"Detected from [INFILTRATION] section ({method})."


def _normalise_method(token: str) -> str:
    """Map a SWMM ``INFILTRATION`` token to our snake_case identifier."""

    cleaned = token.strip().upper()
    table = {
        "HORTON": "horton",
        "MODIFIED_HORTON": "modified_horton",
        "GREEN_AMPT": "green_ampt",
        "MODIFIED_GREEN_AMPT": "modified_green_ampt",
        "CURVE_NUMBER": "curve_number",
    }
    return table.get(cleaned, cleaned.lower())


def _read_sections(inp_text: str) -> dict[str, list[str]]:
    """Slice an INP into a ``{SECTION_NAME: [lines]}`` map.

    Lines inside a section keep their original whitespace so downstream
    parsers can split by columns; comment-only lines (``;;``) are kept
    as-is because some [INFILTRATION] headers carry parameter names in
    a comment row, which the fallback detector consults.
    """

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in inp_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].upper()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        sections[current].append(raw)
    return sections


def _detect_method(sections: dict[str, list[str]]) -> str:
    """Return the snake_case infiltration method or ``"unknown"``.

    Primary source: ``[OPTIONS]`` ``INFILTRATION <token>`` line. This is
    the SWMM-canonical place to declare the method and the only one
    used by ``build_swmm_inp.py``. Fallback (rare): inspect the
    ``[INFILTRATION]`` comment header for parameter names.
    """

    options = sections.get("OPTIONS", [])
    for raw in options:
        stripped = raw.strip()
        if not stripped or stripped.startswith(";"):
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[0].upper() == "INFILTRATION":
            return _normalise_method(parts[1])

    # Fallback: peek at the comment row of [INFILTRATION].
    body = sections.get("INFILTRATION", [])
    for raw in body:
        s = raw.strip()
        if not s.startswith(";"):
            continue
        upper = s.upper()
        if "SUCTION" in upper or "KSAT" in upper or "IMD" in upper:
            return "green_ampt"
        if "MAXRATE" in upper or "DECAY" in upper:
            return "horton"
        if "CURVENUM" in upper or "CURVE_NUM" in upper:
            return "curve_number"
    return "unknown"


def recommend(inp_path: Path) -> dict[str, Any]:
    """Return the structured recommender payload for ``inp_path``.

    The dict shape is::

        {
          "core_required":         list[str],
          "recommended":           list[str],
          "rationale":             dict[str, str],
          "infiltration_method":   str,
        }

    ``recommended`` preserves insertion order: ``core_required`` first,
    then the method-specific extras, then any always-on extras (Slope).
    """

    inp_text = Path(inp_path).read_text(encoding="utf-8", errors="ignore")
    sections = _read_sections(inp_text)
    method = _detect_method(sections)

    recommended: list[str] = list(CORE_REQUIRED)
    extras = list(INFILTRATION_EXTRAS.get(method, ()))
    # Always include Slope as a default-range extra. The static rationale
    # explains that the prior is heuristic (±20%) rather than measured.
    if "Slope" not in extras:
        extras.append("Slope")
    for extra in extras:
        if extra not in recommended:
            recommended.append(extra)

    rationale: dict[str, str] = {}
    # Every parameter in `recommended` that is not in `core_required`
    # must have a rationale (the test asserts this directly). We also
    # provide rationale entries for core parameters where there is a
    # useful evidence-boundary note (e.g. MaxRate/MinRate for Horton).
    #
    # PRD-GF-CORE: each rationale string is enriched with the citation
    # from defaults_table.yaml when available. The yaml lookup is
    # one-shot — we read once and pass the dict to `_rationale_for`.
    defaults = _load_defaults_table()
    core_set = set(CORE_REQUIRED)
    for param in recommended:
        if param in core_set:
            # Only fill rationale for core params that match the
            # detected method, to keep the output noise-free.
            if param in ("MaxRate", "MinRate") and method.endswith("horton"):
                rationale[param] = _rationale_for(param, method, defaults)
            continue
        rationale[param] = _rationale_for(param, method, defaults)

    return {
        "core_required": list(CORE_REQUIRED),
        "recommended": recommended,
        "rationale": rationale,
        "infiltration_method": method,
    }


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--inp",
        required=True,
        type=Path,
        help="Path to the SWMM INP file to inspect.",
    )
    return ap


def main() -> None:
    args = _build_argparser().parse_args()
    payload = recommend(args.inp)
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
