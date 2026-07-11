"""CLI surface tests for the ``--provider`` choice list.

Two API-key providers are supported: ``openai`` (default) and
``anthropic`` (opt-in). The ``--provider`` argparse choice list across
the ``agent`` / ``chat`` / ``model`` / ``setup`` subcommands is
``["openai", "anthropic"]``, and the config schema accepts an
``anthropic.model`` section.
"""
from __future__ import annotations

import argparse
import os
import unittest
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.cli import build_parser
from agentic_swmm.config import CONFIG_DIR_ENV, load_config, set_config_value


def _register_one(register_fn) -> argparse.ArgumentParser:
    """Build a parser carrying a single subcommand from its ``register``."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_fn(sub)
    return parser


class ProviderChoiceParsingTests(unittest.TestCase):
    def test_agent_subcommand_accepts_anthropic(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["agent", "--provider", "anthropic", "hi"])
        self.assertEqual(args.provider, "anthropic")

    def test_agent_subcommand_still_accepts_openai(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["agent", "--provider", "openai", "hi"])
        self.assertEqual(args.provider, "openai")

    # ``chat`` is a router-level alias for ``agent --planner llm`` since
    # ADR-0006 D3 deleted the stand-by commands/chat.py module; its
    # provider choices are the agent verb's (tested above) and the alias
    # rewrite itself is pinned in test_agentic_swmm_cli.py.

    def test_model_subcommand_accepts_anthropic(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["model", "--provider", "anthropic"])
        self.assertEqual(args.provider, "anthropic")

    def test_setup_subcommand_accepts_anthropic(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup", "--provider", "anthropic"])
        self.assertEqual(args.provider, "anthropic")

    def test_unknown_provider_rejected_at_parse_time(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["agent", "--provider", "bogus", "hi"])

    def test_retired_claude_sdk_rejected_at_parse_time(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["agent", "--provider", "claude_sdk", "hi"])

    def test_agent_planner_accepts_llm_and_openai_alias(self) -> None:
        parser = build_parser()
        self.assertEqual(
            parser.parse_args(["agent", "--planner", "llm", "hi"]).planner, "llm"
        )
        self.assertEqual(
            parser.parse_args(["agent", "--planner", "openai", "hi"]).planner,
            "openai",
        )


class AnthropicModelConfigRoundTripTests(unittest.TestCase):
    def test_config_set_get_round_trips_anthropic_model(self) -> None:
        snapshot = "claude-sonnet-4-6"
        with TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {CONFIG_DIR_ENV: tmp}):
                set_config_value("anthropic.model", snapshot)
                config = load_config()
                self.assertEqual(config.get("anthropic.model"), snapshot)

    def test_provider_default_round_trips_anthropic(self) -> None:
        with TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {CONFIG_DIR_ENV: tmp}):
                set_config_value("provider.default", "anthropic")
                config = load_config()
                self.assertEqual(config.get("provider.default"), "anthropic")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
