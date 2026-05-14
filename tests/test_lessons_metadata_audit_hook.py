"""Audit-end metadata-update hook (ME-1, issue #61).

After ``09_audit/experiment_note.md`` is written, the audit pipeline
must update ``memory/modeling-memory/lessons_learned.md`` so that:

1. For every pattern listed in
   ``experiment_provenance.json:failure_patterns``:
     - ``evidence_count`` is incremented by exactly one.
     - ``last_seen_utc`` is refreshed to the audit timestamp.
     - the run id is appended to ``evidence_runs`` (deduplicated).
2. For every pattern (matched or not), ``confidence_score`` is
   recomputed using the current age, ``evidence_count``, and
   ``half_life_days`` already on the block.

ME-1 explicitly does NOT touch ``status`` — active/dormant/retired
transitions belong to ME-2.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _write_lessons(memory_dir: Path) -> Path:
    """Seed a minimal lessons file with two metadata-bearing patterns."""
    lessons = memory_dir / "lessons_learned.md"
    lessons.write_text(
        "<!-- schema_version: 1.1 -->\n"
        "# Lessons Learned\n"
        "\n"
        "## peak_flow_parse_missing\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        "  first_seen_utc: 2026-03-01T10:23:00Z\n"
        "  last_seen_utc: 2026-03-01T10:23:00Z\n"
        "  evidence_count: 6\n"
        "  evidence_runs:\n"
        "    - codex-check-peakfix\n"
        "  status: active\n"
        "  confidence_score: 6.0\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "The peak flow value could not be located.\n"
        "\n"
        "## missing_inp\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        "  first_seen_utc: 2026-01-15T00:00:00Z\n"
        "  last_seen_utc: 2026-01-15T00:00:00Z\n"
        "  evidence_count: 2\n"
        "  evidence_runs: []\n"
        "  status: active\n"
        "  confidence_score: 2.0\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "INP path was not produced.\n",
        encoding="utf-8",
    )
    return lessons


def _write_provenance(run_dir: Path, *, run_id: str, patterns: list[str]) -> Path:
    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "case_name": run_id,
        "schema_version": "1.1",
        "failure_patterns": patterns,
    }
    path = audit_dir / "experiment_provenance.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    (audit_dir / "experiment_note.md").write_text("# note\n", encoding="utf-8")
    return path


def test_update_metadata_for_run_increments_matched_pattern_evidence(tmp_path: Path) -> None:
    from agentic_swmm.memory.lessons_metadata import (
        read_all_patterns,
        update_metadata_for_run,
    )

    memory_dir = tmp_path / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True)
    lessons = _write_lessons(memory_dir)
    run_dir = tmp_path / "runs" / "case-pf"
    _write_provenance(run_dir, run_id="case-pf", patterns=["peak_flow_parse_missing"])

    update_metadata_for_run(lessons_path=lessons, run_dir=run_dir)

    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))
    pf = parsed["peak_flow_parse_missing"]
    assert pf is not None
    # evidence_count bumped by exactly 1.
    assert pf["evidence_count"] == 7
    # run id appended.
    assert "case-pf" in pf["evidence_runs"]
    # last_seen_utc refreshed (past the original 2026-03-01 stamp).
    assert pf["last_seen_utc"] > "2026-03-01T10:23:00Z"

    # The non-matched pattern keeps its evidence_count untouched.
    mi = parsed["missing_inp"]
    assert mi is not None
    assert mi["evidence_count"] == 2
    assert mi["evidence_runs"] == []
    assert mi["last_seen_utc"] == "2026-01-15T00:00:00Z"


def test_update_metadata_for_run_appends_run_id_only_once(tmp_path: Path) -> None:
    from agentic_swmm.memory.lessons_metadata import (
        read_all_patterns,
        update_metadata_for_run,
    )

    memory_dir = tmp_path / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True)
    lessons = _write_lessons(memory_dir)
    run_dir = tmp_path / "runs" / "codex-check-peakfix"
    _write_provenance(
        run_dir,
        run_id="codex-check-peakfix",
        patterns=["peak_flow_parse_missing"],
    )

    update_metadata_for_run(lessons_path=lessons, run_dir=run_dir)

    pf = read_all_patterns(lessons.read_text(encoding="utf-8"))[
        "peak_flow_parse_missing"
    ]
    assert pf is not None
    # The seeded evidence_runs already contains the id. Dedup keeps it
    # to a single entry.
    assert pf["evidence_runs"].count("codex-check-peakfix") == 1


def test_update_metadata_for_run_recomputes_confidence_for_all_patterns(
    tmp_path: Path,
) -> None:
    from agentic_swmm.memory.lessons_metadata import (
        compute_confidence,
        read_all_patterns,
        update_metadata_for_run,
    )

    memory_dir = tmp_path / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True)
    lessons = _write_lessons(memory_dir)
    run_dir = tmp_path / "runs" / "case-pf2"
    _write_provenance(run_dir, run_id="case-pf2", patterns=["peak_flow_parse_missing"])

    update_metadata_for_run(lessons_path=lessons, run_dir=run_dir)

    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))

    pf = parsed["peak_flow_parse_missing"]
    assert pf is not None
    expected_pf = compute_confidence(
        pf["evidence_count"], pf["last_seen_utc"], pf["half_life_days"]
    )
    assert pf["confidence_score"] == round(expected_pf, 3)

    mi = parsed["missing_inp"]
    assert mi is not None
    expected_mi = compute_confidence(
        mi["evidence_count"], mi["last_seen_utc"], mi["half_life_days"]
    )
    assert mi["confidence_score"] == round(expected_mi, 3)


def test_update_metadata_for_run_skips_patterns_without_metadata_block(
    tmp_path: Path,
) -> None:
    from agentic_swmm.memory.lessons_metadata import (
        read_all_patterns,
        update_metadata_for_run,
    )

    memory_dir = tmp_path / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True)
    lessons = memory_dir / "lessons_learned.md"
    lessons.write_text(
        "# Lessons\n"
        "\n"
        "## new_pattern_no_metadata\n"
        "\n"
        "Just appeared, no metadata block yet.\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "runs" / "case-x"
    _write_provenance(
        run_dir, run_id="case-x", patterns=["new_pattern_no_metadata"]
    )

    # Should not raise, should leave the file alone.
    update_metadata_for_run(lessons_path=lessons, run_dir=run_dir)

    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))
    # The pattern still has no metadata; the hook simply skipped it.
    assert parsed["new_pattern_no_metadata"] is None


def test_audit_command_calls_metadata_update_after_experiment_note(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """End-to-end: ``agentic_swmm.commands.audit.main`` invokes the
    metadata hook after the audit subprocess and experiment_note are
    written.
    """
    import argparse
    from agentic_swmm.commands import audit as audit_cmd

    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "case-pf-e2e"
    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps(
            {
                "run_id": "case-pf-e2e",
                "case_name": "PF E2E",
                "schema_version": "1.1",
                "failure_patterns": ["peak_flow_parse_missing"],
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / "experiment_note.md").write_text("# note\n", encoding="utf-8")

    memory_dir = tmp_path / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True)
    lessons = _write_lessons(memory_dir)

    rag = tmp_path / "memory" / "rag-memory"
    rag.mkdir(parents=True)
    (rag / "corpus.jsonl").write_text("", encoding="utf-8")

    class _Result:
        return_code = 0
        stdout = "{}"
        stderr = ""

    # The real audit subprocess re-creates the canonical experiment
    # files after _back_up_prior_audit renames them aside. Mimic that
    # so the downstream summariser can discover the run.
    def _stub_run_command(*args, **kwargs):  # type: ignore[no-untyped-def]
        (audit_dir / "experiment_provenance.json").write_text(
            json.dumps(
                {
                    "run_id": "case-pf-e2e",
                    "case_name": "PF E2E",
                    "schema_version": "1.1",
                    "failure_patterns": ["peak_flow_parse_missing"],
                }
            ),
            encoding="utf-8",
        )
        (audit_dir / "experiment_note.md").write_text("# note\n", encoding="utf-8")
        return _Result()

    monkeypatch.setattr(audit_cmd, "run_command", _stub_run_command)
    monkeypatch.setattr(audit_cmd, "append_trace", lambda *a, **k: None)

    monkeypatch.setenv("AISWMM_LESSONS_PATH", str(lessons))
    monkeypatch.setenv("AISWMM_RAG_DIR", str(rag))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(runs_dir))
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(memory_dir))

    args = argparse.Namespace(
        run_dir=run_dir,
        compare_to=None,
        case_name=None,
        workflow_mode=None,
        objective=None,
        obsidian=False,
        no_memory=False,
        no_rag=True,
        rebuild=False,
    )
    rc = audit_cmd.main(args)
    assert rc == 0

    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))
    pf = parsed["peak_flow_parse_missing"]
    assert pf is not None
    assert pf["evidence_count"] == 7
    assert "case-pf-e2e" in pf["evidence_runs"]


def test_one_shot_migration_already_applied_to_repo_lessons() -> None:
    """The shipped lessons_learned.md must carry metadata for every
    existing failure_pattern section after the one-shot migration."""
    from agentic_swmm.memory.lessons_metadata import read_all_patterns
    from agentic_swmm.utils.paths import repo_root

    lessons_path = (
        repo_root() / "memory" / "modeling-memory" / "lessons_learned.md"
    )
    parsed = read_all_patterns(lessons_path.read_text(encoding="utf-8"))

    expected_patterns = {
        "continuity_parse_missing",
        "missing_inp",
        "partial_run",
        "peak_flow_parse_missing",
        "comparison_mismatch",
    }
    assert expected_patterns.issubset(set(parsed))
    for name in expected_patterns:
        meta = parsed[name]
        assert meta is not None, f"{name} lost its metadata block"
        # Required schema keys.
        for key in (
            "first_seen_utc",
            "last_seen_utc",
            "evidence_count",
            "evidence_runs",
            "status",
            "confidence_score",
            "half_life_days",
        ):
            assert key in meta, f"{name} missing metadata key {key}"
        assert meta["status"] == "active"
        assert meta["half_life_days"] == 90
        assert isinstance(meta["evidence_runs"], list)
        # last_seen / first_seen are ISO-8601 timestamps.
        for key in ("first_seen_utc", "last_seen_utc"):
            datetime.fromisoformat(meta[key].replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
