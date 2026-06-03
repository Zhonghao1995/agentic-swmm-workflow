"""Phase A — runner honesty parity, timeout, and version advisory.

The agent path reaches ``swmm5`` through the ``swmm-runner`` MCP server,
which spawns ``skills/swmm-runner/scripts/swmm_runner.py run`` and only
rejects on a non-zero exit code. Historically the script returned 0 even
when SWMM wrote ``ERROR <n>:`` lines into the .rpt (and even on a non-zero
swmm5 return code), so a failed run flowed back to the agent as success.

These tests pin the fix:

* the runner manifest carries a structured ``run_ok`` + ``solver_errors``
  verdict and a ``swmm5.version_ok`` advisory (additive — ``manifest_version``
  stays ``"1.0"``);
* under ``--gate`` (the flag the MCP server passes on the agent path) a
  not-ok run exits non-zero so the MCP layer surfaces the failure;
* without ``--gate`` (the CLI path, which runs its own honesty scan) the
  script keeps the legacy exit-0 behaviour so the pure-JSON-stdout contract
  in ``test_run_swmm_error_stream_separation`` is untouched;
* ``run_swmm`` bounds swmm5 with a timeout instead of hanging forever.

Tests load the script as a module (mirroring
``test_swmm_runner_peak_parser``) and stub ``run_swmm`` / ``subprocess`` so
no real swmm5 binary is needed.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "skills" / "swmm-runner" / "scripts" / "swmm_runner.py"


def load_runner_module():
    spec = importlib.util.spec_from_file_location("swmm_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLEAN_RPT = (
    "  EPA SWMM 5.2 (Build 5.2.4)\n"
    "  Flow Routing Continuity\n"
    "  Continuity Error (%) .....  0.123\n"
)

ERROR_RPT = (
    "  EPA SWMM 5.2 (Build 5.2.4)\n"
    "  ERROR 138: invalid keyword at line 11 of input file:\n"
    "  ERROR 145: cannot read node depth.\n"
)


class ScanRptForErrorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = load_runner_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def _rpt(self, text: str) -> Path:
        p = self.tmp_path / "model.rpt"
        p.write_text(text, encoding="utf-8")
        return p

    def test_returns_verbatim_error_lines(self) -> None:
        errs = self.runner.scan_rpt_for_errors(self._rpt(ERROR_RPT))
        self.assertEqual(len(errs), 2)
        self.assertTrue(errs[0].startswith("ERROR 138:"))

    def test_clean_rpt_returns_empty(self) -> None:
        self.assertEqual(self.runner.scan_rpt_for_errors(self._rpt(CLEAN_RPT)), [])

    def test_missing_rpt_returns_empty(self) -> None:
        self.assertEqual(
            self.runner.scan_rpt_for_errors(self.tmp_path / "nope.rpt"), []
        )

    def test_narrative_error_word_does_not_false_positive(self) -> None:
        # "error" inside a continuity narrative must not match — only the
        # canonical ``ERROR <digits>:`` form is a solver error.
        rpt = self._rpt("  the continuity error is small\n  Routing Error term\n")
        self.assertEqual(self.runner.scan_rpt_for_errors(rpt), [])


class CmdRunHonestyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = load_runner_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.inp = self.tmp_path / "model.inp"
        self.inp.write_text("[TITLE]\ntest\n", encoding="utf-8")
        self.run_dir = self.tmp_path / "run"
        # Default: swmm5 version matches the pin so version advisory is silent
        # unless a test overrides it.
        self.runner.get_swmm5_version = lambda: self.runner.EXPECTED_SWMM_VERSION

    def _args(self, **over):
        base = dict(
            inp=self.inp,
            run_dir=self.run_dir,
            node="O1",
            rpt_name=None,
            out_name=None,
            timeout=600.0,
            gate=False,
        )
        base.update(over)
        return types.SimpleNamespace(**base)

    def _stub_run(self, rpt_text: str, rc: int = 0):
        def fake(inp, rpt, out, stdout_path, stderr_path, timeout=600.0):
            rpt.write_text(rpt_text, encoding="utf-8")
            out.write_bytes(b"")
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            return rc

        self.runner.run_swmm = fake

    def _manifest(self) -> dict:
        return json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))

    # --- manifest verdict fields (additive, both paths) ---

    def test_clean_run_marks_run_ok_true(self) -> None:
        self._stub_run(CLEAN_RPT, rc=0)
        self.runner.cmd_run(self._args())
        m = self._manifest()
        self.assertTrue(m["run_ok"])
        self.assertEqual(m["solver_errors"], [])
        self.assertEqual(m["manifest_version"], "1.0")  # not bumped

    def test_solver_error_marks_run_ok_false(self) -> None:
        self._stub_run(ERROR_RPT, rc=0)
        self.runner.cmd_run(self._args(gate=False))
        m = self._manifest()
        self.assertFalse(m["run_ok"])
        self.assertEqual(len(m["solver_errors"]), 2)

    def test_nonzero_returncode_marks_run_ok_false(self) -> None:
        self._stub_run(CLEAN_RPT, rc=1)
        self.runner.cmd_run(self._args(gate=False))
        self.assertFalse(self._manifest()["run_ok"])

    # --- gate behaviour (agent path vs CLI path) ---

    def test_gate_exits_nonzero_on_solver_error(self) -> None:
        self._stub_run(ERROR_RPT, rc=0)
        with self.assertRaises(SystemExit) as cm:
            self.runner.cmd_run(self._args(gate=True))
        self.assertNotEqual(cm.exception.code, 0)
        # manifest still written before exit so auditors see the failure
        self.assertFalse(self._manifest()["run_ok"])

    def test_gate_does_not_exit_on_clean_run(self) -> None:
        self._stub_run(CLEAN_RPT, rc=0)
        self.runner.cmd_run(self._args(gate=True))  # must not raise
        self.assertTrue(self._manifest()["run_ok"])

    def test_without_gate_does_not_exit_on_error(self) -> None:
        # CLI path: legacy exit-0 preserved (CLI runs its own honesty scan).
        self._stub_run(ERROR_RPT, rc=0)
        self.runner.cmd_run(self._args(gate=False))  # must not raise
        self.assertFalse(self._manifest()["run_ok"])

    # --- version advisory (never a hard failure) ---

    def test_version_mismatch_is_advisory_only(self) -> None:
        self.runner.get_swmm5_version = lambda: "5.1.0"
        self._stub_run(CLEAN_RPT, rc=0)
        self.runner.cmd_run(self._args(gate=True))  # advisory must not gate
        m = self._manifest()
        self.assertFalse(m["swmm5"]["version_ok"])
        self.assertIsNotNone(m["swmm5"]["version_warning"])
        self.assertTrue(m["run_ok"])  # version mismatch does not fail the run

    def test_version_match_is_ok_and_silent(self) -> None:
        self._stub_run(CLEAN_RPT, rc=0)
        self.runner.cmd_run(self._args())
        m = self._manifest()
        self.assertTrue(m["swmm5"]["version_ok"])
        self.assertIsNone(m["swmm5"]["version_warning"])

    def test_unknown_version_is_advisory(self) -> None:
        self.runner.get_swmm5_version = lambda: None
        self._stub_run(CLEAN_RPT, rc=0)
        self.runner.cmd_run(self._args())
        m = self._manifest()
        self.assertFalse(m["swmm5"]["version_ok"])
        self.assertIsNotNone(m["swmm5"]["version_warning"])


class RunSwmmTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = load_runner_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_timeout_returns_sentinel_and_writes_stderr(self) -> None:
        def fake_run(cmd, capture_output=True, text=True, timeout=None):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        # Swap only the module's ``subprocess`` reference so we don't patch
        # the real subprocess module process-wide.
        self.runner.subprocess = types.SimpleNamespace(
            run=fake_run, TimeoutExpired=subprocess.TimeoutExpired
        )

        rpt = self.tmp_path / "model.rpt"
        out = self.tmp_path / "model.out"
        so = self.tmp_path / "stdout.txt"
        se = self.tmp_path / "stderr.txt"
        rc = self.runner.run_swmm(
            self.tmp_path / "x.inp", rpt, out, so, se, timeout=0.01
        )
        self.assertEqual(rc, self.runner.SWMM_TIMEOUT_RC)
        self.assertIn("timed out", se.read_text(encoding="utf-8").lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
