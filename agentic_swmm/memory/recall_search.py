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
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


_STALENESS_THRESHOLD_SECONDS = 60.0


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

    cleaned: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        result = {
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
        if warning and index == 0:
            result["warning"] = warning
        cleaned.append(result)
    return cleaned
