"""Generate ``runs/INDEX.md`` — the Obsidian MOC over every audited run
and chat session.

Pure with respect to side effects: the function returns a Markdown string
and the caller writes it to ``runs/INDEX.md``.

PRD: ``.claude/prds/PRD_audit.md`` ("Module: MOC generator").
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.audit.run_folder_layout import RunFolder, RunKind, discover


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Minimal YAML frontmatter parser.

    Supports flat ``key: value`` pairs plus a ``tags:`` list with
    ``- value`` items. Quoted scalars are unquoted; other values are
    returned as plain strings. This is enough for the audit/chat notes
    this MOC consumes; pulling in PyYAML would be overkill.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    out: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw in match.group(1).splitlines():
        line = raw.rstrip()
        if not line.strip():
            current_list_key = None
            continue
        if line.startswith("  - ") and current_list_key:
            out.setdefault(current_list_key, []).append(line[4:].strip().strip('"\''))
            continue
        if ":" not in line:
            current_list_key = None
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            # Could be the start of a list.
            current_list_key = key
            out.setdefault(key, [])
            continue
        current_list_key = None
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        out[key] = value
    return out


def _read_note_frontmatter(note_path: Path) -> dict[str, Any]:
    if not note_path.exists():
        return {}
    try:
        text = note_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return _parse_frontmatter(text)


def _note_for(run: RunFolder) -> Path:
    if run.kind is RunKind.SWMM:
        return run.path / "09_audit" / "experiment_note.md"
    if run.kind is RunKind.CHAT:
        return run.path / "chat_note.md"
    return run.path


def _bucket_for(run: RunFolder, runs_root: Path) -> str:
    rel = run.path.relative_to(runs_root)
    parts = rel.parts
    if not parts:
        return "(root)"
    return parts[0] if len(parts) > 1 else "(top-level)"


def _wikilink_target(run: RunFolder, runs_root: Path) -> str:
    rel = run.path.relative_to(runs_root)
    if run.kind is RunKind.SWMM:
        return (rel / "09_audit" / "experiment_note").as_posix()
    if run.kind is RunKind.CHAT:
        return (rel / "chat_note").as_posix()
    return rel.as_posix()


def _row_for(run: RunFolder, runs_root: Path) -> dict[str, Any]:
    note = _note_for(run)
    fm = _read_note_frontmatter(note) if note.exists() else {}
    rel = run.path.relative_to(runs_root).as_posix()
    note_type = fm.get("type") or ("experiment-audit" if run.kind is RunKind.SWMM else "chat-session")
    date = fm.get("date") or ""
    status = fm.get("status") or "unknown"
    case = fm.get("case") or rel.split("/")[-1]
    return {
        "kind": run.kind.value,
        "type": note_type,
        "case": case,
        "status": status,
        "date": str(date),
        "rel": rel,
        "bucket": _bucket_for(run, runs_root),
        "wikilink": _wikilink_target(run, runs_root),
        "audited": note.exists(),
    }


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def generate_moc(runs_root: Path) -> str:
    """Return Markdown content for ``runs/INDEX.md``.

    BFS-walks ``runs_root`` via ``run_folder_layout.discover`` so nested
    case dirs are picked up. Emits two tables (``By date``, ``By bucket``)
    and an ``Unaudited run dirs`` section for SWMM dirs without
    ``09_audit/experiment_note.md``.
    """
    runs_root = Path(runs_root)
    discovered = list(discover(runs_root))
    audited: list[dict[str, Any]] = []
    unaudited: list[dict[str, Any]] = []
    for run in discovered:
        row = _row_for(run, runs_root)
        if row["audited"]:
            audited.append(row)
        else:
            unaudited.append(row)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines: list[str] = []
    lines.extend(
        [
            "---",
            "type: runs-index",
            "project: Agentic SWMM",
            f"generated_at_utc: {now}",
            f"audited_count: {len(audited)}",
            f"unaudited_count: {len(unaudited)}",
            "tags:",
            "  - agentic-swmm",
            "  - runs-index",
            "---",
            "",
            "# Runs Index",
            "",
            "Auto-generated by `agentic_swmm.audit.moc_generator`. ",
            "Do not edit by hand — re-run `aiswmm audit ...` or the audit migration script to refresh.",
            "",
        ]
    )

    by_date_rows = sorted(audited, key=lambda r: (r["date"], r["rel"]), reverse=True)
    lines.extend(["## By date", ""])
    if by_date_rows:
        lines.append(
            _md_table(
                ["Date", "Type", "Case", "Status", "Note"],
                [
                    [
                        row["date"] or "-",
                        row["type"],
                        row["case"],
                        row["status"],
                        f"[[{row['wikilink']}]]",
                    ]
                    for row in by_date_rows
                ],
            )
        )
    else:
        lines.append("_No audited runs yet._")
    lines.append("")

    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for row in audited:
        by_bucket.setdefault(row["bucket"], []).append(row)
    lines.extend(["## By bucket", ""])
    if by_bucket:
        for bucket in sorted(by_bucket):
            lines.append(f"### {bucket}")
            lines.append("")
            lines.append(
                _md_table(
                    ["Date", "Type", "Case", "Status", "Note"],
                    [
                        [
                            row["date"] or "-",
                            row["type"],
                            row["case"],
                            row["status"],
                            f"[[{row['wikilink']}]]",
                        ]
                        for row in sorted(
                            by_bucket[bucket], key=lambda r: (r["date"], r["rel"]), reverse=True
                        )
                    ],
                )
            )
            lines.append("")
    else:
        lines.append("_No audited runs yet._")
        lines.append("")

    lines.extend(["## Unaudited run dirs", ""])
    if unaudited:
        lines.append(
            "These run directories were discovered but have no `09_audit/experiment_note.md`. "
            "Run `aiswmm audit --run-dir <path>` to audit each one."
        )
        lines.append("")
        for row in sorted(unaudited, key=lambda r: r["rel"]):
            lines.append(f"- `{row['rel']}` (`aiswmm audit --run-dir runs/{row['rel']}`)")
        lines.append("")
    else:
        lines.append("_All discovered run dirs are audited._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
