"""``aiswmm runs tidy`` — archive stale, unaudited agent runs.

``runs/agent/`` accumulates one directory per session/tool run and never
sheds any (553 flat siblings at the time of writing; 600 of the repo's
816 runs were never audited). This verb moves the stale ones aside so
the live listing stays readable, under three hard safety rules:

* **Archive, never delete.** Directories MOVE to
  ``runs/archive/agent/<name>`` (collision-bumped), bytes untouched.
* **Audited runs never move.** An audit record
  (``09_audit``/legacy ``06_audit`` with ``experiment_provenance.json``)
  marks curated evidence; it stays where citations expect it.
* **Recent runs never move.** Staleness is the NEWEST mtime anywhere in
  the tree (a dir's own mtime misses edits deep inside), default 30 days.

The archive stays inside ``runs/``, so recursive walkers (memory's
session-repair ``rglob``, the MOC generator) still see archived runs;
only the flat ``runs/agent/`` listing shrinks. ``--dry-run`` previews.
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path
from typing import Any

from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.utils.paths import repo_root

_SECONDS_PER_DAY = 86400.0
DEFAULT_STALE_DAYS = 30


def _newest_mtime(path: Path) -> float:
    """Newest mtime in the tree: the honest 'last touched' signal."""
    newest = path.stat().st_mtime
    for entry in path.rglob("*"):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime > newest:
            newest = mtime
    return newest


def _is_audited(run_dir: Path) -> bool:
    audit_dir = run_layout.find_stage(run_dir, run_layout.AUDIT)
    return audit_dir is not None and (audit_dir / "experiment_provenance.json").is_file()


def _archive_target(archive_root: Path, name: str) -> Path:
    candidate = archive_root / name
    bump = 1
    while candidate.exists():
        bump += 1
        candidate = archive_root / f"{name}-{bump}"
    return candidate


def tidy_agent_runs(
    runs_root: Path,
    *,
    days: int = DEFAULT_STALE_DAYS,
    dry_run: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Archive stale unaudited children of ``runs_root/agent``.

    Pure-ish core (injectable ``now``) so tests drive it directly.
    Returns a report dict; moves nothing when ``dry_run`` is true.
    """
    agent_root = runs_root / "agent"
    archive_root = runs_root / "archive" / "agent"
    cutoff = (now if now is not None else time.time()) - days * _SECONDS_PER_DAY

    report: dict[str, Any] = {
        "runs_root": str(runs_root),
        "archive_root": str(archive_root),
        "days": days,
        "dry_run": dry_run,
        "moved": [],
        "kept_audited": [],
        "kept_recent": [],
    }
    if not agent_root.is_dir():
        return report

    for child in sorted(agent_root.iterdir()):
        if not child.is_dir():
            continue
        if _is_audited(child):
            report["kept_audited"].append(child.name)
            continue
        if _newest_mtime(child) >= cutoff:
            report["kept_recent"].append(child.name)
            continue
        target = _archive_target(archive_root, child.name)
        report["moved"].append({"name": child.name, "to": str(target)})
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(target))

    if report["moved"] and not dry_run:
        _refresh_index(runs_root)
    return report


def _refresh_index(runs_root: Path) -> None:
    """Best-effort ``runs/INDEX.md`` refresh; tidy never fails on it."""
    try:
        from agentic_swmm.audit.moc_generator import generate_moc

        generate_moc(runs_root)
    except Exception:
        return


def main(args: argparse.Namespace) -> int:
    runs_root = (
        args.runs_root.expanduser().resolve()
        if args.runs_root
        else repo_root() / "runs"
    )
    report = tidy_agent_runs(
        runs_root, days=args.days, dry_run=args.dry_run
    )
    verb = "would archive" if args.dry_run else "archived"
    print(
        f"runs tidy: {verb} {len(report['moved'])} run(s); "
        f"kept {len(report['kept_audited'])} audited, "
        f"{len(report['kept_recent'])} recent (cutoff {args.days}d)."
    )
    for item in report["moved"]:
        print(f"  {item['name']} -> {item['to']}")
    if args.dry_run and report["moved"]:
        print("dry run: nothing moved. Re-run without --dry-run to archive.")
    return 0


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = subparsers.add_parser(
        "runs",
        help="Run-directory housekeeping (tidy: archive stale unaudited agent runs).",
    )
    parser.add_argument(
        "action",
        choices=["tidy"],
        help="Housekeeping action. tidy: move stale unaudited runs/agent/* to runs/archive/agent/.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        help=f"Staleness threshold in days (newest mtime in the tree; default {DEFAULT_STALE_DAYS}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be archived without moving anything.",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        help="Override the runs root (default: <repo>/runs).",
    )
    register_example_flag(
        parser,
        example_text="aiswmm runs tidy --dry-run",
    )
    parser.set_defaults(func=main)


__all__ = ["DEFAULT_STALE_DAYS", "main", "register", "tidy_agent_runs"]
