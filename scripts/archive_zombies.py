#!/usr/bin/env python3
"""Archive zombie ``runs/agent/agent-<ts>/`` directories.

The agent runtime occasionally drops short-lived ``runs/agent/agent-<ts>/``
session dirs that pollute the Obsidian graph but are not worth deleting
outright. This one-shot script moves them under ``runs/.archive/`` using
``git mv`` when the tree is tracked, so the move stays in history and can
be reversed via ``git revert`` + ``git mv`` back.

PRD: ``.claude/prds/PRD_audit.md`` ("Module: Archive script", D5).

Usage::

    python3 scripts/archive_zombies.py                # dry-run (default)
    python3 scripts/archive_zombies.py --apply        # actually move

Rules (load-bearing):
- Only ``runs/agent/agent-<digits>/`` dirs are zombies. The literal
  ``runs/agent/interactive/`` dir is the current CLI target and MUST
  stay in place.
- The script is idempotent: re-running with ``--apply`` after the first
  apply is a no-op.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


ZOMBIE_RE = re.compile(r"^agent-\d+$")


def find_zombies(runs_root: Path) -> list[Path]:
    agent_dir = runs_root / "agent"
    if not agent_dir.is_dir():
        return []
    out: list[Path] = []
    for entry in sorted(agent_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name == "interactive":
            continue
        if ZOMBIE_RE.match(entry.name):
            out.append(entry)
    return out


def _git_available(runs_root: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=runs_root,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_mv(src: Path, dst: Path, repo_root: Path) -> bool:
    """Attempt a tracked ``git mv``. Returns True on success."""
    try:
        proc = subprocess.run(
            ["git", "mv", str(src), str(dst)],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return proc.returncode == 0


def _plain_mv(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def archive_one(zombie: Path, archive_root: Path, *, use_git: bool, repo_root: Path) -> str:
    target = archive_root / zombie.name
    if target.exists():
        return f"skip (already archived): {zombie} -> {target}"
    archive_root.mkdir(parents=True, exist_ok=True)
    if use_git and _git_mv(zombie, target, repo_root=repo_root):
        return f"git mv: {zombie} -> {target}"
    _plain_mv(zombie, target)
    return f"mv:     {zombie} -> {target}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument(
        "--runs-root",
        type=Path,
        default=Path("runs"),
        help="Path to the runs/ directory to scan. Defaults to ./runs.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually move zombies. Without this flag, the script is a dry-run.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runs_root: Path = args.runs_root.resolve()
    if not runs_root.exists():
        print(f"runs root does not exist: {runs_root}", file=sys.stderr)
        return 0  # nothing to do; not an error in fresh checkouts
    zombies = find_zombies(runs_root)
    if not zombies:
        print("no zombies under runs/agent/")
        return 0
    archive_root = runs_root / ".archive"
    use_git = _git_available(runs_root)
    if not args.apply:
        print(f"DRY RUN: {len(zombies)} zombie(s) would move under {archive_root}/")
        for zombie in zombies:
            print(f"  would move: {zombie} -> {archive_root / zombie.name}")
        print("Re-run with --apply to perform the moves.")
        return 0

    repo_root = runs_root.parent
    print(f"APPLY: archiving {len(zombies)} zombie(s) under {archive_root}/")
    for zombie in zombies:
        message = archive_one(zombie, archive_root, use_git=use_git, repo_root=repo_root)
        print(f"  {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
