from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from agentic_swmm.utils.paths import require_dir, script_path
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "run"


def _copy_named_audit_artifacts(run_dir: Path, stdout: str) -> list[Path]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    audit_dir = run_dir / "08_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"{_safe_name(str(payload.get('run_id') or run_dir.name))}_{stamp}"
    copied: list[Path] = []
    for key, suffix in (
        ("experiment_provenance", "experiment_provenance.json"),
        ("comparison", "comparison.json"),
        ("experiment_note", "experiment_note.md"),
    ):
        source_value = payload.get(key)
        source = Path(source_value) if source_value else run_dir / suffix
        if source.exists():
            target = audit_dir / f"{prefix}_{suffix}"
            shutil.copy2(source, target)
            copied.append(target)
    return copied


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
    named = _copy_named_audit_artifacts(run_dir, result.stdout)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stdout.strip())
    else:
        payload["named_audit_artifacts"] = [str(path) for path in named]
        print(json.dumps(payload, indent=2))
    return result.return_code
