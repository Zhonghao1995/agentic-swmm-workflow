"""Cross-cutting helpers shared by every tool-handler family (PRD #128).

PR #128 split ``agentic_swmm/agent/tool_registry.py`` into family-organised
modules under ``tool_handlers/`` (web, demo, swmm_memory) and explicitly
deferred this module:

> Cross-cutting helpers (``_failure``, ``_repo_path``, ``_run_cli_tool``,
> ...) stay in ``tool_registry.py`` for now — they will move to
> ``tool_handlers/_shared.py`` in a follow-up PR once the remaining
> families have been extracted.

This module is that follow-up's Phase 1. The remaining family handlers
(swmm_runner, swmm_plot, ...) still live in ``tool_registry.py`` and
will move out in Phase 2 — they import these helpers from here in the
meantime, exactly the same way ``tool_registry.py`` itself does.

What lives here:

* ``_failure`` — canonical fail-soft payload shape every handler emits.
* ``_repo_path`` / ``_repo_output_path`` — repo-root sandbox check.
* ``_strip_html`` — used by ``web.py`` and any other handler that reads
  HTML responses.
* ``_run_cli_tool`` / ``_run_script_tool`` / ``_run_process_tool`` —
  uniform subprocess pipe with stdout/stderr capture and tool-results
  files under the session dir.
* Small text utilities (``_try_json``, ``_tail``, ``_safe_name``,
  ``_process_text``, ``_summarize_cli_result``) that the process helper
  family needs.

What deliberately does NOT live here:

* ``ToolSpec`` / ``AgentToolRegistry`` / ``compute_intent_signals`` /
  ``_VALID_MODE_ENUM`` — these are the registry's public surface (per
  PRD #128 commit ``467e5e8``); they stay in ``tool_registry.py``.
* The MCP-routed handler factory (``_make_mcp_routed_handler``,
  ``_wrap_mcp_result``). It is cross-cutting, but a tests/test_*.py
  contract relies on ``tool_registry.ensure_session_pool`` being
  monkey-patchable through the registry module, so moving it would
  break that fixture pattern. Deferred to a separate follow-up.
* Anything family-specific (skill router, MCP server registry,
  plotting option resolvers, etc.) — those will move with their family
  in Phase 2.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root
from agentic_swmm.utils.subprocess_runner import runtime_env


def _failure(call: ToolCall, summary: str) -> dict[str, Any]:
    return {"tool": call.name, "args": call.args, "ok": False, "summary": summary}


def _repo_path(value: str) -> Path | None:
    raw = Path(value).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (repo_root() / raw).resolve()
    try:
        candidate.relative_to(repo_root().resolve())
    except ValueError:
        return None
    return candidate


def _repo_output_path(value: str) -> Path | None:
    path = _repo_path(value)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_run_dir(call: ToolCall, key: str) -> Path | dict[str, Any]:
    """Resolve a run-directory argument without a repo-sandbox check.

    The convergence tools (``run_swmm_inp``, ``plot_run``, ``map_run``,
    ``audit_run``) accept out-of-repo run dirs — the synth path
    (``synth_swmm_from_bbox``) writes to arbitrary user directories and
    the whole chain must work end-to-end.  The manifest is the contract;
    the repo root is not.

    Relative paths are resolved against ``repo_root()`` for consistency
    with existing in-repo paths (e.g. ``runs/agent/my-run`` resolves the
    same way it always did).  Absolute paths (including out-of-repo ones)
    are used directly.

    Returns a ``Path`` that exists and is a directory, or a fail-soft
    ``_failure(...)`` dict when the argument is missing or the directory
    does not exist.
    """
    value = call.args.get(key)
    if not isinstance(value, str) or not value.strip():
        return _failure(call, f"missing required directory argument: {key}")
    raw = Path(value).expanduser()
    resolved = raw.resolve() if raw.is_absolute() else (repo_root() / raw).resolve()
    if not resolved.exists() or not resolved.is_dir():
        return _failure(call, f"directory not found: {resolved}")
    return resolved


def _resolve_or_create_run_dir(call: ToolCall, key: str) -> Path | dict[str, Any] | None:
    """Resolve or create an optional output run-directory argument.

    Like ``_resolve_run_dir`` but the argument is optional (returns
    ``None`` when absent) and the directory is created when it does not
    yet exist.  Used by ``run_swmm_inp`` where the caller may supply a
    ``run_dir`` for the output, or leave it unset to get an auto-named
    directory under ``runs/agent/``.
    """
    value = call.args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return _failure(call, f"{key} must be a non-empty string")
    raw = Path(value).expanduser()
    resolved = raw.resolve() if raw.is_absolute() else (repo_root() / raw).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _tail(text: str, max_chars: int = 2000) -> str:
    return text.strip()[-max_chars:]


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "agent"


def _process_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _summarize_cli_result(tool: str, stdout: str, return_code: int) -> str:
    if return_code != 0:
        return f"{tool} failed"
    parsed = _try_json(stdout)
    if isinstance(parsed, dict):
        if "run_dir" in parsed:
            return f"run_dir={parsed['run_dir']}"
        if "experiment_note" in parsed:
            return f"audit_note={parsed['experiment_note']}"
        if "ok" in parsed and "issue_count" in parsed:
            return f"ok={parsed['ok']} issue_count={parsed['issue_count']}"
        if "outputs" in parsed:
            return "outputs=" + json.dumps(parsed["outputs"], sort_keys=True)[:500]
    stripped = stdout.strip().splitlines()
    return stripped[-1] if stripped else "completed"


def _run_process_tool(
    call: ToolCall,
    session_dir: Path,
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 120,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, env=runtime_env(), timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        proc = subprocess.CompletedProcess(command, 124, stdout=exc.stdout or "", stderr=exc.stderr or f"command timed out after {timeout}s")
        timed_out = True
    finished = datetime.now(timezone.utc)
    stdout = _process_text(proc.stdout)
    stderr = _process_text(proc.stderr)
    safe_name = _safe_name(call.name)
    stdout_path = session_dir / "tool_results" / f"{safe_name}.stdout.txt"
    stderr_path = session_dir / "tool_results" / f"{safe_name}.stderr.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {"tool": call.name, "args": call.args, "command": command, "ok": proc.returncode == 0, "return_code": proc.returncode, "timed_out": timed_out, "started_at_utc": started.isoformat(timespec="seconds"), "finished_at_utc": finished.isoformat(timespec="seconds"), "stdout_file": str(stdout_path), "stderr_file": str(stderr_path), "stdout_tail": _tail(stdout), "stderr_tail": _tail(stderr), "summary": _summarize_cli_result(call.name, stdout, proc.returncode)}


def _run_cli_tool(call: ToolCall, session_dir: Path, cli_args: list[str]) -> dict[str, Any]:
    return _run_process_tool(call, session_dir, [sys.executable, "-m", "agentic_swmm.cli", *cli_args], cwd=repo_root())


def _run_script_tool(call: ToolCall, session_dir: Path, cli_args: list[str]) -> dict[str, Any]:
    return _run_process_tool(call, session_dir, [sys.executable, *cli_args], cwd=repo_root())


__all__ = [
    "_failure",
    "_repo_path",
    "_repo_output_path",
    "_resolve_run_dir",
    "_resolve_or_create_run_dir",
    "_strip_html",
    "_try_json",
    "_tail",
    "_safe_name",
    "_process_text",
    "_summarize_cli_result",
    "_run_process_tool",
    "_run_cli_tool",
    "_run_script_tool",
]
