"""The memory docs name only verbs and flags that ship (#568)."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _memory_parser():
    import argparse

    from agentic_swmm.commands.memory import register

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    register(sub)
    return parser


def _subcommands_and_flags():
    parser = _memory_parser()
    memory = next(a for a in parser._subparsers._group_actions[0].choices.values())
    verbs: dict[str, set[str]] = {}
    for action in memory._subparsers._group_actions:
        for name, sub in action.choices.items():
            verbs[name] = {opt for a in sub._actions for opt in a.option_strings}
    return verbs


def test_every_memory_verb_and_flag_in_the_docs_exists() -> None:
    verbs = _subcommands_and_flags()
    for doc in ("docs/memory_runtime_cli.md", "docs/modeling-memory-and-skill-evolution.md", "docs/memory_runtime.md"):
        text = (REPO / doc).read_text(encoding="utf-8")
        for verb, flags in re.findall(r"aiswmm memory ([a-z-]+)((?:\s+(?:--[a-z-]+|\[--[a-z-]+[^\]]*\]|<[^>]+>|\[[a-z]+\]|[A-Za-z0-9\"'.<>_-]+))*)", text):
            assert verb in verbs, f"{doc} names `aiswmm memory {verb}`, which does not exist"
            for flag in re.findall(r"--[a-z-]+", flags):
                assert flag in verbs[verb], f"{doc} names `{flag}` for `aiswmm memory {verb}`, which does not exist"
