"""Standalone design-review / code-compliance checker for EPA SWMM runs.

Usage
-----
python3 design_review.py \\
    --run-dir <path>          # expects model.rpt + manifest.json + model.inp inside
    [--rpt <path>]            # explicit override for model.rpt
    [--inp <path>]            # explicit override for model.inp
    [--manifest <path>]       # explicit override for manifest.json
    [--rules <path>]          # rulebook YAML (repeatable; default: bundled gb50014)
    [--out-dir <dir>]         # default: <run-dir>/09_review/
    [--no-inp]                # skip INP-derived metrics (conduit slope, diameter)

Exit codes
----------
0  all rules pass, warn, or needs-data (no FAIL)
1  at least one FAIL
2  script error (unreadable run dir, malformed rulebook, etc.)

Design notes
------------
* Import-free from agentic_swmm — this script is portable and runs standalone.
  Tests that need parity against rpt_summary.py do so independently.
* Rulebook format: YAML (PyYAML is a project dep) with JSON fallback.
* Time for created_at is taken from manifest.created_at (deterministic); if
  absent, a fixed sentinel "1970-01-01T00:00:00Z" is used so the output is
  still deterministic across test runs.
* needs-data is NEVER silently dropped: every rule with an unavailable metric
  appears as needs-data in both JSON and Markdown.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Minimal INP section reader (no agentic_swmm import)
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^\s*\[([A-Z_]+)\]\s*$")


def _parse_inp_sections(text: str) -> dict[str, list[str]]:
    """Split INP text into {SECTION: [non-comment data rows]}."""
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


def _extract_conduits(sections: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """Parse [CONDUITS] rows: {name: {from_node, to_node, length, roughness}}."""
    result: dict[str, dict[str, Any]] = {}
    for row in sections.get("CONDUITS", []):
        cols = row.split()
        if len(cols) < 5:
            continue
        try:
            result[cols[0]] = {
                "from_node": cols[1],
                "to_node": cols[2],
                "length": float(cols[3]),
                "roughness": float(cols[4]),
            }
        except (ValueError, IndexError):
            continue
    return result


def _extract_xsections(sections: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """Parse [XSECTIONS] rows: {link: {shape, geom1}}."""
    result: dict[str, dict[str, Any]] = {}
    for row in sections.get("XSECTIONS", []):
        cols = row.split()
        if len(cols) < 3:
            continue
        try:
            result[cols[0]] = {
                "shape": cols[1].upper(),
                "geom1": float(cols[2]),
            }
        except (ValueError, IndexError):
            continue
    return result


def _extract_node_inverts(sections: dict[str, list[str]]) -> dict[str, float]:
    """Parse [JUNCTIONS] and [OUTFALLS] for invert elevations.
    JUNCTIONS col layout: name elevation max_depth init_depth sur_depth ponded
    OUTFALLS  col layout: name elevation type [stage_data] [gated] [route]
    """
    inverts: dict[str, float] = {}
    for row in sections.get("JUNCTIONS", []):
        cols = row.split()
        if len(cols) >= 2:
            try:
                inverts[cols[0]] = float(cols[1])
            except ValueError:
                continue
    for row in sections.get("OUTFALLS", []):
        cols = row.split()
        if len(cols) >= 2:
            try:
                inverts[cols[0]] = float(cols[1])
            except ValueError:
                continue
    return inverts


def _get_flow_units(sections: dict[str, list[str]]) -> str | None:
    """Return FLOW_UNITS from [OPTIONS], or None if not found."""
    for row in sections.get("OPTIONS", []):
        cols = row.split()
        if len(cols) >= 2 and cols[0].upper() == "FLOW_UNITS":
            return cols[1].upper()
    return None


# ---------------------------------------------------------------------------
# Minimal RPT section reader (no agentic_swmm import)
# ---------------------------------------------------------------------------

def _parse_rpt_section(rpt_text: str, title: str, raw_columns: int) -> list[list[str]]:
    """Locate ``title`` in rpt_text and return tokenised data rows.

    Mirrors the logic in agentic_swmm.agent.swmm_runtime.rpt_summary.parse_section
    so the parity test can assert numeric agreement.
    """
    lines = rpt_text.splitlines()
    title_idx = -1
    for idx, line in enumerate(lines):
        if line.strip() == title:
            title_idx = idx
            break
    if title_idx < 0:
        return []

    cursor = title_idx + 1
    # Advance to first dash row
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        cursor += 1
    cursor += 1  # past top dashes

    # Second dash row = end of column headers
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        cursor += 1
    cursor += 1  # past bottom dashes

    rows: list[list[str]] = []
    while cursor < len(lines):
        line = lines[cursor]
        stripped = line.strip()
        if not stripped or stripped.startswith("---") or stripped.startswith("***"):
            break
        tokens = stripped.split()
        if len(tokens) != raw_columns:
            break
        if tokens[0] == "System":
            cursor += 1
            continue
        rows.append(tokens)
        cursor += 1
    return rows


def _section_exists(rpt_text: str, title: str) -> bool:
    """Return True if the section title line appears anywhere in rpt_text."""
    for line in rpt_text.splitlines():
        if line.strip() == title:
            return True
    return False


def _parse_link_flow_summary(rpt_text: str) -> list[dict[str, Any]] | None:
    """Return per-link dicts from Link Flow Summary, or None if section absent.

    Returns [] (empty list) when the section exists but contains no parseable rows
    (different from None which means the section title was not found at all).
    """
    if not _section_exists(rpt_text, "Link Flow Summary"):
        return None
    raw = _parse_rpt_section(rpt_text, "Link Flow Summary", 8)
    rows = []
    for t in raw:
        try:
            rows.append({
                "link": t[0],
                "type": t[1],
                "peak_flow": float(t[2]),
                "max_velocity": float(t[5]),
                "max_full_flow_ratio": float(t[6]),
                "max_full_depth_ratio": float(t[7]),
            })
        except (ValueError, IndexError):
            continue
    return rows


def _parse_outfall_summary(rpt_text: str) -> list[dict[str, Any]] | None:
    """Return per-outfall dicts from Outfall Loading Summary, or None if absent."""
    if not _section_exists(rpt_text, "Outfall Loading Summary"):
        return None
    raw = _parse_rpt_section(rpt_text, "Outfall Loading Summary", 5)
    rows = []
    for t in raw:
        try:
            rows.append({
                "node": t[0],
                "flow_freq_pct": float(t[1]),
                "avg_flow": float(t[2]),
                "max_flow": float(t[3]),
                "total_volume_10_6_ltr": float(t[4]),
            })
        except (ValueError, IndexError):
            continue
    return rows


def _parse_node_inflow_summary(rpt_text: str) -> list[dict[str, Any]] | None:
    """Return per-node dicts from Node Inflow Summary, or None if absent.

    NOTE: SWMM sometimes writes the volume columns as bare integers (e.g. ``0``)
    or with a unit suffix (``0.000 ltr``) when volumes are zero.  The ``ltr``
    suffix produces 11 tokens instead of 9, which causes the token-count guard
    in ``_parse_rpt_section`` to stop the section early.  Rows with non-zero
    volumes always use the 9-token format and are captured correctly; zero-volume
    rows are skipped by both this parser and by ``rpt_summary.parse_section``
    (which uses the same token-count guard).  The parity test therefore asserts
    agreement on the set of *captured* nodes, not on every node in the file.
    """
    if not _section_exists(rpt_text, "Node Inflow Summary"):
        return None
    raw = _parse_rpt_section(rpt_text, "Node Inflow Summary", 9)
    rows = []
    for t in raw:
        try:
            rows.append({
                "node": t[0],
                "type": t[1],
                "max_lateral_inflow": float(t[2]),
                "max_total_inflow": float(t[3]),
                "lateral_inflow_volume_10_6_ltr": float(t[6]),
                "total_inflow_volume_10_6_ltr": float(t[7]),
                "flow_balance_error_pct": float(t[8]),
            })
        except (ValueError, IndexError):
            continue
    return rows


# ---------------------------------------------------------------------------
# Metric extractors
# ---------------------------------------------------------------------------

# Sentinel for "metric data not available" — extractor returns this.
_UNAVAILABLE = object()


class ArtifactCache:
    """Lazy loader and cache for run artifacts."""

    def __init__(
        self,
        manifest_path: Path,
        rpt_path: Path,
        inp_path: Path | None,
        no_inp: bool = False,
    ) -> None:
        self._manifest_path = manifest_path
        self._rpt_path = rpt_path
        self._inp_path = inp_path
        self._no_inp = no_inp

        self._manifest: dict[str, Any] | None = None
        self._rpt_text: str | None = None
        self._inp_sections: dict[str, list[str]] | None = None

        # Parsed caches
        self._link_flow: list[dict[str, Any]] | None | object = _UNAVAILABLE
        self._outfall: list[dict[str, Any]] | None | object = _UNAVAILABLE
        self._node_inflow: list[dict[str, Any]] | None | object = _UNAVAILABLE
        self._conduits: dict[str, dict[str, Any]] | None | object = _UNAVAILABLE
        self._xsections: dict[str, dict[str, Any]] | None | object = _UNAVAILABLE
        self._node_inverts: dict[str, float] | None | object = _UNAVAILABLE
        self._flow_units: str | None | object = _UNAVAILABLE

    def manifest(self) -> dict[str, Any]:
        if self._manifest is None:
            with open(self._manifest_path) as f:
                self._manifest = json.load(f)
        return self._manifest

    def rpt_text(self) -> str:
        if self._rpt_text is None:
            with open(self._rpt_path) as f:
                self._rpt_text = f.read()
        return self._rpt_text

    def link_flow_rows(self) -> list[dict[str, Any]] | None:
        if self._link_flow is _UNAVAILABLE:
            self._link_flow = _parse_link_flow_summary(self.rpt_text())
        return self._link_flow  # type: ignore[return-value]

    def outfall_rows(self) -> list[dict[str, Any]] | None:
        if self._outfall is _UNAVAILABLE:
            self._outfall = _parse_outfall_summary(self.rpt_text())
        return self._outfall  # type: ignore[return-value]

    def node_inflow_rows(self) -> list[dict[str, Any]] | None:
        if self._node_inflow is _UNAVAILABLE:
            self._node_inflow = _parse_node_inflow_summary(self.rpt_text())
        return self._node_inflow  # type: ignore[return-value]

    def _ensure_inp(self) -> bool:
        """Return True if INP sections are available."""
        if self._no_inp:
            return False
        if self._inp_path is None:
            return False
        if self._inp_sections is None:
            try:
                text = self._inp_path.read_text()
                self._inp_sections = _parse_inp_sections(text)
            except OSError:
                self._inp_sections = {}
        return bool(self._inp_sections)

    def flow_units(self) -> str | None:
        if self._flow_units is _UNAVAILABLE:
            if not self._ensure_inp():
                self._flow_units = None
            else:
                assert self._inp_sections is not None
                self._flow_units = _get_flow_units(self._inp_sections)
        return self._flow_units  # type: ignore[return-value]

    def conduits(self) -> dict[str, dict[str, Any]] | None:
        if self._conduits is _UNAVAILABLE:
            if not self._ensure_inp():
                self._conduits = None
            else:
                assert self._inp_sections is not None
                self._conduits = _extract_conduits(self._inp_sections)
        return self._conduits  # type: ignore[return-value]

    def xsections(self) -> dict[str, dict[str, Any]] | None:
        if self._xsections is _UNAVAILABLE:
            if not self._ensure_inp():
                self._xsections = None
            else:
                assert self._inp_sections is not None
                self._xsections = _extract_xsections(self._inp_sections)
        return self._xsections  # type: ignore[return-value]

    def node_inverts(self) -> dict[str, float] | None:
        if self._node_inverts is _UNAVAILABLE:
            if not self._ensure_inp():
                self._node_inverts = None
            else:
                assert self._inp_sections is not None
                self._node_inverts = _extract_node_inverts(self._inp_sections)
        return self._node_inverts  # type: ignore[return-value]


def _us_customary_units(units: str | None) -> bool:
    """Return True when flow units imply US customary (CFS, GPM, AFD, MGD, ACRE-FT, etc.)."""
    if units is None:
        return False
    return units.upper() in {"CFS", "GPM", "AFD", "MGD"}


def extract_metric(
    metric: str,
    cache: ArtifactCache,
    _scope: dict[str, Any],  # noqa: ARG001
) -> tuple[list[dict[str, Any]] | float | None, str | None]:
    """Resolve a metric name to a value (or None = unavailable).

    Returns (value, reason) where:
      value  — float (run-level), list[{id, value}] (element-level), or None
      reason — human-readable explanation when value is None
    """
    # -----------------------------------------------------------------------
    # Run-level metrics (manifest)
    # -----------------------------------------------------------------------
    if metric == "run.peak_flow":
        m = cache.manifest()
        peak = m.get("metrics", {}).get("peak", {}).get("peak")
        if peak is None:
            return None, "manifest.metrics.peak.peak is absent"
        return float(peak), None

    if metric == "run.continuity_error_pct":
        m = cache.manifest()
        ce = m.get("metrics", {}).get("continuity", {}).get("continuity_error_percent", {})
        # Return the worst (max absolute) of runoff_quantity and flow_routing
        vals = [v for v in ce.values() if isinstance(v, (int, float))]
        if not vals:
            return None, "manifest.metrics.continuity.continuity_error_percent absent"
        # Return both as a list so between-operator can test both
        worst = max(vals, key=abs)
        return float(worst), None

    if metric == "run.return_period_yr":
        m = cache.manifest()
        rp = m.get("metadata", {}).get("storm_return_period_yr")
        if rp is None:
            return None, (
                "manifest.metadata.storm_return_period_yr absent. "
                "Storm provenance is not yet propagated by swmm-runner. "
                "This is PR2 work."
            )
        return float(rp), None

    # -----------------------------------------------------------------------
    # Link-level metrics (rpt Link Flow Summary)
    # -----------------------------------------------------------------------
    if metric in ("link.max_velocity", "link.max_full_flow_ratio",
                  "link.max_full_depth_ratio", "link.peak_flow"):
        rows = cache.link_flow_rows()
        if rows is None:
            return None, "Link Flow Summary section not found in rpt"
        field_map = {
            "link.max_velocity": "max_velocity",
            "link.max_full_flow_ratio": "max_full_flow_ratio",
            "link.max_full_depth_ratio": "max_full_depth_ratio",
            "link.peak_flow": "peak_flow",
        }
        field = field_map[metric]
        return [{"id": r["link"], "type": r.get("type", ""), "value": r[field]}
                for r in rows], None

    # -----------------------------------------------------------------------
    # Outfall metrics (rpt Outfall Loading Summary)
    # -----------------------------------------------------------------------
    if metric == "outfall.max_flow":
        rows = cache.outfall_rows()
        if rows is None:
            return None, "Outfall Loading Summary section not found in rpt"
        return [{"id": r["node"], "value": r["max_flow"]} for r in rows], None

    # -----------------------------------------------------------------------
    # Node metrics (rpt Node Inflow Summary)
    # -----------------------------------------------------------------------
    if metric in ("node.flow_balance_error_pct", "node.max_total_inflow"):
        rows = cache.node_inflow_rows()
        if rows is None:
            return None, "Node Inflow Summary section not found in rpt"
        field_map = {
            "node.flow_balance_error_pct": "flow_balance_error_pct",
            "node.max_total_inflow": "max_total_inflow",
        }
        field = field_map[metric]
        return [{"id": r["node"], "value": r[field]} for r in rows], None

    # -----------------------------------------------------------------------
    # PR2 metrics (rpt sections not yet parsed — always needs-data)
    # -----------------------------------------------------------------------
    if metric in ("node.surcharge_hours", "node.max_depth_m",
                  "node.flooding_hours", "node.flooding_volume_m3"):
        _PR2_REASON = {
            "node.surcharge_hours": (
                "Node Surcharge Summary is not yet parsed (PR2 rpt_summary extension). "
                "This check cannot produce a result until PR2 lands. It is NOT a pass."
            ),
            "node.max_depth_m": (
                "Node Depth Summary is not yet parsed (PR2 rpt_summary extension)."
            ),
            "node.flooding_hours": (
                "Node Flooding Summary numeric per-node values not yet extracted (PR2). "
                "It is NOT a pass."
            ),
            "node.flooding_volume_m3": (
                "Node Flooding Summary numeric per-node values not yet extracted (PR2)."
            ),
        }
        return None, _PR2_REASON[metric]

    if metric == "junction.freeboard_m":
        return None, (
            "junction.freeboard_m requires Node Depth Summary (rpt) + INP JUNCTIONS join. "
            "This is PR2 work. It is NOT a pass."
        )

    # -----------------------------------------------------------------------
    # INP-derived metrics
    # -----------------------------------------------------------------------
    if metric == "conduit.slope_pct":
        # Check for US customary INP — slope is unit-agnostic but flag unit mismatch
        units = cache.flow_units()
        if _us_customary_units(units):
            return None, (
                f"INP FLOW_UNITS={units} (US customary). "
                "All INP-derived metrics emit needs-data to avoid unit mismatch."
            )
        conduits = cache.conduits()
        if conduits is None:
            return None, "INP not available (--no-inp or path missing)"
        inverts = cache.node_inverts()
        if inverts is None:
            return None, "INP JUNCTIONS/OUTFALLS inverts not available"
        # Also pull xsections so scope filters on diameter work correctly.
        xs = cache.xsections()
        rows = []
        for name, c in conduits.items():
            from_inv = inverts.get(c["from_node"])
            to_inv = inverts.get(c["to_node"])
            length = c["length"]
            if from_inv is None or to_inv is None or length <= 0:
                continue
            slope_pct = abs(from_inv - to_inv) / length * 100.0
            elem: dict[str, Any] = {
                "id": name,
                "value": slope_pct,
                "_from_inv": from_inv,
                "_to_inv": to_inv,
                "type": "CONDUIT",
            }
            # Attach diameter so scope filters like min_diameter_m / max_diameter_m work
            if xs and name in xs and xs[name]["shape"] == "CIRCULAR":
                elem["_diameter_m"] = xs[name]["geom1"]
            rows.append(elem)
        if not rows:
            return None, "Could not compute slope for any conduit (missing invert data)"
        return rows, None

    if metric == "conduit.diameter_m":
        units = cache.flow_units()
        if _us_customary_units(units):
            return None, (
                f"INP FLOW_UNITS={units} (US customary). "
                "All INP-derived metrics emit needs-data."
            )
        xs = cache.xsections()
        if xs is None:
            return None, "INP not available (--no-inp or path missing)"
        rows = []
        for name, x in xs.items():
            if x["shape"] == "CIRCULAR":
                rows.append({"id": name, "value": x["geom1"]})
        if not rows:
            return None, "No CIRCULAR conduits found in [XSECTIONS]"
        return rows, None

    if metric == "conduit.roughness":
        conduits = cache.conduits()
        if conduits is None:
            return None, "INP not available (--no-inp or path missing)"
        return [{"id": name, "value": c["roughness"]} for name, c in conduits.items()], None

    return None, f"Unknown metric: {metric!r}"


# ---------------------------------------------------------------------------
# Scope filter
# ---------------------------------------------------------------------------

def _apply_scope(
    elements: list[dict[str, Any]],
    scope: dict[str, Any],
) -> list[dict[str, Any]]:
    """Filter element list according to scope rules.

    Diameter-based filters (min_diameter_m / max_diameter_m) use the ``_diameter_m``
    metadata field injected by the conduit extractor — not the ``value`` field, which
    may carry slope, velocity, or another metric rather than diameter.
    """
    result = elements
    link_type = scope.get("link_type")
    if link_type:
        result = [e for e in result if e.get("type", "").upper() == link_type.upper()]
    node_type = scope.get("node_type")
    if node_type:
        result = [e for e in result if e.get("type", "").upper() == node_type.upper()]
    min_d = scope.get("min_diameter_m")
    if min_d is not None:
        # Elements without _diameter_m metadata cannot satisfy the filter → excluded
        result = [
            e for e in result
            if e.get("_diameter_m") is not None and float(e["_diameter_m"]) >= float(min_d)
        ]
    max_d = scope.get("max_diameter_m")
    if max_d is not None:
        result = [
            e for e in result
            if e.get("_diameter_m") is not None and float(e["_diameter_m"]) <= float(max_d)
        ]
    name_pat = scope.get("name_pattern")
    if name_pat:
        pat = re.compile(name_pat)
        result = [e for e in result if pat.search(e.get("id", ""))]
    # Flow threshold: skip elements with zero flow for VELOCITY_MIN style checks
    min_flow = scope.get("min_peak_flow")
    if min_flow is not None:
        result = [e for e in result if e.get("_peak_flow", 1.0) > float(min_flow)]
    return result


# ---------------------------------------------------------------------------
# Operator evaluation
# ---------------------------------------------------------------------------

def _eval_operator(
    value: float,
    operator: str,
    rule: dict[str, Any],
) -> bool:
    """Return True if value satisfies the rule threshold. Raises ValueError on bad op."""
    if operator == "between":
        lo = float(rule["threshold_low"])
        hi = float(rule["threshold_high"])
        return lo <= value <= hi
    threshold = float(rule["threshold"])
    ops = {
        "lte": lambda v, t: v <= t,
        "lt": lambda v, t: v < t,
        "gte": lambda v, t: v >= t,
        "gt": lambda v, t: v > t,
        "eq": lambda v, t: v == t,
        "neq": lambda v, t: v != t,
    }
    if operator not in ops:
        raise ValueError(f"Unknown operator: {operator!r}")
    return ops[operator](value, threshold)


def _threshold_for_element(
    rule: dict[str, Any],
    element: dict[str, Any],
    metric: str,
) -> float | None:
    """Return the effective threshold for this element (diameter-bucket override)."""
    tbd = rule.get("threshold_by_diameter")
    if not tbd:
        return rule.get("threshold")
    # For diameter-bucket overrides we need the diameter of the element.
    # The element may carry "_diameter_m" if the extractor joined it, or we
    # fall back to the plain threshold.
    d = element.get("_diameter_m")
    if d is None:
        return rule.get("threshold")
    # Find the smallest bucket key that is >= d
    buckets = sorted(float(k) for k in tbd.keys())
    for b in buckets:
        if d <= b:
            return float(tbd[b])
    return float(tbd[max(tbd.keys(), key=float)])


# ---------------------------------------------------------------------------
# Rule evaluator
# ---------------------------------------------------------------------------

def _make_threshold_display(rule: dict[str, Any]) -> str:
    op = rule.get("operator", "")
    if op == "between":
        return f"[{rule.get('threshold_low')}, {rule.get('threshold_high')}]"
    return str(rule.get("threshold", "?"))


def evaluate_rule(
    rule: dict[str, Any],
    cache: ArtifactCache,
) -> dict[str, Any]:
    """Evaluate one rule and return a result dict."""
    rule_id = rule["id"]
    metric = rule["metric"]
    operator = rule.get("operator", "lte")
    severity = rule.get("severity", "FAIL").upper()
    aggregate = rule.get("aggregate", "any")
    scope = rule.get("scope") or {}
    citation = rule.get("citation", "TODO: cite local standard")
    verify = rule.get("verify", True)
    remediation = rule.get("remediation", "")
    units = rule.get("units", "SI")

    # Extract
    raw_value, reason = extract_metric(metric, cache, scope)

    if raw_value is None:
        return {
            "rule_id": rule_id,
            "status": "needs-data",
            "severity": severity,
            "title": rule.get("title", rule_id),
            "metric": metric,
            "citation": citation,
            "verify": verify,
            "units": units,
            "aggregate": aggregate,
            "elements": [],
            "worst_element": None,
            "needs_data_reason": reason,
            "remediation": remediation,
        }

    # Scalar (run-level) → wrap into element list
    if isinstance(raw_value, (int, float)):
        elements_in = [{"id": "<run>", "value": float(raw_value)}]
    else:
        elements_in = list(raw_value)

    # Apply scope filter
    elements_scoped = _apply_scope(elements_in, scope)

    if not elements_scoped:
        # No elements remain after scope filtering → needs-data
        return {
            "rule_id": rule_id,
            "status": "needs-data",
            "severity": severity,
            "title": rule.get("title", rule_id),
            "metric": metric,
            "citation": citation,
            "verify": verify,
            "units": units,
            "aggregate": aggregate,
            "elements": [],
            "worst_element": None,
            "needs_data_reason": f"No elements remain after scope filter: {scope}",
            "remediation": remediation,
        }

    # Evaluate each element
    threshold_display = _make_threshold_display(rule)
    element_results = []
    for elem in elements_scoped:
        val = float(elem["value"])
        # For between operator the threshold_low/high come from rule
        if operator == "between":
            ok = _eval_operator(val, operator, rule)
            thr_used = f"[{rule.get('threshold_low')}, {rule.get('threshold_high')}]"
        else:
            thr = _threshold_for_element(rule, elem, metric)
            if thr is None:
                # Missing threshold → mark as needs-data element
                element_results.append({
                    "id": elem["id"],
                    "value": val,
                    "threshold": None,
                    "units": units,
                    "result": "needs-data",
                })
                continue
            ok = _eval_operator(val, operator, {**rule, "threshold": thr})
            thr_used = str(thr)
        element_results.append({
            "id": elem["id"],
            "value": round(val, 6),
            "threshold": thr_used,
            "units": units,
            "result": "pass" if ok else ("fail" if severity == "FAIL" else "warn"),
        })

    # Determine overall rule status
    failed = [e for e in element_results if e["result"] in ("fail", "warn")]
    worst: dict[str, Any] | None = None
    if failed:
        # worst = element with largest deviation from threshold
        def _deviation(e: dict[str, Any]) -> float:
            v = e["value"]
            thr_str = e.get("threshold", threshold_display)
            try:
                thr_f = float(thr_str)
                return abs(v - thr_f)
            except (TypeError, ValueError):
                return 0.0

        worst = max(failed, key=_deviation)

    if aggregate == "any":
        rule_pass = not failed
    elif aggregate == "all":
        rule_pass = len(failed) == len(element_results)
    else:  # "worst"
        rule_pass = not failed

    if rule_pass:
        status = "pass"
    else:
        status = "fail" if severity == "FAIL" else "warn"

    return {
        "rule_id": rule_id,
        "status": status,
        "severity": severity,
        "title": rule.get("title", rule_id),
        "metric": metric,
        "citation": citation,
        "verify": verify,
        "units": units,
        "aggregate": aggregate,
        "elements": element_results,
        "worst_element": worst["id"] if worst else None,
        "needs_data_reason": None,
        "remediation": remediation,
    }


# ---------------------------------------------------------------------------
# Rulebook loader
# ---------------------------------------------------------------------------

def _load_rulebook(path: Path) -> dict[str, Any]:
    """Load and validate a rulebook YAML or JSON file."""
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import]
            data = yaml.safe_load(text)
        except ImportError:
            raise SystemExit(
                "ERROR: PyYAML is required for .yaml rulebooks. "
                "Install with: pip install PyYAML"
            )
    elif suffix == ".json":
        data = json.loads(text)
    else:
        # Try YAML first, then JSON
        try:
            import yaml  # type: ignore[import]
            data = yaml.safe_load(text)
        except (ImportError, Exception):
            data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError(f"Rulebook {path} must be a YAML/JSON object at the top level")
    if "rules" not in data:
        raise ValueError(f"Rulebook {path} missing required 'rules' key")

    # Validate each rule
    required_fields = {"id", "metric", "operator", "units", "severity", "citation", "verify"}
    for rule in data["rules"]:
        missing = required_fields - set(rule.keys())
        if missing:
            raise ValueError(
                f"Rule {rule.get('id', '<unknown>')} in {path} "
                f"missing required fields: {sorted(missing)}"
            )
        if rule.get("verify") is None:
            raise ValueError(
                f"Rule {rule['id']}: 'verify' field is required (set true or false)"
            )
        if rule.get("operator") not in {
            "lte", "lt", "gte", "gt", "eq", "neq", "between"
        }:
            raise ValueError(
                f"Rule {rule['id']}: unsupported operator {rule.get('operator')!r}"
            )

    return data


# ---------------------------------------------------------------------------
# Engine entry point
# ---------------------------------------------------------------------------

def _overall_status(results: list[dict[str, Any]]) -> str:
    """Derive overall status from rule results."""
    statuses = {r["status"] for r in results}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    if "needs-data" in statuses:
        return "needs-data"
    return "pass"


def evaluate_rulebook(
    cache: ArtifactCache,
    rulebook: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate all rules in rulebook against cache, return review result dict."""
    results = [evaluate_rule(rule, cache) for rule in rulebook.get("rules", [])]
    summary = {
        "total": len(results),
        "pass": sum(1 for r in results if r["status"] == "pass"),
        "fail": sum(1 for r in results if r["status"] == "fail"),
        "warn": sum(1 for r in results if r["status"] == "warn"),
        "needs_data": sum(1 for r in results if r["status"] == "needs-data"),
    }
    return {
        "rulebook_id": rulebook.get("rulebook_id", "unknown"),
        "rulebook_version": rulebook.get("version", "0"),
        "overall_status": _overall_status(results),
        "summary": summary,
        "disclaimer": rulebook.get("disclaimer", ""),
        "results": results,
    }


# ---------------------------------------------------------------------------
# JSON serialiser
# ---------------------------------------------------------------------------

def _to_json(
    review: dict[str, Any],
    run_dir: str,
    created_at: str,
) -> dict[str, Any]:
    """Wrap review result into the final JSON schema."""
    return {
        "schema_version": "1.0",
        "created_at": created_at,
        "run_dir": run_dir,
        "rulebook_id": review["rulebook_id"],
        "rulebook_version": review["rulebook_version"],
        "overall_status": review["overall_status"],
        "summary": review["summary"],
        "disclaimer": review["disclaimer"],
        "results": review["results"],
    }


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

_OP_SYMBOLS = {
    "lte": "≤",
    "lt": "<",
    "gte": "≥",
    "gt": ">",
    "eq": "=",
    "neq": "≠",
    "between": "∈",
}


def _render_markdown(doc: dict[str, Any]) -> str:
    lines: list[str] = []

    lines.append("# Design Review Report")
    lines.append("")
    lines.append(f"**Run:** `{doc['run_dir']}`")
    lines.append(f"**Rulebook:** {doc['rulebook_id']} v{doc['rulebook_version']}")
    lines.append(f"**Reviewed:** {doc['created_at']}")
    overall = doc["overall_status"].upper()
    lines.append(f"**Overall status:** {overall}")
    lines.append("")
    lines.append("> **DISCLAIMER:** This report is generated by aiswmm as decision-support for")
    lines.append("> an engineering review. Findings do NOT constitute legal or regulatory")
    lines.append("> compliance with any drainage standard. All thresholds marked `verify:true`")
    lines.append("> MUST be confirmed against your applicable edition of the standard before")
    lines.append("> accepting any finding.")
    lines.append("")

    s = doc["summary"]
    lines.append("## Summary")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---|")
    lines.append(f"| Pass | {s['pass']} |")
    lines.append(f"| Fail | {s['fail']} |")
    lines.append(f"| Warn | {s['warn']} |")
    lines.append(f"| Needs Data | {s['needs_data']} |")
    lines.append("")

    lines.append("## Findings")
    lines.append("")

    # Group by status
    for group_status, label in [
        ("fail", "FAIL"),
        ("warn", "WARN"),
        ("pass", "PASS"),
        ("needs-data", "NEEDS DATA"),
    ]:
        group = [r for r in doc["results"] if r["status"] == group_status]
        if not group:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for r in group:
            rule_id = r["rule_id"]
            title = r["title"]
            lines.append(f"#### {rule_id} — {title}")
            lines.append("")
            if group_status == "needs-data":
                reason = r.get("needs_data_reason") or "Metric data unavailable."
                lines.append(f"- **Why unavailable:** {reason}")
            else:
                # Show evidence
                elems = r.get("elements", [])
                worst_id = r.get("worst_element")
                if worst_id:
                    worst_elem = next(
                        (e for e in elems if e["id"] == worst_id), None
                    )
                    if worst_elem:
                        v = worst_elem["value"]
                        thr = worst_elem.get("threshold", "?")
                        u = r.get("units", "")
                        verify_tag = " [verify threshold]" if r.get("verify") else ""
                        lines.append(
                            f"- **Evidence:** {worst_id}: {v} {u} "
                            f"(threshold {thr} {u}){verify_tag}"
                        )
                elif elems:
                    # Show first passing element
                    e = elems[0]
                    lines.append(
                        f"- **Evidence:** {e['id']}: {e['value']} {r.get('units', '')} "
                        f"(threshold {e.get('threshold', '?')} {r.get('units', '')})"
                    )
            citation = r.get("citation", "")
            if citation:
                lines.append(f"- **Citation:** {citation}")
            rem = r.get("remediation", "")
            if rem and group_status != "pass":
                lines.append(f"- **Remediation:** {rem.strip()}")
            lines.append("")

    lines.append("## Expert sign-off")
    lines.append("")
    lines.append(
        "_This review was produced automatically. An engineer must review findings "
        "and confirm acceptance before design submission._"
    )
    lines.append("")
    lines.append("| Reviewer | Date | Signature |")
    lines.append("|---|---|---|")
    lines.append("| | | |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _find_file(run_dir: Path, names: list[str]) -> Path | None:
    for name in names:
        p = run_dir / name
        if p.exists():
            return p
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Design review / code-compliance checker for EPA SWMM runs."
    )
    parser.add_argument("--run-dir", required=True, type=Path,
                        help="Run directory (expects model.rpt + manifest.json + model.inp)")
    parser.add_argument("--rpt", type=Path, help="Explicit path to model.rpt")
    parser.add_argument("--inp", type=Path, help="Explicit path to model.inp")
    parser.add_argument("--manifest", type=Path, help="Explicit path to manifest.json")
    parser.add_argument("--rules", type=Path, action="append", dest="rules",
                        metavar="RULEBOOK",
                        help="Path to rulebook YAML/JSON (repeatable; default: bundled GB 50014 template)")
    parser.add_argument("--out-dir", type=Path,
                        help="Output directory (default: <run-dir>/09_review/)")
    parser.add_argument("--no-inp", action="store_true",
                        help="Skip INP-derived metrics (conduit slope, diameter)")
    args = parser.parse_args(argv)

    run_dir: Path = args.run_dir.resolve()
    if not run_dir.is_dir():
        print(f"ERROR: run-dir does not exist or is not a directory: {run_dir}", file=sys.stderr)
        return 2

    # Resolve artifact paths
    rpt_path = (args.rpt or _find_file(run_dir, ["model.rpt"])) or run_dir / "model.rpt"
    inp_path = (args.inp or _find_file(run_dir, ["model.inp"])) if not args.no_inp else None
    manifest_path = (
        args.manifest or _find_file(run_dir, ["manifest.json", "runner_manifest.json"])
    ) or run_dir / "manifest.json"

    if not manifest_path.exists():
        print(f"ERROR: manifest.json not found at {manifest_path}", file=sys.stderr)
        return 2
    if not rpt_path.exists():
        print(f"ERROR: model.rpt not found at {rpt_path}", file=sys.stderr)
        return 2

    # Rulebook
    if args.rules:
        rulebook_paths = args.rules
    else:
        # Default: bundled template
        bundled = Path(__file__).parent.parent / "rulebooks" / "gb50014_template.yaml"
        if not bundled.exists():
            # Try JSON fallback
            bundled_json = bundled.with_suffix(".json")
            if bundled_json.exists():
                bundled = bundled_json
            else:
                print(f"ERROR: Default rulebook not found at {bundled}", file=sys.stderr)
                return 2
        rulebook_paths = [bundled]

    # Load rulebooks (merge rules from all)
    merged_rules: list[dict[str, Any]] = []
    merged_meta: dict[str, Any] = {}
    for rb_path in rulebook_paths:
        try:
            rb = _load_rulebook(Path(rb_path))
        except (ValueError, OSError) as e:
            print(f"ERROR loading rulebook {rb_path}: {e}", file=sys.stderr)
            return 2
        if not merged_meta:
            merged_meta = rb
        merged_rules.extend(rb.get("rules", []))
    rulebook = {**merged_meta, "rules": merged_rules}

    # Output directory
    out_dir = args.out_dir or (run_dir / "09_review")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Cache
    cache = ArtifactCache(
        manifest_path=manifest_path,
        rpt_path=rpt_path,
        inp_path=inp_path,
        no_inp=args.no_inp,
    )

    # Determine created_at from manifest (deterministic)
    try:
        manifest_data = cache.manifest()
        created_at = manifest_data.get("created_at", "1970-01-01T00:00:00Z")
    except (OSError, json.JSONDecodeError):
        created_at = "1970-01-01T00:00:00Z"

    # Evaluate
    review = evaluate_rulebook(cache, rulebook)

    # Serialise
    doc = _to_json(review, str(run_dir), created_at)
    json_path = out_dir / "design_review.json"
    md_path = out_dir / "design_review.md"

    json_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_render_markdown(doc), encoding="utf-8")

    # Print summary to stdout
    s = review["summary"]
    print(
        f"Design review: {review['overall_status'].upper()} "
        f"({s['pass']} pass, {s['fail']} fail, {s['warn']} warn, {s['needs_data']} needs-data)"
    )
    print(f"  Report: {md_path}")

    # Exit code
    if review["overall_status"] == "fail":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
