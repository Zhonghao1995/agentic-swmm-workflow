"""``aiswmm memory compact`` CLI (ME-2, issue #62).

Runs a force-full :func:`apply_decay` pass + rebuilds the RAG corpus,
prints a human-readable ``DecayReport`` summary, and exits 0. Must
complete in < 5s against the current repo's memory directory.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _seed_isolated_memory(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a self-contained memory + rag tree under ``tmp_path``."""
    memory_dir = tmp_path / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    lessons = memory_dir / "lessons_learned.md"
    lessons.write_text(
        "<!-- schema_version: 1.1 -->\n"
        "# Lessons Learned\n"
        "\n"
        "## stale_for_compact\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        f"  first_seen_utc: {_iso(now - timedelta(days=400))}\n"
        f"  last_seen_utc: {_iso(now - timedelta(days=400))}\n"
        "  evidence_count: 1\n"
        "  evidence_runs: []\n"
        "  status: active\n"
        "  confidence_score: 1.0\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Body of a stale pattern.\n"
        "\n"
        "## fresh_for_compact\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        f"  first_seen_utc: {_iso(now - timedelta(days=1))}\n"
        f"  last_seen_utc: {_iso(now - timedelta(days=1))}\n"
        "  evidence_count: 5\n"
        "  evidence_runs: []\n"
        "  status: active\n"
        "  confidence_score: 5.0\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Body of a fresh pattern.\n",
        encoding="utf-8",
    )
    rag_dir = tmp_path / "memory" / "rag-memory"
    rag_dir.mkdir(parents=True)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return memory_dir, rag_dir, runs_dir


def _run_compact(
    cwd: Path,
    *,
    memory_dir: Path,
    rag_dir: Path,
    runs_dir: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    env = {
        **dict(__import__("os").environ),
        "AISWMM_MEMORY_DIR": str(memory_dir),
        "AISWMM_RAG_DIR": str(rag_dir),
        "AISWMM_RUNS_ROOT": str(runs_dir),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", "memory", "compact"],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=60,
    )


def test_memory_compact_exits_zero_and_prints_summary(tmp_path: Path) -> None:
    memory_dir, rag_dir, runs_dir = _seed_isolated_memory(tmp_path)
    proc = _run_compact(
        REPO_ROOT, memory_dir=memory_dir, rag_dir=rag_dir, runs_dir=runs_dir
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    # The CLI prints a structured report. Be tolerant of formatting:
    # require the four bucket labels and the retired pattern name.
    combined = proc.stdout + proc.stderr
    assert "retired" in combined.lower()
    assert "stale_for_compact" in combined


def test_memory_compact_writes_archive(tmp_path: Path) -> None:
    memory_dir, rag_dir, runs_dir = _seed_isolated_memory(tmp_path)
    proc = _run_compact(
        REPO_ROOT, memory_dir=memory_dir, rag_dir=rag_dir, runs_dir=runs_dir
    )
    assert proc.returncode == 0, proc.stderr

    archive = memory_dir / "lessons_archived.md"
    assert archive.is_file()
    assert "## stale_for_compact" in archive.read_text(encoding="utf-8")
    # Fresh pattern is still in the live file.
    lessons_text = (memory_dir / "lessons_learned.md").read_text(encoding="utf-8")
    assert "## fresh_for_compact" in lessons_text
    assert "## stale_for_compact" not in lessons_text


def test_memory_compact_emits_json_report_on_request(tmp_path: Path) -> None:
    """``--json`` flag makes the CLI dump a machine-readable report."""
    memory_dir, rag_dir, runs_dir = _seed_isolated_memory(tmp_path)
    env = {
        **dict(__import__("os").environ),
        "AISWMM_MEMORY_DIR": str(memory_dir),
        "AISWMM_RAG_DIR": str(rag_dir),
        "AISWMM_RUNS_ROOT": str(runs_dir),
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentic_swmm.cli",
            "memory",
            "compact",
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    # The CLI emits the report as a pretty-printed JSON block. Find
    # the outermost ``{ ... }`` pair by scanning braces.
    stdout = proc.stdout
    start = stdout.find("{")
    assert start >= 0, stdout
    depth = 0
    end = -1
    for i in range(start, len(stdout)):
        c = stdout[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end > start, stdout
    payload = json.loads(stdout[start:end])
    assert "retired" in payload
    assert "stale_for_compact" in payload["retired"]


def test_memory_compact_runs_in_under_five_seconds(tmp_path: Path) -> None:
    """Acceptance: the CLI must complete in <5s on a small memory tree."""
    memory_dir, rag_dir, runs_dir = _seed_isolated_memory(tmp_path)
    start = time.monotonic()
    proc = _run_compact(
        REPO_ROOT, memory_dir=memory_dir, rag_dir=rag_dir, runs_dir=runs_dir
    )
    elapsed = time.monotonic() - start
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 5.0, f"memory compact took {elapsed:.2f}s, expected <5s"


def test_memory_compact_idempotent_second_run(tmp_path: Path) -> None:
    memory_dir, rag_dir, runs_dir = _seed_isolated_memory(tmp_path)
    proc1 = _run_compact(
        REPO_ROOT, memory_dir=memory_dir, rag_dir=rag_dir, runs_dir=runs_dir
    )
    assert proc1.returncode == 0
    # Second run: there are no more retirements, but the CLI still
    # exits cleanly and reports the surviving (fresh) pattern as
    # unchanged.
    proc2 = _run_compact(
        REPO_ROOT, memory_dir=memory_dir, rag_dir=rag_dir, runs_dir=runs_dir
    )
    assert proc2.returncode == 0
    combined = proc2.stdout + proc2.stderr
    assert "fresh_for_compact" in combined or "unchanged" in combined.lower()
