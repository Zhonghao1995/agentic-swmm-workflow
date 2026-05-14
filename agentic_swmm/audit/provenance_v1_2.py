"""Schema v1.2 read/write/migrate helpers for ``experiment_provenance.json``.

The schema bump (PRD-Z) adds an optional ``human_decisions`` array to
the provenance document. ``schema_version`` moves from ``"1.1"`` to
``"1.2"``. The field is optional both in writes and in reads:

* :func:`read` upgrades an on-disk v1.1 record to a v1.2 dict in
  memory — schema_version is rewritten, ``human_decisions`` is set to
  an empty list. Other fields are preserved verbatim.
* :func:`write` always emits ``schema_version == "1.2"``, with
  ``human_decisions`` defaulted to ``[]`` if the caller omits it.
* :func:`migrate_from_v1_1` is the one-shot on-disk migrator: it reads
  a v1.1 file, in-memory upgrades it, and atomically rewrites it as
  v1.2. Idempotent: running it twice is a no-op.

This module is *additive* — it does not replace the existing
``audit_run.py`` writer. The audit script keeps writing ``schema_version
== "1.1"`` until the audit pipeline is updated to v1.2; the
``decision_recorder.append_decision`` write path is what first bumps a
file to v1.2 on disk. ``read`` therefore tolerates either version.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.2"


def read(path: Path) -> dict[str, Any]:
    """Read ``path`` and return a v1.2 dict in memory.

    v1.1 documents are upgraded in memory: ``schema_version`` is set to
    ``"1.2"`` and ``human_decisions`` is defaulted to an empty list.
    The disk file is *not* modified by this function — use
    :func:`migrate_from_v1_1` for that. Missing files yield a minimal
    v1.2 dict.
    """
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "human_decisions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "human_decisions": []}
    if not isinstance(payload, dict):
        return {"schema_version": SCHEMA_VERSION, "human_decisions": []}
    payload["schema_version"] = SCHEMA_VERSION
    if not isinstance(payload.get("human_decisions"), list):
        payload["human_decisions"] = []
    return payload


def write(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as a v1.2 provenance document, atomically.

    The caller's dict is shallow-copied so this function does not
    mutate it. ``schema_version`` is forced to ``"1.2"``; missing
    ``human_decisions`` defaults to ``[]``.
    """
    payload = dict(data)
    payload["schema_version"] = SCHEMA_VERSION
    if not isinstance(payload.get("human_decisions"), list):
        payload["human_decisions"] = []
    _atomic_write_json(path, payload)


def migrate_from_v1_1(path: Path) -> None:
    """Rewrite a v1.1 file as a v1.2 file in place.

    Idempotent: a v1.2 file is left untouched (in terms of content,
    although the rewrite still happens so the mtime is bumped and the
    caller can detect that the migration ran).
    """
    payload = read(path)
    _atomic_write_json(path, payload)


def render_human_decisions_section(decisions: Iterable[Any]) -> str:
    """Render the ``## Human Decisions`` markdown block, or ``""``.

    Empty input yields an empty string so the caller can splice the
    section in unconditionally (`note += render_human_decisions_section(...)`)
    and the section disappears when no human decisions exist on a run.

    The table layout matches the PRD's User Story #14 contract:
    Action | By | At (UTC) | Pattern | Evidence | Note.
    """
    rows: list[list[str]] = []
    for entry in decisions:
        if not isinstance(entry, dict):
            continue
        rows.append(
            [
                _cell(entry.get("action")),
                _cell(entry.get("by")),
                _cell(entry.get("at_utc")),
                _cell(entry.get("pattern")),
                _cell(entry.get("evidence_ref")),
                _cell(entry.get("decision_text")),
            ]
        )
    if not rows:
        return ""
    header = "| Action | By | At (UTC) | Pattern | Evidence | Note |"
    sep = "|---|---|---|---|---|---|"
    body_lines = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(
        [
            "## Human Decisions",
            "",
            "Human-authored decisions recorded for this run. Each row "
            "is a checkpoint where the modeller (not the agent) "
            "approved or denied a course of action.",
            "",
            header,
            sep,
            *body_lines,
            "",
        ]
    )


def _cell(value: Any) -> str:
    """Render a single table cell, escaping pipe characters."""
    if value is None or value == "":
        return "—"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    # Long human notes would otherwise blow up the row; keep tables tidy
    # while preserving the leading context the auditor needs.
    if len(text) > 140:
        text = text[:137] + "..."
    return text


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Mirror of :func:`decision_recorder._atomic_write_json`.

    Duplicated to avoid a circular import between the HITL layer and
    the audit layer — both modules need atomic writes against the same
    file, but the audit-layer copy is the one used by the schema
    migrator. The implementations must stay in sync; both are tested.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
