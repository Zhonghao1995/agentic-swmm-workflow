"""Runtime file / repo / skill ops (PRD #128 — Phase 2 Group C, FINAL group).

Family: bounded, repo-sandboxed file and version-control operations
the agent uses to read its own working tree.

* ``_read_file_tool`` / ``_list_dir_tool`` — bounded path reads
  inside the repo sandbox. Both are read-only.
* ``_list_skills_tool`` / ``_read_skill_tool`` — surface the
  configured skill registry so the planner can enumerate or read a
  ``SKILL.md`` without an extra subprocess.
* ``_search_files_tool`` — naive grep over the repo with a glob
  filter (skips ``.git`` / ``.venv`` / ``__pycache__``). The
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
import re
import subprocess
from pathlib import Path
from typing import Any

from agentic_swmm.agent.permissions import is_allowed_write_path, is_evidence_path
from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _repo_path,
    _tail,
)
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.runtime.registry import discover_skills
from agentic_swmm.utils.paths import repo_root


def _read_file_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    path = _repo_path(str(call.args["path"]))
    if path is None:
        return _failure(call, "refusing to read outside repository")
    if not path.exists() or not path.is_file():
        return _failure(call, f"file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"tool": call.name, "args": call.args, "ok": True, "path": str(path), "chars": len(text), "excerpt": text[:4000], "summary": f"read {path.relative_to(repo_root())}"}


def _list_skills_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    skills = [{"name": str(r.get("name")), "enabled": bool(r.get("enabled", True)), "path": str(r.get("path"))} for r in discover_skills()]
    return {"tool": call.name, "args": call.args, "ok": True, "skills": skills, "summary": f"{len(skills)} skills available", "excerpt": json.dumps(skills, indent=2)[:4000]}


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
        return _failure(call, "directory must exist inside repository")
    entries = [{"name": item.name, "type": "dir" if item.is_dir() else "file", "path": str(item.relative_to(repo_root()))} for item in sorted(path.iterdir())[:200]]
    return {"tool": call.name, "args": call.args, "ok": True, "results": entries, "summary": f"{len(entries)} entries"}


def _search_files_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    pattern = _normalize_search_glob(str(call.args.get("glob") or "*"))
    max_results = int(call.args.get("max_results") or 50)
    results: list[dict[str, Any]] = []
    try:
        paths = repo_root().rglob(pattern)
    except ValueError as exc:
        return _failure(call, f"invalid glob pattern: {exc}")
    for path in paths:
        if len(results) >= max_results:
            break
        if not path.is_file() or any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if query.lower() in line.lower():
                results.append({"path": str(path.relative_to(repo_root())), "line": lineno, "text": line.strip()[:300]})
                break
    return {"tool": call.name, "args": call.args, "ok": True, "glob": pattern, "results": results, "summary": f"{len(results)} match(es)"}


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
    return paths


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
]
