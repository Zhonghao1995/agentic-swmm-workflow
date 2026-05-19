"""Round 1 — workflow-mode memory integration tests.

Each runnable :class:`WorkflowMode` adapter (``PreparedInpMode``,
``ExistingRunPlotMode``, ``AuditOnlyOrComparisonMode``) now consults
memory before invoking tools and (for ``PreparedInpMode``) runs the
SWMM pre/postflight gates around the actual SWMM call.

The tests exercise the four scenarios from the spec:

1. Empty memory + clean preflight/postflight → adapter runs as today.
2. Memory hit → ctx.memory_context populated, agent_trace receives
   a memory_consultation event.
3. Preflight FAIL → PlannerRun(ok=False) with the preflight narrative,
   no SWMM call.
4. Postflight FAIL → PlannerRun(ok=False) with the HITL-formatted
   postflight narrative, no plot call.

All four scenarios are driven through a single ``WorkflowContext``
with the four dependencies (gather_memory_context, preflight_inp,
postflight_qa, gates_disabled) injected via a mock
:class:`MemoryIntegration` so we never touch the on-disk store or
shell out to the SWMM binary.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_swmm.agent.memory_context import MemoryContext, ParametricRecord
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.workflow_modes import WorkflowContext, get_mode
from agentic_swmm.agent.workflow_modes._memory_integration import MemoryIntegration


# ---------------------------------------------------------------------------
# Fake executor + helpers (mirrors test_workflow_mode_adapter_run_parity.py)
# ---------------------------------------------------------------------------


class _FakeExecutor:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.dry_run = False
        self.recorded: list[ToolCall] = []

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        self.recorded.append(call)
        name = call.name
        if name == "inspect_plot_options":
            result = {
                "tool": name,
                "args": call.args,
                "ok": True,
                "summary": "options",
                "results": {
                    "rainfall_options": [{"name": "RAIN_A", "used_by_raingage": True}],
                    "node_options": ["J1", "OU2"],
                    "node_attribute_options": [
                        {"name": "Depth_above_invert", "label": "depth"},
                        {"name": "Total_inflow", "label": "flow"},
                    ],
                    "defaults": {
                        "node": "J1",
                        "node_attr": "Total_inflow",
                        "rain_ts": "RAIN_A",
                    },
                    "selections_needed": [],
                    "user_prompt": "",
                },
            }
        else:
            result = {"tool": name, "args": call.args, "ok": True, "summary": f"{name} ok"}
        self.results.append(result)
        return result


@dataclass
class _FakeReport:
    """Stand-in for a PreflightReport / QAReport."""

    status: str = "PASS"
    failures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    classifications: dict[str, str] = field(default_factory=dict)


def _make_integration(
    *,
    hits: list[ParametricRecord] | None = None,
    preflight_status: str = "PASS",
    postflight_status: str = "PASS",
    gates_disabled: bool = False,
) -> tuple[MemoryIntegration, dict[str, list]]:
    """Build a MemoryIntegration with mock callables.

    The second return value is a recorder dict so the test can assert
    which callables fired in which order.
    """
    recorder: dict[str, list] = {
        "consult": [],
        "preflight": [],
        "postflight": [],
    }

    def fake_gather(*, memory_dir, case_name, use_case=None, metrics_of_interest=()):
        recorder["consult"].append({"case_name": case_name})
        return MemoryContext(
            parametric_hits=list(hits or []),
            reference_thresholds={},
            summary=(
                f"{len(hits or [])} prior run(s) of {case_name}"
                if hits
                else ""
            ),
            provenance={"gathered_at_utc": "2026-05-19T00:00:00Z"},
        )

    def fake_preflight(inp_path):
        recorder["preflight"].append({"inp_path": str(inp_path)})
        report = _FakeReport(status=preflight_status)
        if preflight_status == "FAIL":
            report.failures.append(
                {"code": "zero_length_conduit", "detail": "C1 length=0"}
            )
        return report

    def fake_postflight(run_dir):
        recorder["postflight"].append({"run_dir": str(run_dir)})
        report = _FakeReport(status=postflight_status)
        if postflight_status == "FAIL":
            report.failures.append(
                {
                    "code": "runoff_continuity_pct",
                    "detail": "runoff_continuity_pct=42.0 classified FAIL",
                }
            )
            report.metrics["runoff_continuity_pct"] = 42.0
            report.classifications["runoff_continuity_pct"] = "FAIL"
        return report

    integration = MemoryIntegration(
        gather_memory_context=fake_gather,
        preflight_inp=fake_preflight,
        postflight_qa=fake_postflight,
        memory_dir=Path("/tmp/fake-memory-dir"),
        gates_disabled=lambda: gates_disabled,
    )
    return integration, recorder


def _build_context(
    tmp: Path,
    *,
    integration: MemoryIntegration | None,
    inp_path: str = "examples/tecnopolo/tecnopolo.inp",
    case_name: str | None = None,
    goal: str = "examples/tecnopolo/ run with depth on OU2",
) -> tuple[WorkflowContext, _FakeExecutor, Path]:
    """Construct a ``WorkflowContext`` wired with mocks."""
    session_dir = tmp / "session"
    session_dir.mkdir()
    trace_path = session_dir / "agent_trace.jsonl"
    executor = _FakeExecutor()
    ctx = WorkflowContext(
        goal=goal,
        session_dir=session_dir,
        plan=[],
        route={
            "mode": "prepared_inp_cli",
            "provided_values": {"inp_path": inp_path},
        },
        executor=executor,  # type: ignore[arg-type]
        emit=lambda text: None,
        trace_path=trace_path,
        memory_integration=integration,
        case_name=case_name,
    )
    return ctx, executor, trace_path


# ---------------------------------------------------------------------------
# PreparedInpMode integration tests
# ---------------------------------------------------------------------------


class PreparedInpMemoryIntegrationTests(unittest.TestCase):
    def test_empty_memory_runs_as_today(self) -> None:
        adapter = get_mode("prepared_inp_cli")
        assert adapter is not None
        integration, recorder = _make_integration(hits=[])

        with tempfile.TemporaryDirectory() as tmp:
            ctx, executor, trace_path = _build_context(
                Path(tmp), integration=integration, case_name="tecnopolo"
            )
            outcome = adapter.run(ctx)

        self.assertTrue(outcome.ok)
        self.assertIsNotNone(ctx.memory_context)
        self.assertEqual(ctx.memory_context.parametric_hit_count, 0)
        # All four tool calls fire when preflight+postflight pass.
        self.assertEqual(
            [call.name for call in executor.recorded],
            ["run_swmm_inp", "audit_run", "inspect_plot_options", "plot_run"],
        )
        # Consult + preflight + postflight each fired exactly once.
        self.assertEqual(len(recorder["consult"]), 1)
        self.assertEqual(len(recorder["preflight"]), 1)
        self.assertEqual(len(recorder["postflight"]), 1)

    def test_memory_hit_populates_context_and_emits_consultation(self) -> None:
        adapter = get_mode("prepared_inp_cli")
        assert adapter is not None
        hit = ParametricRecord(
            run_id="run_a",
            case_name="tecnopolo",
            recorded_utc="2026-05-01T00:00:00Z",
        )
        integration, _ = _make_integration(hits=[hit])

        with tempfile.TemporaryDirectory() as tmp:
            ctx, _, trace_path = _build_context(
                Path(tmp), integration=integration, case_name="tecnopolo"
            )
            outcome = adapter.run(ctx)

            self.assertTrue(outcome.ok)
            self.assertEqual(ctx.memory_context.parametric_hit_count, 1)
            # agent_trace.jsonl carries the memory_consultation mirror event.
            rows = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        consultation_events = [r for r in rows if r.get("event") == "memory_consultation"]
        self.assertEqual(len(consultation_events), 1)
        self.assertEqual(consultation_events[0]["kind"], "workflow_defaults")
        self.assertEqual(consultation_events[0]["evidence_count"], 1)
        self.assertEqual(
            consultation_events[0]["case_meta"], {"case_name": "tecnopolo"}
        )

    def test_preflight_fail_returns_planner_run_without_swmm_call(self) -> None:
        adapter = get_mode("prepared_inp_cli")
        assert adapter is not None
        integration, recorder = _make_integration(preflight_status="FAIL")

        with tempfile.TemporaryDirectory() as tmp:
            ctx, executor, _ = _build_context(
                Path(tmp), integration=integration, case_name="tecnopolo"
            )
            outcome = adapter.run(ctx)

        self.assertFalse(outcome.ok)
        # SWMM was never invoked: no tools fired after the gate.
        self.assertEqual(executor.recorded, [])
        # Preflight ran, postflight did not.
        self.assertEqual(len(recorder["preflight"]), 1)
        self.assertEqual(recorder["postflight"], [])
        self.assertIn("Preflight FAIL", outcome.final_text)
        self.assertIn("zero_length_conduit", outcome.final_text)

    def test_postflight_fail_surfaces_hitl_prompt_no_plot(self) -> None:
        adapter = get_mode("prepared_inp_cli")
        assert adapter is not None
        integration, recorder = _make_integration(postflight_status="FAIL")

        with tempfile.TemporaryDirectory() as tmp:
            ctx, executor, _ = _build_context(
                Path(tmp), integration=integration, case_name="tecnopolo"
            )
            outcome = adapter.run(ctx)

        self.assertFalse(outcome.ok)
        # SWMM fired; the post-flight FAIL refuses every downstream
        # consumer (audit, plot inspect, plot) per the spec —
        # "no plot, no audit final".
        names = [call.name for call in executor.recorded]
        self.assertIn("run_swmm_inp", names)
        self.assertNotIn("audit_run", names)
        self.assertNotIn("inspect_plot_options", names)
        self.assertNotIn("plot_run", names)
        self.assertEqual(len(recorder["postflight"]), 1)
        # The HITL-formatted prompt is in the final_text.
        self.assertIn("Postflight QA FAIL", outcome.final_text)
        self.assertIn("Memory escalation", outcome.final_text)
        self.assertIn("runoff_continuity_pct", outcome.final_text)

    def test_no_memory_integration_keeps_existing_behaviour(self) -> None:
        """An old-style call site passing no integration still works."""
        adapter = get_mode("prepared_inp_cli")
        assert adapter is not None
        with tempfile.TemporaryDirectory() as tmp:
            ctx, executor, _ = _build_context(
                Path(tmp), integration=None, case_name="tecnopolo"
            )
            outcome = adapter.run(ctx)
        self.assertTrue(outcome.ok)
        # All four tool calls fire (no gates blocked anything).
        self.assertEqual(
            [call.name for call in executor.recorded],
            ["run_swmm_inp", "audit_run", "inspect_plot_options", "plot_run"],
        )


# ---------------------------------------------------------------------------
# Opt-out flag behaviour for the SWMM gates
# ---------------------------------------------------------------------------


class SwmmGatesOptOutTests(unittest.TestCase):
    def test_gates_disabled_skips_preflight_and_postflight(self) -> None:
        adapter = get_mode("prepared_inp_cli")
        assert adapter is not None
        # Fixtures *would* fail if the gates fired — opt-out must
        # bypass them and let the SWMM tool sequence complete.
        integration, recorder = _make_integration(
            preflight_status="FAIL",
            postflight_status="FAIL",
            gates_disabled=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            ctx, executor, _ = _build_context(
                Path(tmp), integration=integration, case_name="tecnopolo"
            )
            outcome = adapter.run(ctx)
        self.assertTrue(outcome.ok)
        # Neither gate callable fired.
        self.assertEqual(recorder["preflight"], [])
        self.assertEqual(recorder["postflight"], [])
        # All four tools ran.
        self.assertEqual(
            [call.name for call in executor.recorded],
            ["run_swmm_inp", "audit_run", "inspect_plot_options", "plot_run"],
        )


# ---------------------------------------------------------------------------
# ExistingRunPlotMode + AuditOnlyOrComparisonMode integration
# ---------------------------------------------------------------------------


class ExistingRunPlotMemoryIntegrationTests(unittest.TestCase):
    def test_consult_fires_but_no_gates(self) -> None:
        adapter = get_mode("existing_run_plot")
        assert adapter is not None
        integration, recorder = _make_integration(
            preflight_status="FAIL",  # would block if it ran
            postflight_status="FAIL",  # would block if it ran
        )
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            session_dir.mkdir()
            executor = _FakeExecutor()
            ctx = WorkflowContext(
                goal="plot OU2 depth",
                session_dir=session_dir,
                plan=[],
                route={
                    "mode": "existing_run_plot",
                    "provided_values": {"run_dir": str(session_dir)},
                },
                executor=executor,  # type: ignore[arg-type]
                emit=lambda text: None,
                trace_path=session_dir / "agent_trace.jsonl",
                memory_integration=integration,
                case_name="tecnopolo",
            )
            outcome = adapter.run(ctx)
        # Memory consult fired exactly once.
        self.assertEqual(len(recorder["consult"]), 1)
        # No SWMM gate fired — this mode does not run SWMM.
        self.assertEqual(recorder["preflight"], [])
        self.assertEqual(recorder["postflight"], [])
        # The adapter still produced a valid PlannerRun (sequence
        # depends on the goal text — what matters here is that the
        # gate behaviour above did not abort the run).
        self.assertIsNotNone(outcome)


class AuditOnlyMemoryIntegrationTests(unittest.TestCase):
    def test_consult_fires_but_no_gates(self) -> None:
        adapter = get_mode("audit_only_or_comparison")
        assert adapter is not None
        integration, recorder = _make_integration()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            session_dir.mkdir()
            run_dir = Path(tmp) / "prior_run"
            run_dir.mkdir()
            executor = _FakeExecutor()
            ctx = WorkflowContext(
                goal="audit this run",
                session_dir=session_dir,
                plan=[],
                route={
                    "mode": "audit_only_or_comparison",
                    "provided_values": {"run_dir": str(run_dir)},
                },
                executor=executor,  # type: ignore[arg-type]
                emit=lambda text: None,
                trace_path=session_dir / "agent_trace.jsonl",
                memory_integration=integration,
                case_name="tecnopolo",
            )
            outcome = adapter.run(ctx)
        self.assertEqual(len(recorder["consult"]), 1)
        self.assertEqual(recorder["preflight"], [])
        self.assertEqual(recorder["postflight"], [])
        self.assertTrue(outcome.ok)


# ---------------------------------------------------------------------------
# Case-name resolution from session_dir fallback
# ---------------------------------------------------------------------------


class CaseNameResolutionTests(unittest.TestCase):
    def test_session_dir_slug_picked_when_no_explicit_case_name(self) -> None:
        from agentic_swmm.agent.workflow_modes._memory_hooks import _resolve_case_name

        @dataclass
        class _MiniCtx:
            session_dir: Path
            route: dict[str, Any] = field(default_factory=dict)
            case_name: str | None = None

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "120000_tecnopolo_run"
            session_dir.mkdir()
            ctx = _MiniCtx(session_dir=session_dir)
            self.assertEqual(_resolve_case_name(ctx), "tecnopolo")

    def test_explicit_case_name_wins(self) -> None:
        from agentic_swmm.agent.workflow_modes._memory_hooks import _resolve_case_name

        @dataclass
        class _MiniCtx:
            session_dir: Path
            route: dict[str, Any] = field(default_factory=dict)
            case_name: str | None = None

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "120000_othercase_run"
            session_dir.mkdir()
            ctx = _MiniCtx(session_dir=session_dir, case_name="explicit")
            self.assertEqual(_resolve_case_name(ctx), "explicit")


if __name__ == "__main__":
    unittest.main()
