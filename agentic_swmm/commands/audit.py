from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from agentic_swmm.audit.moc_generator import generate_moc
from agentic_swmm.utils.paths import require_dir, script_path
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command


REAUDIT_BACKED_UP_FILES = (
    ("experiment_note.md", "md"),
    ("experiment_provenance.json", "json"),
    ("comparison.json", "json"),
    ("model_diagnostics.json", "json"),
)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _back_up_prior_audit(audit_dir: Path) -> list[Path]:
    """Rename existing audit files to ``<name>.<utc-ts>.<ext>.bak``.

    Returns the list of backup paths produced. Called immediately before
    a new audit writes into ``audit_dir``; if no prior files exist this
    is a no-op (first audit case).
    """
    if not audit_dir.is_dir():
        return []
    stamp = _utc_stamp()
    backups: list[Path] = []
    for name, ext in REAUDIT_BACKED_UP_FILES:
        src = audit_dir / name
        if not src.exists():
            continue
        stem = name[: -(len(ext) + 1)]  # strip .ext
        target = audit_dir / f"{stem}.{stamp}.{ext}.bak"
        # Tolerate sub-second collisions by appending a counter.
        counter = 2
        while target.exists():
            target = audit_dir / f"{stem}.{stamp}-{counter}.{ext}.bak"
            counter += 1
        src.rename(target)
        backups.append(target)
    return backups


def _runs_root_for(run_dir: Path) -> Path:
    """Resolve the ``runs/`` root that INDEX.md should describe.

    Order:
      1. ``AISWMM_RUNS_ROOT`` env var (lets tests point at a tmp tree).
      2. The first ancestor of ``run_dir`` whose name is ``runs``.
      3. Fallback: the immediate parent of ``run_dir``.
    """
    env_override = os.environ.get("AISWMM_RUNS_ROOT")
    if env_override:
        return Path(env_override).resolve()
    for parent in run_dir.parents:
        if parent.name == "runs":
            return parent
    return run_dir.parent


def _write_moc(run_dir: Path) -> Path | None:
    """Regenerate ``runs/INDEX.md`` after a successful audit."""
    runs_root = _runs_root_for(run_dir.resolve())
    if not runs_root.exists():
        return None
    text = generate_moc(runs_root)
    index_path = runs_root / "INDEX.md"
    index_path.write_text(text, encoding="utf-8")
    return index_path


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("audit", help="Generate audit provenance, comparison, and note artifacts.")
    parser.add_argument("--run-dir", required=True, type=Path, help="Run directory to audit.")
    parser.add_argument("--compare-to", type=Path, help="Optional baseline run directory.")
    parser.add_argument("--case-name", help="Optional human-readable case name.")
    parser.add_argument("--workflow-mode", help="Optional workflow mode label.")
    parser.add_argument("--objective", help="Optional run objective.")
    parser.add_argument("--obsidian", action="store_true", help="Also export the note to the default Obsidian vault.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    run_dir = require_dir(args.run_dir, "run directory")
    audit_dir = run_dir / "09_audit"
    # Back up any prior audit run before invoking the script that will
    # overwrite the canonical filenames. The PRD requires this so re-audit
    # never silently loses history.
    backups = _back_up_prior_audit(audit_dir)

    script = script_path("skills", "swmm-experiment-audit", "scripts", "audit_run.py")
    command = python_command(script, "--run-dir", str(run_dir))
    if args.compare_to:
        command.extend(["--compare-to", str(require_dir(args.compare_to, "comparison run directory"))])
    if args.case_name:
        command.extend(["--case-name", args.case_name])
    if args.workflow_mode:
        command.extend(["--workflow-mode", args.workflow_mode])
    if args.objective:
        command.extend(["--objective", args.objective])
    if not args.obsidian:
        command.append("--no-obsidian")

    result = run_command(command)
    append_trace(run_dir / "command_trace.json", result, stage="audit")

    moc_path: Path | None = None
    if result.return_code == 0:
        moc_path = _write_moc(run_dir)

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stdout.strip())
    else:
        payload["audit_dir"] = str(audit_dir)
        payload["reaudit_backups"] = [str(path) for path in backups]
        payload["runs_index"] = str(moc_path) if moc_path else None
        print(json.dumps(payload, indent=2))
    return result.return_code
