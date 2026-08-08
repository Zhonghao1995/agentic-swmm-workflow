"""Regression tests: CLI flags do what their help says (2026-08-08 sweep).

Three classes closed here:

* ``--rebuild`` on ``aiswmm audit`` promised to "force re-scan of all
  runs ... (clears .last_sync.json)". The flag was never read anywhere
  AND no ``.last_sync.json`` mechanism exists in the codebase; the
  summariser re-scans on every refresh already. The flag is removed
  rather than implemented: building an incremental-sync system just to
  justify a force-rebuild switch would be inventing a feature to
  excuse decoration.
* ``--quiet`` was registered but unread on seven verbs. Five of them
  (cite, cite-param, compare, doctor, transfer) emit no chrome at all,
  so the uniform flag is vacuously honest there and stays (the
  per-verb flag-consistency guard pins the uniform surface). The two
  with real chrome next to structured output now actually suppress
  it: storm's "wrote ..." line, and uncertainty plan's "wrote ..."
  line plus (under ``--yes``, when no consent prompt runs) its
  estimate block.
* ``--from-library`` on ``aiswmm storm`` silently substituted the
  library entry's ``interval_min`` / ``peak_position`` for
  default-looking CLI values. Both substitutions now emit the standard
  silent-override warning line, matching the depth/idf pair in the
  same file.
"""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.cli import main as cli_main


class RemovedDecorativeFlagsTests(unittest.TestCase):
    def _rejects(self, argv: list[str]) -> None:
        with mock.patch("sys.stderr", io.StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                cli_main(argv)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("unrecognized arguments", err.getvalue())

    def test_audit_rebuild_flag_is_gone(self) -> None:
        self._rejects(["audit", "--run-dir", "runs/x", "--rebuild"])


_LIBRARY_YAML = """\
schema_version: "1.0"

chicago_hyetographs:
  test_region_100yr_3hr_1min:
    idf_params:
      a: 65.4
      b: 0.08
      c: 0.81
    peak_position: 0.4
    duration_min: 180
    interval_min: 1
    citation: test_fixture

huff_user_overrides: {}
scs_user_overrides: {}
user_curated: {}
"""


class StormChromeAndOverrideTests(unittest.TestCase):
    def _run_storm(self, tmp: Path, extra: list[str]) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out_buf), mock.patch("sys.stderr", err_buf):
            rc = cli_main(["storm", *extra])
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_quiet_suppresses_wrote_line(self) -> None:
        with TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "storm.txt"
            rc, out, _err = self._run_storm(
                Path(tmp),
                [
                    "--depth-mm", "25", "--duration-min", "60",
                    "--shape", "triangular",
                    "--out", str(out_file), "--quiet",
                ],
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out_file.exists())
        self.assertNotIn("wrote", out)

    def test_without_quiet_wrote_line_prints(self) -> None:
        with TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "storm.txt"
            rc, out, _err = self._run_storm(
                Path(tmp),
                [
                    "--depth-mm", "25", "--duration-min", "60",
                    "--shape", "triangular",
                    "--out", str(out_file),
                ],
            )
        self.assertEqual(rc, 0)
        self.assertIn("wrote", out)

    def test_library_interval_substitution_warns(self) -> None:
        """Pre-fix: the library's interval_min replaced the default 5
        with no signal anywhere."""
        with TemporaryDirectory() as tmp:
            lib = Path(tmp) / "library.yaml"
            lib.write_text(_LIBRARY_YAML, encoding="utf-8")
            out_file = Path(tmp) / "storm.txt"
            rc, _out, err = self._run_storm(
                Path(tmp),
                [
                    "--shape", "chicago",
                    "--from-library", "test_region_100yr_3hr_1min",
                    "--storm-library-path", str(lib),
                    "--out", str(out_file),
                ],
            )
        self.assertEqual(rc, 0)
        self.assertIn("warning:", err)
        self.assertIn("--interval-min", err)
        self.assertIn("interval_min=1", err)
        # peak_position 0.4 also differs from the default 0.5.
        self.assertIn("--peak-position", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
