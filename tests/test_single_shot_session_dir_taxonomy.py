"""Regression tests: one-shot sessions join the date-first runs/ scheme.

User-requested runs/ root tidy (2026-08-08): interactive turns landed
in ``runs/<YYYY-MM-DD>/<HHMMSS>_<case>_<kind>`` while one-shot goals
landed in ``runs/agent/agent-<unixtime>``, so chats and runs lived in
two parallel hierarchies and the root read as chaos. One-shot sessions
now use the same date-first naming (kind ``run``). Explicit
``--session-id`` and ``--session-dir`` preserve the historic placement
byte-for-byte so pinned scripts keep working, and legacy
``runs/agent/*`` stays read-only (the ``runs tidy`` archiver and the
memory skip rule for that pattern remain valid for legacy dirs).
"""

from __future__ import annotations

import re
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from agentic_swmm.agent import single_shot
from agentic_swmm.utils.paths import repo_root


def _args(**over) -> Namespace:
    base = dict(
        goal=["run", "doctor"],
        session_id=None,
        session_dir=None,
        planner="rule",
        dry_run=True,
        verbose=False,
        max_steps=8,
        safe=False,
    )
    base.update(over)
    return Namespace(**base)


class SessionDirTaxonomyTests(unittest.TestCase):
    def _resolved_dir(self, args: Namespace) -> Path:
        captured: dict[str, Path] = {}

        real_mkdir = Path.mkdir

        def spy_mkdir(self, *a, **k):
            if "session" not in captured:
                captured["session"] = self
            return real_mkdir(self, *a, **k)

        # Stop after dir creation: dry_run + rule planner writes only
        # a report; keep it fully sandboxed by intercepting mkdir and
        # aborting the run right after via a poisoned header writer.
        with mock.patch.object(Path, "mkdir", spy_mkdir):
            with mock.patch.object(
                single_shot,
                "try_write_session_header",
                side_effect=RuntimeError("stop-after-dir"),
            ):
                try:
                    single_shot.run_single_shot(args)
                except RuntimeError:
                    pass
        return captured["session"]

    def test_default_lands_in_date_scheme(self) -> None:
        """Pre-change: runs/agent/agent-<unixtime>."""
        session = self._resolved_dir(_args())
        rel = session.relative_to(repo_root() / "runs")
        self.assertRegex(str(rel.parts[0]), r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(rel.parts[1], r"^\d{6}_[a-z0-9-]+_run(_\d+)?$")
        # Best-effort cleanup of the empty scaffold this test created.
        try:
            session.rmdir()
            session.parent.rmdir()
        except OSError:
            pass

    def test_explicit_session_id_keeps_legacy_placement(self) -> None:
        session = self._resolved_dir(_args(session_id="agent-12345"))
        self.assertEqual(
            session, repo_root() / "runs" / "agent" / "agent-12345"
        )
        try:
            session.rmdir()
        except OSError:
            pass

    def test_explicit_session_dir_wins_verbatim(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "my-run"
            session = self._resolved_dir(_args(session_dir=target))
            self.assertEqual(session, target.resolve())

    def test_kind_suffix_matches_audit_grouping_rule(self) -> None:
        """audit_run groups sessions by ^\\d+_(.+)_(?:run|chat)$; the
        new one-shot naming must match it so case grouping works."""
        session = self._resolved_dir(_args(goal=["check", "tecnopolo", "model"]))
        self.assertIsNotNone(
            re.match(r"^\d+_(.+)_(?:run|chat)(_\d+)?$", session.name)
        )
        try:
            session.rmdir()
            session.parent.rmdir()
        except OSError:
            pass


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
