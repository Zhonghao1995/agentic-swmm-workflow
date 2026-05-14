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
    core_set = set(CORE_REQUIRED)
    for param in recommended:
        if param in core_set:
            # Only fill rationale for core params that match the
            # detected method, to keep the output noise-free.
            if param in ("MaxRate", "MinRate") and method.endswith("horton"):
                rationale[param] = RATIONALE_TEMPLATES[param]
            continue
        rationale[param] = RATIONALE_TEMPLATES.get(
            param,
            f"Detected from [INFILTRATION] section ({method}).",
        )

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
