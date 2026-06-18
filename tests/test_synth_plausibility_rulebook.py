"""Guard tests for the synth_plausibility rulebook.

`skills/swmm-design-review/rulebooks/synth_plausibility.yaml` is the
reference-free plausibility rulebook for SWMManywhere-style synthesized
networks. It must:

1. load + pass the design_review schema validator;
2. use ONLY metrics the extractor can actually resolve (no PR2 / needs-data
   metrics) so it produces a usable score instead of a wall of needs-data;
3. carry real citations and verify:true on every rule (honesty invariant);
4. evaluate live against a real SI model (the saanich-b7 fixture) — at least
   the rpt-based velocity rule must resolve to a real pass/warn, proving the
   rulebook is wired to live metrics.

Import pattern mirrors tests/test_design_review_engine.py: the design_review
script is standalone (import-free from agentic_swmm), loaded by path.
"""

from __future__ import annotations

import importlib.util as _ilu
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = (
    Path(__file__).parent.parent / "skills" / "swmm-design-review" / "scripts"
)
_RULEBOOK = (
    Path(__file__).parent.parent
    / "skills"
    / "swmm-design-review"
    / "rulebooks"
    / "synth_plausibility.yaml"
)
_SAANICH_DIR = (
    Path(__file__).parent.parent
    / "docs"
    / "framework-validation"
    / "saanich-b7-network-routed-20260513"
)

if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def _load_dr() -> Any:
    spec = _ilu.spec_from_file_location(
        "design_review", _SCRIPT_DIR / "design_review.py"
    )
    mod = _ilu.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_dr = _load_dr()


# Metrics the extract_metric() resolver can actually produce a value for.
# Anything outside this set resolves to needs-data by construction, which
# would make a plausibility rule un-scorable.
_WORKING_METRICS = {
    "run.peak_flow",
    "run.continuity_error_pct",
    "link.max_velocity",
    "link.max_full_flow_ratio",
    "link.max_full_depth_ratio",
    "link.peak_flow",
    "outfall.max_flow",
    "node.flow_balance_error_pct",
    "node.max_total_inflow",
    "conduit.slope_pct",
    "conduit.diameter_m",
    "conduit.roughness",
}


def _rulebook() -> dict[str, Any]:
    return _dr._load_rulebook(_RULEBOOK)


def test_rulebook_loads_and_has_rules():
    rb = _rulebook()
    assert rb["rulebook_id"] == "synth_plausibility"
    assert len(rb["rules"]) >= 1


def test_every_metric_is_resolvable_no_needs_data_by_construction():
    """No rule may reference a PR2 / needs-data metric — the whole point of the
    synth rulebook is that it scores without observed data or PR2 extensions."""
    offenders = [
        (r["id"], r["metric"])
        for r in _rulebook()["rules"]
        if r["metric"] not in _WORKING_METRICS
    ]
    assert not offenders, (
        f"Rules using non-resolvable metrics: {offenders}. "
        f"Allowed: {sorted(_WORKING_METRICS)}"
    )


def test_continuity_not_duplicated_postflight_owns_it():
    """Continuity is the postflight hard gate's job; keep it out of this
    rulebook so the two layers don't duplicate (responsibility boundary)."""
    metrics = {r["metric"] for r in _rulebook()["rules"]}
    assert "run.continuity_error_pct" not in metrics


def test_honesty_real_citations_and_verify_true():
    """Every rule carries verify:true and a real (non-TODO) citation."""
    for r in _rulebook()["rules"]:
        assert r.get("verify") is True, f"{r['id']} must have verify:true"
        citation = r.get("citation", "")
        assert "TODO" not in citation, (
            f"{r['id']} still has a placeholder TODO citation: {citation!r}"
        )
        assert len(citation) > 20, f"{r['id']} citation looks empty: {citation!r}"


def test_evaluates_live_against_saanich_si_fixture():
    """The rulebook must produce real scores on a real SI model, not a wall of
    needs-data. The rpt-based velocity rule is guaranteed available on saanich."""
    cache = _dr.ArtifactCache(
        manifest_path=_SAANICH_DIR / "runner_manifest.json",
        rpt_path=_SAANICH_DIR / "model.rpt",
        inp_path=_SAANICH_DIR / "model.inp",
    )
    review = _dr.evaluate_rulebook(cache, _rulebook())
    summary = review["summary"]

    # At least one rule actually evaluated (not everything is needs-data).
    assert summary["needs_data"] < summary["total"], (
        f"Whole rulebook fell to needs-data on saanich: {summary}"
    )

    by_id = {r["rule_id"]: r for r in review["results"]}
    vmax = by_id["VELOCITY_MAX_PLAUSIBLE"]
    assert vmax["status"] in {"pass", "warn"}, (
        f"VELOCITY_MAX_PLAUSIBLE should resolve on saanich, got {vmax['status']!r} "
        f"({vmax.get('needs_data_reason')})"
    )
