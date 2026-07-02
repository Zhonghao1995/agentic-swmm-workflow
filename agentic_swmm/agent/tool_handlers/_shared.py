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
* The MCP-routed handler factory (``_make_mcp_routed_handler``,
  ``_wrap_mcp_result``) — moved here once the supposed
  ``tool_registry.ensure_session_pool`` monkeypatch contract turned out
  to have no remaining users; the pool is resolved through the
  ``mcp_pool`` module attribute at call time.
* ``_inp_source_tool`` — uniform glue for INP-source adapters.
* Small text utilities (``_try_json``, ``_tail``, ``_safe_name``,
  ``_process_text``, ``_summarize_cli_result``) that the process helper
  family needs.

What deliberately does NOT live here:

* ``ToolSpec`` / ``AgentToolRegistry`` — these are the registry's
  public surface (per PRD #128 commit ``467e5e8``); they stay in
  ``tool_registry.py``.
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
from typing import Any, Callable

from agentic_swmm.agent import mcp_pool
from agentic_swmm.agent.mcp_client import McpClientError
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root
from agentic_swmm.utils.subprocess_runner import runtime_env


def _failure(
    call: ToolCall,
    summary: str,
    *,
    hint: str | None = None,
    cause: str | None = None,
) -> dict[str, Any]:
    """Canonical fail-soft payload. ``hint``/``cause`` are optional structured
    remediation (see ``error_remediation.file_resolution_error``); they are
    only added when supplied so legacy callers keep the exact 4-key shape."""
    payload: dict[str, Any] = {
        "tool": call.name,
        "args": call.args,
        "ok": False,
        "summary": summary,
    }
    if cause is not None:
        payload["cause"] = cause
    if hint is not None:
        payload["hint"] = hint
    return payload


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


def _make_mcp_routed_handler(
    server: str,
    tool: str,
    *,
    args_mapper: Callable[[ToolCall, Path], dict[str, Any] | dict[str, Any]] | None = None,
) -> Callable[[ToolCall, Path], dict[str, Any]]:
    """Build a ToolSpec handler that forwards the call through ``MCPPool``.

    ``args_mapper`` is an optional pre-call hook that may:
    * translate ToolSpec snake_case argument names into the MCP server's
      camelCase property names,
    * resolve relative paths / inject defaults (e.g. node auto-detect),
    * return a fail-soft result dict early when validation fails — that
      dict is returned verbatim so handlers behave the same way the
      old in-process subprocess handlers did when args were bad.

    The handler returns a flat ``{tool, args, ok, results, summary}``
    dict shaped like the historical subprocess handlers, so existing
    planner / reporting code does not need updating.

    The session pool is resolved through the ``mcp_pool`` module
    attribute at call time, so tests that patch ``mcp_pool`` internals
    keep working regardless of where this factory lives.
    """

    def handler(call: ToolCall, session_dir: Path) -> dict[str, Any]:
        if args_mapper is None:
            mcp_args: dict[str, Any] = dict(call.args)
        else:
            mapped = args_mapper(call, session_dir)
            if isinstance(mapped, dict) and mapped.get("ok") is False and "summary" in mapped:
                # ``_failure``-shaped early return — surface it unchanged.
                return mapped
            mcp_args = mapped if isinstance(mapped, dict) else {}
        pool = mcp_pool.ensure_session_pool()
        if pool is None:
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "summary": (
                    f"MCP transport unavailable for {server}.{tool}: "
                    "no MCP server registry configured. "
                    "Run: bash scripts/install_mcp_deps.sh (or aiswmm setup --install-mcp)."
                ),
            }
        try:
            result = pool.call_tool(server, tool, mcp_args)
        except McpClientError as exc:
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "summary": f"MCP transport failed: {exc}",
            }
        return _wrap_mcp_result(call, server, tool, result)

    # Routing metadata — the public query surface is
    # ``AgentToolRegistry.mcp_routing(name)``; the lock-in test
    # ``tests/test_handler_lockin_no_direct_subprocess.py`` asserts
    # through it that every deterministic-SWMM ToolSpec handler is
    # built via this factory and not a legacy subprocess shim.
    handler._mcp_routing = {"server": server, "tool": tool}  # type: ignore[attr-defined]
    return handler


def _wrap_mcp_result(
    call: ToolCall,
    server: str,
    tool: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Convert the raw MCP ``tools/call`` result into a ToolSpec response.

    The MCP server returns a JSON-RPC ``result`` object — usually with a
    ``content`` array of text blocks. We pass the body through under the
    ``results`` key, and synthesise an ``excerpt`` from the joined text
    blocks so existing reporting code that reads ``stdout_tail`` /
    ``excerpt`` still surfaces useful context to the user.
    """

    excerpt = ""
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "")
                if text:
                    chunks.append(text)
        excerpt = "\n".join(chunks)[:4000]
    summary = f"called {server}.{tool}"
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": result,
        "excerpt": excerpt,
        "summary": summary,
    }


def _inp_source_tool(
    call: ToolCall,
    *,
    fetch: Callable[[], Any],
    describe: Callable[[Any], tuple[dict[str, Any], str]],
    stage_hint: Callable[[str], str],
) -> dict[str, Any]:
    """Uniform handler glue for INP-source adapters (see
    ``integrations/inp_source.py``): run the fetch, map a stage-tagged
    ``InpSourceError`` onto the fail-soft payload plus an actionable
    hint, and wrap the adapter's result description in the standard
    tool-result envelope. ``fetch`` closures keep their lazy imports so
    tests can patch the underlying runner functions.
    """
    from agentic_swmm.integrations.inp_source import InpSourceError

    try:
        result = fetch()
    except InpSourceError as exc:
        payload = _failure(call, str(exc))
        payload["stage"] = exc.stage
        payload["hint"] = stage_hint(exc.stage)
        return payload
    results, summary = describe(result)
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": results,
        "summary": summary,
    }


__all__ = [
    "_inp_source_tool",
    "_make_mcp_routed_handler",
    "_wrap_mcp_result",
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
