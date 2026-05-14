"""Convert an audit artefact to a memory-corpus entry (PRD M5).

Supports both ``experiment_note.md`` (SWMM run record) and
``chat_note.md`` (chat session). Chat notes derive ``failure_patterns``
from frontmatter ``tags`` of the form ``planner-stuck`` (converted to
``planner_stuck``); tags that look like neutral session markers
(``agentic-swmm``, ``chat-session``) are ignored.

Also exposes :func:`assert_uniform_schema`, the lightweight refusal
hook used by both the summariser and the RAG retriever to refuse
mixing entries that disagree on ``schema_version``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", flags=re.DOTALL)
_NEUTRAL_TAGS = {"agentic-swmm", "chat-session", "experiment-note"}


def _parse_frontmatter(text: str) -> dict[str, Any]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    block = match.group(1)
    out: dict[str, Any] = {}
    current_list_key: str | None = None
    current_list: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if current_list_key and line.lstrip().startswith("- "):
            current_list.append(line.lstrip()[2:].strip().strip('"'))
            continue
        # End of any pending list.
        if current_list_key is not None:
            out[current_list_key] = current_list
            current_list_key = None
            current_list = []
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            # block-style list incoming.
            current_list_key = key
            current_list = []
            continue
        out[key] = value.strip().strip('"')
    if current_list_key is not None:
        out[current_list_key] = current_list
    return out


def _read_provenance_for(note_path: Path) -> dict[str, Any]:
    audit_dir = note_path.parent
    candidate = audit_dir / "experiment_provenance.json"
    if not candidate.is_file():
        return {}
    try:
        parsed = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _failure_patterns_from_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        normalized = tag.strip()
        if not normalized or normalized in _NEUTRAL_TAGS:
            continue
        # Convert kebab-case to snake_case so failure_patterns are uniform.
        out.append(normalized.replace("-", "_"))
    return out


def extract_memory_entry(audit_artifact: Path) -> dict[str, Any]:
    """Return a memory-corpus entry for either kind of audit artefact.

    For ``experiment_note.md``: ``source_type = run_record``, fields
    populated from the sibling ``experiment_provenance.json``.

    For ``chat_note.md``: ``source_type = chat``, ``failure_patterns``
    derived from frontmatter ``tags``.
    """
    if not audit_artifact.is_file():
        raise FileNotFoundError(f"audit artifact not found: {audit_artifact}")
    text = audit_artifact.read_text(encoding="utf-8")
    frontmatter = _parse_frontmatter(text)
    note_type = frontmatter.get("type") or ""

    if audit_artifact.name == "chat_note.md" or note_type == "chat-session":
        case_name = frontmatter.get("case") or audit_artifact.parent.name
        schema_version = str(frontmatter.get("schema_version") or "1.1")
        tags = frontmatter.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return {
            "source_type": "chat",
            "source_path": str(audit_artifact),
            "case_name": case_name,
            "run_id": frontmatter.get("case") or audit_artifact.parent.name,
            "schema_version": schema_version,
            "status": frontmatter.get("status"),
            "failure_patterns": _failure_patterns_from_tags(list(tags)),
            "text": text,
        }

    # SWMM run record.
    provenance = _read_provenance_for(audit_artifact)
    schema_version = str(
        provenance.get("schema_version")
        or frontmatter.get("schema_version")
        or "1.1"
    )
    case_name = (
        provenance.get("case_name")
        or provenance.get("case_id")
        or frontmatter.get("case")
        or audit_artifact.parent.parent.name
    )
    failure_patterns = list(provenance.get("failure_patterns") or [])
    return {
        "source_type": "run_record",
        "source_path": str(audit_artifact),
        "case_name": str(case_name),
        "run_id": provenance.get("run_id") or audit_artifact.parent.parent.name,
        "schema_version": schema_version,
        "status": provenance.get("status") or frontmatter.get("status"),
        "failure_patterns": failure_patterns,
        "text": text,
    }


def assert_uniform_schema(entries: list[dict[str, Any]]) -> str:
    """Return the unique ``schema_version`` across ``entries`` or raise.

    Used by both :mod:`recall_search` and the summariser to refuse
    mixing entries from different schema versions.
    """
    versions = {str(entry.get("schema_version")) for entry in entries if entry.get("schema_version")}
    if not versions:
        return ""
    if len(versions) > 1:
        raise RuntimeError(
            f"mixed schema_version values detected: {sorted(versions)!r}. "
            "Run `aiswmm audit ... --rebuild` to refresh the corpus."
        )
    return next(iter(versions))
