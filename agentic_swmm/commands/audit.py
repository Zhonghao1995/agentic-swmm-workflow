from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from agentic_swmm.audit.moc_generator import generate_moc
from agentic_swmm.utils.paths import require_dir, script_path
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command


# PRD-Z partial integration: after the audit subprocess succeeds, the
# command runs the HITL threshold evaluator against the run's QA
# summary and writes 09_audit/threshold_hits.json if any hits are
# returned. Full ``request_expert_review`` triggering remains a
# follow-up; this PRD just makes the data available.
THRESHOLD_HITS_FILENAME = "threshold_hits.json"
_QA_SUMMARY_REL = Path("06_qa") / "qa_summary.json"


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


def _write_threshold_hits(run_dir: Path) -> Path | None:
    """Run the HITL threshold evaluator against the run's QA summary.

    Returns the path to ``09_audit/threshold_hits.json`` when one or
    more hits were found, ``None`` otherwise. The function is
    deliberately quiet: missing QA artefacts, malformed JSON, or a
    missing thresholds doc all short-circuit to ``None`` — the audit
    pipeline must never crash because the HITL data was incomplete.
    """
    from agentic_swmm.hitl.threshold_evaluator import (
        evaluate,
        load_thresholds_from_md,
    )
    from agentic_swmm.utils.paths import repo_root

    qa_path = run_dir / _QA_SUMMARY_REL
    if not qa_path.is_file():
        return None
    try:
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(qa, dict):
        return None
    thresholds_doc = repo_root() / "docs" / "hitl-thresholds.md"
    try:
        thresholds = load_thresholds_from_md(thresholds_doc)
    except (OSError, ValueError, FileNotFoundError):
        return None
    hits = evaluate(qa, thresholds)
    if not hits:
        return None
    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    out_path = audit_dir / THRESHOLD_HITS_FILENAME
    payload = {
        "generated_at_utc": _utc_stamp(),
        "qa_summary": str(_QA_SUMMARY_REL),
        "thresholds_doc": "docs/hitl-thresholds.md",
        "hits": [
            {
                "pattern": hit.pattern,
                "severity": hit.severity,
                "measured_value": hit.measured_value,
                "threshold_value": hit.threshold_value,
                "evidence_ref": hit.evidence_ref,
                "message": hit.message,
                "rationale_is_placeholder": hit.rationale_is_placeholder,
            }
            for hit in hits
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return out_path


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("audit", help="Generate audit provenance, comparison, and note artifacts.")
    # ``--run-dir`` is required for a full audit but optional for
    # ``--refresh-moc`` (issue #60); we enforce the dependency in main()
    # so argparse can still parse the bare ``--refresh-moc`` form.
    parser.add_argument("--run-dir", type=Path, help="Run directory to audit.")
    parser.add_argument("--compare-to", type=Path, help="Optional baseline run directory.")
    parser.add_argument("--case-name", help="Optional human-readable case name.")
    parser.add_argument("--workflow-mode", help="Optional workflow mode label.")
    parser.add_argument("--objective", help="Optional run objective.")
    parser.add_argument("--obsidian", action="store_true", help="Also export the note to the default Obsidian vault.")
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Skip the audit -> memory auto-trigger (M2). lessons_learned.md "
        "and the RAG corpus are left untouched. Default: trigger.",
    )
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="Skip only the RAG corpus rebuild but still refresh "
        "lessons_learned.md. Useful when the RAG step is the slow part.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Full-rebuild fallback: force re-scan of all runs by the "
        "memory summariser (clears .last_sync.json).",
    )
    parser.add_argument(
        "--refresh-moc",
        action="store_true",
        help="Force-refresh runs/INDEX.md (the Obsidian MOC) without "
        "running a full audit. Exits 0 on success. Does not write into "
        "any 09_audit/ directory. AISWMM_RUNS_ROOT overrides the "
        "auto-resolved runs root.",
    )
    parser.set_defaults(func=main)


def _refresh_moc_only() -> int:
    """Regenerate ``runs/INDEX.md`` against the resolved runs root.

    Honours ``AISWMM_RUNS_ROOT`` (same env var used by the audit
    success path's ``_write_moc``) and falls back to ``repo_root() /
    "runs"``. Returns 0 on success, 1 if the runs root does not exist
    or MOC generation fails.
    """
    env_override = os.environ.get("AISWMM_RUNS_ROOT")
    if env_override:
        runs_root = Path(env_override).expanduser().resolve()
    else:
        from agentic_swmm.utils.paths import repo_root

        runs_root = repo_root() / "runs"
    if not runs_root.exists():
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "runs_root_missing",
                    "runs_root": str(runs_root),
                }
            )
        )
        return 1
    try:
        text = generate_moc(runs_root)
    except Exception as exc:  # noqa: BLE001 — surface the error to the caller
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "moc_generation_failed",
                    "error": str(exc),
                    "runs_root": str(runs_root),
                }
            )
        )
        return 1
    index_path = runs_root / "INDEX.md"
    index_path.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "runs_index": str(index_path),
                "runs_root": str(runs_root),
            }
        )
    )
    return 0


def main(args: argparse.Namespace) -> int:
    # Issue #60 (UX-5): force-refresh path short-circuits the full audit
    # pipeline. We must not require --run-dir, must not invoke the audit
    # subprocess, and must not write any 09_audit/ artefacts.
    if getattr(args, "refresh_moc", False):
        return _refresh_moc_only()

    if args.run_dir is None:
        print(
            "audit: --run-dir is required (or pass --refresh-moc to "
            "regenerate runs/INDEX.md only)",
        )
        return 2
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
    memory_hook: dict | None = None
    threshold_hits_path: Path | None = None
    if result.return_code == 0:
        # PRD-Z partial integration: evaluate HITL thresholds and write
        # 09_audit/threshold_hits.json before the memory hook fires, so
        # the file is on disk when downstream tools sweep the run.
        threshold_hits_path = _write_threshold_hits(run_dir)
        moc_path = _write_moc(run_dir)
        # M2 audit -> memory auto-trigger. Runs after the audit subprocess
        # succeeded and after the runs/INDEX.md MOC has been regenerated.
        from agentic_swmm.memory.audit_hook import trigger_memory_refresh

        memory_hook = trigger_memory_refresh(
            run_dir,
            no_memory=bool(getattr(args, "no_memory", False)),
            no_rag=bool(getattr(args, "no_rag", False)),
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stdout.strip())
    else:
        payload["audit_dir"] = str(audit_dir)
        payload["reaudit_backups"] = [str(path) for path in backups]
        payload["runs_index"] = str(moc_path) if moc_path else None
        if memory_hook is not None:
            payload["memory_hook"] = memory_hook
        payload["threshold_hits"] = (
            str(threshold_hits_path) if threshold_hits_path else None
        )
        print(json.dumps(payload, indent=2))
    return result.return_code
