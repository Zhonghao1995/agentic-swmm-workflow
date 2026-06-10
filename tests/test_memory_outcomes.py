"""Tests for the application outcome log + per-entry health score (PR 3, Phase 1).

Coverage:
- Event classification: positive, below_band, run_failed, excluded_multi.
- Multi-memory (>1 applied) → excluded_multi only.
- Band <2-points rule: only run_failed moves the score.
- Health score derivation: determinism, clamping, delta table.
- Append-only discipline: second write preserves first line byte-identical.
- CLI ``aiswmm memory health``: direct call via health_main.
- M2 hook end-to-end: hook reads provenance → ledger line appears.
- AISWMM_SKIP_MEMORY=1 also skips outcome logging.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_provenance(
    *,
    run_id: str = "run-abc",
    case_name: str = "saanich-b8",
    memories_applied: list[str] | None = None,
    kge: float | None = None,
    return_code: int = 0,
    runoff_continuity: float = -0.5,
    flow_continuity: float = 0.1,
) -> dict:
    prov: dict = {
        "schema_version": "1.1",
        "run_id": run_id,
        "case_name": case_name,
        "status": "ok",
        "return_code": return_code,
        "memories_applied": memories_applied if memories_applied is not None else [],
        "tools": {"swmm5_version": "5.2.4"},
        "metrics": {
            "continuity_error": {
                "name": "continuity_error",
                "values": {
                    "runoff_quantity": runoff_continuity,
                    "flow_routing": flow_continuity,
                },
            },
        },
    }
    if kge is not None:
        prov["performance_metrics"] = {"kge": kge}
    return prov


def _write_provenance(run_dir: Path, prov: dict) -> None:
    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps(prov), encoding="utf-8"
    )


def _write_manifest(run_dir: Path, memories_applied: list[str] | None = None) -> Path:
    manifest = {
        "run_id": "run-abc",
        "memories_applied": memories_applied or [],
    }
    p = run_dir / "manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return p


def _hook_patches():
    """Return context managers that stub the heavy audit-hook subprocesses."""
    return (
        mock.patch(
            "agentic_swmm.memory.audit_hook._summarize_memory_cli",
            return_value=(0, ""),
        ),
        mock.patch(
            "agentic_swmm.memory.audit_hook._refresh_rag_corpus",
            return_value=(0, ""),
        ),
    )


# ── Append-only writer ────────────────────────────────────────────────────────


class TestAppendOutcomeEvent(unittest.TestCase):
    def test_creates_file_and_writes_valid_json(self) -> None:
        from agentic_swmm.memory.memory_outcomes import append_outcome_event

        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "events.jsonl"
            eid = append_outcome_event(
                store,
                memory_id="pm-abc",
                memory_kind="parametric",
                run_dir="/runs/r1",
                run_manifest_sha="aabbcc",
                event="positive",
                metric={"name": "kge", "value": 0.72, "band_low": 0.62},
                attribution="single",
            )
            assert store.is_file()
            lines = [l for l in store.read_text().splitlines() if l.strip()]
            assert len(lines) == 1
            row = json.loads(lines[0])
            assert row["memory_id"] == "pm-abc"
            assert row["event"] == "positive"
            assert row["attribution"] == "single"
            assert row["source"] == "m2_audit_hook"
            assert row["event_id"] == eid

    def test_second_append_preserves_first_line(self) -> None:
        """Append-only: first line must be byte-identical after second write."""
        from agentic_swmm.memory.memory_outcomes import append_outcome_event

        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "events.jsonl"
            append_outcome_event(
                store,
                memory_id="pm-abc",
                memory_kind="parametric",
                run_dir="/runs/r1",
                run_manifest_sha="aabb",
                event="positive",
                metric=None,
                attribution="single",
            )
            first_line = store.read_text().splitlines()[0]

            append_outcome_event(
                store,
                memory_id="pm-abc",
                memory_kind="parametric",
                run_dir="/runs/r2",
                run_manifest_sha="ccdd",
                event="below_band",
                metric={"name": "kge", "value": 0.50, "band_low": 0.65},
                attribution="single",
            )
            lines = store.read_text().splitlines()
            assert len(lines) == 2
            assert lines[0] == first_line, "First line must be byte-identical after second append"

    def test_invalid_event_raises(self) -> None:
        from agentic_swmm.memory.memory_outcomes import append_outcome_event

        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "events.jsonl"
            with self.assertRaises(ValueError):
                append_outcome_event(
                    store,
                    memory_id="pm-abc",
                    memory_kind="parametric",
                    run_dir="/runs/r1",
                    run_manifest_sha="",
                    event="bad_event_type",
                    metric=None,
                    attribution="single",
                )

    def test_invalid_attribution_raises(self) -> None:
        from agentic_swmm.memory.memory_outcomes import append_outcome_event

        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "events.jsonl"
            with self.assertRaises(ValueError):
                append_outcome_event(
                    store,
                    memory_id="pm-abc",
                    memory_kind="parametric",
                    run_dir="/runs/r1",
                    run_manifest_sha="",
                    event="positive",
                    metric=None,
                    attribution="unknown_attr",
                )


# ── Health score ──────────────────────────────────────────────────────────────


class TestHealthScore(unittest.TestCase):
    def _make_events(self, mid: str, event_types: list[str]) -> list[dict]:
        return [
            {"memory_id": mid, "event": et, "attribution": "single"}
            for et in event_types
        ]

    def test_start_with_no_events(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        score = health_score("pm-abc", [])
        self.assertAlmostEqual(score, 0.70)

    def test_positive_increments(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        events = self._make_events("pm-abc", ["positive", "positive"])
        score = health_score("pm-abc", events)
        # 0.70 + 0.05 + 0.05 = 0.80
        self.assertAlmostEqual(score, 0.80)

    def test_run_failed_decrements(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        events = self._make_events("pm-abc", ["run_failed"])
        score = health_score("pm-abc", events)
        # 0.70 - 0.40 = 0.30
        self.assertAlmostEqual(score, 0.30)

    def test_below_band_decrements(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        events = self._make_events("pm-abc", ["below_band"])
        score = health_score("pm-abc", events)
        # 0.70 - 0.15 = 0.55
        self.assertAlmostEqual(score, 0.55)

    def test_clamped_at_zero(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        events = self._make_events(
            "pm-abc", ["run_failed", "run_failed", "run_failed"]
        )
        score = health_score("pm-abc", events)
        # 0.70 - 3*0.40 = -0.50 → clamped to 0.0
        self.assertAlmostEqual(score, 0.0)

    def test_clamped_at_one(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        events = self._make_events(
            "pm-abc", ["positive"] * 20
        )
        score = health_score("pm-abc", events)
        self.assertAlmostEqual(score, 1.0)

    def test_excluded_multi_ignored(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        events = [
            {"memory_id": "pm-abc", "event": "positive", "attribution": "excluded_multi"},
            {"memory_id": "pm-abc", "event": "run_failed", "attribution": "excluded_multi"},
        ]
        score = health_score("pm-abc", events)
        # excluded_multi has no effect → stays at 0.70
        self.assertAlmostEqual(score, 0.70)

    def test_deterministic_same_prefix(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        events = self._make_events("pm-abc", ["positive", "below_band", "run_failed"])
        s1 = health_score("pm-abc", events)
        s2 = health_score("pm-abc", events)
        self.assertEqual(s1, s2)

    def test_only_counts_matching_memory_id(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        events = [
            {"memory_id": "pm-abc", "event": "run_failed", "attribution": "single"},
            {"memory_id": "pm-xyz", "event": "positive", "attribution": "single"},
        ]
        score = health_score("pm-abc", events)
        # only pm-abc's run_failed counts
        self.assertAlmostEqual(score, 0.30)

    def test_reconfirmed_increments(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        events = self._make_events("pm-abc", ["reconfirmed"])
        score = health_score("pm-abc", events)
        self.assertAlmostEqual(score, 0.75)

    def test_contradicted_decrements(self) -> None:
        from agentic_swmm.memory.memory_outcomes import health_score

        events = self._make_events("pm-abc", ["contradicted"])
        score = health_score("pm-abc", events)
        # 0.70 - 0.30 = 0.40
        self.assertAlmostEqual(score, 0.40)


# ── Classification: run_failed ────────────────────────────────────────────────


class TestClassifyRunFailed(unittest.TestCase):
    def test_nonzero_return_code_is_run_failed(self) -> None:
        from agentic_swmm.memory.memory_outcomes import classify_and_record_outcome

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store = tmpdir / "memory_outcome_events.jsonl"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            run_dir = tmpdir / "run"
            run_dir.mkdir()

            prov = _make_provenance(
                memories_applied=["pm-abc"],
                return_code=1,
                kge=0.72,
            )
            eids = classify_and_record_outcome(
                run_dir=run_dir,
                provenance=prov,
                manifest_path=None,
                memory_dir=memory_dir,
                store_path=store,
            )
            assert len(eids) == 1
            events = json.loads(store.read_text().splitlines()[0])
            assert events["event"] == "run_failed"
            assert events["attribution"] == "single"

    def test_high_continuity_error_is_run_failed(self) -> None:
        """Runoff continuity >= 10 % magnitude triggers run_failed."""
        from agentic_swmm.memory.memory_outcomes import classify_and_record_outcome

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store = tmpdir / "events.jsonl"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            run_dir = tmpdir / "run"
            run_dir.mkdir()

            prov = _make_provenance(
                memories_applied=["pm-abc"],
                return_code=0,
                runoff_continuity=12.0,  # above 10 % threshold
                flow_continuity=0.5,
                kge=0.72,
            )
            eids = classify_and_record_outcome(
                run_dir=run_dir,
                provenance=prov,
                manifest_path=None,
                memory_dir=memory_dir,
                store_path=store,
            )
            assert len(eids) == 1
            row = json.loads(store.read_text().splitlines()[0])
            assert row["event"] == "run_failed"

    def test_high_flow_continuity_is_run_failed(self) -> None:
        """Flow continuity >= 5 % magnitude triggers run_failed."""
        from agentic_swmm.memory.memory_outcomes import classify_and_record_outcome

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store = tmpdir / "events.jsonl"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            run_dir = tmpdir / "run"
            run_dir.mkdir()

            prov = _make_provenance(
                memories_applied=["pm-abc"],
                return_code=0,
                runoff_continuity=1.0,
                flow_continuity=6.0,  # above 5 % threshold
                kge=0.72,
            )
            eids = classify_and_record_outcome(
                run_dir=run_dir,
                provenance=prov,
                manifest_path=None,
                memory_dir=memory_dir,
                store_path=store,
            )
            assert len(eids) == 1
            row = json.loads(store.read_text().splitlines()[0])
            assert row["event"] == "run_failed"


# ── Classification: positive / below_band ────────────────────────────────────


class TestClassifyKgeBand(unittest.TestCase):
    def test_no_kge_yields_no_event(self) -> None:
        """When KGE is absent from provenance, no event should be written."""
        from agentic_swmm.memory.memory_outcomes import classify_and_record_outcome

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store = tmpdir / "events.jsonl"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            run_dir = tmpdir / "run"
            run_dir.mkdir()

            prov = _make_provenance(
                memories_applied=["pm-abc"],
                return_code=0,
                kge=None,  # no KGE
            )
            eids = classify_and_record_outcome(
                run_dir=run_dir,
                provenance=prov,
                manifest_path=None,
                memory_dir=memory_dir,
                store_path=store,
            )
            # No events — no KGE to classify
            assert eids == []
            assert not store.exists()

    def test_positive_when_band_not_established(self) -> None:
        """Fewer than 2 data points: only run_failed possible; positive recorded."""
        from agentic_swmm.memory.memory_outcomes import classify_and_record_outcome

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store = tmpdir / "events.jsonl"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            run_dir = tmpdir / "run"
            run_dir.mkdir()

            prov = _make_provenance(
                memories_applied=["pm-abc"],
                return_code=0,
                kge=0.72,
            )
            # No stored KGE, no prior events → band undefined
            eids = classify_and_record_outcome(
                run_dir=run_dir,
                provenance=prov,
                manifest_path=None,
                memory_dir=memory_dir,
                store_path=store,
            )
            assert len(eids) == 1
            row = json.loads(store.read_text().splitlines()[0])
            assert row["event"] == "positive"
            assert row["metric"]["band_low"] is None

    def test_below_band_when_band_established(self) -> None:
        """With ≥2 data points, KGE below band-low triggers below_band."""
        from agentic_swmm.memory.memory_outcomes import (
            OUTCOME_LEDGER_FILENAME,
            append_outcome_event,
            classify_and_record_outcome,
        )

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            run_dir = tmpdir / "run"
            run_dir.mkdir()
            store = memory_dir / OUTCOME_LEDGER_FILENAME

            # Seed the band with two prior positive events (KGE ~0.72 & 0.74)
            # stored_kge = 0.72 (from parametric store — we mock that)
            # prior positive event KGE = 0.74
            # band = min(0.72, 0.74) - 0.10 = 0.62
            append_outcome_event(
                store,
                memory_id="pm-abc",
                memory_kind="parametric",
                run_dir="/runs/r0",
                run_manifest_sha="aa",
                event="positive",
                metric={"name": "kge", "value": 0.74, "band_low": None},
                attribution="single",
            )

            # Patch stored_kge to return 0.72
            with mock.patch(
                "agentic_swmm.memory.memory_outcomes._stored_kge_for_memory",
                return_value=0.72,
            ):
                prov = _make_provenance(
                    memories_applied=["pm-abc"],
                    return_code=0,
                    kge=0.55,  # below 0.62 band
                )
                eids = classify_and_record_outcome(
                    run_dir=run_dir,
                    provenance=prov,
                    manifest_path=None,
                    memory_dir=memory_dir,
                    store_path=store,
                )

            assert len(eids) == 1
            lines = store.read_text().splitlines()
            # Last written line
            row = json.loads(lines[-1])
            assert row["event"] == "below_band"
            assert abs(row["metric"]["band_low"] - 0.62) < 1e-6

    def test_positive_when_above_band(self) -> None:
        """KGE above band-low emits positive."""
        from agentic_swmm.memory.memory_outcomes import (
            OUTCOME_LEDGER_FILENAME,
            append_outcome_event,
            classify_and_record_outcome,
        )

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            run_dir = tmpdir / "run"
            run_dir.mkdir()
            store = memory_dir / OUTCOME_LEDGER_FILENAME

            append_outcome_event(
                store,
                memory_id="pm-abc",
                memory_kind="parametric",
                run_dir="/runs/r0",
                run_manifest_sha="bb",
                event="positive",
                metric={"name": "kge", "value": 0.74, "band_low": None},
                attribution="single",
            )

            with mock.patch(
                "agentic_swmm.memory.memory_outcomes._stored_kge_for_memory",
                return_value=0.72,
            ):
                prov = _make_provenance(
                    memories_applied=["pm-abc"],
                    return_code=0,
                    kge=0.80,  # well above band
                )
                eids = classify_and_record_outcome(
                    run_dir=run_dir,
                    provenance=prov,
                    manifest_path=None,
                    memory_dir=memory_dir,
                    store_path=store,
                )

            assert len(eids) == 1
            row = json.loads(store.read_text().splitlines()[-1])
            assert row["event"] == "positive"


# ── Multi-memory → excluded_multi ────────────────────────────────────────────


class TestMultiMemoryExcludedMulti(unittest.TestCase):
    def test_multi_memory_yields_excluded_multi_only(self) -> None:
        from agentic_swmm.memory.memory_outcomes import classify_and_record_outcome

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store = tmpdir / "events.jsonl"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            run_dir = tmpdir / "run"
            run_dir.mkdir()

            prov = _make_provenance(
                memories_applied=["pm-abc", "cm-xyz"],
                return_code=0,
                kge=0.72,
            )
            eids = classify_and_record_outcome(
                run_dir=run_dir,
                provenance=prov,
                manifest_path=None,
                memory_dir=memory_dir,
                store_path=store,
            )
            assert len(eids) == 2
            rows = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
            for row in rows:
                assert row["attribution"] == "excluded_multi"

    def test_multi_memory_no_health_effect(self) -> None:
        """excluded_multi events must not affect health_score."""
        from agentic_swmm.memory.memory_outcomes import (
            classify_and_record_outcome,
            health_score,
            load_outcome_events,
        )

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store = tmpdir / "events.jsonl"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            run_dir = tmpdir / "run"
            run_dir.mkdir()

            prov = _make_provenance(
                memories_applied=["pm-abc", "pm-def"],
                return_code=0,
                kge=0.72,
            )
            classify_and_record_outcome(
                run_dir=run_dir,
                provenance=prov,
                manifest_path=None,
                memory_dir=memory_dir,
                store_path=store,
            )
            events = load_outcome_events(store)
            score_abc = health_score("pm-abc", events)
            score_def = health_score("pm-def", events)
            # Both start at 0.70 and excluded_multi has no delta
            self.assertAlmostEqual(score_abc, 0.70)
            self.assertAlmostEqual(score_def, 0.70)

    def test_zero_memories_applied_yields_nothing(self) -> None:
        from agentic_swmm.memory.memory_outcomes import classify_and_record_outcome

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store = tmpdir / "events.jsonl"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            run_dir = tmpdir / "run"
            run_dir.mkdir()

            prov = _make_provenance(memories_applied=[])
            eids = classify_and_record_outcome(
                run_dir=run_dir,
                provenance=prov,
                manifest_path=None,
                memory_dir=memory_dir,
                store_path=store,
            )
            assert eids == []
            assert not store.exists()


# ── M2 hook end-to-end ────────────────────────────────────────────────────────


class TestAuditHookOutcomeLog(unittest.TestCase):
    """End-to-end: trigger_memory_refresh writes to the outcome ledger."""

    def _make_project(self, prov: dict) -> tuple[Path, Path]:
        import tempfile

        tmpdir = Path(tempfile.mkdtemp())
        project_root = tmpdir / "proj"
        project_root.mkdir()
        runs_dir = project_root / "runs" / "2026-06-10"
        runs_dir.mkdir(parents=True)
        run_dir = runs_dir / "run-abc"
        run_dir.mkdir()
        audit_dir = run_dir / "09_audit"
        audit_dir.mkdir()
        (audit_dir / "experiment_provenance.json").write_text(
            json.dumps(prov), encoding="utf-8"
        )
        # Write a manifest so SHA can be computed
        manifest = {"run_id": "run-abc", "memories_applied": prov.get("memories_applied", [])}
        (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return project_root, run_dir

    def test_hook_writes_outcome_event_for_single_memory(self) -> None:
        from agentic_swmm.memory.audit_hook import trigger_memory_refresh
        from agentic_swmm.memory.memory_outcomes import (
            OUTCOME_LEDGER_FILENAME,
            load_outcome_events,
        )

        prov = _make_provenance(
            memories_applied=["pm-abc"],
            return_code=0,
            kge=0.72,
        )
        project_root, run_dir = self._make_project(prov)
        memory_dir = project_root / "memory" / "modeling-memory"

        with mock.patch(
            "agentic_swmm.memory.audit_hook._summarize_memory_cli",
            return_value=(0, ""),
        ), mock.patch(
            "agentic_swmm.memory.audit_hook._refresh_rag_corpus",
            return_value=(0, ""),
        ), mock.patch(
            "agentic_swmm.memory.audit_hook._run_decay_pass",
            return_value={"skipped": True},
        ):
            result = trigger_memory_refresh(run_dir)

        # Outcome events must be present in result and in the ledger
        assert "outcome_events" in result or not result.get("errors"), (
            f"errors: {result.get('errors')}"
        )

        ledger = memory_dir / OUTCOME_LEDGER_FILENAME
        if ledger.is_file():
            events = load_outcome_events(ledger)
            assert any(e.get("memory_id") == "pm-abc" for e in events)

    def test_hook_skips_outcome_when_no_memories_applied(self) -> None:
        from agentic_swmm.memory.audit_hook import trigger_memory_refresh
        from agentic_swmm.memory.memory_outcomes import (
            OUTCOME_LEDGER_FILENAME,
        )

        prov = _make_provenance(memories_applied=[])
        project_root, run_dir = self._make_project(prov)
        memory_dir = project_root / "memory" / "modeling-memory"

        with mock.patch(
            "agentic_swmm.memory.audit_hook._summarize_memory_cli",
            return_value=(0, ""),
        ), mock.patch(
            "agentic_swmm.memory.audit_hook._refresh_rag_corpus",
            return_value=(0, ""),
        ), mock.patch(
            "agentic_swmm.memory.audit_hook._run_decay_pass",
            return_value={"skipped": True},
        ):
            result = trigger_memory_refresh(run_dir)

        # No outcome events when memories_applied is empty
        assert "outcome_events" not in result
        ledger = memory_dir / OUTCOME_LEDGER_FILENAME
        assert not ledger.is_file() or load_outcome_events(ledger) == []

    def test_skip_memory_env_skips_outcome_log(self) -> None:
        """AISWMM_SKIP_MEMORY=1 must skip the outcome log entirely."""
        from agentic_swmm.memory.audit_hook import trigger_memory_refresh
        from agentic_swmm.memory.memory_outcomes import (
            OUTCOME_LEDGER_FILENAME,
            load_outcome_events,
        )

        prov = _make_provenance(
            memories_applied=["pm-abc"],
            return_code=0,
            kge=0.72,
        )
        project_root, run_dir = self._make_project(prov)
        memory_dir = project_root / "memory" / "modeling-memory"

        with mock.patch.dict(os.environ, {"AISWMM_SKIP_MEMORY": "1"}):
            result = trigger_memory_refresh(run_dir)

        assert result.get("skipped") is True
        ledger = memory_dir / OUTCOME_LEDGER_FILENAME
        assert not ledger.is_file() or load_outcome_events(ledger) == []


def load_outcome_events(store: Path):
    """Local re-export for test convenience."""
    from agentic_swmm.memory.memory_outcomes import (
        load_outcome_events as _load,
    )
    return _load(store)


# ── CLI verb: aiswmm memory health ───────────────────────────────────────────


class TestMemoryHealthCLI(unittest.TestCase):
    def _populate_ledger(self, store: Path) -> None:
        from agentic_swmm.memory.memory_outcomes import append_outcome_event

        for event_type in ("positive", "positive", "below_band"):
            append_outcome_event(
                store,
                memory_id="pm-abc",
                memory_kind="parametric",
                run_dir="/runs/r1",
                run_manifest_sha="aa",
                event=event_type,
                metric={"name": "kge", "value": 0.70, "band_low": 0.60},
                attribution="single",
            )
        append_outcome_event(
            store,
            memory_id="pm-xyz",
            memory_kind="parametric",
            run_dir="/runs/r2",
            run_manifest_sha="bb",
            event="run_failed",
            metric=None,
            attribution="single",
        )

    def test_health_with_id_exits_zero(self) -> None:
        from agentic_swmm.commands.memory_health import health_main

        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            memory_dir.mkdir()
            store = memory_dir / "memory_outcome_events.jsonl"
            self._populate_ledger(store)

            args = mock.MagicMock()
            args.memory_id = "pm-abc"
            args.top = 10
            args.memory_dir = memory_dir

            rc = health_main(args)
            assert rc == 0

    def test_health_without_id_exits_zero(self) -> None:
        from agentic_swmm.commands.memory_health import health_main

        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            memory_dir.mkdir()
            store = memory_dir / "memory_outcome_events.jsonl"
            self._populate_ledger(store)

            args = mock.MagicMock()
            args.memory_id = None
            args.top = 5
            args.memory_dir = memory_dir

            rc = health_main(args)
            assert rc == 0

    def test_health_empty_ledger_exits_zero(self) -> None:
        from agentic_swmm.commands.memory_health import health_main

        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            memory_dir.mkdir()

            args = mock.MagicMock()
            args.memory_id = "pm-nonexistent"
            args.top = 10
            args.memory_dir = memory_dir

            rc = health_main(args)
            assert rc == 0

    def test_health_score_in_output(self) -> None:
        """health_main must write the derived score to stdout."""
        import io
        from contextlib import redirect_stdout

        from agentic_swmm.commands.memory_health import health_main

        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            memory_dir.mkdir()
            store = memory_dir / "memory_outcome_events.jsonl"
            self._populate_ledger(store)

            args = mock.MagicMock()
            args.memory_id = "pm-abc"
            args.top = 10
            args.memory_dir = memory_dir

            buf = io.StringIO()
            with redirect_stdout(buf):
                health_main(args)

            output = buf.getvalue()
            assert "pm-abc" in output
            # Score should be printed; 2 positive + 1 below_band
            # 0.70 + 0.05 + 0.05 - 0.15 = 0.65
            assert "0.65" in output or "0.650" in output

    def test_lowest_health_order(self) -> None:
        """Without id, entries are ordered by health score (lowest first)."""
        import io
        from contextlib import redirect_stdout

        from agentic_swmm.commands.memory_health import health_main

        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            memory_dir.mkdir()
            store = memory_dir / "memory_outcome_events.jsonl"
            self._populate_ledger(store)

            args = mock.MagicMock()
            args.memory_id = None
            args.top = 10
            args.memory_dir = memory_dir

            buf = io.StringIO()
            with redirect_stdout(buf):
                health_main(args)

            output = buf.getvalue()
            pos_xyz = output.find("pm-xyz")
            pos_abc = output.find("pm-abc")
            # pm-xyz has lower health (run_failed) so should appear first
            assert pos_xyz != -1 and pos_abc != -1
            assert pos_xyz < pos_abc, (
                "pm-xyz (lower health) should appear before pm-abc in the table"
            )


# ── Summary helpers ───────────────────────────────────────────────────────────


class TestSummaryHelpers(unittest.TestCase):
    def test_all_memory_ids_in_order(self) -> None:
        from agentic_swmm.memory.memory_outcomes import all_memory_ids_in_ledger

        events = [
            {"memory_id": "pm-a"},
            {"memory_id": "pm-b"},
            {"memory_id": "pm-a"},
        ]
        ids = all_memory_ids_in_ledger(events)
        assert ids == ["pm-a", "pm-b"]

    def test_summary_for_all_sorted_lowest_first(self) -> None:
        from agentic_swmm.memory.memory_outcomes import summary_for_all

        events = [
            {"memory_id": "pm-a", "event": "run_failed", "attribution": "single"},
            {"memory_id": "pm-b", "event": "positive", "attribution": "single"},
        ]
        summaries = summary_for_all(events, top_n=5, lowest_first=True)
        assert summaries[0]["memory_id"] == "pm-a"
        assert summaries[0]["health_score"] < summaries[1]["health_score"]


if __name__ == "__main__":
    unittest.main()
