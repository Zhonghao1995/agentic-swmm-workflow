"""The parametric hits reach the planner as a prompt block (F-39, 2026-09-02).

Two live sessions, one with a parametric hit and one without, made identical
tool calls: the memory-informed policy resolved the case and logged the prior
run's stats, and nothing put them in front of the planner.
"""

from __future__ import annotations

from agentic_swmm.agent.memory_context import MemoryContext, parametric_memory_block
from agentic_swmm.memory.parametric_memory import ParametricRecord


def _hit(run_id: str, stamp: str, peak: float, status: str | None = None) -> ParametricRecord:
    return ParametricRecord(
        run_id=run_id, case_name="downtown-victoria-bc", swmm_version="5.2.4",
        qa_metrics={"peak_flow_value": peak, "peak_flow_node": "OUT_DMH002395", "peak_flow_unit": "CMS",
                    "peak_flow_time_hhmm": "05:00", "runoff_continuity_pct": -0.09, "flow_continuity_pct": 0.402},
        calibration_status=status, recorded_utc=stamp,
    )


class TestBlock:
    def test_no_hits_costs_nothing(self):
        assert parametric_memory_block(MemoryContext()) == ""

    def test_the_block_names_the_case_and_the_newest_run_first(self):
        ctx = MemoryContext(parametric_hits=[
            _hit("153216_downtown-victoria-bc_run", "2026-09-02T22:34:32Z", 0.116),
            _hit("153611_downtown-victoria-bc_run", "2026-09-02T22:38:40Z", 0.116),
        ])
        block = parametric_memory_block(ctx)
        lines = block.splitlines()
        assert lines[0] == '<parametric-memory case="downtown-victoria-bc" prior_runs="2">'
        assert lines[2].startswith("- 153611_downtown-victoria-bc_run (2026-09-02T22:38)")
        assert "peak 0.116 CMS at OUT_DMH002395 @05:00" in lines[2]
        assert "runoff continuity -0.09%; flow continuity 0.402%; calibration: uncalibrated" in lines[2]
        assert lines[-1] == "</parametric-memory>"
        assert "Do not re-fetch or re-run" in block

    def test_older_runs_fold_behind_the_limit(self):
        ctx = MemoryContext(parametric_hits=[_hit(f"r{i}", f"2026-09-0{i}T00:00:00Z", 0.1) for i in range(1, 6)])
        block = parametric_memory_block(ctx, limit=2)
        assert block.count("\n- r") == 2
        assert "... and 3 older run(s)" in block

    def test_a_recorded_calibration_status_is_carried(self):
        ctx = MemoryContext(parametric_hits=[_hit("r1", "2026-09-01T00:00:00Z", 0.1, "calibrated_against_observed")])
        assert "calibration: calibrated_against_observed" in parametric_memory_block(ctx)


# ---------------------------------------------------------------------------
# Planner wiring: a hit reaches system_prompt_extras before the first call.
# ---------------------------------------------------------------------------

import json
import os
import tempfile
from pathlib import Path

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.planner import Planner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from tests.test_onboarding_wiring import _ScriptedProvider

GOAL = ("Fetch a SWMM model from the Canada service for downtown Victoria BC, rainfall period "
        "November 1 to November 4 2023. Run the model and audit it.")


def _run_planner(tmp: Path, memory_dir: Path) -> tuple[Planner, list[dict]]:
    trace_path = tmp / "agent_trace.jsonl"
    registry = AgentToolRegistry()
    executor = AgentExecutor(registry, session_dir=tmp, trace_path=trace_path, dry_run=False, profile=Profile.QUICK)
    planner = Planner(provider=_ScriptedProvider(), registry=registry, max_steps=2, verbose=False, emit=lambda t: None)  # type: ignore[arg-type]
    prev = os.environ.get("AISWMM_MEMORY_DIR")
    os.environ["AISWMM_MEMORY_DIR"] = str(memory_dir)
    try:
        planner.run(goal=GOAL, session_dir=tmp, trace_path=trace_path, executor=executor, prior_session_state={})
    finally:
        if prev is None:
            os.environ.pop("AISWMM_MEMORY_DIR", None)
        else:
            os.environ["AISWMM_MEMORY_DIR"] = prev
    events = [json.loads(l) for l in trace_path.read_text(encoding="utf-8").splitlines() if l.strip()] if trace_path.exists() else []
    return planner, events


class TestPlannerWiring:
    def test_a_prior_run_of_the_place_reaches_the_prompt(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            memory_dir = tmp / "memory"
            memory_dir.mkdir()
            (memory_dir / "parametric_memory.jsonl").write_text(json.dumps({
                "schema_version": "1.0", "run_id": "153216_downtown-victoria-bc_run",
                "case_name": "downtown-victoria-bc", "swmm_version": "5.2.4", "model_structure": {},
                "qa_metrics": {"peak_flow_value": 0.116, "peak_flow_node": "OUT_DMH002395",
                               "runoff_continuity_pct": -0.09, "flow_continuity_pct": 0.402},
                "performance_metrics": {}, "watershed_classification": {},
                "recorded_utc": "2026-09-02T22:34:32Z",
            }) + "\n", encoding="utf-8")
            planner, events = _run_planner(tmp, memory_dir)
        blocks = [x for x in planner.system_prompt_extras if x.startswith("<parametric-memory")]
        assert len(blocks) == 1, planner.system_prompt_extras
        assert 'case="downtown-victoria-bc" prior_runs="1"' in blocks[0]
        assert "153216_downtown-victoria-bc_run" in blocks[0] and "peak 0.116" in blocks[0]
        injected = [e for e in events if e.get("event") == "memory_context_injected"]
        assert injected and injected[0]["case_name"] == "downtown-victoria-bc" and injected[0]["prior_runs"] == 1

    def test_a_fresh_case_injects_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            memory_dir = tmp / "memory"
            memory_dir.mkdir()
            planner, events = _run_planner(tmp, memory_dir)
        assert not [x for x in planner.system_prompt_extras if x.startswith("<parametric-memory")]
        assert not [e for e in events if e.get("event") == "memory_context_injected"]
