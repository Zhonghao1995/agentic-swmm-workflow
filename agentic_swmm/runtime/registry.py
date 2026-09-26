from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from agentic_swmm.config import mcp_registry_path, memory_registry_path, skills_registry_path
from agentic_swmm.utils.paths import resource_root


MCP_SERVERS = [
    "swmm-builder",
    "swmm-calibration",
    "swmm-climate",
    "swmm-experiment-audit",
    "swmm-gis",
    "swmm-modeling-memory",
    "swmm-network",
    "swmm-params",
    "swmm-plot",
    "swmm-runner",
    "swmm-uncertainty",
]

# All seven LLM-readable startup memory files under ``memory/initial/``
# (hand-written, shipped, loaded into the system prompt under the context
# budget). ``identification`` / ``operational`` / ``evidence`` are the eager
# core, ``soul`` / ``modeling_workflow`` / ``user_bridge`` / ``README`` the
# warm-identity context (PR #74). The README in ``memory/initial/``
# advertises this exact list, so the registry must mirror it (P1-1 in #79).
# The fallback is the pre-2026-09-06 location, so an older packaged root
# still resolves.
LONG_TERM_MEMORY_FILES = [
    ("memory/initial/identification_memory.md", "agent/memory/identification_memory.md"),
    ("memory/initial/operational_memory.md", "agent/memory/operational_memory.md"),
    ("memory/initial/evidence_memory.md", "agent/memory/evidence_memory.md"),
    ("memory/initial/soul.md", "agent/memory/soul.md"),
    ("memory/initial/modeling_workflow_memory.md", "agent/memory/modeling_workflow_memory.md"),
    ("memory/initial/user_bridge_memory.md", "agent/memory/user_bridge_memory.md"),
    ("memory/initial/README.md", "agent/memory/README.md"),
]

# Hand-maintained reference tables that ship next to the startup memory.
# Read at runtime through ``utils.paths.reference_table_path`` (a copy in
# the memory store overrides the shipped one). Never written by the program.
REFERENCE_TABLE_FILES = [
    "memory/initial/reference_benchmarks.yaml",
    "memory/initial/storm_library.yaml",
    "memory/initial/citations.yaml",
]


def discover_skills() -> list[dict[str, Any]]:
    root = resource_root()
    records = []
    for skill_file in sorted((root / "skills").glob("*/SKILL.md")):
        records.append(
            {
                "name": _skill_name(skill_file),
                "path": str(skill_file),
                "directory": str(skill_file.parent),
                "enabled": True,
            }
        )
    return records


def discover_mcp_servers() -> list[dict[str, Any]]:
    root = resource_root()
    launcher = root / "scripts" / "run_mcp_server.mjs"
    node = shutil.which("node") or sys.executable
    records = []
    for server in MCP_SERVERS:
        server_dir = root / "mcp" / server
        records.append(
            {
                "name": server,
                "enabled": True,
                "exists": server_dir.exists(),
                "command": node,
                "args": [str(launcher), server],
                "entrypoint": str(server_dir / "server.js"),
                "package": str(server_dir / "package.json"),
                "launcher": str(launcher),
            }
        )
    return records


def discover_memory_files() -> list[dict[str, Any]]:
    root = resource_root()
    records = []
    for preferred, fallback in LONG_TERM_MEMORY_FILES:
        records.append(
            _memory_record(
                root,
                _existing_relative(root, preferred, fallback),
                layer="long_term",
                load_at_startup=True,
            )
        )
    return records


def memory_layer_counts(records: list[dict[str, Any]] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records or discover_memory_files():
        layer = str(record.get("layer", "unknown"))
        counts[layer] = counts.get(layer, 0) + 1
    return counts


def _memory_record(root: Path, relative: str, *, layer: str, load_at_startup: bool) -> dict[str, Any]:
    path = root / relative
    return {
        "name": Path(relative).stem,
        "path": str(path),
        "relative_path": relative,
        "exists": path.exists(),
        "enabled": True,
        "layer": layer,
        "load_at_startup": load_at_startup,
    }


def _existing_relative(root: Path, preferred: str, fallback: str) -> str:
    if (root / preferred).exists() or not (root / fallback).exists():
        return preferred
    return fallback


def write_runtime_registries() -> tuple[Path, Path, Path]:
    skills_path = skills_registry_path()
    mcp_path = mcp_registry_path()
    memory_path = memory_registry_path()
    skills_path.parent.mkdir(parents=True, exist_ok=True)
    skills_path.write_text(json.dumps({"skills": discover_skills()}, indent=2), encoding="utf-8")
    mcp_path.write_text(json.dumps({"mcp_servers": discover_mcp_servers()}, indent=2), encoding="utf-8")
    memory_path.write_text(json.dumps({"memory_files": discover_memory_files()}, indent=2), encoding="utf-8")
    return skills_path, mcp_path, memory_path


def load_skill_registry() -> list[dict[str, Any]]:
    path = skills_registry_path()
    if not path.exists():
        return discover_skills()
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("skills", [])
    return records if isinstance(records, list) else []


def load_mcp_registry() -> list[dict[str, Any]]:
    path = mcp_registry_path()
    if not path.exists():
        return discover_mcp_servers()
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("mcp_servers", [])
    if not isinstance(records, list):
        records = []
    registered = {record.get("name") for record in records if isinstance(record, dict)}
    missing = sorted(set(MCP_SERVERS) - registered)
    if missing:
        sys.stderr.write(
            f"warn: {path} is missing MCP servers {missing}; "
            "falling back to in-process discovery from this checkout. "
            "Run `aiswmm setup` to refresh the registry.\n"
        )
        return discover_mcp_servers()
    return records


def load_memory_registry() -> list[dict[str, Any]]:
    path = memory_registry_path()
    if not path.exists():
        return discover_memory_files()
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("memory_files", [])
    if not isinstance(records, list):
        return []
    # A registry written before the memory layout moved (2026-09-06) names
    # startup files that no longer exist; without this fallback the shell
    # silently loaded no startup memory until `aiswmm setup` was rerun.
    startup = [r for r in records if isinstance(r, dict) and r.get("load_at_startup")]
    if startup and not any(Path(str(r.get("path", ""))).expanduser().exists() for r in startup):
        return discover_memory_files()
    return records


def enabled_startup_memory_files() -> list[Path]:
    files = []
    for record in load_memory_registry():
        if not record.get("enabled", True) or not record.get("load_at_startup", False):
            continue
        path = Path(str(record.get("path", ""))).expanduser()
        if path.exists() and path.is_file():
            files.append(path)
    return files


def _skill_name(skill_file: Path) -> str:
    for line in skill_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"')
    return skill_file.parent.name
