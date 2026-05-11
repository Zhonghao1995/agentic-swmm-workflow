from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from agentic_swmm.config import load_config, setup_state_path, write_config
from agentic_swmm.commands.doctor import _which_swmm5
from agentic_swmm.runtime.registry import discover_mcp_servers, discover_memory_files, discover_skills, memory_layer_counts, write_runtime_registries
from agentic_swmm.utils.paths import resource_root


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("setup", help="Initialize the local Agentic SWMM orchestration layer.")
    parser.add_argument("--provider", choices=["openai"], default="openai", help="Default provider.")
    parser.add_argument("--model", default=None, help="Default model for the provider.")
    parser.add_argument("--obsidian-dir", type=Path, help="Optional Obsidian vault or folder for audit and memory exports.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable setup state.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    config = load_config()
    values = config.values
    values.setdefault("provider", {})["default"] = args.provider
    if args.model:
        values.setdefault(args.provider, {})["model"] = args.model
    if args.obsidian_dir:
        values.setdefault("obsidian", {})["dir"] = str(args.obsidian_dir.expanduser().resolve())
    config_file = write_config(values, config.path)
    skills_file, mcp_file, memory_file = write_runtime_registries()

    state = _build_setup_state(
        config_file=config_file,
        skills_file=skills_file,
        mcp_file=mcp_file,
        memory_file=memory_file,
        provider=args.provider,
        model=args.model or values.get(args.provider, {}).get("model"),
    )
    state_file = setup_state_path()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(state, indent=2))
    else:
        _print_setup_report(state)
    return 0 if state["status"] in {"ready", "ready_with_warnings"} else 1


def _build_setup_state(*, config_file: Path, skills_file: Path, mcp_file: Path, memory_file: Path, provider: str, model: str | None) -> dict:
    skills = discover_skills()
    mcp_servers = discover_mcp_servers()
    memory_files = discover_memory_files()
    memory_counts = memory_layer_counts(memory_files)
    checks = [
        _check("repo resources", resource_root().exists(), str(resource_root()), required=True),
        _check("python package", True, "agentic_swmm importable", required=True),
        _check("node executable", shutil.which("node") is not None, shutil.which("node") or "missing; needed for MCP launchers", required=False),
        _check("swmm5 executable", _which_swmm5() is not None, _which_swmm5() or "missing; needed for SWMM execution", required=False),
        _check("OPENAI_API_KEY", bool(os.environ.get("OPENAI_API_KEY")), "set" if os.environ.get("OPENAI_API_KEY") else "not set; needed for real OpenAI chat", required=False),
        _check("skills", all(Path(record["path"]).exists() for record in skills), f"registered {len(skills)} skill(s)", required=True),
        _check("mcp servers", all(record["exists"] for record in mcp_servers), f"registered {len(mcp_servers)} MCP server(s)", required=True),
        _check("memory package", all(record["exists"] for record in memory_files), _format_memory_detail(memory_counts), required=True),
    ]
    required_ok = all(check["ok"] or not check["required"] for check in checks)
    warnings = [check for check in checks if not check["ok"] and not check["required"]]
    status = "ready" if required_ok and not warnings else "ready_with_warnings" if required_ok else "incomplete"
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "resource_root": str(resource_root()),
        "provider": {"default": provider, "model": model},
        "registries": {
            "config": str(config_file),
            "skills": str(skills_file),
            "mcp": str(mcp_file),
            "memory": str(memory_file),
            "setup_state": str(setup_state_path()),
        },
        "resources": {
            "skills": len(skills),
            "mcp_servers": len(mcp_servers),
            "memory_files": len(memory_files),
            "memory_layers": memory_counts,
        },
        "checks": checks,
        "next_commands": [
            "export OPENAI_API_KEY=...",
            "aiswmm doctor",
            f"aiswmm chat --provider {provider} \"Explain what this Agentic SWMM installation can do\"",
            "aiswmm run --inp examples/tecnopolo/tecnopolo_r1_199401.inp --run-dir runs/tecnopolo-cli --node OUT_0",
            "aiswmm audit --run-dir runs/tecnopolo-cli",
            "aiswmm memory --runs-dir runs --out-dir memory/modeling-memory",
        ],
    }


def _check(name: str, ok: bool, detail: str, *, required: bool) -> dict:
    return {"name": name, "ok": ok, "detail": detail, "required": required}


def _format_memory_detail(counts: dict[str, int]) -> str:
    parts = [f"{layer}={count}" for layer, count in sorted(counts.items())]
    return f"registered {sum(counts.values())} memory file(s): " + ", ".join(parts)


def _print_setup_report(state: dict) -> None:
    print("Agentic SWMM local setup")
    print()
    for index, check in enumerate(state["checks"], start=1):
        status = "OK" if check["ok"] else ("MISSING" if check["required"] else "WARN")
        print(f"[{index}/{len(state['checks'])}] {check['name']:<16} {status:7} {check['detail']}")
    print()
    print(f"Status: {state['status']}")
    print(f"Config: {state['registries']['config']}")
    print(f"Skills registry: {state['registries']['skills']}")
    print(f"MCP registry: {state['registries']['mcp']}")
    print(f"Memory registry: {state['registries']['memory']}")
    print(f"Setup state: {state['registries']['setup_state']}")
    print()
    print("Next:")
    for command in state["next_commands"]:
        print(f"  {command}")
