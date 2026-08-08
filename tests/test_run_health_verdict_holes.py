"""Regression tests: the two run-health verdict holes (ADR-0012 decision 3).

Hole 1: ``audit_run.derive_status`` trusted ``return_code == 0`` in its
runner-manifest fallback even though the same manifest carries
``run_ok``/``solver_errors`` and swmm5 exits 0 while writing
``ERROR n:`` lines. A no-qa audit of a solver-errored run reported
``status: pass`` (same honesty class as the benchmark-harness fix).

Hole 2: ``run_failures.jsonl`` (the operational failure ledger) was
wired only into the planner loop, so ``aiswmm run`` CLI failures, which
dominate real usage, never landed there. The honesty-gate trip in
``commands/run.py`` now records one row (tool ``aiswmm_run_cli``,
classified ``swmm_error``) without ever changing the exit code.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.commands import run as run_cmd
from agentic_swmm.memory.run_failures import read_run_failures
from agentic_swmm.utils.subprocess_runner import CommandResult


_AUDIT_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/swmm-experiment-audit/scripts/audit_run.py"
)


def _load_audit_module():
    spec = importlib.util.spec_from_file_location(
        "_audit_run_verdict_test", _AUDIT_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DeriveStatusRunOkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_audit_module()

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("_audit_run_verdict_test", None)

    def test_solver_errored_run_with_no_qa_is_fail(self) -> None:
        """The hole: rc==0 + ERROR lines used to derive 'pass'."""
        manifest = {
            "return_code": 0,
            "run_ok": False,
            "solver_errors": ["ERROR 141: ..."],
        }
        self.assertEqual(self.mod.derive_status({}, manifest, True), "fail")

    def test_clean_run_ok_is_pass(self) -> None:
        manifest = {"return_code": 0, "run_ok": True, "solver_errors": []}
        self.assertEqual(self.mod.derive_status({}, manifest, True), "pass")

    def test_legacy_manifest_without_run_ok_keeps_rc_fallback(self) -> None:
        self.assertEqual(
            self.mod.derive_status({}, {"return_code": 0}, True), "pass"
        )
        self.assertEqual(
            self.mod.derive_status({}, {"return_code": 2}, True), "fail"
        )

    def test_qa_verdict_still_outranks_the_manifest(self) -> None:
        qa_fail = {"fail_count": 1}
        manifest = {"return_code": 0, "run_ok": True}
        self.assertEqual(self.mod.derive_status(qa_fail, manifest, True), "fail")
        qa_pass = {"fail_count": 0}
        errored = {"return_code": 0, "run_ok": False}
        # qa is the audit's own gate; when present it wins by design.
        self.assertEqual(self.mod.derive_status(qa_pass, errored, True), "pass")


class CliRunFailureRecordingTests(unittest.TestCase):
    """The honesty-gate trip in ``aiswmm run`` lands in the ledger."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.inp = self.tmp / "broken.inp"
        self.inp.write_text("[TITLE]\nbroken\n", encoding="utf-8")
        self.run_dir = self.tmp / "run"
        self.memory_dir = self.tmp / "memory"
        self.memory_dir.mkdir()
        self._old_env = os.environ.get("AISWMM_MEMORY_DIR")
        os.environ["AISWMM_MEMORY_DIR"] = str(self.memory_dir)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        if self._old_env is None:
            os.environ.pop("AISWMM_MEMORY_DIR", None)
        else:
            os.environ["AISWMM_MEMORY_DIR"] = self._old_env

    def _invoke_with_swmm_error(self) -> int:
        runner_dir = self.run_dir / "05_runner"
        rpt = runner_dir / "model.rpt"
        out = runner_dir / "model.out"

        original_run_command = run_cmd.run_command

        def _stub_run_command(command, *, check: bool = True):
            runner_dir.mkdir(parents=True, exist_ok=True)
            rpt.write_text(
                "  EPA SWMM 5.2\n"
                "  ERROR 141: outfall OF1 has more than one inlet link.\n",
                encoding="utf-8",
            )
            out.write_bytes(b"")
            manifest = {
                "manifest_version": "1.0",
                "swmm5": {"cmd": "swmm5", "version": "5.2.4"},
                "files": {
                    "rpt": str(rpt),
                    "out": str(out),
                    "stdout": str(runner_dir / "stdout.txt"),
                    "stderr": str(runner_dir / "stderr.txt"),
                },
                "metrics": {
                    "peak": {"node": "J1", "peak": None, "source": None},
                    "continuity": {"continuity_error_percent": {}},
                },
                "return_code": 0,
            }
            return CommandResult(
                command=list(command),
                return_code=0,
                started_at_utc="2026-08-08T00:00:00+00:00",
                finished_at_utc="2026-08-08T00:00:01+00:00",
                stdout=json.dumps(manifest, indent=2),
                stderr="",
            )

        run_cmd.run_command = _stub_run_command  # type: ignore[assignment]
        self.addCleanup(
            lambda: setattr(run_cmd, "run_command", original_run_command)
        )

        args = argparse.Namespace(
            inp=self.inp,
            run_dir=self.run_dir,
            node="J1",
            rpt_name=None,
            out_name=None,
            quiet=False,
            case_id=None,
        )
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            try:
                return run_cmd.main(args) or 0
            except SystemExit as exc:  # pragma: no cover - defensive
                return int(exc.code or 0)

    def test_gate_trip_records_a_swmm_error_row(self) -> None:
        code = self._invoke_with_swmm_error()
        self.assertEqual(code, 1)
        rows = read_run_failures(self.memory_dir / "run_failures.jsonl")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.tool, "aiswmm_run_cli")
        self.assertEqual(row.failure_class, "swmm_error")
        self.assertEqual(row.run_id, self.run_dir.name)
        self.assertIn("ERROR 141", row.summary)

    def test_recording_failure_never_changes_the_exit_code(self) -> None:
        # Point the store at a path that cannot be a directory parent.
        os.environ["AISWMM_MEMORY_DIR"] = str(self.tmp / "not-a-dir-file")
        (self.tmp / "not-a-dir-file").write_text("occupied", encoding="utf-8")
        code = self._invoke_with_swmm_error()
        self.assertEqual(code, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
