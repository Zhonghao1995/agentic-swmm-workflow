"""Preflight INP validation (PRD-06 Phase A.3).

A modeler invoking SWMM blind discovers a topology bug five minutes
later when they read the .rpt. The preflight catches the obvious
classes of bug *before* the run starts:

1. **zero_length_conduit** — a CONDUITS row with length <= 0
2. **missing_invert** — a JUNCTIONS or OUTFALLS row with no elevation
3. **undefined_raingage** — a SUBCATCHMENTS row whose raingage is
   not declared in [RAINGAGES]
4. **flow_units_mismatch** — FLOW_UNITS in [OPTIONS] inconsistent
   with the implied units from a TIMESERIES rainfall entry
5. **routing_step_too_large** — ROUTING_STEP (sub-step) greater than
   WET_STEP (main step)

Each check returns a structured row added to ``PreflightReport.failures``
(FAIL) or ``.warnings`` (WARN). The overall status is the max severity
across all checks. ``PASS`` means we got nothing back to flag.

The parser is intentionally regex-and-section based — SWMM's INP is
fixed-column-ish but tolerant of whitespace; we follow the SWMM 5
manual's section header convention (``[SECTION]``) and skip
``;``-prefixed comment lines. This is *not* a full INP parser; it
extracts only what each check needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# SWMM accepts section headers in any case (e.g. [subcatchments]); match
# case-insensitively and the parser upper-cases the captured name (review P2-7).
_SECTION_RE = re.compile(r"^\s*\[([A-Za-z_]+)\]\s*$")
_PASS = "PASS"
_WARN = "WARN"
_FAIL = "FAIL"


@dataclass
class PreflightReport:
    """Structured outcome of :func:`preflight_inp`.

    ``status`` is the max severity across all checks:
    ``"PASS" | "WARN" | "FAIL"``. ``failures`` and ``warnings`` are
    lists of ``{"code": ..., "detail": ...}`` rows so callers can
    render a checklist (and pin remediation against ``code``).
    """

    status: str = _PASS
    failures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def add_fail(self, code: str, detail: str) -> None:
        self.failures.append({"code": code, "detail": detail})
        self.status = _FAIL

    def add_warn(self, code: str, detail: str) -> None:
        self.warnings.append({"code": code, "detail": detail})
        if self.status == _PASS:
            self.status = _WARN


def _parse_sections(text: str) -> dict[str, list[str]]:
    """Split INP text into ``{section: [non-comment lines]}``.

    Comments (``;``-prefixed) and blank lines are filtered out — every
    check downstream operates on data rows only.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(";"):
            continue
        m = _SECTION_RE.match(raw)
        if m:
            current = m.group(1).upper()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        sections[current].append(stripped)
    return sections


def _check_zero_length_conduits(
    sections: dict[str, list[str]], report: PreflightReport
) -> None:
    """Any CONDUITS row with ``length <= 0`` is a FAIL."""
    for row in sections.get("CONDUITS", []):
        cols = row.split()
        if len(cols) < 4:
            continue
        name, _from, _to, length = cols[0], cols[1], cols[2], cols[3]
        try:
            length_f = float(length)
        except ValueError:
            continue
        if length_f <= 0:
            report.add_fail(
                "zero_length_conduit",
                f"Conduit {name!r} has length={length_f} (must be > 0).",
            )


def _check_missing_inverts(
    sections: dict[str, list[str]], report: PreflightReport
) -> None:
    """JUNCTIONS / OUTFALLS rows must carry an elevation in column 2.

    SWMM's parser blows up when invert elevation is missing — by the
    time we get to the .rpt the error is two layers removed from the
    user's edit. Catching it here surfaces "junction J1 has no
    elevation" instead of "ERROR 312: link …".
    """
    for section in ("JUNCTIONS", "OUTFALLS"):
        for row in sections.get(section, []):
            cols = row.split()
            if len(cols) < 2:
                report.add_fail(
                    "missing_invert",
                    f"{section[:-1].title()} {cols[0]!r} has no invert elevation.",
                )
                continue
            try:
                float(cols[1])
            except ValueError:
                report.add_fail(
                    "missing_invert",
                    f"{section[:-1].title()} {cols[0]!r} has non-numeric "
                    f"elevation {cols[1]!r}.",
                )


def _check_undefined_raingages(
    sections: dict[str, list[str]], report: PreflightReport
) -> None:
    """SUBCATCHMENTS column 2 must name a gage from RAINGAGES.

    Catches a common typo where the user renames a gage in one
    section but forgets the other. SWMM warns but continues — the
    runoff for that subcatchment is silently zero, which a calibrator
    chases for hours before noticing.
    """
    # SWMM matches object IDs case-insensitively, so compare with casefold to
    # avoid a false "undefined_raingage" when the two sections differ only in
    # case (review P2-7).
    declared = {row.split()[0].casefold() for row in sections.get("RAINGAGES", []) if row.split()}
    for row in sections.get("SUBCATCHMENTS", []):
        cols = row.split()
        if len(cols) < 2:
            continue
        name, gage = cols[0], cols[1]
        if gage.casefold() not in declared:
            report.add_fail(
                "undefined_raingage",
                f"Subcatchment {name!r} references raingage {gage!r} "
                f"which is not declared in [RAINGAGES].",
            )


_METRIC_FLOW_UNITS = {"CMS", "LPS", "MLD"}
_US_FLOW_UNITS = {"CFS", "GPM", "MGD"}
_US_RAIN_UNIT_TOKENS = {"in", "inches", "in/hr", "iph"}
_METRIC_RAIN_UNIT_TOKENS = {"mm", "mm/hr", "mmph"}


def _flow_units(sections: dict[str, list[str]]) -> str | None:
    """Return the FLOW_UNITS value from [OPTIONS], upper-cased."""
    for row in sections.get("OPTIONS", []):
        cols = row.split()
        if len(cols) >= 2 and cols[0].upper() == "FLOW_UNITS":
            return cols[1].upper()
    return None


def _check_flow_units_mismatch(
    sections: dict[str, list[str]], report: PreflightReport
) -> None:
    """Cross-check FLOW_UNITS against rainfall-source unit hints.

    SWMM's RAINGAGES row format is ``Name Format Interval SCF Source``
    where Source may be ``TIMESERIES name`` or
    ``FILE "rainfall.dat" stationID rain_units``. The rain_units
    token in the FILE form (column 7 onward) is what we cross-check
    against FLOW_UNITS. The TIMESERIES form does not carry a unit
    token, so we PASS for that case rather than guess.
    """
    flow_units = _flow_units(sections)
    if flow_units is None:
        return
    flow_is_metric = flow_units in _METRIC_FLOW_UNITS
    flow_is_us = flow_units in _US_FLOW_UNITS

    for row in sections.get("RAINGAGES", []):
        cols = row.split()
        if "FILE" not in {c.upper() for c in cols}:
            continue
        # Last meaningful token is the unit token — strip enclosing quotes.
        for tok in reversed(cols):
            tok_stripped = tok.strip("\"'").lower()
            if not tok_stripped:
                continue
            if tok_stripped in _US_RAIN_UNIT_TOKENS and flow_is_metric:
                report.add_warn(
                    "flow_units_mismatch",
                    f"FLOW_UNITS={flow_units} (metric) but raingage "
                    f"{cols[0]!r} declares US units ({tok_stripped}).",
                )
                break
            if tok_stripped in _METRIC_RAIN_UNIT_TOKENS and flow_is_us:
                report.add_warn(
                    "flow_units_mismatch",
                    f"FLOW_UNITS={flow_units} (US) but raingage "
                    f"{cols[0]!r} declares metric units ({tok_stripped}).",
                )
                break
            if tok_stripped in (
                _US_RAIN_UNIT_TOKENS | _METRIC_RAIN_UNIT_TOKENS
            ):
                # Matching system; stop scanning this row.
                break


def _hms_to_seconds(token: str) -> float | None:
    """Parse a ``HH:MM:SS`` token or a plain seconds value to seconds.

    SWMM is liberal: WET_STEP is ``HH:MM:SS``, ROUTING_STEP is a
    decimal seconds value. Returns ``None`` if unparseable.
    """
    token = token.strip()
    if ":" in token:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        try:
            h, m, s = (float(p) for p in parts)
        except ValueError:
            return None
        return h * 3600.0 + m * 60.0 + s
    try:
        return float(token)
    except ValueError:
        return None


def _option_value(sections: dict[str, list[str]], key: str) -> str | None:
    for row in sections.get("OPTIONS", []):
        cols = row.split()
        if len(cols) >= 2 and cols[0].upper() == key.upper():
            return cols[1]
    return None


def _check_time_step_sanity(
    sections: dict[str, list[str]], report: PreflightReport
) -> None:
    """ROUTING_STEP (sub-step, seconds) must be <= WET_STEP (main step).

    Flagging this is a FAIL: SWMM will run but the timestep ordering
    is a sign of a config-paste mistake, not a deliberate choice.
    """
    wet_raw = _option_value(sections, "WET_STEP")
    routing_raw = _option_value(sections, "ROUTING_STEP")
    if wet_raw is None or routing_raw is None:
        return
    wet_sec = _hms_to_seconds(wet_raw)
    routing_sec = _hms_to_seconds(routing_raw)
    if wet_sec is None or routing_sec is None:
        return
    if routing_sec > wet_sec:
        report.add_fail(
            "routing_step_too_large",
            f"ROUTING_STEP={routing_sec:g}s exceeds WET_STEP={wet_sec:g}s; "
            f"sub-step must not be larger than main step.",
        )


def preflight_inp(inp_path: Path) -> PreflightReport:
    """Run all preflight checks against ``inp_path`` and return a report.

    Missing or unreadable INP is itself a FAIL (``inp_unreadable``).
    """
    path = Path(inp_path)
    report = PreflightReport()
    if not path.is_file():
        report.add_fail("inp_unreadable", f"INP file not found: {path}")
        return report
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        report.add_fail("inp_unreadable", f"could not read INP: {exc}")
        return report

    sections = _parse_sections(text)

    _check_zero_length_conduits(sections, report)
    _check_missing_inverts(sections, report)
    _check_undefined_raingages(sections, report)
    _check_flow_units_mismatch(sections, report)
    _check_time_step_sanity(sections, report)

    return report
