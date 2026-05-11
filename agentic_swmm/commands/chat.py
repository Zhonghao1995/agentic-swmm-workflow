from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from agentic_swmm.config import load_config
from agentic_swmm.providers.openai_api import OpenAIProvider
from agentic_swmm.runtime.context import build_system_prompt
from agentic_swmm.utils.paths import repo_root


CHAT_MODE_PROMPT = """Runtime mode:
- You are running inside the local Agentic SWMM CLI chat command.
- This chat command can answer, inspect provided project context, and give exact local commands, but it does not receive shell tools.
- Do not claim that the whole Agentic SWMM runtime lacks local filesystem or command access.
- If a user asks you to execute a SWMM run, QA, audit, plot, or memory refresh, explain that chat mode cannot execute it directly and give the exact `agentic-swmm run`, `agentic-swmm audit`, `agentic-swmm plot`, or `agentic-swmm agent` command path.
- Prefer wording like "chat mode cannot execute that directly" instead of "I do not have local shell access".
"""


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("chat", help="Start a local Agentic SWMM chat using a configured provider.")
    parser.add_argument("prompt", nargs="*", help="Prompt text. If omitted, starts an interactive loop.")
    parser.add_argument("--provider", choices=["openai"], help="Provider to use. Defaults to config provider.default.")
    parser.add_argument("--model", help="Model override for this request.")
    parser.add_argument("--context-max-chars", type=int, help="Maximum project context characters to preload.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    config = load_config()
    provider_name = args.provider or config.get("provider.default", "openai")
    model = args.model or config.get(f"{provider_name}.model")
    context_max_chars = args.context_max_chars or int(config.get("runtime.context_max_chars", 20000))
    provider = _build_provider(provider_name, model)
    system_prompt = build_system_prompt(max_chars=context_max_chars) + "\n\n" + CHAT_MODE_PROMPT.strip()

    initial_prompt = " ".join(args.prompt).strip()
    if initial_prompt:
        result = provider.complete(system_prompt=system_prompt, prompt=initial_prompt)
        print(result.text)
        return 0

    print("Welcome to Agentic SWMM.")
    print("Chat mode can explain workflows and write exact commands; use `agentic-swmm agent` for tool-executing runs.")
    print("Type /exit to quit.\n")
    while True:
        try:
            prompt = input("you> ").strip()
        except EOFError:
            print()
            return 0
        if prompt in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if not prompt:
            continue
        local_result = _try_handle_local_request(prompt)
        if local_result is not None:
            _print_assistant_message(local_result)
            continue
        result = provider.complete(system_prompt=system_prompt, prompt=prompt)
        _print_assistant_message(result.text)


def _build_provider(provider_name: str, model: str | None) -> OpenAIProvider:
    if provider_name != "openai":
        raise ValueError(f"unsupported provider for this MVP: {provider_name}")
    if not model:
        raise ValueError("OpenAI model is not configured. Run `aiswmm model --provider openai --model gpt-5.5`.")
    return OpenAIProvider(model=model)


def _print_assistant_message(text: str) -> None:
    print("\naiswmm:")
    print(text.rstrip())
    print()


def _try_handle_local_request(prompt: str) -> str | None:
    paths = _extract_existing_paths(prompt)
    if not paths:
        return None
    if not _mentions_swmm_input(prompt):
        return None

    inp_files = _resolve_inp_files(paths)
    if not inp_files:
        return "I can see the path, but I did not find any `.inp` files under it."

    if not _asks_to_execute(prompt):
        lines = ["I can see these `.inp` file(s):", ""]
        lines.extend(f"- {path}" for path in inp_files[:20])
        if len(inp_files) > 20:
            lines.append(f"- ... {len(inp_files) - 20} more")
        lines.append("")
        lines.append("Say `run this inp` with the path if you want me to execute run + audit.")
        return "\n".join(lines)

    if len(inp_files) > 1:
        selected = _select_preferred_inp(inp_files)
        selection_note = f"Found {len(inp_files)} `.inp` files. I selected `{selected.name}`."
    else:
        selected = inp_files[0]
        selection_note = f"Found `.inp`: `{selected}`."

    node = _first_outfall_node(selected) or "O1"
    run_dir = repo_root() / "runs" / _safe_run_name(selected.stem)
    run_result = _run_subcommand(["run", "--inp", str(selected), "--run-dir", str(run_dir), "--node", node])
    audit_result = None
    if run_result.returncode == 0:
        audit_result = _run_subcommand(
            [
                "audit",
                "--run-dir",
                str(run_dir),
                "--workflow-mode",
                "prepared-input-chat",
                "--objective",
                prompt,
            ]
        )

    return _format_local_execution_result(
        selection_note=selection_note,
        inp=selected,
        node=node,
        run_dir=run_dir,
        run_result=run_result,
        audit_result=audit_result,
    )


def _extract_existing_paths(prompt: str) -> list[Path]:
    raw_paths = re.findall(r"(?:~|/)[^`'\"，。；;\n]+", prompt)
    paths: list[Path] = []
    for raw in raw_paths:
        resolved = _longest_existing_path(raw)
        if resolved is None:
            continue
        if resolved.exists() and resolved not in paths:
            paths.append(resolved)
    return paths


def _longest_existing_path(raw: str) -> Path | None:
    candidate = raw.strip().rstrip(".,:;)]}")
    while candidate:
        path = Path(candidate).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = None
        if resolved is not None and resolved.exists():
            return resolved
        if " " not in candidate:
            return None
        candidate = candidate.rsplit(" ", 1)[0].rstrip(".,:;)]}")
    return None


def _mentions_swmm_input(prompt: str) -> bool:
    lowered = prompt.lower()
    return ".inp" in lowered or "inp" in lowered or "swmm" in lowered or "路径" in prompt


def _asks_to_execute(prompt: str) -> bool:
    lowered = prompt.lower()
    execute_words = ("run", "execute", "运行", "跑", "执行", "帮我运行", "帮我跑")
    inspect_only_words = ("can you see", "能看到", "有没有", "list", "列出", "查看")
    if any(word in prompt for word in ("帮我运行", "帮我跑")):
        return True
    if any(word in lowered for word in execute_words):
        return True
    if any(word in lowered for word in inspect_only_words) or any(word in prompt for word in inspect_only_words):
        return False
    return False


def _resolve_inp_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix.lower() == ".inp":
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(item for item in path.rglob("*.inp") if item.is_file()))
    return sorted(dict.fromkeys(files))


def _select_preferred_inp(inp_files: list[Path]) -> Path:
    tecnopolo = [path for path in inp_files if "tecnopolo" in str(path).lower()]
    return sorted(tecnopolo or inp_files)[0]


def _first_outfall_node(inp: Path) -> str | None:
    in_section = False
    for line in inp.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.upper() == "[OUTFALLS]":
            in_section = True
            continue
        if in_section and stripped.startswith("["):
            return None
        if in_section:
            return stripped.split()[0]
    return None


def _run_subcommand(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", *args],
        cwd=repo_root(),
        capture_output=True,
        text=True,
    )


def _format_local_execution_result(
    *,
    selection_note: str,
    inp: Path,
    node: str,
    run_dir: Path,
    run_result: subprocess.CompletedProcess[str],
    audit_result: subprocess.CompletedProcess[str] | None,
) -> str:
    lines = [
        selection_note,
        f"Input: `{inp}`",
        f"Node/outfall: `{node}`",
        f"Run directory: `{run_dir}`",
        "",
        f"`agentic-swmm run`: {'PASS' if run_result.returncode == 0 else 'FAIL'}",
    ]
    if run_result.stdout.strip():
        lines.extend(["", _tail(run_result.stdout)])
    if run_result.stderr.strip():
        lines.extend(["", "stderr:", _tail(run_result.stderr)])
    if run_result.returncode != 0:
        return "\n".join(lines)

    if audit_result is not None:
        lines.append("")
        lines.append(f"`agentic-swmm audit`: {'PASS' if audit_result.returncode == 0 else 'FAIL'}")
        if audit_result.stdout.strip():
            lines.extend(["", _summarize_audit_stdout(audit_result.stdout)])
        if audit_result.stderr.strip():
            lines.extend(["", "audit stderr:", _tail(audit_result.stderr)])

    expected = [
        run_dir / "05_runner" / "model.rpt",
        run_dir / "05_runner" / "model.out",
        run_dir / "05_runner" / "manifest.json",
        run_dir / "06_qa" / "qa_summary.json",
        run_dir / "experiment_provenance.json",
        run_dir / "comparison.json",
        run_dir / "experiment_note.md",
    ]
    existing = [path for path in expected if path.exists()]
    if existing:
        lines.extend(["", "Generated evidence files:"])
        lines.extend(f"- {path}" for path in existing)
    return "\n".join(lines)


def _summarize_audit_stdout(stdout: str) -> str:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return _tail(stdout)
    if not isinstance(payload, dict):
        return _tail(stdout)
    keys = ("experiment_provenance", "comparison", "experiment_note", "named_audit_artifacts")
    summary = {key: payload.get(key) for key in keys if payload.get(key)}
    return json.dumps(summary, indent=2) if summary else _tail(stdout)


def _tail(text: str, max_chars: int = 3000) -> str:
    stripped = text.strip()
    return stripped[-max_chars:]


def _safe_run_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "prepared-inp"
