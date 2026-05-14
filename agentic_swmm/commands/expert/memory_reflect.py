"""``aiswmm memory reflect [--apply]`` — expert-only reflection CLI (ME-3).

Phase ME-3 of the memory-evolution-with-forgetting PRD layers an
LLM-driven *active* reflection pass on top of the lifecycle metadata
(ME-1) and decay-based forgetting (ME-2):

* Without ``--apply`` the CLI is read-only-plus-proposal-write. It
  ingests the last N audit notes + the active/dormant lessons, asks an
  LLM proposer for a structured diff (merge / refine / retire /
  promote), and writes the diff to ``<audit_dir>/memory_reflection_proposal.md``
  in Obsidian-readable markdown. The modeller is expected to open the
  file in Obsidian, review, and re-run with ``--apply``.
* With ``--apply`` the CLI walks each proposed change interactively;
  ``y`` at stdin applies the change to ``lessons_learned.md`` and
  appends a ``human_decisions`` record (PRD-Z schema v1.2) with
  ``action="memory_reflect_apply"``. ``n`` is a non-event: nothing is
  written, nothing is recorded.

**Not** a ToolSpec, **not** an MCP tool. The agent may read this file
to learn the command exists; the agent itself has no path to invoke
it. PRD memory-evolution-with-forgetting governance pattern, mirroring
``aiswmm calibration accept``.

The LLM call is overridable via ``AISWMM_MEMORY_REFLECT_STUB_JSON``
(a JSON payload with a ``changes`` list). Tests use the stub to pin
the proposal structure without depending on a live LLM. Production
callers should leave the env var unset; future work will wire in a
real LLM client behind the same seam.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.commands.expert._shared import (
    record_and_print,
)
from agentic_swmm.memory.lessons_metadata import (
    _iter_pattern_spans,
    read_metadata,
)


# ---------------------------------------------------------------------------
# Public surface

DEFAULT_AUDIT_NOTES_LIMIT = 10
PROPOSAL_FILENAME = "memory_reflection_proposal.md"
ALLOWED_CHANGE_TYPES = ("merge", "refine", "retire", "promote")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``memory reflect`` under the ``memory`` subparser.

    Wiring lives in :mod:`agentic_swmm.commands.memory` because
    ``aiswmm memory`` is one top-level command with multiple
    sub-actions. We re-export :func:`add_subparser` so the parent
    module can call us without importing argparse machinery itself.
    """
    add_subparser(subparsers)


def add_subparser(
    memory_subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``reflect`` action on the given memory subparsers."""
    parser = memory_subparsers.add_parser(
        "reflect",
        help=(
            "Expert-only: LLM-driven reflection over recent audit notes "
            "+ current lessons. Writes a proposal; ``--apply`` applies "
            "the modeller-ratified changes."
        ),
        description=(
            "Read the last N audit notes and the active/dormant lessons, "
            "ask an LLM for a structured diff, and write the proposal to "
            "09_audit/memory_reflection_proposal.md. With --apply, walk "
            "each change Y/N at stdin and record human_decisions for "
            "ratified changes."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Walk each proposed change interactively. Approved changes "
            "are applied to lessons_learned.md and recorded as "
            "human_decisions entries (action=memory_reflect_apply)."
        ),
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=None,
        help=(
            "Directory to write memory_reflection_proposal.md into. "
            "Defaults to runs/<latest>/09_audit. Override is required "
            "for tests and CI."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_AUDIT_NOTES_LIMIT,
        help=(
            "Max audit notes to include in the LLM prompt context. "
            f"Defaults to {DEFAULT_AUDIT_NOTES_LIMIT}."
        ),
    )
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    """Entry point invoked by ``aiswmm memory reflect``.

    The two paths (proposal-only vs ``--apply``) share the same
    proposal build step so the on-disk artefact is identical in both
    modes — ``--apply`` simply adds the ratification loop on top.
    """
    memory_dir = _resolve_memory_dir()
    runs_dir = _resolve_runs_dir()
    audit_dir = _resolve_audit_dir(args.audit_dir, runs_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)

    lessons_path = memory_dir / "lessons_learned.md"
    if not lessons_path.is_file():
        print(
            f"error: lessons_learned.md not found at {lessons_path}.",
            file=sys.stderr,
        )
        return 2

    audit_notes = _collect_audit_notes(runs_dir, args.limit)
    relevant_patterns = _collect_active_and_dormant_patterns(lessons_path)
    proposal = _request_llm_proposal(audit_notes, relevant_patterns)

    proposal_path = audit_dir / PROPOSAL_FILENAME
    proposal_text = _render_proposal_markdown(
        proposal=proposal,
        audit_notes=audit_notes,
        patterns=relevant_patterns,
    )
    proposal_path.write_text(proposal_text, encoding="utf-8")

    if not args.apply:
        print(
            f"Wrote {proposal_path}. Review in Obsidian then re-run "
            f"with --apply."
        )
        return 0

    # ``--apply``: walk the changes interactively.
    provenance_path = audit_dir / "experiment_provenance.json"
    proposal_sha = _sha256(proposal_path)
    evidence_ref = f"{audit_dir.name}/{PROPOSAL_FILENAME}#{proposal_sha}"
    applied = 0
    rejected = 0
    for change in proposal.get("changes") or []:
        change_type = str(change.get("change_type") or "").strip()
        pattern = str(change.get("pattern") or "").strip()
        if change_type not in ALLOWED_CHANGE_TYPES or not pattern:
            # Skip malformed proposals silently rather than crash —
            # the modeller can fix the file and re-run if needed.
            continue
        if not _confirm_change(change_type, pattern, change):
            rejected += 1
            continue
        _apply_change_to_lessons(lessons_path, change)
        decision_text = _decision_text(change)
        record_and_print(
            provenance_path,
            action="memory_reflect_apply",
            evidence_ref=evidence_ref,
            decision_text=decision_text,
            pattern=pattern,
        )
        applied += 1

    print(
        f"memory reflect --apply: applied={applied}, rejected={rejected}, "
        f"proposal={proposal_path}"
    )
    return 0


# ---------------------------------------------------------------------------
# Helpers — path resolution


def _resolve_memory_dir() -> Path:
    """Return ``memory/modeling-memory`` honouring ``AISWMM_MEMORY_DIR``."""
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override).expanduser().resolve()
    from agentic_swmm.utils.paths import repo_root

    return repo_root() / "memory" / "modeling-memory"


def _resolve_runs_dir() -> Path:
    """Return the runs directory honouring ``AISWMM_RUNS_ROOT``."""
    override = os.environ.get("AISWMM_RUNS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    from agentic_swmm.utils.paths import repo_root

    return repo_root() / "runs"


def _resolve_audit_dir(explicit: Path | None, runs_dir: Path) -> Path:
    """Return the audit directory the proposal lands in.

    When the caller passes ``--audit-dir`` we use it as-is. Otherwise
    we pick the most recently-modified ``09_audit`` under ``runs/`` so
    the proposal lands beside the modeller's most recent work. If no
    such directory exists yet, fall back to ``runs/_reflection/09_audit``
    so the proposal still has a stable home.
    """
    if explicit is not None:
        return explicit.expanduser().resolve()
    candidates = sorted(
        runs_dir.glob("*/09_audit"),
        key=lambda p: p.stat().st_mtime if p.is_dir() else 0,
        reverse=True,
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return (runs_dir / "_reflection" / "09_audit").resolve()


# ---------------------------------------------------------------------------
# Helpers — audit notes


def _collect_audit_notes(runs_dir: Path, limit: int) -> list[dict[str, Any]]:
    """Return the ``limit`` most recent audit notes as ``{path, text}`` dicts.

    Sorted by mtime descending. Each note is read with
    ``errors="ignore"`` so an oddly-encoded file in the runs tree
    cannot crash the reflection pass.
    """
    if not runs_dir.is_dir():
        return []
    notes = sorted(
        runs_dir.glob("*/09_audit/experiment_note.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    for note in notes[: max(0, int(limit))]:
        try:
            text = note.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        selected.append({"path": str(note), "text": text})
    return selected


# ---------------------------------------------------------------------------
# Helpers — lessons file


def _collect_active_and_dormant_patterns(
    lessons_path: Path,
) -> list[dict[str, Any]]:
    """Return patterns with status active|dormant; retired are skipped.

    Each entry is a small dict with the pattern name, the parsed
    metadata, and the block body. The LLM prompt uses the metadata
    summary; we keep the block body for the proposal renderer's
    Obsidian-readable preview.
    """
    text = lessons_path.read_text(encoding="utf-8")
    out: list[dict[str, Any]] = []
    for name, start, end in _iter_pattern_spans(text):
        block = text[start:end]
        meta = read_metadata(block) or {}
        status = str(meta.get("status") or "active")
        if status == "retired":
            continue
        out.append({"name": name, "metadata": meta, "block": block})
    return out


def _apply_change_to_lessons(lessons_path: Path, change: dict[str, Any]) -> None:
    """Apply one ratified change to ``lessons_learned.md``.

    The implementation is deliberately conservative: rather than
    rewrite metadata fences (which would risk corrupting the YAML
    payload), we append a short Markdown note inside the affected
    pattern block recording what the modeller ratified. The
    decay/forgetting pipeline (ME-2) remains the authoritative
    statemachine for the lifecycle fields; reflection is a paper
    trail of human-ratified intent.

    Layout: the note is inserted directly before the next ``## ``
    heading (or end-of-file for the last block) so subsequent
    metadata reads continue to find the fence at the top of the
    section.
    """
    pattern = str(change.get("pattern") or "").strip()
    change_type = str(change.get("change_type") or "").strip()
    if not pattern or change_type not in ALLOWED_CHANGE_TYPES:
        return
    text = lessons_path.read_text(encoding="utf-8")
    found_start: int | None = None
    found_end: int | None = None
    for name, start, end in _iter_pattern_spans(text):
        if name == pattern:
            found_start, found_end = start, end
            break
    note = _format_reflection_note(change)
    if found_start is None or found_end is None:
        # The pattern named by the LLM doesn't exist in the file —
        # append a new placeholder section so the modeller's
        # ratification still lands somewhere a human can find.
        new_block = f"\n## {pattern}\n\n{note}\n"
        if not text.endswith("\n"):
            text += "\n"
        lessons_path.write_text(text + new_block, encoding="utf-8")
        return
    block = text[found_start:found_end]
    # Splice the note before any trailing blank-line tail so the next
    # section heading is not pushed down.
    stripped = block.rstrip("\n")
    new_block = stripped + "\n\n" + note + "\n\n"
    lessons_path.write_text(
        text[:found_start] + new_block + text[found_end:], encoding="utf-8"
    )


def _format_reflection_note(change: dict[str, Any]) -> str:
    """Render one ratified change as a small Obsidian-readable block."""
    change_type = str(change.get("change_type") or "").strip()
    summary = str(change.get("summary") or "").strip()
    merge_with = str(change.get("merge_with") or "").strip()
    stamp = _now_utc_iso()
    parts = [
        f"> **memory_reflect_apply** ({change_type}) — ratified {stamp}",
    ]
    if merge_with:
        parts.append(f"> merge_with: `{merge_with}`")
    if summary:
        parts.append(f"> {summary}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Helpers — LLM stub seam


def _request_llm_proposal(
    audit_notes: list[dict[str, Any]],
    patterns: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the LLM proposal payload.

    Production callers will replace this with a real LLM client; for
    now the stub seam (``AISWMM_MEMORY_REFLECT_STUB_JSON``) is the
    contract tests pin against. When the env var is unset and no LLM
    client is configured, the helper returns an empty proposal so the
    CLI degrades gracefully — it writes a proposal file noting that no
    changes were suggested.
    """
    stub = os.environ.get("AISWMM_MEMORY_REFLECT_STUB_JSON")
    if stub:
        try:
            parsed = json.loads(stub)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            return parsed
        return {}
    # No LLM client wired yet — return an empty proposal. The Obsidian
    # file still gets written, but with no changes to ratify.
    _ = (audit_notes, patterns)
    return {"changes": []}


# ---------------------------------------------------------------------------
# Helpers — markdown renderer


def _render_proposal_markdown(
    *,
    proposal: dict[str, Any],
    audit_notes: list[dict[str, Any]],
    patterns: list[dict[str, Any]],
) -> str:
    """Render the proposal payload as Obsidian-readable markdown.

    The renderer is intentionally simple — one section per change
    plus a short context recap so the modeller can review without
    cross-referencing the audit dir manually.
    """
    lines: list[str] = []
    lines.append("# Memory Reflection Proposal")
    lines.append("")
    lines.append(f"Generated at UTC: `{_now_utc_iso()}`")
    lines.append("")
    lines.append(
        "This proposal is produced by `aiswmm memory reflect` "
        "(expert-only). Run with `--apply` to ratify changes; each "
        "ratification is recorded as a `human_decisions` row "
        "(action=`memory_reflect_apply`)."
    )
    lines.append("")

    changes = proposal.get("changes") or []
    lines.append("## Proposed Changes")
    lines.append("")
    if not changes:
        lines.append("- _No changes proposed._")
        lines.append("")
    for index, change in enumerate(changes, start=1):
        change_type = str(change.get("change_type") or "").strip() or "unknown"
        pattern = str(change.get("pattern") or "").strip() or "unknown"
        summary = str(change.get("summary") or "").strip()
        merge_with = str(change.get("merge_with") or "").strip()
        lines.append(f"### {index}. {change_type} — `{pattern}`")
        lines.append("")
        if merge_with:
            lines.append(f"- **merge_with**: `{merge_with}`")
        if summary:
            lines.append(f"- **summary**: {summary}")
        if not summary and not merge_with:
            lines.append("- _LLM provided no summary for this change._")
        lines.append("")

    lines.append("## Context — Recent Audit Notes")
    lines.append("")
    if not audit_notes:
        lines.append("- _No audit notes found under runs/._")
    else:
        for note in audit_notes:
            lines.append(f"- `{note['path']}`")
    lines.append("")

    lines.append("## Context — Active / Dormant Patterns")
    lines.append("")
    if not patterns:
        lines.append("- _No active or dormant patterns in lessons file._")
    else:
        for entry in patterns:
            meta = entry.get("metadata") or {}
            status = meta.get("status", "?")
            score = meta.get("confidence_score", "?")
            lines.append(
                f"- `{entry['name']}` — status={status}, confidence={score}"
            )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers — interactive prompt


def _confirm_change(
    change_type: str, pattern: str, change: dict[str, Any]
) -> bool:
    """Print the change and read Y/N from stdin.

    Mirrors the spirit of :func:`permissions.prompt_user` but with a
    crucial difference: when stdin is *not* a TTY, this helper reads
    one line from stdin instead of auto-approving. Reflection mutates
    the lessons file, so a non-TTY pipe must produce an explicit
    answer rather than a silent yes. ``AISWMM_AUTO_APPROVE=1``
    short-circuits to ``True`` for CI consistency with the other
    expert commands.
    """
    if os.environ.get("AISWMM_AUTO_APPROVE") == "1":
        return True
    summary = str(change.get("summary") or "").strip()
    merge_with = str(change.get("merge_with") or "").strip()
    print("")
    print("=" * 72)
    print(f"  memory reflect: {change_type} — pattern={pattern!r}")
    if merge_with:
        print(f"  merge_with    : {merge_with}")
    if summary:
        print(f"  summary       : {summary}")
    print("-" * 72)
    prompt = f"Apply this change? [y/N] "
    try:
        if sys.stdin.isatty():
            answer = input(prompt).strip().lower()
        else:
            # Non-TTY: still read explicitly. Echo the prompt so the
            # subprocess transcript shows what was asked.
            print(prompt, end="", flush=True)
            line = sys.stdin.readline()
            answer = line.strip().lower()
    except EOFError:
        answer = ""
    return answer in {"y", "yes"}


# ---------------------------------------------------------------------------
# Helpers — small utilities


def _sha256(path: Path) -> str:
    """Return a hex SHA-256 digest of ``path``'s bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_utc_iso() -> str:
    """ISO-8601 UTC stamp with second precision."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _decision_text(change: dict[str, Any]) -> str:
    """Render the human_decisions ``decision_text`` for one change."""
    parts: list[str] = []
    change_type = str(change.get("change_type") or "").strip()
    if change_type:
        parts.append(f"change_type={change_type}")
    merge_with = str(change.get("merge_with") or "").strip()
    if merge_with:
        parts.append(f"merge_with={merge_with}")
    summary = str(change.get("summary") or "").strip()
    if summary:
        # Keep the table cell tidy — the audit_note renderer
        # truncates at 140 anyway.
        parts.append(summary)
    return "; ".join(parts)
