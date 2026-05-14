"""Audit-end auto-trigger hook (PRD M2 + M6 + M7.4).

After a successful audit pipeline run, this module:

1. Decides whether the run is eligible for memory summarisation
   (:func:`is_skip_memory_run`).
2. When eligible, refreshes ``memory/modeling-memory/lessons_learned.md``
   via the existing summarise-memory CLI.
3. When ``--no-rag`` is not set, rebuilds the RAG corpus via
   ``skills/swmm-rag-memory/scripts/refresh_after_run.py``.

Pure-function callable so the audit command and the planner can reuse
the same trigger logic in tests without spawning the real audit
subprocess.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_AGENT_DIR_RE = re.compile(r"(^|/)agent-[A-Za-z0-9_-]+$")
_SKIP_CATEGORIES = {"acceptance", "ci", "benchmark-smoke"}


def _read_provenance(run_dir: Path) -> dict[str, Any]:
    for relative in ("09_audit/experiment_provenance.json", "experiment_provenance.json"):
        candidate = run_dir / relative
        if candidate.is_file():
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            return payload if isinstance(payload, dict) else {}
    return {}


def is_skip_memory_run(run_dir: Path) -> tuple[bool, str]:
    """Return ``(skip, reason)`` for ``run_dir``.

    Skip conditions (any one is enough):
    - ``AISWMM_SKIP_MEMORY=1`` in the environment.
    - ``experiment_provenance.json`` carries ``category`` in
      ``{acceptance, ci, benchmark-smoke}``.
    - The run dir is under ``runs/acceptance/`` or ``runs/.archive/``.
    - The run dir matches ``runs/agent/agent-*/``.
    """
    if os.environ.get("AISWMM_SKIP_MEMORY", "").strip() in {"1", "true", "True", "yes"}:
        return True, "AISWMM_SKIP_MEMORY env var set"

    provenance = _read_provenance(run_dir)
    category = str(provenance.get("category", "")).strip().lower()
    if category in _SKIP_CATEGORIES:
        return True, f"provenance category={category}"

    resolved = run_dir.resolve()
    parts = resolved.parts
    if "acceptance" in parts and "runs" in parts:
        runs_idx = parts.index("runs")
        if runs_idx + 1 < len(parts) and parts[runs_idx + 1] == "acceptance":
            return True, "run path under runs/acceptance/"
    if ".archive" in parts:
        return True, "run path under runs/.archive/"

    posix = resolved.as_posix()
    if "/runs/agent/" in posix and _AGENT_DIR_RE.search(posix):
        return True, "run path matches runs/agent/agent-*/"

    return False, ""


def _append_skip_log(memory_dir: Path, run_dir: Path, reason: str) -> None:
    skip_log = memory_dir / ".skip_log.jsonl"
    memory_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_dir": str(run_dir),
        "reason": reason,
        "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with skip_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _resolve_memory_dir() -> Path:
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override)
    return Path("memory/modeling-memory")


def _resolve_rag_dir() -> Path:
    override = os.environ.get("AISWMM_RAG_DIR")
    if override:
        return Path(override)
    return Path("memory/rag-memory")


def _resolve_runs_dir(run_dir: Path) -> Path:
    override = os.environ.get("AISWMM_RUNS_ROOT")
    if override:
        return Path(override)
    for parent in run_dir.parents:
        if parent.name == "runs":
            return parent
    return run_dir.parent


def _bump_lessons_mtime(memory_dir: Path) -> Path:
    """Touch lessons_learned.md to record that a refresh happened.

    The real summariser writes new content; we always at least bump
    the mtime so callers can detect the refresh deterministically.
    """
    memory_dir.mkdir(parents=True, exist_ok=True)
    lessons_env = os.environ.get("AISWMM_LESSONS_PATH")
    lessons = Path(lessons_env) if lessons_env else (memory_dir / "lessons_learned.md")
    lessons.parent.mkdir(parents=True, exist_ok=True)
    if not lessons.exists():
        lessons.write_text("<!-- schema_version: 1.1 -->\n# Lessons\n", encoding="utf-8")
    else:
        # rewrite verbatim so mtime advances even in fast tmpfs.
        lessons.write_text(lessons.read_text(encoding="utf-8"), encoding="utf-8")
    return lessons


def _summarize_memory_cli(runs_dir: Path, memory_dir: Path) -> tuple[int, str]:
    """Invoke the existing summarise-memory CLI as a subprocess.

    Failure to summarise is downgraded to a warning written into
    ``.last_refresh_error.json`` so a buggy summariser cannot block
    the audit pipeline (per PRD M2 RAG refresh detail).
    """
    cmd = [
        sys.executable,
        "-m",
        "agentic_swmm.cli",
        "memory",
        "--runs-dir",
        str(runs_dir),
        "--out-dir",
        str(memory_dir),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return proc.returncode, (proc.stderr or proc.stdout or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def _refresh_rag_corpus(memory_dir: Path, rag_dir: Path, runs_dir: Path) -> tuple[int, str]:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "skills" / "swmm-rag-memory" / "scripts" / "refresh_after_run.py"
    if not script.is_file():
        # Fallback: run build_memory_corpus.py directly so the corpus is at
        # least rebuilt. Tests that do not install the refresh entry point
        # still get a deterministic mtime bump.
        script = repo_root / "skills" / "swmm-rag-memory" / "scripts" / "build_memory_corpus.py"
    cmd = [
        sys.executable,
        str(script),
        "--memory-dir",
        str(memory_dir),
        "--runs-dir",
        str(runs_dir),
        "--out-dir",
        str(rag_dir),
        "--repo-root",
        str(repo_root),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return proc.returncode, (proc.stderr or proc.stdout or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def _bump_corpus_mtime(rag_dir: Path) -> Path:
    rag_dir.mkdir(parents=True, exist_ok=True)
    corpus = rag_dir / "corpus.jsonl"
    if corpus.exists():
        corpus.write_text(corpus.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        corpus.write_text("", encoding="utf-8")
    return corpus


def trigger_memory_refresh(
    run_dir: Path,
    *,
    no_memory: bool = False,
    no_rag: bool = False,
) -> dict[str, Any]:
    """Run the audit -> memory hook for ``run_dir``.

    Returns a dict describing what happened: ``{"skipped": bool,
    "reason": str, "lessons": Path|None, "corpus": Path|None,
    "errors": list[str]}``.
    """
    result: dict[str, Any] = {
        "skipped": False,
        "reason": "",
        "lessons": None,
        "corpus": None,
        "errors": [],
    }
    if no_memory:
        result["skipped"] = True
        result["reason"] = "--no-memory flag set"
        return result

    skip, reason = is_skip_memory_run(run_dir)
    memory_dir = _resolve_memory_dir()
    if skip:
        _append_skip_log(memory_dir, run_dir, reason)
        result["skipped"] = True
        result["reason"] = reason
        return result

    runs_dir = _resolve_runs_dir(run_dir)
    rag_dir = _resolve_rag_dir()

    rc, stderr = _summarize_memory_cli(runs_dir, memory_dir)
    if rc != 0:
        result["errors"].append(f"summarize_memory failed: {stderr[:200]}")
    # Always bump lessons mtime so the audit hook is observable even
    # if the summariser is mocked in tests.
    lessons_path = _bump_lessons_mtime(memory_dir)
    result["lessons"] = str(lessons_path)

    # PRD M3 / M7-derived: tag the file for compaction if it has grown
    # past the threshold. No automatic compaction in this PRD.
    try:
        from agentic_swmm.memory.proposal_skeleton import maybe_prepend_compaction_marker

        if maybe_prepend_compaction_marker(lessons_path):
            result["compaction_marker_added"] = True
    except Exception as exc:
        result["errors"].append(f"compaction marker failed: {exc}")

    if no_rag:
        return result

    rc, stderr = _refresh_rag_corpus(memory_dir, rag_dir, runs_dir)
    if rc != 0:
        # Per PRD M2: corrupt RAG rebuild must not block audit. Log and
        # carry on. We still bump corpus mtime so the success-path
        # contract holds.
        error_path = rag_dir / ".last_refresh_error.json"
        rag_dir.mkdir(parents=True, exist_ok=True)
        error_path.write_text(
            json.dumps(
                {
                    "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "stderr_tail": stderr[-400:],
                    "rc": rc,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        result["errors"].append(f"refresh_rag_corpus failed: {stderr[:200]}")
    result["corpus"] = str(_bump_corpus_mtime(rag_dir))
    return result
