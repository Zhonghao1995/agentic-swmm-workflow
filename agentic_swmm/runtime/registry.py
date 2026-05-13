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
    "swmm-gis",
    "swmm-network",
    "swmm-params",
    "swmm-plot",
    "swmm-runner",
]

LONG_TERM_MEMORY_FILES = [
    "agent/memory/identification_memory.md",
    "agent/memory/operational_memory.md",
    "agent/memory/evidence_memory.md",
]

MODELING_MEMORY_FILES = [
    "memory/modeling-memory/modeling_memory_index.md",
    "memory/modeling-memory/lessons_learned.md",
    "memory/modeling-memory/benchmark_verification_plan.md",
    "memory/modeling-memory/skill_update_proposals.md",
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
    for relative in LONG_TERM_MEMORY_FILES:
        records.append(_memory_record(root, relative, layer="long_term", load_at_startup=True))
    for relative in MODELING_MEMORY_FILES:
        records.append(_memory_record(root, relative, layer="project_modeling", load_at_startup=False))
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
    return records if isinstance(records, list) else []


def load_memory_registry() -> list[dict[str, Any]]:
    path = memory_registry_path()
    if not path.exists():
        return discover_memory_files()
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("memory_files", [])
    return records if isinstance(records, list) else []


def enabled_skill_files() -> list[Path]:
    files = []
    for record in load_skill_registry():
        if not record.get("enabled", True):
            continue
        path = Path(str(record.get("path", ""))).expanduser()
        if path.exists() and path.is_file():
            files.append(path)
    return files


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
