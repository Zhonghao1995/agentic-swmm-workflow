"""Audit-end decay pass (ME-2, issue #62).

The audit hook ``trigger_memory_refresh`` must, after ME-1's metadata
update step:

1. Run a lightweight :func:`apply_decay` pass against
   ``lessons_learned.md``.
2. Append any retired blocks to ``lessons_archived.md`` (with ``git mv``
   semantics when the repo is a git working tree, otherwise plain
   filesystem move).
3. Write ``<run_dir>/09_audit/decay_report.json`` summarising what
   moved between buckets.

These tests use the same tmp-path layout as the ME-1 hook tests so the
two extensions can coexist.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _seed_environment(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Build a self-contained project tree under ``tmp_path``.

    Returns ``(run_dir, memory_dir, rag_dir, lessons_path)``.
    """
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "case-decay-1"
    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps(
            {
                "run_id": "case-decay-1",
                "case_name": "decay test",
                "schema_version": "1.1",
                "failure_patterns": ["recent_pattern"],
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / "experiment_note.md").write_text("# note\n", encoding="utf-8")

    memory_dir = tmp_path / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True)

    now = datetime.now(timezone.utc)
    lessons = memory_dir / "lessons_learned.md"
    lessons.write_text(
        "<!-- schema_version: 1.1 -->\n"
        "# Lessons Learned\n"
        "\n"
        "## recent_pattern\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        f"  first_seen_utc: {_iso(now - timedelta(days=5))}\n"
        f"  last_seen_utc: {_iso(now - timedelta(days=5))}\n"
        "  evidence_count: 3\n"
        "  evidence_runs: []\n"
        "  status: active\n"
        "  confidence_score: 3.0\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Body of the recent pattern.\n"
        "\n"
        "## old_pattern\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        f"  first_seen_utc: {_iso(now - timedelta(days=300))}\n"
        f"  last_seen_utc: {_iso(now - timedelta(days=300))}\n"
        "  evidence_count: 1\n"
        "  evidence_runs: []\n"
        "  status: active\n"
        "  confidence_score: 1.0\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Body of the very old pattern.\n",
        encoding="utf-8",
    )

    rag_dir = tmp_path / "memory" / "rag-memory"
    rag_dir.mkdir(parents=True)
    (rag_dir / "corpus.jsonl").write_text("", encoding="utf-8")
    return run_dir, memory_dir, rag_dir, lessons


def _stub_summariser_passthrough(monkeypatch) -> None:
    """Replace the summariser CLI with a no-op.

    The summariser is run inside ``trigger_memory_refresh`` as a
    subprocess; in tests we want it to leave the seeded
    ``lessons_learned.md`` alone so the decay assertions can see the
    fixture patterns.
    """
    from agentic_swmm.memory import audit_hook as hook_mod

    def _fake_summarise(runs_dir, memory_dir):  # type: ignore[no-untyped-def]
        return 0, ""

    monkeypatch.setattr(hook_mod, "_summarize_memory_cli", _fake_summarise)


def test_audit_hook_runs_decay_after_metadata_update(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end: trigger_memory_refresh produces decay_report.json."""
    run_dir, memory_dir, rag_dir, lessons = _seed_environment(tmp_path)
    _stub_summariser_passthrough(monkeypatch)

    monkeypatch.setenv("AISWMM_LESSONS_PATH", str(lessons))
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(memory_dir))
    monkeypatch.setenv("AISWMM_RAG_DIR", str(rag_dir))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(run_dir.parent))

    from agentic_swmm.memory.audit_hook import trigger_memory_refresh

    result = trigger_memory_refresh(run_dir, no_rag=True)
    assert not result["skipped"], result

    # decay_report.json landed in 09_audit/.
    decay_report = run_dir / "09_audit" / "decay_report.json"
    assert decay_report.is_file()
    payload = json.loads(decay_report.read_text(encoding="utf-8"))
    assert "retired" in payload
    assert "old_pattern" in payload["retired"]
    assert "recent_pattern" not in payload["retired"]


def test_audit_hook_moves_retired_to_archive(
    tmp_path: Path, monkeypatch
) -> None:
    run_dir, memory_dir, rag_dir, lessons = _seed_environment(tmp_path)
    _stub_summariser_passthrough(monkeypatch)

    monkeypatch.setenv("AISWMM_LESSONS_PATH", str(lessons))
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(memory_dir))
    monkeypatch.setenv("AISWMM_RAG_DIR", str(rag_dir))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(run_dir.parent))

    from agentic_swmm.memory.audit_hook import trigger_memory_refresh

    trigger_memory_refresh(run_dir, no_rag=True)

    lessons_text = lessons.read_text(encoding="utf-8")
    archive_path = memory_dir / "lessons_archived.md"
    archive_text = archive_path.read_text(encoding="utf-8") if archive_path.is_file() else ""

    assert "## old_pattern" not in lessons_text
    assert "## old_pattern" in archive_text


def test_audit_hook_decay_pass_failure_does_not_crash(
    tmp_path: Path, monkeypatch
) -> None:
    """A broken lessons file degrades to an error entry, not an exception."""
    run_dir, memory_dir, rag_dir, lessons = _seed_environment(tmp_path)
    # Overwrite lessons with intentionally malformed YAML so apply_decay
    # would fail mid-flight if it tried to load it.
    lessons.write_text(
        "<!-- schema_version: 1.1 -->\n# Lessons\n\n## broken\n\n"
        "<!-- aiswmm-metadata\nthis is not yaml\n/aiswmm-metadata -->\n",
        encoding="utf-8",
    )
    _stub_summariser_passthrough(monkeypatch)

    monkeypatch.setenv("AISWMM_LESSONS_PATH", str(lessons))
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(memory_dir))
    monkeypatch.setenv("AISWMM_RAG_DIR", str(rag_dir))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(run_dir.parent))

    from agentic_swmm.memory.audit_hook import trigger_memory_refresh

    # Must not raise.
    result = trigger_memory_refresh(run_dir, no_rag=True)
    # And the decay summary, if attached, should be a dict.
    if "decay" in result:
        assert isinstance(result["decay"], dict)
