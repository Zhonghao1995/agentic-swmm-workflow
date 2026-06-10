"""Application outcome log and per-entry health score (PR 3, Phase 1).

Operational context
-------------------
aiswmm accumulates modeling-memory entries (parametric, calibration, and
negative lessons) through explicit human-gated promotion verbs.  Once an
entry is in the store and its id is stamped into a run manifest's
``memories_applied`` field, the outcome of that run is observable.

This module:

1.  Appends outcome events to ``memory/modeling-memory/memory_outcome_events.jsonl``
    — an append-only ledger written only by the M2 post-audit hook.
2.  Derives a **health score** for each memory entry as a pure function
    of its ledger history — deterministic, no wall-clock term.
3.  Exposes :func:`classify_and_record_outcome` as the single write path
    called from ``audit_hook.py``.

Event schema
------------
Each line is a JSON object::

    {
      "event_id":        "oe-<ts_compact>-<n>",
      "ts_utc":          "2026-06-10T12:34:56Z",
      "memory_id":       "pm-abc123",
      "memory_kind":     "parametric|calibration|lesson",
      "run_dir":         "/abs/path/to/run",
      "run_manifest_sha": "<sha256 hex>",
      "event":           "positive|below_band|run_failed|contradicted|reconfirmed",
      "metric":          {"name": "kge", "value": 0.71, "band_low": 0.65},
      "attribution":     "single|excluded_multi",
      "source":          "m2_audit_hook"
    }

The ``metric`` field is ``null`` for ``run_failed`` events (no KGE to
report) and for ``excluded_multi`` records (no score computed).

Health score defaults (all in :data:`_HEALTH_TUNABLES`)
--------------------------------------------------------
Start: 0.70
positive / reconfirmed: +0.05
below_band: −0.15
run_failed: −0.40
contradicted: −0.30
excluded_multi: no effect
Clamped to [0.0, 1.0].

Band definition
---------------
The "historical band" low bound for a memory entry is derived from the
stored performance metrics on the memory record itself plus any prior
``positive`` events in the ledger.  Requires ≥ 2 data points before
the band is active — with fewer points only ``run_failed`` events
affect the health score (the classifier still records positives; they
widen the band history).

Append-only discipline
----------------------
:func:`append_outcome_event` follows the same ``open(..., "a")``
pattern as the other JSONL stores in this package.  The function never
reads-then-rewrites the file.  An interrupted write leaves a torn final
line; readers must tolerate that (standard pattern here).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────────

#: All health score parameters live here so downstream tooling can replay
#: the same ledger under different thresholds without changing code.
_HEALTH_TUNABLES: dict[str, float] = {
    "start": 0.70,
    "delta_positive": +0.05,
    "delta_reconfirmed": +0.05,
    "delta_below_band": -0.15,
    "delta_run_failed": -0.40,
    "delta_contradicted": -0.30,
    # Minimum absolute KGE tolerance when comparing a new run to the band.
    "below_band_tolerance": 0.10,
}

# ── Threading lock for append ────────────────────────────────────────────────
# A process-level lock protects the write path against concurrent audit
# hooks in the same process (e.g. parallel test fixture teardowns).
# Cross-process safety relies on the OS serialising short O_APPEND writes
# on a local filesystem — same contract as the other JSONL stores here.
_append_lock = threading.Lock()

# ── Ledger file name ─────────────────────────────────────────────────────────
OUTCOME_LEDGER_FILENAME = "memory_outcome_events.jsonl"

# ── Event types ──────────────────────────────────────────────────────────────
_VALID_EVENTS = frozenset(
    {"positive", "below_band", "run_failed", "contradicted", "reconfirmed"}
)
_VALID_ATTRIBUTIONS = frozenset({"single", "excluded_multi"})
_VALID_KINDS = frozenset({"parametric", "calibration", "lesson"})


# ── Low-level writer ────────────────────────────────────────────────────────


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _make_event_id(ts_str: str, store_path: Path) -> str:
    """Derive a collision-resistant event id without reading the file.

    Uses a 6-hex digest of (timestamp + process-id + file-path) as the
    suffix so concurrent writers in separate processes do not collide.
    """
    raw = f"{ts_str}-{os.getpid()}-{store_path}"
    suffix = hashlib.sha256(raw.encode()).hexdigest()[:6]
    compact = ts_str.replace("-", "").replace(":", "").replace("Z", "")
    return f"oe-{compact}-{suffix}"


def append_outcome_event(
    store_path: Path,
    *,
    memory_id: str,
    memory_kind: str,
    run_dir: str,
    run_manifest_sha: str,
    event: str,
    metric: dict[str, Any] | None,
    attribution: str,
) -> str:
    """Append one outcome event line to ``store_path``.

    Creates parent directories if needed.  The write is a single
    ``open(..., "a")`` call — readers always see complete earlier lines.

    Returns the ``event_id`` of the written record.

    Raises:
        ValueError: Any enumerated field is out of range.
    """
    if event not in _VALID_EVENTS:
        raise ValueError(
            f"event must be one of {sorted(_VALID_EVENTS)}; got {event!r}"
        )
    if attribution not in _VALID_ATTRIBUTIONS:
        raise ValueError(
            f"attribution must be one of {sorted(_VALID_ATTRIBUTIONS)}; "
            f"got {attribution!r}"
        )
    if memory_kind not in _VALID_KINDS:
        raise ValueError(
            f"memory_kind must be one of {sorted(_VALID_KINDS)}; "
            f"got {memory_kind!r}"
        )

    ts_str = _utc_now_str()
    event_id = _make_event_id(ts_str, store_path)

    record: dict[str, Any] = {
        "event_id": event_id,
        "ts_utc": ts_str,
        "memory_id": memory_id,
        "memory_kind": memory_kind,
        "run_dir": str(run_dir),
        "run_manifest_sha": run_manifest_sha,
        "event": event,
        "metric": metric,
        "attribution": attribution,
        "source": "m2_audit_hook",
    }

    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"

    with _append_lock:
        with store_path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    return event_id


# ── Ledger reader ────────────────────────────────────────────────────────────


def load_outcome_events(store_path: Path) -> list[dict[str, Any]]:
    """Return all valid events from ``store_path``.

    Tolerates missing files (returns ``[]``) and torn final lines
    (skips them) — same contract as the other JSONL stores.
    """
    store_path = Path(store_path)
    if not store_path.is_file():
        return []
    events: list[dict[str, Any]] = []
    with store_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                # Torn final line during a concurrent write — skip.
                continue
            if isinstance(row, dict):
                events.append(row)
    return events


def events_for_memory(
    events: list[dict[str, Any]], memory_id: str
) -> list[dict[str, Any]]:
    """Filter ``events`` to those for ``memory_id``."""
    return [e for e in events if e.get("memory_id") == memory_id]


# ── Health score ─────────────────────────────────────────────────────────────


def health_score(memory_id: str, events: list[dict[str, Any]]) -> float:
    """Derive the health score for ``memory_id`` from ``events``.

    Pure function: same ``events`` prefix → same score.
    Clamped to [0.0, 1.0].  ``excluded_multi`` events are ignored.
    """
    tunables = _HEALTH_TUNABLES
    score = tunables["start"]

    delta_map = {
        "positive": tunables["delta_positive"],
        "reconfirmed": tunables["delta_reconfirmed"],
        "below_band": tunables["delta_below_band"],
        "run_failed": tunables["delta_run_failed"],
        "contradicted": tunables["delta_contradicted"],
    }

    for ev in events:
        if ev.get("memory_id") != memory_id:
            continue
        if ev.get("attribution") == "excluded_multi":
            continue
        ev_type = ev.get("event", "")
        delta = delta_map.get(ev_type, 0.0)
        score += delta

    return max(0.0, min(1.0, score))


# ── Band computation ─────────────────────────────────────────────────────────


def _band_low_for_memory(
    memory_id: str,
    prior_events: list[dict[str, Any]],
    stored_kge: float | None,
) -> float | None:
    """Return the historical band lower bound for ``memory_id``.

    Collects KGE data points from:
    - The memory record's stored performance metric (``stored_kge``).
    - Prior ``positive`` events in the ledger that carry a metric value.

    Requires ≥ 2 data points; returns ``None`` when fewer are available.
    The band low is the minimum of all data points minus the tolerance.
    """
    tolerance = _HEALTH_TUNABLES["below_band_tolerance"]
    kge_values: list[float] = []

    if stored_kge is not None:
        kge_values.append(stored_kge)

    for ev in prior_events:
        if ev.get("memory_id") != memory_id:
            continue
        if ev.get("event") != "positive":
            continue
        m = ev.get("metric") or {}
        v = m.get("value")
        if v is not None:
            try:
                kge_values.append(float(v))
            except (TypeError, ValueError):
                pass

    if len(kge_values) < 2:
        return None

    return min(kge_values) - tolerance


# ── Event classification ─────────────────────────────────────────────────────


def _manifest_sha(manifest_path: Path) -> str:
    """Return the SHA-256 hex digest of ``manifest_path``."""
    try:
        data = manifest_path.read_bytes()
        return hashlib.sha256(data).hexdigest()
    except OSError:
        return ""


def _kge_from_provenance(provenance: dict[str, Any]) -> float | None:
    """Extract KGE from a provenance dict.

    Looks in ``performance_metrics.kge`` (parametric/calibration records
    and provenance written by the audit pipeline) and in
    ``metrics.kge`` as a fallback.  Returns ``None`` when absent.
    """
    # Primary path: performance_metrics block (matches ParametricRecord schema)
    pm = provenance.get("performance_metrics") or {}
    if isinstance(pm, dict):
        kge = pm.get("kge")
        if kge is not None:
            try:
                return float(kge)
            except (TypeError, ValueError):
                pass

    # Fallback: top-level metrics block written by some audit paths
    m = provenance.get("metrics") or {}
    if isinstance(m, dict):
        kge = m.get("kge")
        if kge is not None:
            try:
                return float(kge)
            except (TypeError, ValueError):
                pass

    return None


def _stored_kge_for_memory(
    memory_id: str,
    memory_dir: Path,
) -> float | None:
    """Look up the stored KGE for ``memory_id`` in the relevant memory store.

    ``pm-<run_id>`` → parametric_memory.jsonl
    ``cm-<run_id>`` → calibration_memory.jsonl
    Returns ``None`` when the store does not exist or the entry has no KGE.
    """
    if memory_id.startswith("pm-"):
        run_id = memory_id[3:]
        store = memory_dir / "parametric_memory.jsonl"
        try:
            from agentic_swmm.memory.parametric_memory import recall_parametric

            rows = recall_parametric(store, {"run_id": run_id})
        except Exception:
            return None
        for row in rows:
            kge = (row.get("performance_metrics") or {}).get("kge")
            if kge is not None:
                try:
                    return float(kge)
                except (TypeError, ValueError):
                    pass
        return None

    if memory_id.startswith("cm-"):
        run_id = memory_id[3:]
        store = memory_dir / "calibration_memory.jsonl"
        try:
            from agentic_swmm.memory.calibration_memory import recall_calibration

            rows = recall_calibration(store, {"run_id": run_id})
        except Exception:
            return None
        for row in rows:
            # calibration records use objective_name/objective_value;
            # also check secondary_metrics for kge.
            obj_name = row.get("objective_name") or ""
            if obj_name.lower() in {"kge", "kling-gupta"}:
                v = row.get("objective_value")
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            kge = (row.get("secondary_metrics") or {}).get("kge")
            if kge is not None:
                try:
                    return float(kge)
                except (TypeError, ValueError):
                    pass
        return None

    return None


def _memory_kind_for_id(memory_id: str) -> str:
    """Infer memory kind from the id prefix."""
    if memory_id.startswith("pm-"):
        return "parametric"
    if memory_id.startswith("cm-"):
        return "calibration"
    return "lesson"


def _is_run_failed(provenance: dict[str, Any]) -> bool:
    """Return True when the run's provenance indicates a solver or QA failure.

    Reuses the QA-gate thresholds from ``postflight.py`` fallbacks
    (runoff continuity > 10 % magnitude, flow continuity > 5 %).
    Also checks ``return_code`` and ``solver_errors`` for solver failures.

    Thresholds source: agentic_swmm/agent/swmm_runtime/postflight.py
    _FALLBACK_CONTINUITY_THRESHOLDS: runoff_continuity_pct fail=10.0,
    flow_continuity_pct fail=5.0.
    """
    # Solver error check
    return_code = provenance.get("return_code")
    if return_code is not None:
        try:
            if int(return_code) != 0:
                return True
        except (TypeError, ValueError):
            pass

    solver_errors = provenance.get("solver_errors") or []
    if solver_errors:
        return True

    # QA continuity gate — same conservative fallbacks as postflight.py
    # _FALLBACK_CONTINUITY_THRESHOLDS lines 42-46 in postflight.py
    _RUNOFF_FAIL = 10.0  # agentic_swmm/agent/swmm_runtime/postflight.py:44
    _FLOW_FAIL = 5.0      # agentic_swmm/agent/swmm_runtime/postflight.py:45

    metrics = provenance.get("metrics") or {}
    continuity = (metrics.get("continuity_error") or {}).get("values") or {}

    # Accept both the v1.1 key names and the older aliases
    runoff_val = continuity.get("runoff_quantity", continuity.get("runoff"))
    flow_val = continuity.get("flow_routing", continuity.get("flow"))

    for val, threshold in (
        (runoff_val, _RUNOFF_FAIL),
        (flow_val, _FLOW_FAIL),
    ):
        if val is not None:
            try:
                if abs(float(val)) >= threshold:
                    return True
            except (TypeError, ValueError):
                pass

    return False


def classify_and_record_outcome(
    *,
    run_dir: Path,
    provenance: dict[str, Any],
    manifest_path: Path | None,
    memory_dir: Path,
    store_path: Path,
) -> list[str]:
    """Classify the run's memory outcomes and append events to the ledger.

    Entry point called from ``audit_hook.trigger_memory_refresh`` after
    a successful audit.  Reads ``provenance["memories_applied"]`` and
    emits one event per memory id according to the attribution rules.

    Returns the list of ``event_id`` values written (may be empty).
    """
    memories_applied: list[str] = list(provenance.get("memories_applied") or [])

    if not memories_applied:
        return []

    sha = _manifest_sha(manifest_path) if manifest_path else ""
    run_dir_str = str(run_dir)

    # Load existing events once for band computation.
    prior_events = load_outcome_events(store_path)

    written: list[str] = []

    if len(memories_applied) > 1:
        # Multi-memory: write one excluded_multi record per id, no health effect.
        for mid in memories_applied:
            kind = _memory_kind_for_id(mid)
            try:
                eid = append_outcome_event(
                    store_path,
                    memory_id=mid,
                    memory_kind=kind,
                    run_dir=run_dir_str,
                    run_manifest_sha=sha,
                    event="run_failed"
                    if _is_run_failed(provenance)
                    else "positive",
                    metric=None,
                    attribution="excluded_multi",
                )
                written.append(eid)
            except Exception as exc:
                _log.debug("outcome log excluded_multi write failed for %s: %s", mid, exc)
        return written

    # Exactly one memory applied → classify properly.
    memory_id = memories_applied[0]
    kind = _memory_kind_for_id(memory_id)

    # Check for negative lesson secondary events first (only for lesson kind).
    if kind == "lesson":
        lesson_events = _classify_lesson_events(
            memory_id=memory_id,
            provenance=provenance,
            run_dir_str=run_dir_str,
            sha=sha,
            store_path=store_path,
            memory_dir=memory_dir,
        )
        written.extend(lesson_events)
        return written

    # Parametric / calibration: check run_failed first.
    if _is_run_failed(provenance):
        try:
            eid = append_outcome_event(
                store_path,
                memory_id=memory_id,
                memory_kind=kind,
                run_dir=run_dir_str,
                run_manifest_sha=sha,
                event="run_failed",
                metric=None,
                attribution="single",
            )
            written.append(eid)
        except Exception as exc:
            _log.debug("outcome log run_failed write failed for %s: %s", memory_id, exc)
        return written

    # Run succeeded — attempt KGE-based band classification.
    run_kge = _kge_from_provenance(provenance)
    if run_kge is None:
        _log.debug(
            "outcome log: skipping classification for %s — KGE absent from provenance",
            memory_id,
        )
        return written

    stored_kge = _stored_kge_for_memory(memory_id, memory_dir)
    band_low = _band_low_for_memory(memory_id, prior_events, stored_kge)

    metric_payload: dict[str, Any] = {"name": "kge", "value": run_kge}

    if band_low is None:
        # Fewer than 2 data points — record positive but no below_band possible.
        ev_type = "positive"
        metric_payload["band_low"] = None
    else:
        metric_payload["band_low"] = band_low
        ev_type = "below_band" if run_kge < band_low else "positive"

    try:
        eid = append_outcome_event(
            store_path,
            memory_id=memory_id,
            memory_kind=kind,
            run_dir=run_dir_str,
            run_manifest_sha=sha,
            event=ev_type,
            metric=metric_payload,
            attribution="single",
        )
        written.append(eid)
    except Exception as exc:
        _log.debug("outcome log write failed for %s: %s", memory_id, exc)

    return written


def _classify_lesson_events(
    *,
    memory_id: str,
    provenance: dict[str, Any],
    run_dir_str: str,
    sha: str,
    store_path: Path,
    memory_dir: Path,
) -> list[str]:
    """Classify outcome events for a negative-lesson memory entry.

    Emits:
    - ``reconfirmed`` — always when the lesson id is applied (maps to
      the existing evidence_count++ path in the hook).
    - ``contradicted`` — when the run's parameters fall inside the
      lesson's machine-checkable known-bad parameter region AND the run
      succeeded cleanly (no run_failed).  Only machine-checkable regions;
      no LLM calls.

    Returns list of event_ids written.
    """
    written: list[str] = []
    run_failed = _is_run_failed(provenance)

    # Determine the lesson id (strip prefix; lessons use the run_id as key)
    # Lesson ids are typically just the run_id string without a prefix,
    # but by convention they may also be prefixed. We try both.
    # The negative lessons store is keyed by run_id; memory_id is the full
    # id (which for lessons may be just the run_id itself if no prefix was used).
    lesson_run_id = memory_id  # could be "nl-<run_id>" or just "<run_id>"

    # Try to find the lesson in the store.
    lesson = None
    jsonl_store = memory_dir / "negative_lessons.jsonl"
    md_store = memory_dir / "negative_lessons.md"
    try:
        from agentic_swmm.memory.negative_lessons import recall_negative_lessons

        candidates = recall_negative_lessons(jsonl_store)
        # Match by memory_id (which may be run_id) in the candidates
        for cand in candidates:
            if cand.run_id == lesson_run_id:
                lesson = cand
                break
    except Exception:
        pass

    # Check contradicted: run succeeded + params in known-bad region.
    if not run_failed and lesson is not None:
        parameters_run: dict[str, float] = {}
        calib = provenance.get("calibration") or {}
        if isinstance(calib, dict):
            for k, v in (calib.get("parameters") or {}).items():
                try:
                    parameters_run[str(k)] = float(v)
                except (TypeError, ValueError):
                    pass
        if not parameters_run:
            for k, v in (provenance.get("parameters") or {}).items():
                try:
                    parameters_run[str(k)] = float(v)
                except (TypeError, ValueError):
                    pass

        # Machine-checkable region match: same logic as is_param_set_known_bad.
        if parameters_run and lesson.parameters_tried:
            from agentic_swmm.memory.negative_lessons import is_param_set_known_bad

            matched = is_param_set_known_bad(
                jsonl_store,
                provenance.get("case_name", ""),
                parameters_run,
            )
            if matched is not None:
                try:
                    eid = append_outcome_event(
                        store_path,
                        memory_id=memory_id,
                        memory_kind="lesson",
                        run_dir=run_dir_str,
                        run_manifest_sha=sha,
                        event="contradicted",
                        metric=None,
                        attribution="single",
                    )
                    written.append(eid)
                except Exception as exc:
                    _log.debug(
                        "outcome log contradicted write failed for %s: %s",
                        memory_id,
                        exc,
                    )

    # Reconfirmed: always emit when lesson is applied.
    try:
        eid = append_outcome_event(
            store_path,
            memory_id=memory_id,
            memory_kind="lesson",
            run_dir=run_dir_str,
            run_manifest_sha=sha,
            event="reconfirmed",
            metric=None,
            attribution="single",
        )
        written.append(eid)
    except Exception as exc:
        _log.debug(
            "outcome log reconfirmed write failed for %s: %s", memory_id, exc
        )

    return written


# ── CLI helpers ──────────────────────────────────────────────────────────────


def all_memory_ids_in_ledger(events: list[dict[str, Any]]) -> list[str]:
    """Return deduplicated memory ids present in the ledger, in first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for ev in events:
        mid = ev.get("memory_id") or ""
        if mid and mid not in seen:
            seen.add(mid)
            result.append(mid)
    return result


def summary_for_all(
    events: list[dict[str, Any]], *, top_n: int = 10, lowest_first: bool = True
) -> list[dict[str, Any]]:
    """Return health summaries for all memory ids, sorted by health score.

    Each entry: ``{"memory_id", "health_score", "event_count"}``.
    When ``lowest_first`` is True (the default for the ``memory health``
    verb with no id) entries are sorted ascending by health score.
    """
    mids = all_memory_ids_in_ledger(events)
    summaries = []
    for mid in mids:
        ev_for_mid = events_for_memory(events, mid)
        score = health_score(mid, ev_for_mid)
        summaries.append(
            {
                "memory_id": mid,
                "health_score": score,
                "event_count": len(ev_for_mid),
            }
        )
    summaries.sort(key=lambda x: x["health_score"], reverse=not lowest_first)
    return summaries[:top_n]


__all__ = [
    "OUTCOME_LEDGER_FILENAME",
    "_HEALTH_TUNABLES",
    "append_outcome_event",
    "load_outcome_events",
    "events_for_memory",
    "health_score",
    "classify_and_record_outcome",
    "all_memory_ids_in_ledger",
    "summary_for_all",
]
