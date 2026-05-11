from __future__ import annotations

import argparse

from agentic_swmm.config import load_config
from agentic_swmm.providers.openai_api import OpenAIProvider
from agentic_swmm.runtime.context import build_system_prompt


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
    system_prompt = build_system_prompt(max_chars=context_max_chars)

    initial_prompt = " ".join(args.prompt).strip()
    if initial_prompt:
        result = provider.complete(system_prompt=system_prompt, prompt=initial_prompt)
        print(result.text)
        return 0

    print("Welcome to Agentic SWMM.")
    print("Type /exit to quit.")
    while True:
        try:
            prompt = input("aiswmm> ").strip()
        except EOFError:
            print()
            return 0
        if prompt in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if not prompt:
            continue
        result = provider.complete(system_prompt=system_prompt, prompt=prompt)
        print(result.text)


def _build_provider(provider_name: str, model: str | None) -> OpenAIProvider:
    if provider_name != "openai":
        raise ValueError(f"unsupported provider for this MVP: {provider_name}")
    if not model:
        raise ValueError("OpenAI model is not configured. Run `aiswmm model --provider openai --model gpt-5.5`.")
    return OpenAIProvider(model=model)
