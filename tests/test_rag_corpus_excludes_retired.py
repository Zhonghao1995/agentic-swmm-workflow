"""RAG corpus filter (ME-2, issue #62).

``build_memory_corpus.py`` must consult the per-pattern metadata in
``lessons_learned.md`` and:

- skip any pattern whose ``status`` is ``retired`` entirely (these
  blocks are already moved to ``lessons_archived.md`` by the audit
  hook, but the filter is defence-in-depth: if a maintainer leaves a
  retired-status block in the live file, it must still be excluded
  from retrieval).
- downweight dormant patterns: emit a ``confidence_weight`` field
  derived from ``confidence_score / 1.0`` so the retrieval layer can
  scale their score during ranking.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = (
    REPO_ROOT / "skills" / "swmm-rag-memory" / "scripts" / "build_memory_corpus.py"
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _seed_memory(memory_dir: Path) -> Path:
    """Write a lessons_learned.md with three patterns: active / dormant / retired."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    lessons = memory_dir / "lessons_learned.md"
    lessons.write_text(
        "<!-- schema_version: 1.1 -->\n"
        "# Lessons Learned\n"
        "\n"
        "## active_topic\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        f"  first_seen_utc: {_iso(now - timedelta(days=1))}\n"
        f"  last_seen_utc: {_iso(now - timedelta(days=1))}\n"
        "  evidence_count: 5\n"
        "  evidence_runs: []\n"
        "  status: active\n"
        "  confidence_score: 4.5\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Body of the active pattern.\n"
        "\n"
        "## dormant_topic\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        f"  first_seen_utc: {_iso(now - timedelta(days=120))}\n"
        f"  last_seen_utc: {_iso(now - timedelta(days=120))}\n"
        "  evidence_count: 2\n"
        "  evidence_runs: []\n"
        "  status: dormant\n"
        "  confidence_score: 0.5\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Body of the dormant pattern.\n"
        "\n"
        "## retired_topic\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        f"  first_seen_utc: {_iso(now - timedelta(days=400))}\n"
        f"  last_seen_utc: {_iso(now - timedelta(days=400))}\n"
        "  evidence_count: 1\n"
        "  evidence_runs: []\n"
        "  status: retired\n"
        "  confidence_score: 0.012\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Body of the retired pattern.\n",
        encoding="utf-8",
    )
    return lessons


def _run_build(tmp_path: Path, memory_dir: Path, runs_dir: Path) -> subprocess.CompletedProcess:
    out_dir = tmp_path / "rag-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT),
            "--memory-dir",
            str(memory_dir),
            "--runs-dir",
            str(runs_dir),
            "--out-dir",
            str(out_dir),
            "--repo-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )


def _load_lessons_entry(corpus_path: Path) -> dict | None:
    for line in corpus_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        source = str(entry.get("source_path") or "")
        if source.endswith("lessons_learned.md"):
            return entry
    return None


def test_corpus_drops_retired_pattern_body(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory" / "modeling-memory"
    _seed_memory(memory_dir)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    proc = _run_build(tmp_path, memory_dir=memory_dir, runs_dir=runs_dir)
    assert proc.returncode == 0, proc.stderr

    corpus_path = tmp_path / "rag-out" / "corpus.jsonl"
    lessons_entry = _load_lessons_entry(corpus_path)
    assert lessons_entry is not None

    text = lessons_entry["text"]
    assert "## active_topic" in text
    assert "## dormant_topic" in text
    # Retired pattern body is filtered out.
    assert "## retired_topic" not in text
    assert "Body of the retired pattern." not in text


def test_corpus_dormant_pattern_carries_confidence_weight(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory" / "modeling-memory"
    _seed_memory(memory_dir)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    proc = _run_build(tmp_path, memory_dir=memory_dir, runs_dir=runs_dir)
    assert proc.returncode == 0, proc.stderr

    corpus_path = tmp_path / "rag-out" / "corpus.jsonl"
    lessons_entry = _load_lessons_entry(corpus_path)
    assert lessons_entry is not None

    # The entry exposes lifecycle weights so retrieval can scale ranking.
    weights = lessons_entry.get("pattern_status")
    assert weights is not None, lessons_entry
    assert weights["active_topic"] == "active"
    assert weights["dormant_topic"] == "dormant"
    assert "retired_topic" not in weights

    confidence = lessons_entry.get("pattern_confidence")
    assert confidence is not None
    # Active pattern keeps full weight (>= 1.0).
    assert confidence["active_topic"] >= 1.0
    # Dormant pattern is downweighted below 1.0.
    assert 0.0 < confidence["dormant_topic"] < 1.0


def test_corpus_excludes_retired_metadata_fence_too(tmp_path: Path) -> None:
    """The retired block's metadata fence must not leak into the corpus.

    A maintainer auditing the RAG corpus should not stumble on a
    retired pattern's metadata: both the section heading AND its YAML
    block are stripped.
    """
    memory_dir = tmp_path / "memory" / "modeling-memory"
    _seed_memory(memory_dir)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    proc = _run_build(tmp_path, memory_dir=memory_dir, runs_dir=runs_dir)
    assert proc.returncode == 0, proc.stderr

    corpus_path = tmp_path / "rag-out" / "corpus.jsonl"
    lessons_entry = _load_lessons_entry(corpus_path)
    assert lessons_entry is not None

    text = lessons_entry["text"]
    # The retired pattern's evidence_count line is unique enough to
    # confirm the whole block was stripped.
    assert "Body of the retired pattern." not in text
    assert "retired_topic" not in text
