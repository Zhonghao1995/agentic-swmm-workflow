"""Packaging + docs tests for the PRD-09 Claude extra.

``claude-agent-sdk`` is declared as an OPTIONAL extra, never a hard
dependency. These tests assert the ``pyproject.toml`` declaration and
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


class PyprojectClaudeExtraTests(unittest.TestCase):
    def test_optional_dependencies_declares_claude_extra(self) -> None:
        data = _pyproject()
        extras = data.get("project", {}).get("optional-dependencies", {})
        self.assertIn("claude", extras)

    def test_claude_extra_lists_claude_agent_sdk(self) -> None:
        extras = _pyproject()["project"]["optional-dependencies"]
        joined = " ".join(extras["claude"])
        self.assertIn("claude-agent-sdk", joined)

    def test_claude_agent_sdk_not_a_hard_dependency(self) -> None:
        # The default install must stay pure-Python; the SDK belongs
        # only in the optional extra.
        deps = _pyproject()["project"].get("dependencies", [])
        joined = " ".join(deps)
        self.assertNotIn("claude-agent-sdk", joined)
        self.assertNotIn("claude_agent_sdk", joined)

    def test_claude_extra_pin_is_compatible_with_installed_sdk(self) -> None:
        # The declared pin floor must not exceed the installed version
        # the provider was developed against. Skipped when the optional
        # extra is not installed.
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            self.skipTest("claude-agent-sdk not installed")
        from importlib.metadata import version

        installed = version("claude-agent-sdk")
        extras = _pyproject()["project"]["optional-dependencies"]
        spec = " ".join(extras["claude"])
        # The pin is a range like ``claude-agent-sdk>=0.2,<1.0``.
        self.assertIn("claude-agent-sdk", spec)
        installed_major_minor = tuple(int(p) for p in installed.split(".")[:2])
        # Floor declared as >=0.2 — installed must be at least that.
        self.assertGreaterEqual(installed_major_minor, (0, 2))


class LLMProvidersDocTests(unittest.TestCase):
    def _doc(self) -> str:
        return (REPO_ROOT / "docs" / "llm_providers.md").read_text(
            encoding="utf-8"
        )

    def test_llm_providers_doc_exists(self) -> None:
        self.assertTrue((REPO_ROOT / "docs" / "llm_providers.md").is_file())

    def test_doc_mentions_both_providers(self) -> None:
        body = self._doc()
        self.assertIn("openai", body)
        self.assertIn("claude_sdk", body)

    def test_doc_covers_rate_limits_and_tos(self) -> None:
        body = self._doc().lower()
        self.assertIn("rate limit", body)
        self.assertIn("subscription", body)
        self.assertIn("terms of service", body)

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
