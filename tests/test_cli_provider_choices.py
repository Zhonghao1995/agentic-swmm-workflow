"""CLI surface tests for the ``--provider`` choice list.

``claude_sdk`` is now a first-class, default provider (the
subscription path). The ``--provider`` argparse choice list across the
``agent`` / ``chat`` / ``model`` / ``setup`` subcommands is
``["openai", "claude_sdk"]`` with no env gate, and the config schema
accepts a ``claude_sdk.model`` section.
"""
from __future__ import annotations

import argparse
import os
import unittest
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.cli import build_parser
from agentic_swmm.commands import chat as chat_cmd
from agentic_swmm.config import CONFIG_DIR_ENV, load_config, set_config_value


def _register_one(register_fn) -> argparse.ArgumentParser:
    """Build a parser carrying a single subcommand from its ``register``."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_fn(sub)
    return parser


class ProviderChoiceParsingTests(unittest.TestCase):
    def test_agent_subcommand_accepts_claude_sdk(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["agent", "--provider", "claude_sdk", "hi"])
        self.assertEqual(args.provider, "claude_sdk")

    def test_agent_subcommand_still_accepts_openai(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["agent", "--provider", "openai", "hi"])
        self.assertEqual(args.provider, "openai")

    def test_chat_subcommand_accepts_claude_sdk(self) -> None:
        # ``chat`` is dispatched via the argv router, not ``build_parser``;
        # exercise its ``register`` directly.
        parser = _register_one(chat_cmd.register)
        args = parser.parse_args(["chat", "--provider", "claude_sdk"])
        self.assertEqual(args.provider, "claude_sdk")
        with self.assertRaises(SystemExit):
            parser.parse_args(["chat", "--provider", "bogus"])

    def test_model_subcommand_accepts_claude_sdk(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["model", "--provider", "claude_sdk"])
        self.assertEqual(args.provider, "claude_sdk")

    def test_setup_subcommand_accepts_claude_sdk(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup", "--provider", "claude_sdk"])
        self.assertEqual(args.provider, "claude_sdk")

    def test_unknown_provider_rejected_at_parse_time(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["agent", "--provider", "bogus", "hi"])

    def test_agent_planner_accepts_llm_and_openai_alias(self) -> None:
        parser = build_parser()
        self.assertEqual(
            parser.parse_args(["agent", "--planner", "llm", "hi"]).planner, "llm"
        )
        self.assertEqual(
            parser.parse_args(["agent", "--planner", "openai", "hi"]).planner,
            "openai",
        )


class ClaudeSdkModelConfigRoundTripTests(unittest.TestCase):
    def test_config_set_get_round_trips_claude_sdk_model(self) -> None:
        snapshot = "claude-sonnet-4-5-20250929"
        with TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {CONFIG_DIR_ENV: tmp}):
                set_config_value("claude_sdk.model", snapshot)
                config = load_config()
                self.assertEqual(config.get("claude_sdk.model"), snapshot)

    def test_provider_default_round_trips_claude_sdk(self) -> None:
        with TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {CONFIG_DIR_ENV: tmp}):
                set_config_value("provider.default", "claude_sdk")
                config = load_config()
                self.assertEqual(config.get("provider.default"), "claude_sdk")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
