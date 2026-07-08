"""``_plot_run_args`` resolves a ``rain_ts`` default (issue #327).

A bare ``plot_run`` call used to reach the plot script with its literal
``"<rainfall-series-name>"`` placeholder and fail: the planner hop that
once injected the choice (``planner._extract_plot_choice``, still cited
by ``mcp/swmm-plot/server.js``) no longer exists. The mapper now
resolves the same default ``inspect_plot_options`` reports — the
raingage-referenced series, else the first series found — while an
explicit ``rain_ts`` still wins and a series-free INP forwards nothing
(the script's own error stays the authority).

The end-to-end guard (real swmm5 + real MCP server) lives in
``tests/test_handler_chain_convergence.py``; this file pins the mapper
logic itself.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentic_swmm.agent import tool_registry as registry_mod
from agentic_swmm.agent.tool_handlers import _shared as shared_mod
from agentic_swmm.agent.tool_handlers.swmm_plot import _plot_run_args
from agentic_swmm.agent.types import ToolCall

INP_TWO_SERIES_GAGE_SECOND = """[TITLE]
fixture
[RAINGAGES]
RG1 VOLUME 0:05 1.0 TIMESERIES TS_RAIN
[TIMESERIES]
TS_OTHER 06/01/2022 00:00 0.0
TS_RAIN  06/01/2022 00:00 0.0
"""

INP_CUMULATIVE_GAGE = """[TITLE]
fixture
[RAINGAGES]
RG1 CUMULATIVE 0:05 1.0 TIMESERIES TS_CUM
[TIMESERIES]
TS_CUM 06/01/2022 00:00 0.0
"""

INP_NO_SERIES = """[TITLE]
fixture — no rainfall series at all
"""


def _seed_run_dir(root: Path, inp_text: str) -> Path:
    run_dir = root / "runs" / "agent" / "test-run"
    (run_dir / "04_builder").mkdir(parents=True)
    (run_dir / "05_runner").mkdir(parents=True)
    (run_dir / "04_builder" / "model.inp").write_text(inp_text, encoding="utf-8")
    (run_dir / "05_runner" / "model.out").write_bytes(b"\x00")
    return run_dir


def _with_fake_repo_root(tmp: Path, fn):
    """Repoint registry + shared repo_root to ``tmp`` for the test body."""
    orig_reg = registry_mod.repo_root
    orig_shared = shared_mod.repo_root
    registry_mod.repo_root = lambda: tmp  # type: ignore[assignment]
    shared_mod.repo_root = lambda: tmp  # type: ignore[assignment]
    try:
        return fn()
    finally:
        registry_mod.repo_root = orig_reg  # type: ignore[assignment]
        shared_mod.repo_root = orig_shared  # type: ignore[assignment]


class RainTsDefaultResolutionTests(unittest.TestCase):
    def _payload(self, inp_text: str, extra_args: dict | None = None) -> dict:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir = _seed_run_dir(tmp, inp_text)
            call = ToolCall(
                "plot_run",
                {"run_dir": str(run_dir.relative_to(tmp)), "node": "O1", **(extra_args or {})},
            )
            return _with_fake_repo_root(tmp, lambda: _plot_run_args(call, run_dir))

    def test_bare_call_resolves_raingage_referenced_series(self) -> None:
        """The gage-referenced series wins even when listed second —
        the same preference ``inspect_plot_options`` reports as default."""
        payload = self._payload(INP_TWO_SERIES_GAGE_SECOND)
        self.assertEqual(payload.get("rainTs"), "TS_RAIN")

    def test_explicit_rain_ts_still_wins(self) -> None:
        payload = self._payload(INP_TWO_SERIES_GAGE_SECOND, {"rain_ts": "TS_OTHER"})
        self.assertEqual(payload.get("rainTs"), "TS_OTHER")

    def test_cumulative_gage_forwards_rain_kind(self) -> None:
        """A CUMULATIVE gage carries rain_kind so the script converts
        cumulative depth correctly without the caller knowing to ask."""
        payload = self._payload(INP_CUMULATIVE_GAGE)
        self.assertEqual(payload.get("rainTs"), "TS_CUM")
        self.assertEqual(payload.get("rainKind"), "cumulative_depth_mm")

    def test_explicit_rain_kind_overrides_resolved_default(self) -> None:
        payload = self._payload(INP_CUMULATIVE_GAGE, {"rain_kind": "intensity_mm_hr"})
        self.assertEqual(payload.get("rainKind"), "intensity_mm_hr")

    def test_inp_without_series_forwards_nothing(self) -> None:
        """No series in the INP: the mapper stays silent and the plot
        script's own missing-rainfall error remains the authority."""
        payload = self._payload(INP_NO_SERIES)
        self.assertNotIn("rainTs", payload)
        self.assertNotIn("rainKind", payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
