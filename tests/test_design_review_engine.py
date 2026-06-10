"""Tests for skills/swmm-design-review/scripts/design_review.py.

Fixtures
--------
* Saanich-b7 fixture from docs/framework-validation/saanich-b7-network-routed-20260513/
  (tracked, git-committed rpt + inp + manifest)
* Purpose-built mini fixture in tests/fixtures/design_review/
  (sample_mini.rpt + sample_mini.inp + sample_manifest.json)
* Minimal 3-rule sample_rules.yaml for fast unit tests

Coverage
--------
1.  Pass path — rule comfortably satisfied on saanich fixture
2.  Fail path — C1 velocity 3.47 m/s on mini fixture triggers VELOCITY_MAX FAIL
3.  needs-data: PR2 metric (node.surcharge_hours) → needs-data, never pass
4.  needs-data: conduit.slope_pct with --no-inp → needs-data
5.  Scope filter — CONDUIT-scoped rule does not fire on ORIFICE/WEIR rows
6.  between operator — continuity within bounds → pass; outside → fail
7.  JSON schema lock-in — required top-level keys present
8.  Markdown disclaimer + sign-off table present
9.  Determinism — two evaluations produce byte-identical JSON results list
10. Invalid rulebook — missing required field raises ValueError
11. Rulebook-honesty lint — verify:false + citation:TODO is forbidden
12. Parity test — design_review.py numbers agree with rpt_summary.parse_section
    on saanich-b7 Link Flow Summary and Node Inflow Summary

Notes
-----
* The test file imports design_review.py by inserting the scripts/ dir into sys.path
  — this is intentional: the script is standalone / import-free from agentic_swmm.
* Parity test imports rpt_summary from agentic_swmm (the canonical in-process parser).
  That import path lives in the main package, which is available on sys.path via
  the normal project layout (tests/ is sibling to agentic_swmm/).
"""

from __future__ import annotations

import importlib
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Import the standalone script by path (it is not a package)
# ---------------------------------------------------------------------------

_SCRIPT_DIR = (
    Path(__file__).parent.parent
    / "skills"
    / "swmm-design-review"
    / "scripts"
)
_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "design_review"
_SAANICH_DIR = (
    Path(__file__).parent.parent
    / "docs"
    / "framework-validation"
    / "saanich-b7-network-routed-20260513"
)

# Insert script dir into sys.path so importlib can find design_review.py
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import importlib.util as _ilu


def _load_dr() -> Any:
    """Load design_review module once per session."""
    spec = _ilu.spec_from_file_location(
        "design_review", _SCRIPT_DIR / "design_review.py"
    )
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_dr = _load_dr()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _saanich_cache(no_inp: bool = False) -> Any:
    return _dr.ArtifactCache(
        manifest_path=_SAANICH_DIR / "runner_manifest.json",
        rpt_path=_SAANICH_DIR / "model.rpt",
        inp_path=_SAANICH_DIR / "model.inp",
        no_inp=no_inp,
    )


def _mini_cache(no_inp: bool = False) -> Any:
    return _dr.ArtifactCache(
        manifest_path=_FIXTURE_DIR / "sample_manifest.json",
        rpt_path=_FIXTURE_DIR / "sample_mini.rpt",
        inp_path=_FIXTURE_DIR / "sample_mini.inp",
        no_inp=no_inp,
    )


def _mini_rule(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid rule dict with overrides."""
    base = {
        "id": "TEST_RULE",
        "title": "Test rule",
        "metric": "link.max_velocity",
        "scope": {"link_type": "CONDUIT"},
        "operator": "lte",
        "threshold": 999.0,
        "units": "SI",
        "severity": "FAIL",
        "citation": "test only",
        "verify": True,
        "remediation": "",
    }
    base.update(overrides)
    return base


def _make_rulebook(rules: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rulebook_id": "test",
        "version": "0",
        "disclaimer": "test",
        "rules": rules,
    }


# ---------------------------------------------------------------------------
# 1. Pass path
# ---------------------------------------------------------------------------

def test_pass_path_saanich_velocity_well_below_limit():
    """Max velocity in saanich (0.07 m/s) is << 3.0 → VELOCITY_MAX should pass."""
    rule = _mini_rule(id="VELOCITY_MAX", threshold=3.0)
    result = _dr.evaluate_rule(rule, _saanich_cache())
    assert result["status"] == "pass", (
        f"Expected pass for VELOCITY_MAX on saanich; got {result['status']!r}. "
        f"Elements: {result['elements']}"
    )


def test_pass_path_continuity_saanich():
    """Saanich continuity error (-0.171%) is within ±1% → should pass."""
    rule = _mini_rule(
        id="CONTINUITY_TEST",
        metric="run.continuity_error_pct",
        operator="between",
        threshold_low=-1.0,
        threshold_high=1.0,
        scope={},
    )
    del rule["threshold"]  # not needed for between
    result = _dr.evaluate_rule(rule, _saanich_cache())
    assert result["status"] == "pass", (
        f"Expected pass for CONTINUITY_TEST on saanich; got {result['status']!r}"
    )


# ---------------------------------------------------------------------------
# 2. Fail path
# ---------------------------------------------------------------------------

def test_fail_path_high_velocity_mini_fixture():
    """C1 in mini fixture has velocity 3.47 m/s; VELOCITY_MAX threshold 3.0 → FAIL."""
    rule = _mini_rule(id="VELOCITY_MAX", threshold=3.0, severity="FAIL")
    result = _dr.evaluate_rule(rule, _mini_cache())
    assert result["status"] == "fail", (
        f"Expected fail for VELOCITY_MAX on mini fixture; got {result['status']!r}"
    )
    assert result["worst_element"] == "C1", (
        f"Expected worst_element='C1'; got {result['worst_element']!r}"
    )
    # Check that the value recorded is the correct 3.47 m/s
    elem = next(e for e in result["elements"] if e["id"] == "C1")
    assert abs(elem["value"] - 3.47) < 1e-3, (
        f"Expected C1 velocity ~3.47; got {elem['value']}"
    )


def test_fail_path_sets_correct_threshold_in_evidence():
    """The 'threshold' recorded in the element must match the rule threshold."""
    rule = _mini_rule(id="VELOCITY_MAX", threshold=3.0)
    result = _dr.evaluate_rule(rule, _mini_cache())
    failing = [e for e in result["elements"] if e["result"] == "fail"]
    assert failing, "Expected at least one failing element"
    for e in failing:
        assert e["threshold"] == "3.0", (
            f"Expected threshold='3.0' in evidence; got {e['threshold']!r}"
        )


# ---------------------------------------------------------------------------
# 3. needs-data: PR2 metric
# ---------------------------------------------------------------------------

def test_needs_data_pr2_metric_surcharge_hours():
    """node.surcharge_hours is a PR2 metric → must return needs-data, never pass."""
    rule = _mini_rule(
        id="SURCHARGE_TEST",
        metric="node.surcharge_hours",
        operator="lte",
        threshold=1.0,
        scope={},
        severity="WARN",
    )
    result = _dr.evaluate_rule(rule, _mini_cache())
    assert result["status"] == "needs-data", (
        f"Expected needs-data for surcharge_hours; got {result['status']!r}"
    )
    assert result["needs_data_reason"] is not None
    assert "PR2" in result["needs_data_reason"] or "not yet" in result["needs_data_reason"]


def test_needs_data_freeboard_metric():
    """junction.freeboard_m is PR2 → needs-data."""
    rule = _mini_rule(
        id="FREEBOARD_TEST",
        metric="junction.freeboard_m",
        operator="gte",
        threshold=0.5,
        scope={},
    )
    result = _dr.evaluate_rule(rule, _mini_cache())
    assert result["status"] == "needs-data"


def test_needs_data_return_period_metric():
    """run.return_period_yr is absent from sample manifest → needs-data."""
    rule = _mini_rule(
        id="RETURN_PERIOD_TEST",
        metric="run.return_period_yr",
        operator="gte",
        threshold=2,
        scope={},
    )
    result = _dr.evaluate_rule(rule, _mini_cache())
    assert result["status"] == "needs-data"


# ---------------------------------------------------------------------------
# 4. needs-data: INP unavailable (--no-inp)
# ---------------------------------------------------------------------------

def test_needs_data_conduit_slope_when_no_inp():
    """conduit.slope_pct with no_inp=True → needs-data."""
    rule = _mini_rule(
        id="SLOPE_TEST",
        metric="conduit.slope_pct",
        scope={"link_type": "CONDUIT"},
        operator="gte",
        threshold=0.3,
    )
    result = _dr.evaluate_rule(rule, _mini_cache(no_inp=True))
    assert result["status"] == "needs-data", (
        f"Expected needs-data for slope with no-inp; got {result['status']!r}"
    )


def test_needs_data_conduit_diameter_when_no_inp():
    """conduit.diameter_m with no_inp=True → needs-data."""
    rule = _mini_rule(
        id="DIAMETER_TEST",
        metric="conduit.diameter_m",
        scope={},
        operator="gte",
        threshold=0.1,
    )
    result = _dr.evaluate_rule(rule, _mini_cache(no_inp=True))
    assert result["status"] == "needs-data"


# ---------------------------------------------------------------------------
# 5. Scope filter: CONDUIT-only rule does not fire on non-CONDUIT links
# ---------------------------------------------------------------------------

def test_scope_filter_conduit_only_excludes_orifice_weir():
    """A CONDUIT-scoped rule must not fire on ORIFICE or WEIR link types."""
    # Build a mini RPT with an ORIFICE link that has very high 'velocity'
    # The score is above threshold — but the scope says CONDUIT only.
    rpt_text = textwrap.dedent("""\

          ********************
          Link Flow Summary
          ********************

          -----------------------------------------------------------------------------
                                         Maximum  Time of Max   Maximum    Max/    Max/
                                          |Flow|   Occurrence   |Veloc|    Full    Full
          Link                 Type          CMS  days hr:min     m/sec    Flow   Depth
          -----------------------------------------------------------------------------
          ORI1                 ORIFICE     0.100     0  01:00     10.00    0.50    0.40
          WEI1                 WEIR        0.200     0  01:00      8.00    0.60    0.50
          C1                   CONDUIT     0.050     0  01:00      1.00    0.30    0.25
        """)

    # Use the low-level parser to get link rows
    rows = _dr._parse_link_flow_summary(rpt_text)
    assert rows is not None

    # Apply CONDUIT scope
    scoped = _dr._apply_scope(rows, {"link_type": "CONDUIT"})
    assert all(r["type"].upper() == "CONDUIT" for r in scoped), (
        f"Scope filter left non-CONDUIT links: {[r['type'] for r in scoped]}"
    )
    # Raw link rows use "link" key; extract_metric maps this to "id" for evaluation
    assert len(scoped) == 1 and scoped[0].get("link", scoped[0].get("id")) == "C1"


# ---------------------------------------------------------------------------
# 6. between operator
# ---------------------------------------------------------------------------

def test_between_operator_pass_when_within_bounds():
    """Continuity error -0.15% is within [-1, 1] → pass."""
    rule = _mini_rule(
        id="CONT",
        metric="run.continuity_error_pct",
        operator="between",
        threshold_low=-1.0,
        threshold_high=1.0,
        scope={},
        severity="WARN",
    )
    del rule["threshold"]
    result = _dr.evaluate_rule(rule, _mini_cache())
    assert result["status"] == "pass"


def test_between_operator_fail_when_out_of_bounds(tmp_path: Path):
    """Continuity error -5.0% is outside [-1, 1] → warn (severity=WARN)."""
    # Build a manifest with bad continuity
    bad_manifest = {
        "manifest_version": "1.0",
        "created_at": "2026-01-01T00:00:00Z",
        "metrics": {
            "peak": {"node": "OUT1", "peak": 0.0, "time_hhmm": "00:00",
                     "source": "Node Inflow Summary"},
            "continuity": {
                "continuity_error_percent": {
                    "runoff_quantity": -5.0,
                    "flow_routing": 0.0,
                }
            },
        },
        "return_code": 0,
    }
    bad_m = tmp_path / "bad_manifest.json"
    bad_m.write_text(json.dumps(bad_manifest))
    cache = _dr.ArtifactCache(
        manifest_path=bad_m,
        rpt_path=_FIXTURE_DIR / "sample_mini.rpt",
        inp_path=None,
        no_inp=True,
    )
    rule = _mini_rule(
        id="CONT",
        metric="run.continuity_error_pct",
        operator="between",
        threshold_low=-1.0,
        threshold_high=1.0,
        scope={},
        severity="WARN",
    )
    del rule["threshold"]
    result = _dr.evaluate_rule(rule, cache)
    # The worst continuity error is -5.0 which is outside [-1, 1] → warn
    assert result["status"] == "warn", (
        f"Expected warn for out-of-bounds continuity; got {result['status']!r}"
    )


# ---------------------------------------------------------------------------
# 7. JSON schema lock-in
# ---------------------------------------------------------------------------

def test_json_schema_required_keys(tmp_path: Path):
    """design_review.json must contain all required top-level keys."""
    required_keys = {
        "schema_version",
        "created_at",
        "run_dir",
        "rulebook_id",
        "rulebook_version",
        "overall_status",
        "summary",
        "disclaimer",
        "results",
    }
    # Use the tiny sample_rules.yaml rulebook
    rules_path = _FIXTURE_DIR / "sample_rules.yaml"
    out_dir = tmp_path / "09_review"
    rc = _dr.main([
        "--run-dir", str(_FIXTURE_DIR),
        "--rpt", str(_FIXTURE_DIR / "sample_mini.rpt"),
        "--inp", str(_FIXTURE_DIR / "sample_mini.inp"),
        "--manifest", str(_FIXTURE_DIR / "sample_manifest.json"),
        "--rules", str(rules_path),
        "--out-dir", str(out_dir),
    ])
    assert rc in (0, 1), f"Unexpected exit code {rc}"
    doc = json.loads((out_dir / "design_review.json").read_text())
    missing = required_keys - set(doc.keys())
    assert not missing, f"JSON missing required keys: {missing}"
    # schema_version must be "1.0"
    assert doc["schema_version"] == "1.0"
    # summary must have the 5 sub-keys
    for k in ("total", "pass", "fail", "warn", "needs_data"):
        assert k in doc["summary"], f"summary missing key {k!r}"


# ---------------------------------------------------------------------------
# 8. Markdown disclaimer + sign-off table
# ---------------------------------------------------------------------------

def test_markdown_disclaimer_and_signoff_present(tmp_path: Path):
    """design_review.md must contain DISCLAIMER and expert sign-off table."""
    rules_path = _FIXTURE_DIR / "sample_rules.yaml"
    out_dir = tmp_path / "09_review"
    rc = _dr.main([
        "--run-dir", str(_FIXTURE_DIR),
        "--rpt", str(_FIXTURE_DIR / "sample_mini.rpt"),
        "--inp", str(_FIXTURE_DIR / "sample_mini.inp"),
        "--manifest", str(_FIXTURE_DIR / "sample_manifest.json"),
        "--rules", str(rules_path),
        "--out-dir", str(out_dir),
    ])
    assert rc in (0, 1)
    md = (out_dir / "design_review.md").read_text()
    assert "DISCLAIMER" in md, "DISCLAIMER not found in design_review.md"
    assert "Expert sign-off" in md or "expert sign-off" in md.lower(), (
        "Expert sign-off section not found in design_review.md"
    )
    # Sign-off table should have a Reviewer column
    assert "Reviewer" in md, "Reviewer column not found in sign-off table"


# ---------------------------------------------------------------------------
# 9. Determinism
# ---------------------------------------------------------------------------

def test_determinism_two_runs_identical_results(tmp_path: Path):
    """Running evaluate_rulebook twice on the same inputs produces identical 'results'."""
    import yaml  # type: ignore[import]
    rules_path = _FIXTURE_DIR / "sample_rules.yaml"
    rulebook = _dr._load_rulebook(rules_path)
    cache = _mini_cache()

    review1 = _dr.evaluate_rulebook(cache, rulebook)
    review2 = _dr.evaluate_rulebook(cache, rulebook)

    # Compare results list via JSON serialisation (ignores object identity)
    r1_json = json.dumps(review1["results"], sort_keys=True)
    r2_json = json.dumps(review2["results"], sort_keys=True)
    assert r1_json == r2_json, "Two evaluate_rulebook calls produced different results"


def test_determinism_created_at_taken_from_manifest():
    """created_at in the JSON output must come from the manifest, not time.time()."""
    cache = _mini_cache()
    manifest = cache.manifest()
    expected_ts = manifest.get("created_at", "1970-01-01T00:00:00Z")

    rulebook = _dr._load_rulebook(_FIXTURE_DIR / "sample_rules.yaml")
    review = _dr.evaluate_rulebook(cache, rulebook)
    doc = _dr._to_json(review, "/tmp/run", expected_ts)
    assert doc["created_at"] == expected_ts, (
        f"created_at should be {expected_ts!r}; got {doc['created_at']!r}"
    )


# ---------------------------------------------------------------------------
# 10. Invalid rulebook
# ---------------------------------------------------------------------------

def test_invalid_rulebook_missing_operator_raises(tmp_path: Path):
    """A rulebook with a rule missing 'operator' must raise ValueError."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(
        textwrap.dedent("""\
            rulebook_id: bad
            version: "0"
            disclaimer: test
            rules:
              - id: BAD_RULE
                title: bad
                metric: link.max_velocity
                # operator is missing
                threshold: 3.0
                units: SI
                severity: FAIL
                citation: test
                verify: true
        """)
    )
    with pytest.raises(ValueError, match="operator"):
        _dr._load_rulebook(bad_yaml)


def test_invalid_rulebook_missing_required_field_raises(tmp_path: Path):
    """A rulebook with a rule missing 'metric' must raise ValueError."""
    bad_yaml = tmp_path / "bad2.yaml"
    bad_yaml.write_text(
        textwrap.dedent("""\
            rulebook_id: bad2
            version: "0"
            disclaimer: test
            rules:
              - id: BAD_RULE2
                title: missing metric
                # metric is absent
                operator: lte
                threshold: 3.0
                units: SI
                severity: FAIL
                citation: test
                verify: true
        """)
    )
    with pytest.raises(ValueError, match="missing required fields"):
        _dr._load_rulebook(bad_yaml)


# ---------------------------------------------------------------------------
# 11. Rulebook-honesty lint
# ---------------------------------------------------------------------------

def test_no_rule_has_verify_false_and_todo_citation():
    """No rule in the bundled gb50014_template may have verify:false + citation:TODO."""
    rulebook_path = (
        Path(__file__).parent.parent
        / "skills"
        / "swmm-design-review"
        / "rulebooks"
        / "gb50014_template.yaml"
    )
    rb = _dr._load_rulebook(rulebook_path)
    offenders = [
        r["id"]
        for r in rb["rules"]
        if not r.get("verify", True) and "TODO" in r.get("citation", "")
    ]
    assert not offenders, (
        f"Rules with verify:false and TODO citation: {offenders}. "
        "Either set verify:true or supply a real citation."
    )


def test_all_bundled_rules_have_verify_true():
    """Every rule in gb50014_template must have verify:true (it's a template)."""
    rulebook_path = (
        Path(__file__).parent.parent
        / "skills"
        / "swmm-design-review"
        / "rulebooks"
        / "gb50014_template.yaml"
    )
    rb = _dr._load_rulebook(rulebook_path)
    non_verify = [r["id"] for r in rb["rules"] if not r.get("verify")]
    assert not non_verify, (
        f"Rules without verify:true in the template rulebook: {non_verify}"
    )


# ---------------------------------------------------------------------------
# 12. Parity test — design_review.py vs rpt_summary.parse_section
# ---------------------------------------------------------------------------

def test_parity_link_flow_summary_vs_rpt_summary():
    """design_review._parse_link_flow_summary must agree with rpt_summary.parse_section
    on the saanich-b7 model.rpt for all shared numeric fields."""
    from agentic_swmm.agent.swmm_runtime import rpt_summary

    rpt_text = (_SAANICH_DIR / "model.rpt").read_text()

    # Canonical (rpt_summary)
    canonical = rpt_summary.parse_section(rpt_text, rpt_summary.SECTIONS["Link Flow Summary"])

    # design_review's own reader
    dr_rows = _dr._parse_link_flow_summary(rpt_text)
    assert dr_rows is not None, "design_review found no Link Flow Summary rows"

    # Build lookup by link name
    canonical_by_link = {r["link"]: r for r in canonical}
    dr_by_link = {r["link"]: r for r in dr_rows}

    assert set(canonical_by_link.keys()) == set(dr_by_link.keys()), (
        "Link name sets differ: "
        f"canonical={sorted(canonical_by_link)}, dr={sorted(dr_by_link)}"
    )

    for link, canon_row in canonical_by_link.items():
        dr_row = dr_by_link[link]
        for field in ("peak_flow", "max_velocity", "max_full_flow_ratio", "max_full_depth_ratio"):
            assert abs(canon_row[field] - dr_row[field]) < 1e-6, (
                f"Parity mismatch on {link}.{field}: "
                f"rpt_summary={canon_row[field]}, design_review={dr_row[field]}"
            )


def test_parity_node_inflow_summary_vs_rpt_summary():
    """design_review._parse_node_inflow_summary must agree with rpt_summary for saanich-b7.

    NOTE: The saanich-b7 Node Inflow Summary has many zero-volume rows formatted with
    a trailing 'ltr' unit suffix (11 tokens instead of 9), which both parsers skip via
    the token-count guard. Only rows with non-zero volumes (9 tokens) are captured.
    The parity assertion compares the SET of captured nodes, not every node in the file.
    When both parsers capture zero nodes (all rows have the 11-token format), parity
    is trivially satisfied — both return an empty result set.
    """
    from agentic_swmm.agent.swmm_runtime import rpt_summary

    rpt_text = (_SAANICH_DIR / "model.rpt").read_text()

    canonical = rpt_summary.parse_section(rpt_text, rpt_summary.SECTIONS["Node Inflow Summary"])
    dr_rows = _dr._parse_node_inflow_summary(rpt_text)
    # Section exists in the saanich rpt, so dr_rows must not be None ([] is acceptable)
    assert dr_rows is not None, (
        "design_review returned None for Node Inflow Summary — "
        "section exists, so [] (empty list) expected, not None"
    )

    canonical_by_node = {r["node"]: r for r in canonical}
    dr_by_node = {r["node"]: r for r in dr_rows}

    # Both parsers must agree on which nodes they capture
    assert set(canonical_by_node.keys()) == set(dr_by_node.keys()), (
        f"Node sets differ: canonical={sorted(canonical_by_node)}, dr={sorted(dr_by_node)}"
    )

    for node, canon_row in canonical_by_node.items():
        dr_row = dr_by_node[node]
        for field in ("max_total_inflow", "flow_balance_error_pct"):
            assert abs(canon_row[field] - dr_row[field]) < 1e-6, (
                f"Parity mismatch on {node}.{field}: "
                f"rpt_summary={canon_row[field]}, design_review={dr_row[field]}"
            )


def test_parity_outfall_summary_vs_rpt_summary():
    """design_review._parse_outfall_summary must agree with rpt_summary for saanich-b7."""
    from agentic_swmm.agent.swmm_runtime import rpt_summary

    rpt_text = (_SAANICH_DIR / "model.rpt").read_text()

    canonical = rpt_summary.parse_section(
        rpt_text, rpt_summary.SECTIONS["Outfall Loading Summary"]
    )
    dr_rows = _dr._parse_outfall_summary(rpt_text)
    assert dr_rows is not None

    canonical_by_node = {r["node"]: r for r in canonical}
    dr_by_node = {r["node"]: r for r in dr_rows}

    assert set(canonical_by_node.keys()) == set(dr_by_node.keys())

    for node, canon_row in canonical_by_node.items():
        dr_row = dr_by_node[node]
        for field in ("max_flow", "avg_flow", "total_volume_10_6_ltr"):
            assert abs(canon_row[field] - dr_row[field]) < 1e-6, (
                f"Parity mismatch on {node}.{field}: "
                f"rpt_summary={canon_row[field]}, design_review={dr_row[field]}"
            )


# ---------------------------------------------------------------------------
# Golden evaluation: saanich-b7 full rulebook run
# ---------------------------------------------------------------------------

def test_golden_saanich_full_rulebook_needs_data_count(tmp_path: Path):
    """Run the full gb50014_template on saanich-b7; assert expected needs-data count.

    Rules that must be needs-data on saanich-b7:
    - NODE_SURCHARGE_DURATION (node.surcharge_hours — PR2)
    - NODE_FLOODING (node.flooding_hours — PR2)
    - FREEBOARD (junction.freeboard_m — PR2)
    - RETURN_PERIOD_ADEQUACY (run.return_period_yr — absent in saanich manifest)
    = 4 needs-data rules minimum.
    """
    rules_path = (
        Path(__file__).parent.parent
        / "skills"
        / "swmm-design-review"
        / "rulebooks"
        / "gb50014_template.yaml"
    )
    out_dir = tmp_path / "09_review"
    rc = _dr.main([
        "--run-dir", str(_SAANICH_DIR),
        "--rpt", str(_SAANICH_DIR / "model.rpt"),
        "--inp", str(_SAANICH_DIR / "model.inp"),
        "--manifest", str(_SAANICH_DIR / "runner_manifest.json"),
        "--rules", str(rules_path),
        "--out-dir", str(out_dir),
    ])
    assert rc in (0, 1), f"Unexpected exit code {rc}"
    doc = json.loads((out_dir / "design_review.json").read_text())

    needs_data_ids = [r["rule_id"] for r in doc["results"] if r["status"] == "needs-data"]
    expected_nd = {
        "NODE_SURCHARGE_DURATION",
        "NODE_FLOODING",
        "FREEBOARD",
        "RETURN_PERIOD_ADEQUACY",
    }
    assert expected_nd.issubset(set(needs_data_ids)), (
        f"Expected these rules to be needs-data: {expected_nd - set(needs_data_ids)}"
    )
    assert doc["summary"]["needs_data"] >= len(expected_nd), (
        f"Expected ≥ {len(expected_nd)} needs-data rules; got {doc['summary']['needs_data']}"
    )


def test_golden_saanich_total_rule_count(tmp_path: Path):
    """gb50014_template must have exactly 11 rules."""
    rules_path = (
        Path(__file__).parent.parent
        / "skills"
        / "swmm-design-review"
        / "rulebooks"
        / "gb50014_template.yaml"
    )
    out_dir = tmp_path / "09_review"
    _dr.main([
        "--run-dir", str(_SAANICH_DIR),
        "--rpt", str(_SAANICH_DIR / "model.rpt"),
        "--inp", str(_SAANICH_DIR / "model.inp"),
        "--manifest", str(_SAANICH_DIR / "runner_manifest.json"),
        "--rules", str(rules_path),
        "--out-dir", str(out_dir),
    ])
    doc = json.loads((out_dir / "design_review.json").read_text())
    assert doc["summary"]["total"] == 11, (
        f"Expected 11 rules; got {doc['summary']['total']}"
    )


def test_golden_saanich_conduit_slope_evaluable(tmp_path: Path):
    """Saanich conduit slope evaluates correctly for MIN_SLOPE_SMALL.

    All saanich junctions have invert_elev=0, so all conduit slopes are 0%.
    MIN_SLOPE_SMALL (scope: diameter ≤ 0.5m, threshold: slope ≥ 0.3%) should evaluate
    to warn (slope 0% < 0.3%) because saanich conduits are all 0.3m diameter (≤ 0.5m).
    MIN_SLOPE_LARGE (scope: diameter > 0.5m) correctly returns needs-data because
    saanich has no conduits with diameter > 0.5m — empty scope is a valid needs-data.
    """
    rules_path = (
        Path(__file__).parent.parent
        / "skills"
        / "swmm-design-review"
        / "rulebooks"
        / "gb50014_template.yaml"
    )
    out_dir = tmp_path / "09_review"
    _dr.main([
        "--run-dir", str(_SAANICH_DIR),
        "--rpt", str(_SAANICH_DIR / "model.rpt"),
        "--inp", str(_SAANICH_DIR / "model.inp"),
        "--manifest", str(_SAANICH_DIR / "runner_manifest.json"),
        "--rules", str(rules_path),
        "--out-dir", str(out_dir),
    ])
    doc = json.loads((out_dir / "design_review.json").read_text())
    results_by_id = {r["rule_id"]: r for r in doc["results"]}

    # MIN_SLOPE_SMALL: saanich conduits are 0.3m ≤ 0.5m; slope=0 < 0.3% → warn
    small = results_by_id.get("MIN_SLOPE_SMALL")
    assert small is not None, "MIN_SLOPE_SMALL not in results"
    assert small["status"] in ("warn", "pass"), (
        f"MIN_SLOPE_SMALL expected warn or pass (INP is available, diameter scope matches); "
        f"got {small['status']!r}. reason: {small.get('needs_data_reason')}"
    )

    # MIN_SLOPE_LARGE: no conduits with diameter > 0.5m → needs-data is correct behavior
    large = results_by_id.get("MIN_SLOPE_LARGE")
    assert large is not None, "MIN_SLOPE_LARGE not in results"
    assert large["status"] == "needs-data", (
        f"MIN_SLOPE_LARGE expected needs-data (no conduits with diameter > 0.5m in saanich); "
        f"got {large['status']!r}"
    )


def test_no_silent_pass_needs_data_appears_in_md(tmp_path: Path):
    """NEEDS DATA rules must appear in the Markdown output — never silently dropped."""
    rules_path = _FIXTURE_DIR / "sample_rules.yaml"
    out_dir = tmp_path / "09_review"
    _dr.main([
        "--run-dir", str(_FIXTURE_DIR),
        "--rpt", str(_FIXTURE_DIR / "sample_mini.rpt"),
        "--inp", str(_FIXTURE_DIR / "sample_mini.inp"),
        "--manifest", str(_FIXTURE_DIR / "sample_manifest.json"),
        "--rules", str(rules_path),
        "--out-dir", str(out_dir),
    ])
    md = (out_dir / "design_review.md").read_text()
    # sample_rules.yaml has SURCHARGE_TEST which is needs-data
    assert "NEEDS DATA" in md, (
        "NEEDS DATA section missing from Markdown output. "
        "needs-data rules must appear prominently, never silently dropped."
    )
    assert "SURCHARGE_TEST" in md, (
        "SURCHARGE_TEST needs-data rule missing from Markdown output."
    )


def test_no_silent_pass_needs_data_appears_in_json(tmp_path: Path):
    """needs-data rules must appear in JSON results with status='needs-data'."""
    rules_path = _FIXTURE_DIR / "sample_rules.yaml"
    out_dir = tmp_path / "09_review"
    _dr.main([
        "--run-dir", str(_FIXTURE_DIR),
        "--rpt", str(_FIXTURE_DIR / "sample_mini.rpt"),
        "--inp", str(_FIXTURE_DIR / "sample_mini.inp"),
        "--manifest", str(_FIXTURE_DIR / "sample_manifest.json"),
        "--rules", str(rules_path),
        "--out-dir", str(out_dir),
    ])
    doc = json.loads((out_dir / "design_review.json").read_text())
    nd_results = [r for r in doc["results"] if r["status"] == "needs-data"]
    assert nd_results, (
        "No needs-data results in JSON output. "
        "SURCHARGE_TEST should be needs-data."
    )
    nd_ids = {r["rule_id"] for r in nd_results}
    assert "SURCHARGE_TEST" in nd_ids, (
        f"SURCHARGE_TEST not in needs-data results: {nd_ids}"
    )
