#!/usr/bin/env python3
"""Build the Agentic SWMM RAG memory corpus.

PRD M6 hygiene contract (post-processing layer; the underlying 761-LOC
``rag_memory_lib.build_corpus`` is left untouched):

- Every emitted entry must have a non-empty ``case_name``. Resolution
  order: existing entry value, ``experiment_provenance.json:case_id``,
  ``experiment_provenance.json:case_name``, ``modeling_memory_index``
  ``case_name`` for matching ``run_id``, audit-note frontmatter
  ``case:`` field, parent run-dir name. If all sources fail for an
  audit-derived entry, the script exits non-zero with a clear message.
- Every emitted entry carries a ``schema_version`` field, copied from
  the source ``experiment_provenance.json:schema_version`` when
  available, falling back to the lessons file marker, falling back to
  the package default ``DEFAULT_SCHEMA_VERSION``.
- A one-line summary is written to stderr after a successful build.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from rag_memory_lib import build_corpus, write_corpus


DEFAULT_SCHEMA_VERSION = "1.1"
_SCHEMA_VERSION_RE = re.compile(r"schema_version\s*[:=]\s*([0-9]+\.[0-9]+)")
_AUDIT_FRONTMATTER_CASE_RE = re.compile(r"^case\s*:\s*(.+?)\s*$", flags=re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an Agentic SWMM RAG memory corpus.")
    parser.add_argument("--memory-dir", type=Path, default=Path("memory/modeling-memory"))
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--out-dir", type=Path, default=Path("memory/rag-memory"))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--allow-missing-case-name",
        action="store_true",
        help="Do not exit non-zero when an audit-derived entry has no resolvable case_name.",
    )
    return parser.parse_args()


def _lessons_schema_version(memory_dir: Path) -> str | None:
    path = memory_dir / "lessons_learned.md"
    if not path.is_file():
        return None
    try:
        head = path.read_text(encoding="utf-8")[:4000]
    except OSError:
        return None
    match = _SCHEMA_VERSION_RE.search(head)
    return match.group(1) if match else None


def _modeling_index_case_names(memory_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    path = memory_dir / "modeling_memory_index.json"
    if not path.is_file():
        return out
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(parsed, dict):
        return out
    for record in parsed.get("records", []) or []:
        if not isinstance(record, dict):
            continue
        run_id = record.get("run_id")
        case_name = record.get("case_name")
        if run_id and case_name:
            out[str(run_id)] = str(case_name)
    return out


def _audit_dir_for(run_dir: Path) -> Path:
    new = run_dir / "09_audit"
    if new.is_dir():
        return new
    return run_dir


def _read_provenance(audit_dir: Path) -> dict[str, Any]:
    path = audit_dir / "experiment_provenance.json"
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_audit_note_case(audit_dir: Path) -> str | None:
    path = audit_dir / "experiment_note.md"
    if not path.is_file():
        return None
    try:
        head = path.read_text(encoding="utf-8")[:4000]
    except OSError:
        return None
    match = _AUDIT_FRONTMATTER_CASE_RE.search(head)
    return match.group(1).strip() if match else None


def _candidate_run_dirs(runs_dir: Path) -> Iterable[Path]:
    if not runs_dir.is_dir():
        return []
    return [path for path in runs_dir.rglob("*") if path.is_dir() and (path / "09_audit").is_dir() or _looks_like_run_dir(path)]


def _looks_like_run_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    for marker in ("experiment_provenance.json", "09_audit/experiment_provenance.json"):
        if (path / marker).is_file():
            return True
    return False


def _resolve_case_name_for_entry(
    entry: dict[str, Any],
    *,
    repo_root: Path,
    runs_dir: Path,
    index_case_names: dict[str, str],
) -> tuple[str | None, str | None]:
    """Return ``(case_name, schema_version)`` for ``entry``."""
    existing = entry.get("case_name")
    if existing:
        return str(existing), None

    run_id = entry.get("run_id")
    source_path = entry.get("source_path")

    # 1. modeling_memory_index by run_id.
    if run_id and run_id in index_case_names:
        return index_case_names[str(run_id)], None

    # 2. provenance / audit-note via the source path.
    if source_path:
        candidate = (repo_root / source_path).resolve()
        # walk upward looking for an audit dir.
        for parent in [candidate.parent, *candidate.parents]:
            try:
                parent.relative_to(repo_root.resolve())
            except ValueError:
                break
            audit_dir = _audit_dir_for(parent)
            provenance = _read_provenance(audit_dir)
            if provenance:
                case_id = provenance.get("case_id") or provenance.get("case_name")
                schema_version = provenance.get("schema_version")
                if case_id:
                    return str(case_id), (str(schema_version) if schema_version else None)
                note_case = _read_audit_note_case(audit_dir)
                if note_case:
                    return note_case, (str(schema_version) if schema_version else None)
            if audit_dir != parent and parent != repo_root.resolve():
                # Use the run-dir name as last-resort fallback.
                return parent.name, None

    # 3. By run_id within runs/.
    if run_id and runs_dir.is_dir():
        match = next(
            (path for path in runs_dir.rglob(str(run_id)) if path.is_dir()),
            None,
        )
        if match is not None:
            return match.name, None

    # 4. Curated memory documents (lessons / index / proposals): synthesise a
    #    stable, greppable case_name from the source path stem. These entries
    #    have no run, so the PRD's case_name field is informational only.
    if source_path:
        source = Path(str(source_path))
        curated_stems = {
            "lessons_learned",
            "modeling_memory_index",
            "project_memory_index",
            "skill_update_proposals",
            "benchmark_verification_plan",
        }
        if source.stem in curated_stems or "modeling-memory" in source.parts:
            return f"_curated_memory_{source.stem}", None

    return (str(run_id) if run_id else None), None


def _post_process(
    entries: list[dict[str, Any]],
    *,
    repo_root: Path,
    runs_dir: Path,
    memory_dir: Path,
    allow_missing_case_name: bool,
) -> tuple[list[dict[str, Any]], list[str], str]:
    """Apply case_name + schema_version hygiene.

    Returns the cleaned entries, a list of source_paths with missing
    case_name (empty unless ``allow_missing_case_name`` was set), and
    the schema version stamped on entries that lacked one.
    """
    index_case_names = _modeling_index_case_names(memory_dir)
    lessons_schema = _lessons_schema_version(memory_dir)
    fallback_schema = lessons_schema or DEFAULT_SCHEMA_VERSION

    missing: list[str] = []
    cleaned: list[dict[str, Any]] = []
    for entry in entries:
        case_name, derived_schema = _resolve_case_name_for_entry(
            entry,
            repo_root=repo_root,
            runs_dir=runs_dir,
            index_case_names=index_case_names,
        )
        if not case_name:
            missing.append(str(entry.get("source_path") or entry.get("run_id") or "<unknown>"))
            if not allow_missing_case_name:
                continue
        if case_name:
            entry["case_name"] = case_name
        if not entry.get("schema_version"):
            entry["schema_version"] = derived_schema or fallback_schema
        cleaned.append(entry)

    return cleaned, missing, fallback_schema


def main() -> int:
    args = parse_args()
    entries = build_corpus(args.memory_dir, args.runs_dir, args.repo_root)
    cleaned, missing, schema = _post_process(
        entries,
        repo_root=args.repo_root,
        runs_dir=args.runs_dir,
        memory_dir=args.memory_dir,
        allow_missing_case_name=args.allow_missing_case_name,
    )

    if missing and not args.allow_missing_case_name:
        print(
            json.dumps(
                {
                    "error": "build_memory_corpus refused to emit entries with empty case_name",
                    "missing_sources": missing,
                    "hint": "Add case_id / case_name to experiment_provenance.json or pass --allow-missing-case-name to bypass.",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1

    write_corpus(cleaned, args.out_dir)
    distinct_cases = {entry.get("case_name") for entry in cleaned if entry.get("case_name")}
    print(
        json.dumps(
            {
                "entry_count": len(cleaned),
                "out_dir": str(args.out_dir),
                "embedding_backend": "local-hashed-token-char-ngram",
                "schema_version": schema,
                "distinct_cases": len(distinct_cases),
            },
            sort_keys=True,
        )
    )
    print(
        f"built corpus: {len(cleaned)} entries, {len(distinct_cases)} distinct cases, schema={schema}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
