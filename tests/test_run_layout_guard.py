"""Layout guard (ADR-0004 section 4): a sixth scheme fails CI on arrival.

Five run-directory layouts coexisted before ADR-0004 (two shifted
numbering generations, a flat agent path, and two upstream shapes),
kept invisible by alias-tolerant readers. The canonical registry now
lives in ``run_layout.py``; this guard makes any NEW deviation loud:

* the registry itself is frozen in shape (two-digit prefixes, ordered,
  collision-free, disjoint from legacy alias names), and
* a real ``run_swmm_inp -> audit_run -> plot_run`` chain must leave a
  run directory containing ONLY canonical entries.

Legacy names stay readable forever (``find_stage`` tolerance); they are
asserted here to be exactly that: read-side aliases, never writer
targets.
"""
from __future__ import annotations

import os
import re
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent import mcp_pool
from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall

REPO_ROOT = Path(__file__).resolve().parents[1]
TODCREEK_INP = REPO_ROOT / "examples" / "todcreek" / "model_chicago5min.inp"

_STAGE_RE = re.compile(r"^\d{2}_[a-z][a-z0-9_]*$")


class CanonicalRegistryShapeTests(unittest.TestCase):
    """The registry is the single source of truth; keep it well-formed."""

    def test_stage_names_are_two_digit_prefixed_and_ordered(self) -> None:
        for stage in run_layout.CANONICAL_STAGES:
            self.assertRegex(stage, _STAGE_RE)
        prefixes = [int(stage[:2]) for stage in run_layout.CANONICAL_STAGES]
        self.assertEqual(prefixes, sorted(prefixes))

    def test_no_number_collisions(self) -> None:
        prefixes = [stage[:2] for stage in run_layout.CANONICAL_STAGES]
        self.assertEqual(len(prefixes), len(set(prefixes)))

    def test_legacy_aliases_never_shadow_canonical_names(self) -> None:
        canonical = set(run_layout.CANONICAL_STAGES)
        for stage, aliases in run_layout.LEGACY_ALIASES.items():
            self.assertIn(stage, canonical)
            for alias in aliases:
                self.assertNotIn(
                    alias,
                    canonical,
                    f"{alias!r} is both a legacy alias and a canonical stage",
                )

    def test_stage_dir_rejects_unregistered_names(self) -> None:
        with TemporaryDirectory() as raw:
            with self.assertRaises(ValueError):
                run_layout.stage_dir(Path(raw), "12_new_scheme")


@unittest.skipUnless(
    shutil.which("swmm5") and TODCREEK_INP.exists(),
    "needs swmm5 + todcreek INP",
)
class FreshRunLayoutGuardTests(unittest.TestCase):
    """Run the real chain and assert the run dir is canonical-only."""

    @classmethod
    def setUpClass(cls) -> None:
        mcp_pool.clear_session_pool()
        cls._scratch = TemporaryDirectory()
        scratch = Path(cls._scratch.name)
        cls._real_home = os.environ.get("HOME")
        os.environ["HOME"] = str(scratch / "home")
        (scratch / "home").mkdir()
        try:
            mcp_pool.ensure_session_pool()
            registry = AgentToolRegistry()
            session_dir = scratch / "session"
            session_dir.mkdir()
            cls.run_dir = scratch / "guard-run"
            for call in (
                ToolCall(
                    "run_swmm_inp",
                    {"inp_path": str(TODCREEK_INP), "run_dir": str(cls.run_dir)},
                ),
                ToolCall("audit_run", {"run_dir": str(cls.run_dir)}),
                ToolCall("plot_run", {"run_dir": str(cls.run_dir), "node": "O1"}),
            ):
                result = registry.execute(call, session_dir)
                assert result.get("ok"), f"{call.name} failed: {result!r}"
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

    def test_run_dir_contains_only_canonical_entries(self) -> None:
        allowed_dirs = set(run_layout.CANONICAL_STAGES)
        allowed_files = set(run_layout.CANONICAL_ROOT_FILES)
        offenders = [
            entry.name
            for entry in sorted(self.run_dir.iterdir())
            if not (
                (entry.is_dir() and entry.name in allowed_dirs)
                or (entry.name in allowed_files)
            )
        ]
        self.assertEqual(
            offenders,
            [],
            "non-canonical entries in a FRESH run dir (a sixth layout "
            f"scheme is being born): {offenders}",
        )

    def test_chain_artifacts_landed_in_their_reserved_spots(self) -> None:
        runner = self.run_dir / run_layout.RUNNER
        self.assertTrue((runner / "model.rpt").is_file())
        self.assertTrue((runner / "model.out").is_file())
        self.assertTrue(
            (self.run_dir / run_layout.AUDIT / "experiment_provenance.json").is_file()
        )
        plots = list((self.run_dir / run_layout.PLOT).glob("fig_*.png"))
        self.assertTrue(plots, "no figure under 08_plot/")
        # And the root is not silently collecting flat engine outputs.
        self.assertFalse((self.run_dir / "model.rpt").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
