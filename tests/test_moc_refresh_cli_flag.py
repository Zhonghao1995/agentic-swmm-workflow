"""UX-5 (issue #60): ``aiswmm audit --refresh-moc`` force-refresh path.

The flag short-circuits the full audit pipeline. It regenerates
``runs/INDEX.md`` against the current ``runs/`` tree and exits 0. It
must NOT call the audit subprocess, must NOT write into ``09_audit/``,
and must NOT require ``--run-dir``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]


CHAT_NOTE = """---
type: chat-session
case: hello-from-test
date: 2026-05-14
goal: "say hi"
status: ok
tags:
  - agentic-swmm
  - chat-session
---

# Chat Session - hello-from-test
"""


def _seed_chat_session(runs_root: Path) -> Path:
    """Create one chat session under ``runs/`` so the MOC has content."""
    session_dir = runs_root / "2026-05-14" / "120000_hello_chat"
    session_dir.mkdir(parents=True)
    (session_dir / "session_state.json").write_text("{}", encoding="utf-8")
    (session_dir / "agent_trace.jsonl").write_text("", encoding="utf-8")
    (session_dir / "chat_note.md").write_text(CHAT_NOTE, encoding="utf-8")
    return session_dir


class RefreshMocFlagTests(unittest.TestCase):
    def test_refresh_moc_writes_runs_index_without_audit_artefacts(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_root = tmp_path / "runs"
            runs_root.mkdir(parents=True)
            session_dir = _seed_chat_session(runs_root)

            env = os.environ.copy()
            env["AISWMM_RUNS_ROOT"] = str(runs_root)

            proc = subprocess.run(
                [sys.executable, "-m", "agentic_swmm.cli", "audit", "--refresh-moc"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

            index = runs_root / "INDEX.md"
            self.assertTrue(index.exists(), "--refresh-moc must produce runs/INDEX.md")
            text = index.read_text(encoding="utf-8")
            self.assertIn("type: runs-index", text)
            # The chat session we seeded must show up in the MOC.
            self.assertIn("hello-from-test", text)

            # No 09_audit/ artefacts: the chat session dir must remain
            # untouched apart from what we seeded.
            self.assertFalse((session_dir / "09_audit").exists())
            # And no command_trace.json (which the audit subprocess writes).
            self.assertFalse((session_dir / "command_trace.json").exists())

    def test_refresh_moc_does_not_require_run_dir(self) -> None:
        """The flag must be usable without --run-dir."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_root = tmp_path / "runs"
            runs_root.mkdir(parents=True)
            _seed_chat_session(runs_root)

            env = os.environ.copy()
            env["AISWMM_RUNS_ROOT"] = str(runs_root)

            proc = subprocess.run(
                [sys.executable, "-m", "agentic_swmm.cli", "audit", "--refresh-moc"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((runs_root / "INDEX.md").exists())

    def test_refresh_moc_short_circuits_full_audit(self) -> None:
        """``--refresh-moc`` must NOT invoke the audit subprocess.

        We detect this indirectly: a successful refresh against a runs
        tree with no audited SWMM runs would still exit 0 (the MOC just
        shows the unaudited section), and the JSON payload printed by
        the regular audit pipeline must not appear.
        """
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_root = tmp_path / "runs"
            runs_root.mkdir(parents=True)
            _seed_chat_session(runs_root)

            env = os.environ.copy()
            env["AISWMM_RUNS_ROOT"] = str(runs_root)

            proc = subprocess.run(
                [sys.executable, "-m", "agentic_swmm.cli", "audit", "--refresh-moc"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # The full audit JSON contract includes an ``audit_dir`` key
            # (see commands/audit.py main()). The refresh-only path must
            # NOT emit that payload.
            stdout = proc.stdout
            # If stdout is JSON, it must not be the audit payload shape.
            try:
                payload = json.loads(stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                payload = None
            if isinstance(payload, dict):
                self.assertNotIn("audit_dir", payload)
                self.assertNotIn("reaudit_backups", payload)


if __name__ == "__main__":
    unittest.main()
