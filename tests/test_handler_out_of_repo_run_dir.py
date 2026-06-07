"""Regression test for Bug #238 (path-sandbox part).

The synth→audit→run→plot chain can span an out-of-repo run_dir
(SWMManywhere puts its outputs in arbitrary user directories).
``audit_run`` and ``synth_swmm_from_bbox`` already accept out-of-repo
paths. This test confirms that ``run_swmm_inp`` and ``plot_run`` also
accept out-of-repo run dirs — they must NOT return the "must be inside
repository" failure message.

``map_run`` is excluded: it passes run_dir straight to the CLI without
a repo check, so it already works and is covered in
``tests/test_tool_handlers_swmm_map.py``.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.types import ToolCall


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_out_of_repo_run_dir(parent: Path) -> Path:
    """Create a minimal fake run dir that ``_find_inp``/``_find_out`` can
    discover.  Only ``_plot_run_args`` reads the manifest; no manifest
    means manifest defaults (glob-based discovery) kick in.
    """
    run_dir = parent / "out_of_repo_run"
    (run_dir / "04_builder").mkdir(parents=True)
    (run_dir / "05_runner").mkdir(parents=True)
    (run_dir / "04_builder" / "model.inp").write_text(
        "[TITLE]\nfixture\n", encoding="utf-8"
    )
    (run_dir / "05_runner" / "model.out").write_bytes(b"\x00")
    return run_dir


# ── test cases ───────────────────────────────────────────────────────────────

class RunSwmmInpOutOfRepoRunDirTests(unittest.TestCase):
    """``run_swmm_inp`` must accept an out-of-repo ``run_dir``."""

    def test_out_of_repo_run_dir_does_not_produce_must_be_inside_repository(self) -> None:
        """Bug #238: _optional_repo_output_dir rejected out-of-repo run_dir."""
        from agentic_swmm.agent.tool_handlers.swmm_runner import _run_swmm_inp_args

        with TemporaryDirectory() as tmp:
            run_dir = _make_out_of_repo_run_dir(Path(tmp))
            # Supply a minimal valid inp_path so inp resolution succeeds —
            # we're testing run_dir, not inp validation.
            call = ToolCall(
                "run_swmm_inp",
                {
                    "inp_path": str(run_dir / "04_builder" / "model.inp"),
                    "run_dir": str(run_dir),
                },
            )
            result = _run_swmm_inp_args(call, Path(tmp))

        # Must NOT have been rejected with the sandbox error.
        self.assertNotIn(
            "must be inside repository",
            str(result.get("summary", "")),
            msg="run_swmm_inp must accept out-of-repo run_dir; got: " + repr(result),
        )
        # On success the mapper returns a dict with 'runDir', not 'ok=False'.
        self.assertIn(
            "runDir",
            result,
            msg="successful args-mapper must return runDir key; got: " + repr(result),
        )


class PlotRunOutOfRepoRunDirTests(unittest.TestCase):
    """``plot_run`` must accept an out-of-repo ``run_dir``."""

    def test_out_of_repo_run_dir_does_not_produce_must_be_inside_repository(self) -> None:
        """Bug #238: _required_repo_dir rejected out-of-repo run_dir in plot."""
        from agentic_swmm.agent.tool_handlers.swmm_plot import _plot_run_args

        with TemporaryDirectory() as tmp:
            run_dir = _make_out_of_repo_run_dir(Path(tmp))
            call = ToolCall(
                "plot_run",
                {
                    "run_dir": str(run_dir),
                    "node": "O1",
                },
            )
            result = _plot_run_args(call, Path(tmp))

        self.assertNotIn(
            "must be inside repository",
            str(result.get("summary", "")),
            msg="plot_run must accept out-of-repo run_dir; got: " + repr(result),
        )
        # On success the mapper returns the MCP args with 'inp'.
        self.assertIn(
            "inp",
            result,
            msg="successful plot args-mapper must return inp key; got: " + repr(result),
        )


class InRepoRunDirContinuesToWorkTests(unittest.TestCase):
    """No regression: in-repo run_dir paths must still be accepted."""

    def test_in_repo_run_dir_accepted_by_run_swmm_inp(self) -> None:
        from agentic_swmm.agent.tool_handlers.swmm_runner import _run_swmm_inp_args
        from agentic_swmm.agent.tool_handlers._shared import _repo_path
        import agentic_swmm.agent.tool_registry as registry_mod
        import agentic_swmm.agent.tool_handlers._shared as shared_mod

        with TemporaryDirectory() as tmp:
            fake_root = Path(tmp)
            run_dir = fake_root / "runs" / "agent" / "test-run"
            (run_dir / "04_builder").mkdir(parents=True)
            (run_dir / "05_runner").mkdir(parents=True)
            (run_dir / "04_builder" / "model.inp").write_text(
                "[TITLE]\nfixture\n", encoding="utf-8"
            )
            (run_dir / "05_runner" / "model.out").write_bytes(b"\x00")

            orig_registry = registry_mod.repo_root
            orig_shared = shared_mod.repo_root
            registry_mod.repo_root = lambda: fake_root  # type: ignore[assignment]
            shared_mod.repo_root = lambda: fake_root  # type: ignore[assignment]
            try:
                call = ToolCall(
                    "run_swmm_inp",
                    {
                        "inp_path": str(run_dir / "04_builder" / "model.inp"),
                        "run_dir": str(run_dir),
                    },
                )
                result = _run_swmm_inp_args(call, fake_root)
            finally:
                registry_mod.repo_root = orig_registry  # type: ignore[assignment]
                shared_mod.repo_root = orig_shared  # type: ignore[assignment]

        self.assertIn(
            "runDir",
            result,
            msg="in-repo run_dir must still be accepted; got: " + repr(result),
        )
        self.assertNotIn(
            "must be inside repository",
            str(result.get("summary", "")),
        )

    def test_in_repo_run_dir_accepted_by_plot_run(self) -> None:
        from agentic_swmm.agent.tool_handlers.swmm_plot import _plot_run_args
        import agentic_swmm.agent.tool_registry as registry_mod
        import agentic_swmm.agent.tool_handlers._shared as shared_mod

        with TemporaryDirectory() as tmp:
            fake_root = Path(tmp)
            run_dir = fake_root / "runs" / "agent" / "test-run"
            (run_dir / "04_builder").mkdir(parents=True)
            (run_dir / "05_runner").mkdir(parents=True)
            (run_dir / "04_builder" / "model.inp").write_text(
                "[TITLE]\nfixture\n", encoding="utf-8"
            )
            (run_dir / "05_runner" / "model.out").write_bytes(b"\x00")

            orig_registry = registry_mod.repo_root
            orig_shared = shared_mod.repo_root
            registry_mod.repo_root = lambda: fake_root  # type: ignore[assignment]
            shared_mod.repo_root = lambda: fake_root  # type: ignore[assignment]
            try:
                call = ToolCall(
                    "plot_run",
                    {
                        "run_dir": str(run_dir),
                        "node": "O1",
                    },
                )
                result = _plot_run_args(call, fake_root)
            finally:
                registry_mod.repo_root = orig_registry  # type: ignore[assignment]
                shared_mod.repo_root = orig_shared  # type: ignore[assignment]

        self.assertIn(
            "inp",
            result,
            msg="in-repo run_dir must still be accepted; got: " + repr(result),
        )
        self.assertNotIn(
            "must be inside repository",
            str(result.get("summary", "")),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
