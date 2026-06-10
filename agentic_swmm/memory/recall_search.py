"""Wrap the 761-LOC ``rag_memory_lib`` retriever as a recall tool (PRD M6).

This module is deliberately thin: the hashed-embedding hybrid retrieval
algorithm, scoring weights, and Chinese query-expansion table all live
in ``skills/swmm-rag-memory/scripts/rag_memory_lib.py`` and are out of
scope for the Memory PRD. The wrapper adds three things on top of
``retrieve``:

1. A staleness guard: compare ``corpus.jsonl`` mtime to
   ``lessons_learned.md`` mtime and surface a ``warning`` on the
   first result if the corpus lags by more than 60 s.
2. A schema-version refusal: if the corpus and the lessons file carry
   different ``schema_version`` markers, raise ``RuntimeError`` with the
   M2/M5 guidance to re-run audit.
3. A clean return shape: every dict carries ``text``, ``run_id``,
   ``source_path``, ``case_name``, ``score``, ``matched_terms``,
   ``schema_version`` so the planner sees a stable contract.

Recency weighting (P0-2)
------------------------
An optional multiplicative recency factor can be applied to each result's
``score`` after retrieval.  When ``half_life_days`` > 0, the score is
multiplied by ``0.5 ** (age_days / half_life_days)`` so older entries rank
lower.  When ``half_life_days == 0`` (the default), the code path is a
no-op: scores and ordering are byte-identical to the pre-P0-2 behaviour.

Age is computed as days since the entry's most recent timestamp field.
For RAG corpus entries the field used is (in priority order):
  1. ``last_seen_utc`` — updated whenever the lesson is reinforced.
  2. ``recorded_utc`` — the run record timestamp.
  3. ``created_utc`` — fallback for entries that predate later fields.
When none of these fields is present or parseable, the entry is treated as
age 0 (no weighting applied) so missing timestamps never penalise an entry.

The ``now_fn`` parameter (default: ``time.time``) accepts an alternative
callable returning a POSIX timestamp.  Tests inject a fixed value via this
seam — the live runtime never passes it explicitly.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_STALENESS_THRESHOLD_SECONDS = 60.0

# Field names checked (in priority order) when computing entry age.
# For RAG entries: last_seen_utc is preferred because it reflects the
# most recent reinforcement of a lesson.  recorded_utc is the run-record
# timestamp.  created_utc is a rarely-set fallback.
_AGE_TIMESTAMP_FIELDS = ("last_seen_utc", "recorded_utc", "created_utc")


def _parse_iso_to_posix(iso_str: str) -> float | None:
    """Parse an ISO-8601 timestamp string into a POSIX float, or ``None``."""
    if not iso_str:
        return None
    # Normalise the common ``+00:00`` / ``Z`` variants.
    normalised = iso_str.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalised)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _age_days_for_entry(
    entry: dict[str, Any],
    now_posix: float,
) -> float | None:
    """Return age in days for ``entry``, or ``None`` when not determinable.

    Checks :data:`_AGE_TIMESTAMP_FIELDS` in priority order and returns
    the age of the first parseable field.  Returns ``None`` when no
    timestamp is available so callers can distinguish "unknown age"
    from "age 0".
    """
    for field in _AGE_TIMESTAMP_FIELDS:
        raw = entry.get(field)
        if not raw:
            continue
        ts = _parse_iso_to_posix(str(raw))
        if ts is not None:
            age_seconds = max(0.0, now_posix - ts)
            return age_seconds / 86400.0
    return None


def _apply_recency_weight(
    results: list[dict[str, Any]],
    *,
    half_life_days: float,
    now_fn: Callable[[], float] = time.time,
) -> list[dict[str, Any]]:
    """Apply an optional recency weighting to ``results`` in-place and re-sort.

    When ``half_life_days <= 0``, this function is a no-op: the input list
    is returned unmodified.  This preserves byte-identical ranking and
    scores compared to the pre-weighting behaviour.

    For ``half_life_days > 0``, each result's ``score`` is multiplied by
    ``0.5 ** (age_days / half_life_days)``.  Entries with unknown age are
    left at their original score (no penalty for missing timestamps).
    After adjustment the list is re-sorted by score descending so higher-
    recency entries bubble up.
    """
    if half_life_days <= 0:
        return results

    now_posix = now_fn()
    for entry in results:
        age = _age_days_for_entry(entry, now_posix)
        if age is None:
            continue
        original_score = entry.get("score") or 0.0
        weight = 0.5 ** (age / half_life_days)
        entry["score"] = original_score * weight

    results.sort(key=lambda e: (e.get("score") or 0.0), reverse=True)
    return results


def _rag_scripts_dir() -> Path:
    """Resolve the directory holding ``rag_memory_lib.py``.

    Checks the source tree first (development checkouts) and falls back
    to the packaged resource root used by the wheel install layout.
    """
    in_tree = Path(__file__).resolve().parents[2] / "skills" / "swmm-rag-memory" / "scripts"
    if (in_tree / "rag_memory_lib.py").is_file():
        return in_tree
    try:
        from agentic_swmm.utils.paths import resource_path

        return resource_path("skills", "swmm-rag-memory", "scripts")
    except FileNotFoundError:
        return in_tree


def _load_rag_lib():
    """Import the bundled ``rag_memory_lib`` once, lazily.

    The script directory is added to ``sys.path`` only when this wrapper
    is first called. We do NOT vendor or re-import the 761-LOC file.
    """
    scripts_dir = _rag_scripts_dir()
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import rag_memory_lib  # type: ignore[import-not-found]

    return rag_memory_lib


_SCHEMA_VERSION_RE = re.compile(r"schema_version\s*[:=]\s*([0-9]+\.[0-9]+)")


def _detect_lessons_schema_version(lessons_path: Path) -> str | None:
    if not lessons_path.is_file():
        return None
    try:
        head = lessons_path.read_text(encoding="utf-8")[:4000]
    except OSError:
        return None
    match = _SCHEMA_VERSION_RE.search(head)
    return match.group(1) if match else None


def _detect_corpus_schema_version(entries: list[dict[str, Any]]) -> str | None:
    for entry in entries:
        version = entry.get("schema_version")
        if version:
            return str(version)
    return None


def _corpus_is_stale(corpus_path: Path, lessons_path: Path) -> bool:
    if not corpus_path.is_file() or not lessons_path.is_file():
        return False
    try:
        corpus_mtime = corpus_path.stat().st_mtime
        lessons_mtime = lessons_path.stat().st_mtime
    except OSError:
        return False
    return (lessons_mtime - corpus_mtime) > _STALENESS_THRESHOLD_SECONDS


def _format_stale_warning(corpus_path: Path, lessons_path: Path) -> str:
    from datetime import datetime, timezone

    def _date(path: Path) -> str:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
        except OSError:
            return "unknown"

    return (
        f"rag corpus is stale (built {_date(corpus_path)}, "
        f"lessons updated {_date(lessons_path)}); run aiswmm audit to refresh"
    )


def recall_search(
    query: str,
    top_k: int = 3,
    *,
    index_dir: Path,
    corpus_path: Path,
    lessons_path: Path,
    project: str | None = None,
    retriever: str = "hybrid",
    half_life_days: float = 0.0,
    now_fn: Callable[[], float] = time.time,
) -> list[dict[str, Any]]:
    """Return up to ``top_k`` similar historical entries for ``query``.

    Parameters
    ----------
    query:
        Natural-language query.
    top_k:
        Maximum number of results.
    index_dir:
        Directory containing ``corpus.jsonl`` + ``embedding_index.json``.
    corpus_path:
        Explicit path to the corpus jsonl (must be inside ``index_dir``).
    lessons_path:
        Curated lessons file used for staleness + schema checks.
    project:
        Optional project key to bias scoring (defers to lib default).
    retriever:
        ``"keyword"`` or ``"hybrid"``. Defaults to ``"hybrid"`` to use
        the existing hashed embedding alongside the keyword score.
    half_life_days:
        Optional recency weighting.  When > 0, each result's ``score``
        is multiplied by ``0.5 ** (age_days / half_life_days)`` and the
        list is re-sorted.  When 0 (default), scores and ordering are
        unchanged — identical to the pre-weighting behaviour.  Read from
        config key ``memory.recall_half_life_days``; the caller is
        responsible for passing the configured value.
    now_fn:
        Callable returning the current POSIX time.  Defaults to
        ``time.time``.  Pass a fixed lambda in tests to get
        deterministic age calculations.

    Returns
    -------
    list[dict[str, Any]]
        Result dicts with at least ``text``, ``run_id``, ``source_path``,
        ``case_name``, ``score``, ``matched_terms``, ``schema_version``,
        ``layer``, and (if the corpus is stale) ``warning``.

    Raises
    ------
    RuntimeError
        If the corpus and the lessons file disagree on ``schema_version``.
        The error message tells the user to re-run ``aiswmm audit``.
    """
    if not corpus_path.is_file():
        return []

    lib = _load_rag_lib()
    entries = lib.load_corpus(corpus_path)
    if not entries:
        return []

    corpus_schema = _detect_corpus_schema_version(entries)
    lessons_schema = _detect_lessons_schema_version(lessons_path)
    if corpus_schema and lessons_schema and corpus_schema != lessons_schema:
        raise RuntimeError(
            "mixed schema_version detected: "
            f"corpus={corpus_schema!r}, lessons={lessons_schema!r}. "
            "Run `aiswmm audit ... --rebuild` to refresh the corpus."
        )

    # Hybrid retrieval can use the pre-built embedding vectors when
    # they line up with the corpus; otherwise the lib recomputes them.
    embedding_vectors: list[dict[str, float]] | None = None
    embedding_path = index_dir / "embedding_index.json"
    if retriever == "hybrid" and embedding_path.is_file():
        try:
            embedding_vectors = lib.load_embedding_vectors(embedding_path)
        except Exception:
            embedding_vectors = None
        if embedding_vectors is not None and len(embedding_vectors) != len(entries):
            embedding_vectors = None

    raw = lib.retrieve(
        entries,
        query,
        top_k=top_k,
        project=project,
        retriever=retriever,
        embedding_vectors=embedding_vectors,
    )

    stale = _corpus_is_stale(corpus_path, lessons_path)
    warning = _format_stale_warning(corpus_path, lessons_path) if stale else None

    # Build the cleaned list before optional recency weighting so that we
    # also carry through the raw timestamp fields needed for age computation.
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        result: dict[str, Any] = {
            "run_id": entry.get("run_id"),
            "source_path": entry.get("source_path"),
            "case_name": entry.get("case_name"),
            "score": entry.get("score"),
            "matched_terms": entry.get("matched_terms", []),
            "schema_version": entry.get("schema_version") or corpus_schema,
            "failure_patterns": entry.get("failure_patterns", []),
            "source_type": entry.get("source_type"),
            "layer": "raw" if entry.get("source_type") in {"experiment_note", "chat_note"} else "curated",
            "text": entry.get("excerpt") or "",
        }
        # Carry timestamp fields through so _apply_recency_weight can read
        # them for age calculation.  They are not part of the public contract
        # but harmless to keep — callers that do not need them ignore them.
        for ts_field in _AGE_TIMESTAMP_FIELDS:
            if ts_field in entry:
                result[ts_field] = entry[ts_field]
        cleaned.append(result)

    # Apply the optional recency weighting (no-op when half_life_days <= 0).
    cleaned = _apply_recency_weight(cleaned, half_life_days=half_life_days, now_fn=now_fn)

    # Attach staleness warning to the (now re-ranked) first entry.
    if warning and cleaned:
        cleaned[0]["warning"] = warning

    return cleaned
