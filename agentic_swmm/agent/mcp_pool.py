"""Long-running MCP server pool.

Per PRD-X, ``MCPPool`` is a deep module: the only public contract is the four
methods below. Construction is cheap and side-effect-free — servers are only
spawned the first time they are referenced by ``list_tools`` / ``call_tool``.

Wire protocol delegated to ``mcp_client`` helpers (NDJSON; see PR #41). The
pool reuses the same ``_send`` / ``_readline`` helpers so a future protocol
fix only has to be made in one place.

Concurrency: single-threaded for now (one chat turn at a time). Per-server
locking is a future PRD if/when concurrent calls become a real workload.

Lifecycle:
- ``__init__(registry)`` — register names + command + args; do not spawn.
- ``list_tools(server)`` / ``call_tool(server, tool, args)`` — lazy spawn,
  handshake, persistent stdio for subsequent calls on the same server.
- ``shutdown()`` — terminate every started server, idempotent. Owner is
  responsible for calling this (or registering it via ``atexit``).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent.mcp_client import McpClientError, _read, _send
from agentic_swmm.utils.paths import repo_root
from agentic_swmm.utils.subprocess_runner import runtime_env


@dataclass(frozen=True)
class ServerSpec:
    """Static configuration for one MCP server."""

    name: str
    command: str
    args: list[str]


@dataclass
class MCPServerHandle:
    """Per-server runtime state. ``None`` proc = never started or terminated."""

    spec: ServerSpec
    proc: subprocess.Popen[bytes] | None = None
    initialized: bool = False
    last_used_utc: str | None = None
    error: str | None = None
    # Monotonic JSON-RPC id source; pinned per-server so requests/responses
    # can be matched even if we ever change the wire to async.
    next_id: int = 1
    # Per-server send/receive lock for cooperative single-threaded reentry.
    lock: threading.Lock = field(default_factory=threading.Lock)


class MCPPool:
    """A registry of lazy MCP server handles plus a small JSON-RPC client.

    The class is intentionally small: ``list_tools`` and ``call_tool`` are
    thin wrappers over ``_ensure_started`` + ``_request``. All retry /
    error policy lives in ``_ensure_started``.
    """

    def __init__(self, registry: list[ServerSpec]) -> None:
        self._handles: dict[str, MCPServerHandle] = {
            spec.name: MCPServerHandle(spec=spec) for spec in registry
        }
        self._shutdown_called = False

    # ---- public API ----------------------------------------------------------

    def list_servers(self) -> list[str]:
        return list(self._handles)

    def list_tools(self, server: str, *, timeout: int = 20) -> list[dict[str, Any]]:
        response = self._request(server, "tools/list", {}, timeout=timeout)
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return tools if isinstance(tools, list) else []

    def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout: int = 60,
    ) -> dict[str, Any]:
        response = self._request(
            server,
            "tools/call",
            {"name": tool, "arguments": arguments},
            timeout=timeout,
        )
        result = response.get("result")
        return result if isinstance(result, dict) else {"result": result}

    def shutdown(self) -> None:
        """Terminate every started server. Idempotent."""

        if self._shutdown_called:
            return
        self._shutdown_called = True
        for handle in self._handles.values():
            proc = handle.proc
            if proc is None or proc.poll() is not None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            handle.proc = None
            handle.initialized = False

    # ---- internal ------------------------------------------------------------

    def _request(
        self,
        server: str,
        method: str,
        params: dict[str, Any],
        *,
        timeout: int,
    ) -> dict[str, Any]:
        if server not in self._handles:
            raise McpClientError(f"unknown MCP server: {server}")
        handle = self._handles[server]
        if handle.error:
            raise McpClientError(handle.error)

        with handle.lock:
            self._ensure_started(handle, timeout=timeout)
            return self._send_recv(handle, method, params, timeout=timeout)

    def _ensure_started(self, handle: MCPServerHandle, *, timeout: int) -> None:
        if handle.proc is not None and handle.proc.poll() is None and handle.initialized:
            return

        try:
            _preflight(handle.spec)
            proc = subprocess.Popen(
                [handle.spec.command, *handle.spec.args],
                cwd=repo_root(),
                env=runtime_env(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            handle.error = (
                f"MCP server '{handle.spec.name}' failed to start: "
                f"{handle.spec.command} not found ({exc})"
            )
            raise McpClientError(handle.error) from exc
        except McpClientError as exc:
            handle.error = str(exc)
            raise

        handle.proc = proc
        try:
            init_id = handle.next_id
            handle.next_id += 1
            _send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": init_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "aiswmm-agent", "version": "0.1"},
                    },
                },
            )
            response = _read(proc, timeout=timeout)
            if "error" in response:
                err_blob = json.dumps(response["error"], sort_keys=True)
                handle.error = err_blob
                raise McpClientError(err_blob)
            _send(
                proc,
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            )
            handle.initialized = True
        except Exception:
            # Initialize failed — tear the half-built child down so a later
            # call cannot reuse a broken pipe.
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            handle.proc = None
            handle.initialized = False
            if handle.error is None:
                handle.error = "MCP server failed to initialize"
            raise

    def _send_recv(
        self,
        handle: MCPServerHandle,
        method: str,
        params: dict[str, Any],
        *,
        timeout: int,
    ) -> dict[str, Any]:
        proc = handle.proc
        if proc is None:
            raise McpClientError(f"MCP server '{handle.spec.name}' is not running")
        rpc_id = handle.next_id
        handle.next_id += 1
        _send(
            proc,
            {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}},
        )
        response = _read(proc, timeout=timeout)
        handle.last_used_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if "error" in response:
            raise McpClientError(json.dumps(response["error"], sort_keys=True))
        return response


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _preflight(spec: ServerSpec) -> None:
    """Surface a friendly error before ``subprocess.Popen``.

    Mirrors the policy in ``mcp_client._preflight`` so the pool fails fast
    when the toolchain or deps are missing.
    """

    if spec.command == "node" and shutil.which("node") is None:
        raise McpClientError(
            "node is not on PATH; MCP servers require Node.js. "
            "Install Node 18+ (or run: aiswmm setup --install-mcp)."
        )
    for arg in spec.args:
        if not isinstance(arg, str) or not arg.endswith("server.js"):
            continue
        server_path = Path(arg)
        if not server_path.is_absolute():
            server_path = repo_root() / server_path
        node_modules = server_path.parent / "node_modules"
        if node_modules.exists():
            continue
        server_name = server_path.parent.name or str(server_path.parent)
        raise McpClientError(
            f"MCP server {server_name} has no node_modules. "
            "Run: bash scripts/install_mcp_deps.sh (or aiswmm setup --install-mcp)"
        )


def registry_from_records(records: list[dict[str, Any]]) -> list[ServerSpec]:
    """Build a list of ``ServerSpec`` from the records returned by
    ``runtime.registry.load_mcp_registry``. Disabled / missing servers are
    skipped silently — they would just fail at lazy-spawn anyway, and the
    pool reports per-server errors lazily.
    """

    specs: list[ServerSpec] = []
    for record in records:
        if not record.get("enabled", True):
            continue
        name = str(record.get("name") or "").strip()
        command = str(record.get("command") or "").strip()
        args_raw = record.get("args") or []
        if not name or not command or not isinstance(args_raw, list):
            continue
        args = [str(a) for a in args_raw if isinstance(a, (str, int))]
        specs.append(ServerSpec(name=name, command=command, args=args))
    return specs


# ---------------------------------------------------------------------------
# Per-process singleton + crash-safe cleanup
# ---------------------------------------------------------------------------


_session_pool: MCPPool | None = None
_signal_installed = False
_prev_sigterm_handler: Any = None


def session_pool() -> MCPPool | None:
    """Return the pool bound to the current aiswmm process, or ``None``.

    ``mcp_client.call_mcp`` routes through this pool when present; otherwise
    it falls back to the historical spawn-per-call path (still useful for
    tests that mock ``subprocess.Popen``).
    """

    return _session_pool


def _load_mcp_registry() -> list[dict[str, Any]]:
    """Indirect access to the runtime MCP registry so tests can stub it.

    Imported lazily so that ``agentic_swmm.runtime`` is not pulled into
    the import graph at module load time (the pool is supposed to be
    cheap to import).
    """

    from agentic_swmm.runtime.registry import load_mcp_registry

    return load_mcp_registry()


def ensure_session_pool() -> MCPPool | None:
    """Lazy-instantiate and bind the per-process MCP pool.

    Idempotent: subsequent calls return the same singleton. Returns
    ``None`` (and binds nothing) when the registry is empty, so callers
    can transparently fall back to the spawn-per-call code path on a
    degraded install.
    """

    existing = session_pool()
    if existing is not None:
        return existing
    records = _load_mcp_registry()
    specs = registry_from_records(records)
    if not specs:
        return None
    pool = MCPPool(specs)
    bind_session_pool(pool)
    return pool


def bind_session_pool(pool: MCPPool) -> None:
    """Install ``pool`` as the per-process MCP session.

    Idempotent in the sense that calling twice replaces the previous pool —
    callers are expected to shutdown the previous pool first when re-binding.
    Also installs the ``atexit`` and ``SIGTERM`` cleanup hooks lazily.
    """

    global _session_pool
    _session_pool = pool
    _install_signal_handler_once()


def clear_session_pool() -> None:
    """Detach (but do not shutdown) the bound pool. Tests use this to
    return the process to spawn-per-call mode."""

    global _session_pool
    _session_pool = None


def _install_signal_handler_once() -> None:
    """Best-effort cleanup on ``SIGTERM``. Only installs in the main thread —
    Python rejects ``signal.signal`` calls from other threads with
    ``ValueError`` so we ignore that case (e.g. test workers, async loops).
    """

    global _signal_installed, _prev_sigterm_handler
    if _signal_installed:
        return
    try:
        _prev_sigterm_handler = signal.signal(signal.SIGTERM, _on_sigterm)
        _signal_installed = True
    except ValueError:
        # Not in the main thread — that's fine, atexit still runs.
        return

    import atexit

    atexit.register(_atexit_cleanup)


def _atexit_cleanup() -> None:
    pool = _session_pool
    if pool is None:
        return
    try:
        pool.shutdown()
    except Exception:
        # atexit handlers must not raise; cleanup is best-effort.
        pass


def _on_sigterm(signum: int, frame: Any) -> None:  # pragma: no cover — signal path
    pool = _session_pool
    if pool is not None:
        try:
            pool.shutdown()
        except Exception:
            pass
    # Re-raise default behaviour so the parent's exit code reflects SIGTERM.
    prev = _prev_sigterm_handler
    if callable(prev):
        try:
            prev(signum, frame)
            return
        except Exception:
            pass
    # Fall back to the platform default — terminate.
    os._exit(128 + signum)


__all__ = [
    "MCPPool",
    "MCPServerHandle",
    "ServerSpec",
    "bind_session_pool",
    "clear_session_pool",
    "ensure_session_pool",
    "registry_from_records",
    "session_pool",
]
