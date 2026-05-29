"""Packaging + docs tests for the two API-key providers.

Both providers (openai default + anthropic opt-in) are pure-stdlib
``urllib`` clients, so NO LLM SDK ships as a dependency and there is no
``[claude]`` extra. These tests assert the ``pyproject.toml`` shape and
the presence of the provider documentation page.
"""
from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )


class PyprojectProviderDepsTests(unittest.TestCase):
    def test_no_claude_agent_sdk_dependency(self) -> None:
        # The subscription SDK was removed; it must not appear in core
        # dependencies any more.
        deps = " ".join(_pyproject()["project"].get("dependencies", []))
        self.assertNotIn("claude-agent-sdk", deps)

    def test_no_claude_extra(self) -> None:
        extras = _pyproject()["project"].get("optional-dependencies", {})
        self.assertNotIn("claude", extras)

    def test_anywhere_extra_left_untouched(self) -> None:
        extras = _pyproject()["project"].get("optional-dependencies", {})
        self.assertIn("anywhere", extras)

    def test_no_llm_sdk_in_core_dependencies(self) -> None:
        # Sanity: neither the anthropic SDK nor the openai SDK is a dep —
        # both providers are raw-urllib.
        deps = " ".join(_pyproject()["project"].get("dependencies", [])).lower()
        self.assertNotIn("anthropic", deps)
        self.assertNotIn("openai", deps)


class LLMProvidersDocTests(unittest.TestCase):
    def _doc(self) -> str:
        return (REPO_ROOT / "docs" / "llm_providers.md").read_text(encoding="utf-8")

    def test_llm_providers_doc_exists(self) -> None:
        self.assertTrue((REPO_ROOT / "docs" / "llm_providers.md").is_file())

    def test_doc_mentions_both_providers(self) -> None:
        body = self._doc()
        self.assertIn("openai", body)
        self.assertIn("anthropic", body)

    def test_doc_has_no_subscription_narrative(self) -> None:
        body = self._doc().lower()
        self.assertNotIn("subscription", body)
        self.assertNotIn("claude_sdk", body)

    def test_readme_links_llm_providers_doc(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/llm_providers.md", readme)

    def test_installation_doc_links_llm_providers(self) -> None:
        installation = (REPO_ROOT / "docs" / "installation.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("llm_providers.md", installation)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
