"""Integration tests for scripts/archive_zombies.py.

PRD requires:
  - --dry-run is the default, no filesystem mutation
  - --apply moves runs/agent/agent-<ts>/ -> runs/.archive/agent-<ts>/
  - runs/agent/interactive/ MUST stay in place
  - re-apply on already-archived tree is a no-op
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "archive_zombies.py"


def _seed_runs(root: Path) -> dict[str, Path]:
    """Create a synthetic runs/ tree with zombies and a kept interactive subtree."""
    runs = root / "runs"
    (runs / "agent").mkdir(parents=True)
    zombies: list[Path] = []
    for ts in ("agent-1778717638", "agent-1778717900", "agent-1778718000"):
        zdir = runs / "agent" / ts
        zdir.mkdir()
        (zdir / "agent_trace.jsonl").write_text("", encoding="utf-8")
        zombies.append(zdir)
    keep = runs / "agent" / "interactive"
    (keep / "session").mkdir(parents=True)
    (keep / "session" / "marker.txt").write_text("KEEP-ME", encoding="utf-8")
    return {"runs": runs, "interactive": keep, **{p.name: p for p in zombies}}


def _git_init(root: Path) -> None:
    """Initialize a throwaway git repo so the script can use ``git mv``."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True)


def _run_script(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the script with cwd=root and --runs-root pointing at root/runs."""
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--runs-root",
        str(root / "runs"),
        *args,
    ]
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True)


class ArchiveZombiesScriptTests(unittest.TestCase):
    def test_dry_run_is_default_and_does_not_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeded = _seed_runs(root)
            _git_init(root)
            proc = _run_script(root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # Zombies still in place.
            for name in ("agent-1778717638", "agent-1778717900", "agent-1778718000"):
                self.assertTrue((seeded["runs"] / "agent" / name).is_dir())
            # No .archive/ yet.
            self.assertFalse((seeded["runs"] / ".archive").exists())
            self.assertIn("would move", proc.stdout)

    def test_apply_moves_zombies_to_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeded = _seed_runs(root)
            _git_init(root)
            proc = _run_script(root, "--apply")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # Zombies moved.
            for name in ("agent-1778717638", "agent-1778717900", "agent-1778718000"):
                self.assertFalse((seeded["runs"] / "agent" / name).exists())
                self.assertTrue((seeded["runs"] / ".archive" / name).is_dir())
            # interactive/ untouched.
            self.assertTrue(seeded["interactive"].is_dir())
            self.assertEqual(
                (seeded["interactive"] / "session" / "marker.txt").read_text(encoding="utf-8"),
                "KEEP-ME",
            )

    def test_reapply_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_runs(root)
            _git_init(root)
            first = _run_script(root, "--apply")
            self.assertEqual(first.returncode, 0)
            subprocess.run(["git", "commit", "-q", "-am", "applied"], cwd=root, check=True)
            second = _run_script(root, "--apply")
            self.assertEqual(second.returncode, 0)
            self.assertIn("no zombies", second.stdout.lower())

    def test_does_not_touch_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeded = _seed_runs(root)
            _git_init(root)
            # Even with --apply, the interactive/ dir must remain in place.
            _run_script(root, "--apply")
            self.assertTrue((seeded["runs"] / "agent" / "interactive" / "session").is_dir())
            self.assertFalse((seeded["runs"] / ".archive" / "interactive").exists())


if __name__ == "__main__":
    unittest.main()
