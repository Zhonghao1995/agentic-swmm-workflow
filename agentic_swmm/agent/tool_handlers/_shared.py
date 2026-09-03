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
import time
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


def _timestamped_run_dir(call: ToolCall, *, prefix: str) -> Path:
    """Explicit ``run_dir`` argument, or a fresh timestamped default.

    The INP-source fetchers (``synth_swmm_from_bbox``,
    ``fetch_swmm_from_canada``) default to
    ``runs/agent/<prefix>-<unix-ts>`` so a re-run under the same name
    never lands in (and silently overwrites) a previous run's directory
    (issues #246/#234); a ``-N`` suffix bumps until the name is free.
    An EXPLICIT ``run_dir`` is passed through untouched: same-dir reuse
    (e.g. the synth ``00_raw/`` snapshot workflow) stays a deliberate
    caller choice. One home for the collision policy (issue #296).
    """
    raw = call.args.get("run_dir")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    base = f"{prefix}-{int(time.time())}"
    root = repo_root() / "runs" / "agent"
    candidate = root / base
    bump = 1
    while candidate.exists():
        bump += 1
        candidate = root / f"{base}-{bump}"
    return candidate


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


_DOCTOR_STATUS_TOKENS = ("OK", "WARN", "FAIL", "MISSING", "UNSET")


def doctor_verdict(stdout: str) -> str:
    """One line that says what doctor found, not the last line it printed.

    Live finding F-64 (2026-09-02): with an unreachable SWMMCanada endpoint
    the tool's summary was the tail of doctor's last line, "the service is
    up", which read as a contradiction next to the failed fetch.
    """
    counts = {token: 0 for token in _DOCTOR_STATUS_TOKENS}
    first_problem = ""
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        token = line.split(None, 1)[0]
        if token not in counts:
            continue
        counts[token] += 1
        if token in ("WARN", "FAIL", "MISSING") and not first_problem:
            rest = line[len(token):].strip()
            first_problem = rest.split(" - ", 1)[0].strip() or rest[:60]
    checked = counts["OK"] + counts["WARN"] + counts["FAIL"] + counts["MISSING"]
    if not checked:
        stripped = stdout.strip().splitlines()
        return stripped[-1] if stripped else "doctor completed"
    parts = [f"{counts['OK']} OK"]
    for token in ("WARN", "FAIL", "MISSING"):
        if counts[token]:
            parts.append(f"{counts[token]} {token}")
    verdict = "doctor: " + ", ".join(parts)
    if first_problem:
        verdict += f"; first problem: {first_problem}"
    return verdict


def _summarize_cli_result(tool: str, stdout: str, return_code: int) -> str:
    if tool == "doctor":
        verdict = doctor_verdict(stdout)
        return verdict if return_code == 0 else f"doctor failed (exit {return_code}); {verdict}"
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
                "summary": _augment_engine_failure(
                    call, f"MCP transport failed: {_trim_traceback(str(exc))}"
                ),
            }
        return _wrap_mcp_result(call, server, tool, result)

    # Routing metadata — the public query surface is
    # ``AgentToolRegistry.mcp_routing(name)``; the lock-in test
    # ``tests/test_handler_lockin_no_direct_subprocess.py`` asserts
    # through it that every deterministic-SWMM ToolSpec handler is
    # built via this factory and not a legacy subprocess shim.
    handler._mcp_routing = {"server": server, "tool": tool}  # type: ignore[attr-defined]
    return handler


def _trim_traceback(message: str) -> str:
    """Keep the error line of an embedded Python traceback, drop the frames.

    A skill script that dies under the MCP server hands back its whole
    stack (twelve lines of pathlib internals for one missing file, live
    finding F-36, 2026-09-02). The planner and the person watching need
    the exception line; the frames stay in the trace file for debugging.
    """
    marker = "Traceback (most recent call last):"
    if marker not in message:
        return message
    head, _, tail = message.partition(marker)
    lines = [line for line in tail.replace("\\n", "\n").splitlines() if line.strip()]
    error_line = next(
        (line.strip() for line in reversed(lines) if not line.startswith((" ", "\t")) and "File \"" not in line),
        "",
    )
    head = head.rstrip()
    if error_line:
        return f"{head} {error_line} (traceback trimmed)".strip()
    return f"{head} (traceback trimmed)".strip()


def _augment_engine_failure(call: ToolCall, summary: str) -> str:
    """Attach a next step to engine errors the raw line cannot explain.

    SWMM reports a missing external data file by series name and leaves the
    filename in a different INP section, so a planner holding only
    "ERROR 361 ... TEMP_ROME" has nothing to act on. One of these cost a live
    session a repository-wide search before it worked the answer out. When the
    builder does not recognise the message it returns None and the summary is
    passed through untouched.
    """
    from agentic_swmm.agent.error_remediation import swmm_external_file_error

    inp_arg = call.args.get("inp_path") or call.args.get("inp")
    try:
        remediation = swmm_external_file_error(
            summary,
            inp_path=Path(str(inp_arg)) if inp_arg else None,
            search_root=repo_root(),
        )
    except Exception:  # never let a hint helper turn into a second failure
        return summary
    if remediation is None:
        return summary
    extra = [part for part in (remediation.cause, remediation.hint) if part]
    return summary if not extra else summary + " — " + "; ".join(extra)


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
    # An MCP tool signals a tool-level failure with ``isError: true`` in an
    # otherwise well-formed result. Hardcoding ok=True here made the planner
    # count a failed call as success (review P1-6); honour the flag instead.
    is_error = bool(result.get("isError")) if isinstance(result, dict) else False
    summary = f"{server}.{tool} reported an error" if is_error else f"called {server}.{tool}"
    return {
        "tool": call.name,
        "args": call.args,
        "ok": not is_error,
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


# ---------------------------------------------------------------------------
# Leaf helpers extracted from tool_registry.py (issue #358 PR A)
# ---------------------------------------------------------------------------
#
# These were the reason the family modules lazily imported back into the
# registry (the documented end-of-file cycle). They are pure utilities:
# schema building, repo-sandboxed path/INP resolution, plot-option
# derivation, MCP schema mapping, and the run_allowed_command allowlist.
# tool_registry re-imports every name so historical
# ``from agentic_swmm.agent.tool_registry import X`` sites (and the
# ``monkeypatch.setattr(tool_registry, "X", ...)`` seam in tests) keep
# working byte-for-byte.
#
# Note the near-miss that is NOT a duplicate: ``_required_repo_file``
# is repo-SANDBOXED input resolution (inside-repo enforcement), while
# ``_resolve_run_dir`` / ``_resolve_or_create_run_dir`` above are the
# deliberately non-sandboxed run-dir seam (Bug #238). Keep them apart.


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def _mcp_failure(call: ToolCall, summary: str, *, server: str | None = None) -> dict[str, Any]:
    result = _failure(call, summary)
    result["recovery"] = "Use list_mcp_servers/list_mcp_tools to refresh available MCP tools, then retry with corrected server/tool/arguments or fall back to the CLI wrapper."
    result["fallback_tools"] = _mcp_fallback_tools(server or str(call.args.get("server") or ""))
    return result


def _map_mcp_tool_schema(server_name: str, tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("name") or "tool")
    description = str(tool.get("description") or f"MCP tool exposed by {server_name}.")
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        schema = tool.get("schema") if isinstance(tool.get("schema"), dict) else {}
    parameters = _normalize_json_schema(schema)
    return {
        "server": server_name,
        "mcp_tool": name,
        "planner_tool": "call_mcp_tool",
        "description": description,
        "arguments": {"server": server_name, "tool": name, "arguments_schema": parameters},
    }


def _normalize_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if not schema:
        return {"type": "object", "properties": {}, "additionalProperties": True}
    normalized = dict(schema)
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    normalized.setdefault("additionalProperties", True)
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    return normalized


def _mcp_fallback_tools(server_name: str) -> list[str]:
    mapping = {
        "swmm-builder": ["build_inp"],
        "swmm-climate": ["format_rainfall"],
        "swmm-network": ["network_qa", "network_to_inp"],
        "swmm-plot": ["plot_run"],
        "swmm-runner": ["run_swmm_inp"],
    }
    return mapping.get(server_name, ["list_mcp_servers", "list_mcp_tools"])


def _mcp_server(name: str) -> dict[str, Any] | None:
    from agentic_swmm.runtime.registry import load_mcp_registry

    for server in load_mcp_registry():
        if str(server.get("name")) == name:
            return server
    return None


def _find_repo_inp(value: str) -> Path | None:
    if not value or Path(value).is_absolute() or "/" in value:
        return None
    root = repo_root() / "examples"
    if not root.exists():
        return None
    matches = sorted(path for path in root.rglob(value) if path.is_file() and path.suffix.lower() == ".inp")
    return matches[0] if matches else None


def _resolve_existing_inp(value: str) -> Path | None:
    path = _repo_path(value)
    if path is not None and path.exists() and path.is_file() and path.suffix.lower() == ".inp":
        return path
    external = Path(value).expanduser()
    try:
        external = external.resolve()
    except OSError:
        return None
    if external.exists() and external.is_file() and external.suffix.lower() == ".inp":
        return external
    return _find_repo_inp(value)


def _node_suggestions(inp_path: str | None, limit: int = 8) -> list[str]:
    if not inp_path:
        return []
    candidate = _resolve_existing_inp(inp_path)
    if candidate is None:
        return []
    sections: dict[str, list[str]] = {"[OUTFALLS]": [], "[JUNCTIONS]": []}
    section: str | None = None
    for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.upper()
            continue
        if section in {"[OUTFALLS]", "[JUNCTIONS]"}:
            name = stripped.split()[0]
            if name not in sections[section]:
                sections[section].append(name)
    suggestions = [*sections["[OUTFALLS]"], *sections["[JUNCTIONS]"]]
    deduped = list(dict.fromkeys(suggestions))
    return deduped[:limit]


def _node_attribute_options(out_file: Path | None, node_options: list[str]) -> list[dict[str, Any]]:
    from agentic_swmm.commands.plot import NODE_ATTRIBUTE_CHOICES, NODE_ATTRIBUTE_LABELS

    if out_file is None or not out_file.exists():
        return _default_node_attribute_options()
    try:
        from swmmtoolbox import catalog

        rows = catalog(str(out_file), "node")
    except Exception:
        return _default_node_attribute_options()
    attrs: list[str] = []
    for row in rows:
        if len(row) < 3 or row[0] != "node":
            continue
        node, attr = str(row[1]), str(row[2])
        if node_options and node not in node_options:
            continue
        if attr not in attrs:
            attrs.append(attr)
    if not attrs:
        return _default_node_attribute_options()
    preferred = [attr for attr in NODE_ATTRIBUTE_CHOICES if attr in attrs]
    remainder = [attr for attr in attrs if attr not in preferred]
    return [{"name": attr, "label": NODE_ATTRIBUTE_LABELS.get(attr, attr.replace("_", " "))} for attr in [*preferred, *remainder]]


def _default_node_attribute_options() -> list[dict[str, str]]:
    from agentic_swmm.commands.plot import NODE_ATTRIBUTE_CHOICES, NODE_ATTRIBUTE_LABELS

    return [{"name": attr, "label": NODE_ATTRIBUTE_LABELS.get(attr, attr.replace("_", " "))} for attr in NODE_ATTRIBUTE_CHOICES]


def _required_repo_file(call: ToolCall, key: str, *, suffix: str | None = None) -> Path | dict[str, Any]:
    value = call.args.get(key)
    if not isinstance(value, str) or not value.strip():
        return _failure(call, f"missing required file argument: {key}")
    path = _repo_path(value)
    if path is None:
        return _failure(call, f"{key} must be inside repository")
    if suffix and path.suffix.lower() != suffix:
        return _failure(call, f"{key} must end with {suffix}")
    if not path.exists() or not path.is_file():
        return _failure(call, f"file not found: {path}")
    return path


def _resolve_inp_for_run(call: ToolCall) -> Path | dict[str, Any]:
    raw = str(call.args.get("inp_path", "")).strip()
    if not raw:
        return _failure(call, "missing required file argument: inp_path")
    repo_file = _required_repo_file(call, "inp_path", suffix=".inp")
    if not isinstance(repo_file, dict):
        return repo_file
    resolved = _find_repo_inp(raw)
    if resolved is not None:
        return resolved
    external = Path(raw).expanduser()
    try:
        external = external.resolve()
    except OSError:
        return _failure(call, f"inp_path could not be resolved: {raw}")
    if external.suffix.lower() != ".inp":
        return _failure(call, "inp_path must end with .inp")
    if not external.exists() or not external.is_file():
        return _failure(call, f"external INP file not found: {external}")
    return external


# pytest flags that can load arbitrary code (plugins) or point pytest at an
# attacker-controlled config/ini. Refused so ``run_allowed_command`` cannot be
# turned into an arbitrary-code-execution primitive (review P1-2).
_PYTEST_BANNED_FLAGS = {"-p", "-c", "--config-file", "-o", "--override-ini", "--pyargs"}


def _pytest_args_ok(args: list[str]) -> bool:
    """Every positional target must resolve inside the repo; no plugin/config injection."""
    for arg in args:
        if arg.startswith("-"):
            flag = arg.split("=", 1)[0]
            if flag in _PYTEST_BANNED_FLAGS:
                return False
            continue
        # Positional: a path or a nodeid (``path::Class::test``). Only the file
        # part is a path; it must resolve inside the repo.
        path_part = arg.split("::", 1)[0]
        if path_part and _repo_path(path_part) is None:
            return False
    return True


def _node_script_ok(target: str) -> bool:
    """Allow only ``.mjs`` files that resolve to something under ``scripts/``."""
    resolved = _repo_path(target)
    if resolved is None or resolved.suffix != ".mjs":
        return False
    try:
        rel = resolved.relative_to(repo_root().resolve())
    except ValueError:
        return False
    return rel.parts[:1] == ("scripts",)


def _command_allowed(command: list[str]) -> bool:
    exe = Path(command[0]).name.lower()
    if exe in {"pytest", "pytest.exe"}:
        return _pytest_args_ok(command[1:])
    if exe in {"python", "python.exe"} or exe.startswith("python3") or command[0] == sys.executable:
        if not (len(command) >= 3 and command[1] == "-m" and command[2] in {"pytest", "agentic_swmm.cli"}):
            return False
        return _pytest_args_ok(command[3:]) if command[2] == "pytest" else True
    if exe in {"node", "node.exe"}:
        return len(command) >= 2 and _node_script_ok(command[1])
    if exe in {"swmm5", "swmm5.exe", "swmm5.cmd"}:
        return True
    return False


__all__ = [
    "_inp_source_tool",
    "_make_mcp_routed_handler",
    "_wrap_mcp_result",
    "_failure",
    "_repo_path",
    "_repo_output_path",
    "_resolve_run_dir",
    "_resolve_or_create_run_dir",
    "_timestamped_run_dir",
    "_strip_html",
    "_try_json",
    "_tail",
    "_safe_name",
    "_process_text",
    "_summarize_cli_result",
    "_run_process_tool",
    "_run_cli_tool",
    "_run_script_tool",
    "_object",
    "_mcp_failure",
    "_map_mcp_tool_schema",
    "_normalize_json_schema",
    "_mcp_fallback_tools",
    "_mcp_server",
    "_find_repo_inp",
    "_resolve_existing_inp",
    "_node_suggestions",
    "_node_attribute_options",
    "_default_node_attribute_options",
    "_required_repo_file",
    "_resolve_inp_for_run",
    "_PYTEST_BANNED_FLAGS",
    "_pytest_args_ok",
    "_node_script_ok",
    "_command_allowed",
]
