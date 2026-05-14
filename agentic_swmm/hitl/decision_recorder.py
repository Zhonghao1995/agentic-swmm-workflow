"""Atomic recorder for ``human_decisions`` provenance entries (PRD-Z).

Every expert decision — an interactive Y/N reply at the
``request_expert_review`` prompt, or one of the four CLI subcommands —
appends a :class:`HumanDecision` to the run's
``experiment_provenance.json``. The PRD bumps the schema 1.1 → 1.2 and
makes ``human_decisions`` an optional array. This module is the single
write seam for that array.

The append is atomic: we read the JSON, mutate the in-memory dict,
write the new payload to a temp file in the same directory, fsync it,
and then ``os.replace`` it onto the canonical path. If the rename fails
the original file is left intact and the tmp file is removed so the
caller can retry. The audit pipeline depends on
``experiment_provenance.json`` being a valid JSON document at all
times; this contract protects that invariant against an interrupted
write.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.2"


@dataclass(frozen=True)
class HumanDecision:
    """One human-authored provenance record.

    Fields mirror the PRD's contract: ``id`` (caller-generated when
    omitted), ``action`` (free-form, e.g. ``"expert_review_approved"``),
    ``by`` (``$USER``), ``at_utc`` (ISO-8601 UTC string), ``pattern``
    (HITL threshold name, optional for CLI commands that aren't
    threshold-driven), ``evidence_ref`` (a relative path inside the run
    directory or under ``runs/``), and an optional free-text
    ``decision_text`` for human notes.
    """

    id: str
    action: str
    by: str
    at_utc: str
    pattern: str | None = None
    evidence_ref: str | None = None
    decision_text: str | None = None


def now_utc_iso() -> str:
    """Return an ISO-8601 UTC timestamp with second resolution."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_decision_id() -> str:
    """Return a short, sortable-ish decision ID.

    The format is ``hd-<utc-stamp>-<rand>``: the UTC stamp keeps a
    natural lexical order across same-day appends; the random tail
    makes IDs unique even if two decisions land in the same second.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"hd-{stamp}-{uuid.uuid4().hex[:8]}"


def make_decision(
    *,
    action: str,
    by: str | None = None,
    pattern: str | None = None,
    evidence_ref: str | None = None,
    decision_text: str | None = None,
) -> HumanDecision:
    """Construct a :class:`HumanDecision` with sensible defaults.

    Defaults to ``$USER`` if ``by`` is omitted, and stamps the current
    UTC time. Decision IDs are generated. The function is purely
    factory — no I/O — so callers can build a record, log it, and only
    then commit it via :func:`append_decision`.
    """
    actor = by if by is not None else os.environ.get("USER", "unknown")
    return HumanDecision(
        id=new_decision_id(),
        action=action,
        by=actor,
        at_utc=now_utc_iso(),
        pattern=pattern,
        evidence_ref=evidence_ref,
        decision_text=decision_text,
    )


def _read_provenance(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "human_decisions": []}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "human_decisions": []}
    if not isinstance(parsed, dict):
        return {"schema_version": SCHEMA_VERSION, "human_decisions": []}
    return parsed


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` to ``path`` via tmp-file + ``os.replace``.

    On any failure during the rename step the temp file is unlinked so
    the file system is left clean for the caller's retry. Errors are
    re-raised so the caller can decide whether to surface them.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``tempfile.NamedTemporaryFile`` with ``delete=False`` gives us a
    # writable temp inside the same directory (required for an atomic
    # rename on POSIX) without inheriting the destination's perms.
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
        # Best-effort cleanup: a leftover .tmp would clutter the audit
        # dir and confuse later atomic writes that scan for stale
        # temp files. Suppress unlink errors — the original exception
        # is what the caller cares about.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def append_decision(provenance_path: Path, decision: HumanDecision) -> None:
    """Atomically append ``decision`` to ``provenance_path``.

    If the file is a v1.1 provenance (no ``human_decisions`` key) the
    field is materialised and the schema version is bumped to 1.2.
    Reading still works for either version — see :func:`read_decisions`.
    """
    payload = _read_provenance(provenance_path)
    payload["schema_version"] = SCHEMA_VERSION
    decisions = payload.get("human_decisions")
    if not isinstance(decisions, list):
        decisions = []
    decisions.append(asdict(decision))
    payload["human_decisions"] = decisions
    _atomic_write_json(provenance_path, payload)


def read_decisions(provenance_path: Path) -> list[HumanDecision]:
    """Return the recorded :class:`HumanDecision` list.

    Missing files and v1.1 provenance (no ``human_decisions``) yield an
    empty list — the field is optional by design so older runs do not
    appear "corrupt" after the schema bump.
    """
    payload = _read_provenance(provenance_path)
    raw = payload.get("human_decisions")
    if not isinstance(raw, list):
        return []
    out: list[HumanDecision] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        out.append(
            HumanDecision(
                id=str(entry.get("id", "")),
                action=str(entry.get("action", "")),
                by=str(entry.get("by", "")),
                at_utc=str(entry.get("at_utc", "")),
                pattern=entry.get("pattern"),
                evidence_ref=entry.get("evidence_ref"),
                decision_text=entry.get("decision_text"),
            )
        )
    return out
