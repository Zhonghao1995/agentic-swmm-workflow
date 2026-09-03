"""Runtime file / repo / skill ops (PRD #128 — Phase 2 Group C, FINAL group).

Family: bounded, repo-sandboxed file and version-control operations
the agent uses to read its own working tree.

* ``_read_file_tool`` / ``_list_dir_tool`` — bounded path reads
  inside the repo sandbox. Both are read-only.
* ``_list_skills_tool`` / ``_read_skill_tool`` — surface the
  configured skill registry so the planner can enumerate or read a
  ``SKILL.md`` without an extra subprocess.
* ``_search_files_tool`` — naive grep over the repo with a glob
  filter (skips vendored and generated trees, oversized files,
  and caps how many files it will read; a capped scan says so).
  The
  ``_normalize_search_glob`` helper rewrites the common
  ``**.ext`` mistake LLM planners make into the equivalent
  ``**/*.ext`` pathlib expects.
* ``_git_diff_tool`` — read-only ``git diff`` / ``git diff --stat``
  passthrough.
* ``_apply_patch_tool`` — applies a unified diff to repo files,
  enforcing the write-permission policy
  (``is_allowed_write_path`` / ``is_evidence_path``) plus the
  repo-sandbox check on every ``+++ b/`` / ``--- a/`` / ``diff
  --git`` path. The ``_patch_paths`` helper that extracts those
  paths moves with the handler since it has no other caller.

All six handlers share the ``_repo_path`` / ``_failure`` / ``_tail``
helpers from ``tool_handlers/_shared``. ``_apply_patch_tool`` is the
only writer in the bundle — the others are read-only.
"""

from __future__ import annotations

import json
import os
import re
import time
import subprocess
from pathlib import Path
from typing import Any, Iterable

from agentic_swmm.agent.permissions import is_allowed_write_path, is_evidence_path
from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _repo_path,
    _tail,
)
from agentic_swmm.agent.error_remediation import file_resolution_error
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.runtime.registry import discover_skills
from agentic_swmm.utils.paths import repo_root


_PATH_LINE_SUFFIX = re.compile(r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)?$")


def _split_path_line(raw: str) -> tuple[str, int | None]:
    """Split ``path:391`` (or ``path:391:7``) into the path and the line.

    Planners quote files the way grep and tracebacks do (live finding
    F-29, 2026-09-02: ``agent_trace.jsonl:391`` was answered "file not
    found"). A path that exists as written is never split.
    """
    match = _PATH_LINE_SUFFIX.match(raw.strip())
    if not match:
        return raw, None
    candidate = _repo_path(raw)
    if candidate is not None and candidate.exists():
        return raw, None
    return match.group("path"), int(match.group("line"))


#: Live finding F-54 (2026-09-02): asked how uncertain a peak was, the
#: planner spent 32 of 34 tool calls (about 350k tokens) reading the
#: product's own source (search_files over agentic_swmm/**, read_file on
#: uncertainty_propagate.py, inp_patch.py, commands/uncertainty.py) to learn
#: how a tool worked, then asked the user a questionnaire. Tool contracts
#: come from select_skill and evidence from the run's artifacts; the
#: implementation is not evidence for a modeling question. SKILL.md files,
#: examples/*.json and every run artifact stay readable.
SOURCE_READS_ENV = "AISWMM_ALLOW_SOURCE_READS"
_SOURCE_DIRS = ("agentic_swmm", "src", "tests", "mcp")
SOURCE_READ_HINT = (
    "The product's source code is not evidence for a modeling question. "
    "Tool contracts come from select_skill/read_skill; results come from the "
    "run's artifacts (list_dir, read_rpt_summary, read_file on the run). "
    f"Set {SOURCE_READS_ENV}=1 to read source during development."
)


def _source_reads_allowed() -> bool:
    return os.environ.get(SOURCE_READS_ENV, "").strip() in ("1", "true", "yes")


def is_product_source(path: Path) -> bool:
    """True for a Python file that implements the product (never its docs or examples)."""
    if path.suffix.lower() not in (".py", ".pyi"):
        return False
    try:
        rel = path.resolve().relative_to(repo_root().resolve())
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        return False
    if parts[0] in _SOURCE_DIRS:
        return True
    return parts[0] == "skills" and len(parts) >= 3 and parts[2] == "scripts"


def _read_file_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    raw, line_hint = _split_path_line(str(call.args["path"]))
    path = _repo_path(raw)
    if path is None:
        return _failure(call, "refusing to read outside repository")
    if is_product_source(path) and not _source_reads_allowed():
        return _failure(call, f"refusing to read product source: {raw}", hint=SOURCE_READ_HINT)
    if not path.exists() or not path.is_file():
        err = file_resolution_error(
            f"file not found: {path}", requested=path, search_dir=path.parent
        )
        return _failure(call, err.summary, hint=err.hint, cause=err.cause)
    text = path.read_text(encoding="utf-8", errors="replace")
    excerpt = text[:4000]
    summary = f"read {path.relative_to(repo_root())}"
    if line_hint is not None:
        # Centre the bounded excerpt on the requested line instead of the
        # top of the file, so the reference the planner quoted is what it
        # gets back.
        lines = text.splitlines(keepends=True)
        start = max(0, min(line_hint - 1, len(lines)) - 20)
        excerpt = "".join(lines[start:])[:4000]
        summary += f" from line {start + 1} (requested line {line_hint})"
    return {"tool": call.name, "args": call.args, "ok": True, "path": str(path), "chars": len(text), "excerpt": excerpt, "summary": summary}


def _list_skills_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    # Names only (ADR token economy): the one-line-per-skill index with
    # descriptions already sits in the system prompt, and select_skill
    # returns the chosen skill's full tool contracts. The old full-path
    # JSON excerpt re-paid ~4k chars through replay on every later call.
    names = [str(r.get("name")) for r in discover_skills() if r.get("enabled", True)]
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "skills": names,
        "summary": (
            f"{len(names)} skills available (descriptions are in your "
            "system prompt's <skill-index>; select_skill returns full contracts)"
        ),
    }


def _read_skill_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    skill_name = str(call.args["skill_name"])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", skill_name):
        return _failure(call, "invalid skill name")
    path = _repo_path(f"skills/{skill_name}/SKILL.md")
    if path is None or not path.exists() or not path.is_file():
        return _failure(call, f"skill not found: {skill_name}")
    text = path.read_text(encoding="utf-8", errors="replace")
    # SKILL.md is the LLM's dispatch surface; a silent 4000-char cut hid the
    # tool list / "when to use" routing sections of the largest skills the
    # planner dispatches on. Return the whole file up to a generous cap
    # (largest current SKILL.md ~22 KB) and mark overflow explicitly,
    # mirroring the "[truncated]" convention in prompts.py.
    cap = 40000
    excerpt = text if len(text) <= cap else text[:cap].rstrip() + "\n[truncated]"
    return {"tool": call.name, "args": call.args, "ok": True, "path": str(path), "chars": len(text), "excerpt": excerpt, "summary": f"read skill {skill_name}"}


def _list_dir_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    path = _repo_path(str(call.args.get("path") or "."))
    if path is None or not path.exists() or not path.is_dir():
        err = file_resolution_error(
            "directory must exist inside repository",
            requested=call.args.get("path"),
            search_dir=path.parent if path is not None else None,
        )
        return _failure(call, err.summary, hint=err.hint, cause=err.cause)
    entries = [{"name": item.name, "type": "dir" if item.is_dir() else "file", "path": str(item.relative_to(repo_root()))} for item in sorted(path.iterdir())[:200]]
    return {"tool": call.name, "args": call.args, "ok": True, "results": entries, "summary": f"{len(entries)} entries"}


# Directories that never hold model or project content, only vendored or
# generated bytes. node_modules is the one that mattered: an install with the
# 11 MCP servers present put ~52k files in front of the ~11k that are actually
# searchable, and every one of them was read into memory in full. A single
# search took two and a half minutes and read like a hang.
_SEARCH_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        "site-packages",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
    }
)
# A model .inp is kilobytes and a rainfall .dat is megabytes at worst. Anything
# past this is a binary or an archive, and reading it is pure latency.
_MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024
# Hard ceiling on files actually read, so a search on an unfamiliar tree cannot
# run away. Hitting it is reported, never silently absorbed.
_MAX_SEARCH_SCANNED = 5000


#: Files a text search never opens: reading and failing to decode a 4 MB
#: upstream zip or a binary .out per call was the bulk of a 2-minute search
#: (live finding F-28, 2026-09-02).
_SEARCH_SKIP_SUFFIXES = frozenset(
    {".out", ".zip", ".gz", ".png", ".pdf", ".docx", ".pyc", ".sqlite", ".db", ".jpg", ".jpeg", ".whl", ".log", ".jsonl"}
)


def _is_operator_or_trace_path(path: Path) -> bool:
    """True inside a directory whose name starts with an underscore.

    ``_agent/`` holds the planner's own trace and reports and ``_harness/``
    the operator's tooling and transcripts. Live finding F-59 (2026-09-02):
    asked for a design storm, the planner searched the campaign's raw
    transcripts and read the scenario file that had produced its own
    prompt. None of that is evidence; read_file still opens any of it when
    a path is named explicitly.
    """
    return any(part.startswith("_") and part != "__pycache__" for part in path.parts[:-1])
#: Wall-clock budget for one search; the result says when it was hit.
_MAX_SEARCH_SECONDS = 10.0


def _search_candidates(root: Path, pattern: str) -> Iterable[Path]:
    """Yield the paths a pattern names, anchored when the pattern is a path.

    ``Path.rglob(pattern)`` prepends ``**/`` and walks the WHOLE repository
    for every call, so a search scoped to one run directory
    (``runs/<date>/<run>/**/*``) still visited every other run, every
    node_modules tree and every archived session (live finding F-28: two
    minutes per call). A pattern that contains a path separator is
    anchored at the repository root; an absolute pattern inside the
    repository is made relative first; a bare filename pattern
    (``*.inp``) keeps the recursive walk it always had.
    """
    if pattern.startswith("/"):
        try:
            pattern = str(Path(pattern).resolve().relative_to(root.resolve()))
        except ValueError:
            return iter(())
    if "/" in pattern:
        return root.glob(pattern)
    return root.rglob(pattern)


def _search_files_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    pattern = _normalize_search_glob(str(call.args.get("glob") or "*"))
    max_results = int(call.args.get("max_results") or 50)
    results: list[dict[str, Any]] = []
    scanned = 0
    truncated = False
    stop_reason = ""
    started = time.monotonic()
    root = repo_root()
    source_reads_allowed = _source_reads_allowed()
    try:
        paths = _search_candidates(root, pattern)
    except ValueError as exc:
        return _failure(call, f"invalid glob pattern: {exc}")
    skipped_source = 0
    for path in paths:
        if len(results) >= max_results:
            break
        if not source_reads_allowed and is_product_source(path):
            skipped_source += 1
            continue
        if scanned >= _MAX_SEARCH_SCANNED:
            truncated = True
            stop_reason = f"scanned {scanned} files"
            break
        if time.monotonic() - started > _MAX_SEARCH_SECONDS:
            truncated = True
            stop_reason = f"{_MAX_SEARCH_SECONDS:.0f} s time budget after {scanned} files"
            break
        if any(part in _SEARCH_SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in _SEARCH_SKIP_SUFFIXES:
            continue
        if _is_operator_or_trace_path(path):
            continue
        try:
            if not path.is_file() or path.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            if query.lower() in line.lower():
                results.append({"path": str(path.relative_to(root)), "line": lineno, "text": line.strip()[:300]})
                break
    elapsed = round(time.monotonic() - started, 2)
    summary = f"{len(results)} match(es) in {elapsed}s"
    if truncated:
        # Say it. A caller that reads "0 match(es)" after a capped scan would
        # conclude the file is absent when the search simply stopped early.
        summary += f"; stopped after {stop_reason}, results are incomplete"
    payload = {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "glob": pattern,
        "results": results,
        "scanned": scanned,
        "truncated": truncated,
        "elapsed_seconds": elapsed,
        "skipped_source_files": skipped_source,
        "summary": summary,
    }
    if skipped_source and not results:
        payload["hint"] = SOURCE_READ_HINT
        payload["summary"] = f"{summary}; {skipped_source} product source file(s) skipped"
    return payload


def _normalize_search_glob(pattern: str) -> str:
    cleaned = pattern.strip() or "*"
    # pathlib requires "**" to be a complete path component. LLM planners often
    # produce "**.inp" when they mean a recursive extension search.
    cleaned = re.sub(r"(?<!/)\*\*\.([A-Za-z0-9_*?[\]-]+)$", r"**/*.\1", cleaned)
    cleaned = re.sub(r"/\*\*\.([A-Za-z0-9_*?[\]-]+)$", r"/**/*.\1", cleaned)
    return cleaned


def _git_diff_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    command = ["git", "diff", "--stat" if call.args.get("stat_only", True) else "--"]
    if call.args.get("path"):
        command.extend(["--", str(call.args["path"])])
    proc = subprocess.run(command, cwd=repo_root(), capture_output=True, text=True)
    return {"tool": call.name, "args": call.args, "ok": proc.returncode == 0, "return_code": proc.returncode, "excerpt": proc.stdout[:8000], "stderr_tail": _tail(proc.stderr), "summary": "git diff read" if proc.returncode == 0 else "git diff failed"}


def _apply_patch_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    patch = str(call.args.get("patch") or "")
    if not patch.strip():
        return _failure(call, "patch is required")
    touched = _patch_paths(patch)
    if not touched:
        return _failure(call, "patch did not contain recognizable file paths")
    allow_evidence = bool(call.args.get("allow_evidence_edits"))
    for path in touched:
        full = _repo_path(path)
        if full is None:
            return _failure(call, f"patch path must be inside repository: {path}")
        if not is_allowed_write_path(full):
            return _failure(call, f"patch path is blocked by policy: {path}")
        if is_evidence_path(full) and not allow_evidence:
            return _failure(call, f"patch modifies evidence/generated memory path; set allow_evidence_edits only for explicit regenerate tasks: {path}")
    patch_path = session_dir / "tool_results" / "apply_patch.diff"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(patch, encoding="utf-8")
    if _is_envelope_patch(patch):
        # OpenAI patch-envelope format (Add/Update/Delete File
        # sections) — what GPT-family planners emit natively. Applied
        # by the in-process envelope engine; ``git apply`` only
        # understands unified diffs.
        try:
            op_count, summary = _apply_envelope(patch)
        except ValueError as exc:
            return _failure(call, f"envelope patch failed: {exc}")
        return {
            "tool": call.name,
            "args": {"path_count": len(touched), "allow_evidence_edits": allow_evidence},
            "ok": True,
            "return_code": 0,
            "path": str(patch_path),
            "summary": summary,
        }
    proc = subprocess.run(["git", "apply", str(patch_path)], cwd=repo_root(), capture_output=True, text=True)
    return {
        "tool": call.name,
        "args": {"path_count": len(touched), "allow_evidence_edits": allow_evidence},
        "ok": proc.returncode == 0,
        "return_code": proc.returncode,
        "path": str(patch_path),
        "stdout_tail": _tail(proc.stdout),
        "stderr_tail": _tail(proc.stderr),
        "summary": f"applied patch to {len(touched)} path(s)" if proc.returncode == 0 else "patch failed",
    }


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            path = line[6:].strip()
            if path != "/dev/null" and path not in paths:
                paths.append(path)
        elif line.startswith("diff --git "):
            parts = line.split()
            for part in parts[2:4]:
                if part.startswith(("a/", "b/")):
                    path = part[2:]
                    if path not in paths:
                        paths.append(path)
        elif line.startswith(("*** Add File: ", "*** Update File: ", "*** Delete File: ")):
            # OpenAI patch-envelope headers — the format GPT-family
            # planners emit natively (found 2026-08-09: every
            # apply_patch attempt from the codex route failed with
            # "no recognizable file paths" because only unified-diff
            # headers were parsed).
            path = line.split(":", 1)[1].strip()
            if path and path not in paths:
                paths.append(path)
    return paths


_ENVELOPE_BEGIN = "*** Begin Patch"


def _is_envelope_patch(patch: str) -> bool:
    return patch.lstrip().startswith(_ENVELOPE_BEGIN)


def _parse_envelope(patch: str) -> list[dict[str, Any]]:
    """Parse the OpenAI patch-envelope format into file operations.

    Supported sections: ``*** Add File: <path>`` (body: ``+`` lines),
    ``*** Delete File: <path>``, ``*** Update File: <path>`` (body:
    context/``-``/``+`` hunks, optionally separated by ``@@`` markers).
    Raises ``ValueError`` on malformed input so the caller can fail
    with an honest message instead of guessing.
    """
    ops: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in patch.splitlines():
        if raw.strip() in (_ENVELOPE_BEGIN, "*** End Patch", "*** End of File"):
            continue
        if raw.startswith("*** Add File: "):
            current = {"action": "add", "path": raw.split(":", 1)[1].strip(), "content": []}
            ops.append(current)
            continue
        if raw.startswith("*** Delete File: "):
            ops.append({"action": "delete", "path": raw.split(":", 1)[1].strip()})
            current = None
            continue
        if raw.startswith("*** Update File: "):
            current = {"action": "update", "path": raw.split(":", 1)[1].strip(), "hunks": [[]]}
            ops.append(current)
            continue
        if raw.startswith("*** Move to: ") and current is not None and current["action"] == "update":
            current["move_to"] = raw.split(":", 1)[1].strip()
            continue
        if current is None:
            if raw.strip():
                raise ValueError(f"unexpected line outside a file section: {raw[:80]!r}")
            continue
        if current["action"] == "add":
            if raw.startswith("+"):
                current["content"].append(raw[1:])
            elif not raw.strip():
                current["content"].append("")
            else:
                raise ValueError(f"Add File body lines must start with '+': {raw[:80]!r}")
        elif current["action"] == "update":
            if raw.startswith("@@"):
                if current["hunks"][-1]:
                    current["hunks"].append([])
            else:
                current["hunks"][-1].append(raw)
    return ops


def _apply_update_hunk(lines: list[str], hunk: list[str], path: str) -> list[str]:
    """Splice one context hunk into ``lines`` by exact context match.

    The hunk's old text (context + removed lines) must occur EXACTLY
    once in the file; zero matches or more than one raise ``ValueError``
    so ambiguity fails loudly instead of patching the wrong spot.
    """
    old = [l[1:] if l[:1] in (" ", "-") else l for l in hunk if l[:1] in (" ", "-") or not l.strip()]
    new = [l[1:] if l[:1] in (" ", "+") else l for l in hunk if l[:1] in (" ", "+") or not l.strip()]
    if not old:
        raise ValueError(f"update hunk for {path} has no context or removed lines")
    matches = [
        i
        for i in range(len(lines) - len(old) + 1)
        if lines[i : i + len(old)] == old
    ]
    if len(matches) != 1:
        raise ValueError(
            f"update hunk context matched {len(matches)} location(s) in {path}; "
            "need exactly 1 (add more surrounding context lines)"
        )
    i = matches[0]
    return lines[:i] + new + lines[i + len(old) :]


def _apply_envelope(patch: str) -> tuple[int, str]:
    """Apply a parsed envelope. Returns ``(op_count, summary)``.

    Raises ``ValueError`` with an actionable message on any failure;
    the caller converts that into the standard fail-soft result. Ops
    are validated fully before any write so a failed patch leaves the
    tree untouched.
    """
    ops = _parse_envelope(patch)
    if not ops:
        raise ValueError("envelope patch contained no file sections")
    staged: list[tuple[Path, str | None]] = []  # (target, new_text|None=delete)
    for op in ops:
        full = _repo_path(op["path"])
        if full is None:
            raise ValueError(f"patch path must be inside repository: {op['path']}")
        if op["action"] == "add":
            staged.append((full, "\n".join(op["content"]) + "\n"))
        elif op["action"] == "delete":
            if not full.exists():
                raise ValueError(f"cannot delete missing file: {op['path']}")
            staged.append((full, None))
        else:
            if not full.exists():
                raise ValueError(f"cannot update missing file: {op['path']}")
            lines = full.read_text(encoding="utf-8").splitlines()
            for hunk in op["hunks"]:
                if hunk:
                    lines = _apply_update_hunk(lines, hunk, op["path"])
            target = _repo_path(op.get("move_to") or op["path"])
            if target is None:
                raise ValueError(f"move target must be inside repository: {op.get('move_to')}")
            if target != full:
                staged.append((full, None))
            staged.append((target, "\n".join(lines) + "\n"))
    for target, text in staged:
        if text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    return len(ops), f"applied envelope patch: {len(ops)} file op(s)"


__all__ = [
    "_read_file_tool",
    "_list_skills_tool",
    "_read_skill_tool",
    "_list_dir_tool",
    "_search_files_tool",
    "_normalize_search_glob",
    "_git_diff_tool",
    "_apply_patch_tool",
    "_patch_paths",
    "tool_specs",
]


def tool_specs():
    """This family's planner tools (issue #358 self-registration):
    the repository-workspace surface (read/list/search/diff/patch)."""
    from agentic_swmm.agent.tool_handlers._shared import _object
    from agentic_swmm.agent.types import ToolSpec

    return [
        ToolSpec("read_file", "Read a repository file and return a bounded excerpt (capped at 4000 chars). NOTE: for SWMM .rpt summary sections (Link Flow / Outfall Loading / Node Inflow / water-quality sections), use read_rpt_summary instead — read_file's 4000-char cap cannot reach summary sections, which sit past the rpt header in 300+ KB files.", _object({"path": {"type": "string"}}, ["path"]), _read_file_tool, is_read_only=True),
        ToolSpec("read_skill", "Read a skill contract from skills/<skill_name>/SKILL.md.", _object({"skill_name": {"type": "string"}}, ["skill_name"]), _read_skill_tool, is_read_only=True),
        ToolSpec("list_skills", "List available repository skills.", _object({}), _list_skills_tool, is_read_only=True),
        ToolSpec("list_dir", "List a repository directory.", _object({"path": {"type": "string"}}), _list_dir_tool, is_read_only=True),
        ToolSpec("search_files", "Search text files in the repository.", _object({"query": {"type": "string"}, "glob": {"type": "string"}, "max_results": {"type": "integer"}}), _search_files_tool, is_read_only=True),
        ToolSpec("git_diff", "Read the current repository diff or diff stat.", _object({"stat_only": {"type": "boolean"}, "path": {"type": "string"}}), _git_diff_tool, is_read_only=True),
        ToolSpec("apply_patch", "Apply a unified diff patch to repository files. Writes are repo-only and blocked for .git/.venv/secret paths.", _object({"patch": {"type": "string"}, "allow_evidence_edits": {"type": "boolean"}}, ["patch"]), _apply_patch_tool),
    ]
