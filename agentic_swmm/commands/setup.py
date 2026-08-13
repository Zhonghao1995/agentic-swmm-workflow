from __future__ import annotations

import argparse
import argparse as _argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from agentic_swmm.agent.experimental_providers import (
    available_provider_choices,
    provider_help_text,
)
from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.config import DEFAULT_PROVIDER, load_config, mcp_registry_path, setup_state_path, write_config
from agentic_swmm.commands.doctor import _which_swmm5
from agentic_swmm.runtime.registry import (
    discover_mcp_servers,
    discover_memory_files,
    discover_skills,
    memory_layer_counts,
    write_runtime_registries,
)
from agentic_swmm.utils.paths import resource_root


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("setup", help="Initialize the local Agentic SWMM orchestration layer.")
    parser.add_argument(
        "--provider",
        choices=available_provider_choices(),
        default=None,
        help=provider_help_text(
            "Default provider route (skips the interactive wizard)."
        ),
    )
    parser.add_argument("--model", default=None, help="Default model for the provider.")
    parser.add_argument(
        "--fallback",
        choices=available_provider_choices(),
        default=None,
        help=(
            "Optional fallback route engaged when the primary fails "
            "(auth/quota/connection), e.g. a local ollama."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Accept defaults non-interactively (never start the wizard).",
    )
    parser.add_argument("--obsidian-dir", type=Path, help="Optional Obsidian vault or folder for audit and memory exports.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable setup state.")
    # Issue #114: no-prompt path that only regenerates mcp.json against
    # the current editable install. Lets the user re-align after moving
    # the install between checkouts without re-running the full
    # interactive setup.
    parser.add_argument(
        "--refresh-mcp",
        action="store_true",
        help=(
            "Regenerate ~/.aiswmm/mcp.json against the active editable "
            "install and exit. Does not touch any other ~/.aiswmm/ file."
        ),
    )
    register_example_flag(parser, example_text="aiswmm setup --provider openai")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    if getattr(args, "refresh_mcp", False):
        return _refresh_mcp_only()

    provider = args.provider
    model = args.model
    fallback = getattr(args, "fallback", None)
    wizard_base_url = ""
    if _should_run_wizard(args):
        import getpass as _getpass

        from agentic_swmm.commands.setup_wizard import run_wizard

        # The two managed-gateway callables are passed explicitly: run_wizard
        # deliberately does not default them, so no caller can start a 58 MB
        # download or a browser OAuth flow by omission.
        from agentic_swmm.commands import gateway as _gateway

        result = run_wizard(
            ask=input,
            ask_secret=_getpass.getpass,
            install_gateway=_gateway.install_gateway,
            gateway_login=lambda: _gateway.main(
                _argparse.Namespace(gateway_action="login")
            ),
        )
        if result is None:
            return 1
        provider = result.route
        model = result.model
        fallback = result.fallback or None
        wizard_base_url = result.base_url
        if result.api_key:
            from agentic_swmm.commands.login import _write_key_to_env
            from agentic_swmm.providers.routes import ROUTES

            env_path = _write_key_to_env(ROUTES[provider].key_env, result.api_key)
            print(f"Stored the {provider} API key in {env_path} (mode 0600).")
        print()
    provider = provider or DEFAULT_PROVIDER

    config = load_config()
    values = config.values
    values.setdefault("provider", {})["default"] = provider
    if fallback:
        values.setdefault("provider", {})["fallback"] = fallback
    if model:
        values.setdefault(provider, {})["model"] = model
    if wizard_base_url:
        values.setdefault(provider, {})["base_url"] = wizard_base_url
    if args.obsidian_dir:
        values.setdefault("obsidian", {})["dir"] = str(args.obsidian_dir.expanduser().resolve())
    config_file = write_config(values, config.path)
    skills_file, mcp_file, memory_file = write_runtime_registries()

    state = _build_setup_state(
        config_file=config_file,
        skills_file=skills_file,
        mcp_file=mcp_file,
        memory_file=memory_file,
        provider=provider,
        model=model or values.get(provider, {}).get("model"),
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
    from agentic_swmm.agent.provider_preflight import provider_key_present

    openai_key = provider_key_present("openai")
    anthropic_key = provider_key_present("anthropic")
    route_ready = provider_key_present(provider)
    checks = [
        _check("repo resources", resource_root().exists(), str(resource_root()), required=True),
        _check("python package", True, "agentic_swmm importable", required=True),
        _check("node executable", shutil.which("node") is not None, shutil.which("node") or "missing; needed for MCP launchers", required=False),
        _check("swmm5 executable", _which_swmm5() is not None, _which_swmm5() or "missing; needed for SWMM execution", required=False),
        # The two original API-key providers stay visible; the selected
        # route (which may be any of the ADR-0008 routes) gets its own row.
        _check(
            "OPENAI_API_KEY",
            openai_key,
            "set" if openai_key else "not set; set via `aiswmm login --openai`",
            required=False,
        ),
        _check(
            "ANTHROPIC_API_KEY",
            anthropic_key,
            "set" if anthropic_key else "not set; opt-in via `aiswmm login --anthropic`",
            required=False,
        ),
        _check(
            "provider route",
            route_ready,
            f"{provider}: " + ("ready" if route_ready else f"needs `aiswmm login {provider}`"),
            required=False,
        ),
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
            "aiswmm login",
            "aiswmm doctor",
            "aiswmm \"Explain what this Agentic SWMM installation can do\"",
            "aiswmm run --inp examples/tecnopolo/tecnopolo_r1_199401.inp --run-dir runs/tecnopolo-cli --node OUT_0",
            "aiswmm audit --run-dir runs/tecnopolo-cli",
            "aiswmm memory --runs-dir runs --out-dir memory/modeling-memory",
        ],
    }


def _should_run_wizard(args: argparse.Namespace) -> bool:
    """Interactive wizard iff bare `aiswmm setup` on a real terminal.

    Any explicit selection flag (``--provider`` / ``--model`` /
    ``--fallback``), automation flag (``--yes`` / ``--json``), or a
    non-TTY stdin/stdout keeps the historical non-interactive behavior
    byte-for-byte (CI and scripts never see prompts).
    """
    if args.provider is not None or args.model or getattr(args, "fallback", None):
        return False
    if getattr(args, "yes", False) or getattr(args, "json", False):
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _refresh_mcp_only() -> int:
    """Regenerate ``~/.aiswmm/mcp.json`` against the active repo_root().

    Issue #114: the full setup re-writes config.toml, skills.json,
    memory.json, setup_state.json, and mcp.json. This is too heavy when
    the user only needs to re-align the MCP launcher paths after
    re-installing the editable package from a different checkout.

    The contract is intentionally narrow: rewrite ``mcp.json``, leave
    everything else untouched, print where the file landed.
    """

    mcp_path = mcp_registry_path()
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mcp_servers": discover_mcp_servers()}
    mcp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Refreshed mcp.json: {mcp_path}")
    print(f"Active repo root: {resource_root()}")
    print(f"Servers registered: {len(payload['mcp_servers'])}")
    return 0


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
