"""Phase A — end-to-end proof that the agent path now fails a broken run.

Before the honesty wiring, swmm5 would write ``ERROR 205`` lines into the
.rpt yet return exit code 0, and the swmm-runner MCP server (which only
rejects on a non-zero exit) flowed that back to the agent as success. This
test drives the real MCP harness with a deliberately broken INP and asserts
the call now fails, with the manifest recording ``run_ok = false``.

Requires a real ``swmm5`` binary (like ``test_swmm_run_outfall_autodetect``);
skipped otherwise.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "skills/swmm-end-to-end/scripts/mcp_stdio_call.py"

# A bogus section makes swmm5 emit ``ERROR 205: invalid keyword`` lines while
# still returning exit code 0 — the exact silent-failure shape.
BROKEN_INP = """[TITLE]
broken model

[JUNK_SECTION]
this is not valid swmm
WANGWANG 123 456
"""


@unittest.skipUnless(shutil.which("swmm5"), "requires a real swmm5 binary")
class SwmmRunGateIntegrationTests(unittest.TestCase):
    def test_broken_inp_fails_on_agent_path_and_records_not_ok(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inp = tmp_path / "broken.inp"
            inp.write_text(BROKEN_INP, encoding="utf-8")
            run_dir = tmp_path / "run"
            response = tmp_path / "response.json"

            proc = subprocess.run(
                [
                    sys.executable, str(HARNESS),
                    "--server-dir", "mcp/swmm-runner",
                    "--tool", "swmm_run",
                    "--arguments-json",
                    json.dumps({"inp": str(inp), "runDir": str(run_dir), "node": "J1"}),
                    "--out-response", str(response),
                ],
                cwd=REPO_ROOT, capture_output=True, text=True,
            )

            # The agent path must reject the run (non-zero), not return success.
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("ERROR 205", proc.stderr)

            # The manifest is still written (auditors can see what happened),
            # with a structured not-ok verdict.
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["run_ok"])
            self.assertTrue(manifest["solver_errors"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
