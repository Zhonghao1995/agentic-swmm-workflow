from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from agentic_swmm.config import mcp_schema_cache_dir


DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60


def cache_key(server: dict[str, Any]) -> str:
    payload = {
        "name": server.get("name"),
        "command": server.get("command"),
        "args": server.get("args", []),
        "entrypoint": server.get("entrypoint"),
        "package": server.get("package"),
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    return f"{_safe_name(str(server.get('name') or 'server'))}-{digest}"


def read_cached_tools(server: dict[str, Any], *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> list[dict[str, Any]] | None:
    path = _cache_path(server)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    created_at = float(payload.get("created_at_epoch") or 0)
    if ttl_seconds > 0 and time.time() - created_at > ttl_seconds:
        return None
    tools = payload.get("tools")
    return tools if isinstance(tools, list) else None


def write_cached_tools(server: dict[str, Any], tools: list[dict[str, Any]]) -> Path:
    cache_dir = mcp_schema_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(server)
    payload = {
        "created_at_epoch": time.time(),
        "server": {
            "name": server.get("name"),
            "command": server.get("command"),
            "args": server.get("args", []),
            "entrypoint": server.get("entrypoint"),
            "package": server.get("package"),
        },
        "tools": tools,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _cache_path(server: dict[str, Any]) -> Path:
    return mcp_schema_cache_dir() / f"{cache_key(server)}.json"


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in value.strip())
    return cleaned.strip("-") or "server"
