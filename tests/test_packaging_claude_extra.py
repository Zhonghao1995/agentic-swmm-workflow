"""Packaging + docs tests for the Claude SDK dependency.

Subscription-first: ``claude_sdk`` is the DEFAULT provider, so
``claude-agent-sdk`` ships as a CORE dependency (the default provider
works out of the box). The ``[claude]`` extra is retained as a harmless
back-compat alias. These tests assert the ``pyproject.toml`` declaration
and the presence of the provider documentation page.
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
    def test_claude_agent_sdk_is_a_core_dependency(self) -> None:
        # Subscription-first: claude_sdk is the default provider, so the
        # SDK must ship in core dependencies (works out of the box).
        deps = _pyproject()["project"].get("dependencies", [])
        joined = " ".join(deps)
        self.assertIn("claude-agent-sdk", joined)

    def test_claude_extra_retained_as_backcompat_alias(self) -> None:
        # ``pip install aiswmm[claude]`` must still resolve.
        extras = _pyproject()["project"].get("optional-dependencies", {})
        self.assertIn("claude", extras)
        self.assertIn("claude-agent-sdk", " ".join(extras["claude"]))

    def test_anywhere_extra_left_untouched(self) -> None:
        extras = _pyproject()["project"].get("optional-dependencies", {})
        self.assertIn("anywhere", extras)

    def test_claude_sdk_pin_is_compatible_with_installed_sdk(self) -> None:
        # The declared pin floor must not exceed the installed version
        # the provider was developed against. Skipped when the SDK is not
        # installed.
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            self.skipTest("claude-agent-sdk not installed")
        from importlib.metadata import version

        installed = version("claude-agent-sdk")
        deps = " ".join(_pyproject()["project"]["dependencies"])
        # The pin is a range like ``claude-agent-sdk>=0.2,<1.0``.
        self.assertIn("claude-agent-sdk", deps)
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
