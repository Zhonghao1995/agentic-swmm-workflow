"""Handler-level convergence guard for the planner-facing SWMM chain
(issue #235, part 4b).

CONTEXT.md (~line 198) cites ``scripts/spike_swmmanywhere/05_e2e_chain.py``
as the project's end-to-end guard, but that script drives ``aiswmm``
*CLI verbs* (``run`` / ``audit`` / ``plot``) via subprocess. Nothing in the
suite exercised the AGENT-facing chain --
``run_swmm_inp -> audit_run -> plot_run`` -- at the tool-*handler* level,
which is the seam the LLM planner actually calls (through
``AgentToolRegistry.execute``). This test closes that gap: it drives the
three handlers through the real registry, which routes every one of them
through the real ``MCPPool`` (genuine ``node`` subprocess servers under
``mcp/swmm-runner`` / ``mcp/swmm-experiment-audit`` / ``mcp/swmm-plot``,
each of which shells out to the skill's Python script and, for the
runner, the bundled ``swmm5`` engine) -- not stubs. Skipped when a real
``swmm5`` is not on PATH, mirroring the project's established pattern
(``tests/test_swmm_run_gate_integration.py``,
``tests/test_case_grouping_alignment.py``).

The fixture is ``examples/todcreek/model_chicago5min.inp`` -- the
smallest shipped example by both line count and byte size, already the
project's go-to real-swmm5 fixture for fast smoke tests (see
``tests/test_rainfall_ensemble_swmm_smoke.py``,
``tests/test_sensitivity_oat.py``/``_morris``/``_sobol``,
``tests/test_calibrate_sceua_smoke.py``) and confirmed here to pass the
#288 preflight gate (``agentic_swmm.agent.swmm_runtime.preflight``).

HOME override -- protecting a real, local side effect
------------------------------------------------------
``audit_run``'s underlying script
(``skills/swmm-experiment-audit/scripts/audit_run.py``) defaults
``--obsidian-dir`` to ``~/Documents/Agentic-SWMM-Obsidian-Vault/...`` and
*unconditionally* writes a note there and rewrites the vault's
``Experiment Audit Index.md`` unless ``--no-obsidian`` is passed. The
agent-facing ``audit_run`` ToolSpec (``agentic_swmm/agent/tool_handlers/
swmm_audit.py::_audit_run_args``) and the ``audit_run`` MCP server
(``mcp/swmm-experiment-audit/server.js``) expose no argument that maps to
``--no-obsidian`` -- so, unlike ``aiswmm audit`` on the CLI (which passes
``--no-obsidian`` by default; see ``agentic_swmm/commands/audit.py``),
every agent-driven ``audit_run`` call writes into a developer's real
Obsidian vault with no way to opt out. Left unguarded, running this test
on a machine with that vault present (as this one has) would silently
rewrite real personal files. HOME is monkeypatched to a scratch directory
for the duration of the chain -- the same lever
``tests/conftest.py``'s ``isolated_home`` fixture uses -- so
``Path.home()`` resolves inside the scratch tree in every process in the
call chain (``runtime_env()`` pins ``PYTHON=sys.executable`` and snapshots
``os.environ`` at the moment each MCP child is lazily spawned, so the
override propagates from this test process to the ``node`` server to the
Python scripts it shells out to). ``swmm5`` resolution is unaffected: it
still falls back to ``shutil.which("swmm5")`` when
``$HOME/.aiswmm/swmm/swmm5`` is absent.

CONTRACT MISMATCH found while writing this guard
-------------------------------------------------
The bare chain does **not** converge: ``plot_run`` called with just
``run_dir`` + ``node`` (the only inputs its own ToolSpec description
tells the planner it needs -- "pick node ids from inspect_plot_options",
nothing said about ``rain_ts``) returns ``ok=False``. The underlying
error is the placeholder-rejection in
``skills/swmm-plot/scripts/plot_rain_runoff_si.py``: ``rain_ts`` defaults
to the literal string ``"<rainfall-series-name>"`` and the script refuses
to run with it. ``mcp/swmm-plot/server.js`` documents (in the comment
above its ``Args`` zod schema) that this placeholder is supposed to be
"unreachable in the agent-driven path" because
``planner._extract_plot_choice`` reads ``inspect_plot_options`` and
injects the resolved ``rain_ts``/``node`` before the call ever reaches
``tool_registry._plot_run_args``. That function no longer exists
anywhere under ``agentic_swmm/`` -- a repo-wide grep for
``_extract_plot_choice`` turns up only a stale comment reference in
``agentic_swmm/agent/intent_classifier.py`` (documenting where
``_is_negated`` was lifted from, not a live call site). The
auto-injection the MCP server's own comment relies on has rotted away;
today nothing but prompt-level convention (the system prompt telling the
LLM to call ``inspect_plot_options`` first and echo its ``defaults``
forward) stands between a planner and this failure -- confirmed
separately: supplying the exact ``rain_ts`` ``inspect_plot_options``
reports (``"TS_RAIN"`` for this fixture) makes the same ``plot_run`` call
converge (``ok=True``, PNG written). Originally reported as a
non-convergent chain and marked ``unittest.expectedFailure``; fixed in
issue #327 by making ``_plot_run_args`` resolve the default series
itself, so the plot leg below now asserts convergence like the others.
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent import mcp_pool
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


REPO_ROOT = Path(__file__).resolve().parents[1]
TODCREEK_INP = REPO_ROOT / "examples" / "todcreek" / "model_chicago5min.inp"


@unittest.skipUnless(
    shutil.which("swmm5") and TODCREEK_INP.exists(),
    "needs swmm5 + todcreek INP",
)
class HandlerChainConvergenceTests(unittest.TestCase):
    """``run_swmm_inp -> audit_run -> plot_run`` through the real registry.

    One real chain execution (``setUpClass``) backs two assertion
    methods: the leg that converges (``run_swmm_inp`` + ``audit_run``)
    is asserted without a decorator so a future regression there fails
    loudly; the leg that does not converge (``plot_run``) is isolated
    into its own ``expectedFailure``-marked method so it documents the
    known gap without masking a regression in the other two.
    """

    @classmethod
    def setUpClass(cls) -> None:
        mcp_pool.clear_session_pool()

        cls._real_home = os.environ.get("HOME")
        cls._scratch = TemporaryDirectory(prefix="handler-chain-convergence-")
        scratch_path = Path(cls._scratch.name)
        cls.run_dir = scratch_path / "run"
        session_dir = scratch_path / "session"
        session_dir.mkdir(parents=True, exist_ok=True)
        fake_home = scratch_path / "home"
        fake_home.mkdir(parents=True, exist_ok=True)

        cls.registry = AgentToolRegistry()
        try:
            # See module docstring: audit_run's script writes into the
            # real Obsidian vault under $HOME with no opt-out exposed by
            # the ToolSpec/MCP contract. Redirect HOME before the first
            # MCP call so every subprocess in the chain (node server ->
            # python script) inherits the scratch HOME instead.
            os.environ["HOME"] = str(fake_home)

            cls.run_result = cls.registry.execute(
                ToolCall(
                    "run_swmm_inp",
                    {
                        "inp_path": str(TODCREEK_INP),
                        "run_dir": str(cls.run_dir),
                        "node": "O1",
                    },
                ),
                session_dir,
            )
            cls.audit_result = cls.registry.execute(
                ToolCall("audit_run", {"run_dir": str(cls.run_dir)}),
                session_dir,
            )
            # Deliberately minimal args -- see CONTRACT MISMATCH above.
            cls.plot_result = cls.registry.execute(
                ToolCall(
                    "plot_run",
                    {"run_dir": str(cls.run_dir), "node": "O1"},
                ),
                session_dir,
            )
        finally:
            if cls._real_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = cls._real_home

    @classmethod
    def tearDownClass(cls) -> None:
        pool = mcp_pool.session_pool()
        if pool is not None:
            pool.shutdown()
        mcp_pool.clear_session_pool()
        cls._scratch.cleanup()

    def test_run_and_audit_converge(self) -> None:
        """``run_swmm_inp`` then ``audit_run`` succeed and leave artifacts."""
        self.assertTrue(
            self.run_result.get("ok"),
            msg=f"run_swmm_inp did not converge: {self.run_result!r}",
        )
        # ADR-0004: the agent path now lands runner outputs in the
        # canonical 06_runner stage dir instead of flat at run_dir root.
        rpt_path = self.run_dir / "06_runner" / "model.rpt"
        self.assertTrue(rpt_path.is_file(), msg=f"missing .rpt at {rpt_path}")

        self.assertTrue(
            self.audit_result.get("ok"),
            msg=f"audit_run did not converge: {self.audit_result!r}",
        )
        provenance_path = self.run_dir / "09_audit" / "experiment_provenance.json"
        self.assertTrue(
            provenance_path.is_file(),
            msg=f"missing audit record at {provenance_path}",
        )

    def test_plot_run_converges(self) -> None:
        """``plot_run`` on a bare run_dir+node call converges (issue #327).

        This leg was born ``expectedFailure``: the script rejected its
        ``rain_ts`` placeholder because the injection hop
        ``mcp/swmm-plot/server.js`` cites (``planner._extract_plot_choice``)
        no longer exists. Fixed by ``_plot_run_args`` resolving the same
        default ``inspect_plot_options`` reports (raingage-referenced
        series, else first) when the caller omits ``rain_ts``
        (tests/test_plot_run_rain_ts_default.py covers the mapper). This
        method now guards the fix end-to-end against the real script.
        """
        self.assertTrue(
            self.plot_result.get("ok"),
            msg=f"plot_run did not converge: {self.plot_result!r}",
        )
        # ADR-0004: plot_run's default output now lands in the canonical
        # 08_plot stage dir instead of the legacy 07_plots.
        fig_path = self.run_dir / "08_plot" / "fig_O1_series.png"
        self.assertTrue(fig_path.is_file(), msg=f"missing figure at {fig_path}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
