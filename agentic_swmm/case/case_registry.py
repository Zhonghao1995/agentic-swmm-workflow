"""Readonly facade + writer for ``cases/<id>/case_meta.yaml``.

This module is intentionally minimal. The PRD only commits the
namespace (the directory and the metadata schema); downstream PRDs
fill ``cases/<id>/`` with feature-specific artefacts (canonical INPs,
gap_defaults.yaml, lessons.md). Those PRDs will add their own
helpers; this one stays small enough to read on one screen.

The schema mirrors the PRD's ``Schema (cases/<case_id>/case_meta.yaml)``
table. Optional fields default to ``None`` (catchment area), ``None``
(land use), or the empty string (notes). Reading is forgiving — unknown
keys in the YAML are preserved on the dataclass via the ``extra``
field so an older client never strips data written by a newer one.

The writer always emits ``schema_version: 1`` and uses ``yaml.safe_dump``
with ``sort_keys=False`` so the canonical key order matches the PRD's
example block (humans diff these files in PRs, so key order matters).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agentic_swmm.case.case_id import validate_case_id
from agentic_swmm.utils.paths import repo_root as _default_repo_root


SCHEMA_VERSION = 1
CASES_DIRNAME = "cases"
CASE_META_FILENAME = "case_meta.yaml"


class CaseMetaError(RuntimeError):
    """Base class for case-metadata errors."""


class CaseMetaNotFoundError(CaseMetaError):
    """Raised when ``cases/<id>/case_meta.yaml`` does not exist."""


class CaseMetaInvalidError(CaseMetaError):
    """Raised when the YAML on disk fails the schema invariants."""


@dataclass(frozen=True)
class CaseMeta:
    """Structured view over ``case_meta.yaml``.

    The dataclass exposes the fields the PRD's User Stories
    explicitly reference (``display_name``, ``study_purpose``,
    ``catchment``, ``inputs``, ``notes``). Anything else that may
    have been written by a newer client lives under ``extra`` so the
    round-trip is non-lossy.
    """

    case_id: str
    display_name: str
    study_purpose: str
    created_utc: str
    catchment: dict[str, Any]
    inputs: dict[str, Any]
    notes: str
    extra: dict[str, Any] = field(default_factory=dict)


def repo_root() -> Path:
    """Hook seam — tests monkey-patch this to point at a tmp dir.

    Wrapping :func:`agentic_swmm.utils.paths.repo_root` here gives
    tests a single attribute to override without having to touch
    every call site in this module.
    """
    return _default_repo_root()


def _cases_dir(repo: Path) -> Path:
    return repo / CASES_DIRNAME


def _case_dir(repo: Path, case_id: str) -> Path:
    # Validation here is belt-and-braces: every public entry point
    # validates too, but a defensive check inside the path-builder
    # guarantees we never construct ``cases/../etc`` even if a future
    # refactor forgets to validate upstream.
    validate_case_id(case_id)
    return _cases_dir(repo) / case_id


def _meta_path(repo: Path, case_id: str) -> Path:
    return _case_dir(repo, case_id) / CASE_META_FILENAME


def _parse_meta(case_id: str, raw: dict[str, Any]) -> CaseMeta:
    """Decode a YAML dict into a :class:`CaseMeta`.

    Missing optional fields default sensibly; an unexpected
    ``case_id`` mismatch (the YAML says one slug but the directory
    name says another) is a hard error because the rest of the system
    keys off the directory name.
    """
    declared = raw.get("case_id")
    if isinstance(declared, str) and declared != case_id:
        raise CaseMetaInvalidError(
            f"case_meta.yaml for {case_id!r} declares case_id={declared!r}; "
            "the directory name and the file must agree."
        )

    catchment = raw.get("catchment") or {}
    inputs = raw.get("inputs") or {}
    if not isinstance(catchment, dict):
        catchment = {}
    if not isinstance(inputs, dict):
        inputs = {}

    known_keys = {
        "schema_version",
        "case_id",
        "display_name",
        "study_purpose",
        "created_utc",
        "catchment",
        "inputs",
        "notes",
    }
    extra = {k: v for k, v in raw.items() if k not in known_keys}

    return CaseMeta(
        case_id=case_id,
        display_name=str(raw.get("display_name") or ""),
        study_purpose=str(raw.get("study_purpose") or ""),
        created_utc=str(raw.get("created_utc") or ""),
        catchment=dict(catchment),
        inputs=dict(inputs),
        notes=str(raw.get("notes") or ""),
        extra=extra,
    )


def list_cases(repo: Path | None = None) -> list[CaseMeta]:
    """Return every case under ``<repo>/cases/`` that has a meta file.

    A directory under ``cases/`` without ``case_meta.yaml`` is
    skipped silently — it might be in the middle of being created or
    might be a stash of artefacts under a partial case. The PRD's
    promise is that ``aiswmm list cases`` shows fully-formed cases;
    half-formed ones do not appear.

    Returns the list sorted by ``case_id`` so the output is stable
    across filesystems with different iteration orders.
    """
    base = repo if repo is not None else repo_root()
    cases = _cases_dir(base)
    if not cases.is_dir():
        return []
    out: list[CaseMeta] = []
    for child in sorted(cases.iterdir()):
        if not child.is_dir():
            continue
        meta_path = child / CASE_META_FILENAME
        if not meta_path.is_file():
            continue
        try:
            payload = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            # Malformed files are skipped rather than crashing the
            # listing — the user sees a partial list, which is more
            # useful than a hard failure at the shell prompt.
            continue
        if not isinstance(payload, dict):
            continue
        try:
            out.append(_parse_meta(child.name, payload))
        except CaseMetaInvalidError:
            continue
    return out


def read_case_meta(case_id: str, *, repo_root: Path | None = None) -> CaseMeta:
    """Read the metadata for ``case_id``.

    Raises :class:`CaseMetaNotFoundError` when the directory or the
    YAML file is missing. Validation of the slug happens before the
    filesystem call so an attacker-crafted ``case_id`` cannot
    escape the ``cases/`` namespace.
    """
    base = repo_root if repo_root is not None else _default_repo_root()
    path = _meta_path(base, case_id)
    if not path.is_file():
        raise CaseMetaNotFoundError(
            f"no case_meta.yaml for {case_id!r} at {path}"
        )
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise CaseMetaInvalidError(
            f"case_meta.yaml for {case_id!r} could not be parsed: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CaseMetaInvalidError(
            f"case_meta.yaml for {case_id!r} is not a mapping"
        )
    return _parse_meta(case_id, payload)


def write_case_meta(meta: CaseMeta, *, repo_root: Path | None = None) -> Path:
    """Serialise ``meta`` to ``cases/<id>/case_meta.yaml`` and return the path.

    The function refuses to overwrite an existing file: ``aiswmm
    case init`` is a one-shot bootstrap, not an edit command. If the
    user wants to edit the metadata they can do so by hand (the file
    is plain YAML).
    """
    base = repo_root if repo_root is not None else _default_repo_root()
    case_dir = _case_dir(base, meta.case_id)
    case_dir.mkdir(parents=True, exist_ok=True)
    path = case_dir / CASE_META_FILENAME
    if path.exists():
        raise CaseMetaError(
            f"case_meta.yaml for {meta.case_id!r} already exists at {path}; "
            "edit it by hand or delete the file before re-initialising."
        )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "case_id": meta.case_id,
        "display_name": meta.display_name,
        "study_purpose": meta.study_purpose,
        "created_utc": meta.created_utc or _now_utc(),
        "catchment": meta.catchment,
        "inputs": meta.inputs,
        "notes": meta.notes,
    }
    # Preserve any extra keys the caller carried forward (round-trip
    # safety for forward-compatibility with a future schema bump).
    for key, value in meta.extra.items():
        payload.setdefault(key, value)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _now_utc() -> str:
    """ISO-8601 UTC timestamp, second resolution.

    Matches the format used elsewhere in the codebase (see
    ``decision_recorder.now_utc_iso``) so audit consumers can diff
    timestamps across modules without parsing variants.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "CASES_DIRNAME",
    "CASE_META_FILENAME",
    "SCHEMA_VERSION",
    "CaseMeta",
    "CaseMetaError",
    "CaseMetaInvalidError",
    "CaseMetaNotFoundError",
    "list_cases",
    "read_case_meta",
    "repo_root",
    "write_case_meta",
]
