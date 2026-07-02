"""Per-case promoted gap-fill defaults — the ``gap_defaults.yaml`` store.

One file per case at ``cases/<id>/gap_defaults.yaml``, written only by
the explicit ``aiswmm gap promote-to-case`` verb (Key invariant 4:
memory-like stores mutate via explicit verbs only). The schema is owned
here, next to ``case_registry`` (which owns ``case_meta.yaml``) — it
originally lived inside the CLI verb module as a deliberately
self-contained block, which left the store's schema undiscoverable from
the library layer; the 2026-07 architecture pass moved it to its
natural home.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentic_swmm.utils.paths import repo_root as default_repo_root


SCHEMA_VERSION = 1
CASE_DEFAULTS_FILENAME = "gap_defaults.yaml"


@dataclass(frozen=True)
class CaseDefaultEntry:
    """One promoted gap-fill default.

    The fields mirror the PRD's schema block. ``notes`` is the only
    optional field; the rest are populated by the CLI on every
    promote. All values are plain Python primitives so the YAML round-
    trip stays lossless without custom representers.
    """

    value: Any
    source: str
    promoted_at: str
    promoted_by: str
    notes: str | None = None


@dataclass(frozen=True)
class CaseDefaults:
    """In-memory view over ``cases/<id>/gap_defaults.yaml``.

    The ``entries`` map is field-name → :class:`CaseDefaultEntry`. The
    reader returns an empty :class:`CaseDefaults` when the file is
    missing so callers can treat "no promotions yet" identically to
    "an empty file on disk".
    """

    case_id: str
    schema_version: int = SCHEMA_VERSION
    entries: dict[str, CaseDefaultEntry] = field(default_factory=dict)


def _resolve_repo_root() -> Path:
    """Return the active repo root, honouring ``AISWMM_REPO_ROOT``.

    Tests inject a temporary directory through the env var so the CLI
    subprocess writes its ``cases/`` artefacts under the test fixture
    instead of the real repo. Production callers leave the env var
    unset and the function falls back to the canonical repo root.
    """
    override = os.environ.get("AISWMM_REPO_ROOT")
    if override:
        return Path(override)
    return default_repo_root()


def _case_defaults_path(repo_root: Path, case_id: str) -> Path:
    return repo_root / "cases" / case_id / CASE_DEFAULTS_FILENAME


def read_case_defaults(case_id: str, *, repo_root: Path | None = None) -> CaseDefaults:
    """Read the case-defaults file or return an empty container.

    A missing or unparseable file yields an empty :class:`CaseDefaults`
    with the requested ``case_id``. The reader is forgiving so a half-
    written file (e.g. interrupted promote) does not lock out future
    promotes — the writer will overwrite the file atomically anyway.
    """
    base = repo_root if repo_root is not None else _resolve_repo_root()
    path = _case_defaults_path(base, case_id)
    if not path.is_file():
        return CaseDefaults(case_id=case_id)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return CaseDefaults(case_id=case_id)
    if not isinstance(payload, dict):
        return CaseDefaults(case_id=case_id)
    raw_entries = payload.get("entries") or {}
    entries: dict[str, CaseDefaultEntry] = {}
    if isinstance(raw_entries, dict):
        for name, raw in raw_entries.items():
            if not isinstance(raw, dict):
                continue
            entries[str(name)] = CaseDefaultEntry(
                value=raw.get("value"),
                source=str(raw.get("source") or ""),
                promoted_at=str(raw.get("promoted_at") or ""),
                promoted_by=str(raw.get("promoted_by") or ""),
                notes=(str(raw["notes"]) if raw.get("notes") else None),
            )
    return CaseDefaults(
        case_id=str(payload.get("case_id") or case_id),
        schema_version=int(payload.get("schema_version") or SCHEMA_VERSION),
        entries=entries,
    )


def write_case_defaults(
    case_id: str,
    entries: dict[str, CaseDefaultEntry],
    *,
    repo_root: Path | None = None,
) -> Path:
    """Serialise ``entries`` to ``cases/<id>/gap_defaults.yaml``.

    The writer always emits the canonical key order
    (``schema_version`` → ``case_id`` → ``entries``) so humans diffing
    the file in PRs see a predictable shape. Returns the written path
    so callers can include it in audit messages.
    """
    base = repo_root if repo_root is not None else _resolve_repo_root()
    path = _case_defaults_path(base, case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "entries": {
            name: {
                "value": entry.value,
                "source": entry.source,
                "promoted_at": entry.promoted_at,
                "promoted_by": entry.promoted_by,
                **({"notes": entry.notes} if entry.notes else {}),
            }
            for name, entry in entries.items()
        },
    }
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


__all__ = [
    "CASE_DEFAULTS_FILENAME",
    "SCHEMA_VERSION",
    "CaseDefaultEntry",
    "CaseDefaults",
    "read_case_defaults",
    "write_case_defaults",
]
