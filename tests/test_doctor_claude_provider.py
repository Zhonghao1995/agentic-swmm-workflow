"""Doctor LLM-provider reporting tests (PRD-09 §5.4).

``aiswmm doctor`` gains an "LLM provider" section that reports whether
a Claude Code OAuth session is present, and adds ``ANTHROPIC_API_KEY``
to the runtime-knobs section. These tests drive the doctor extension
data layer directly and through the CLI.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.cli import main as cli_main
from agentic_swmm.commands.doctor_extension import (
    collect_llm_provider_status,
    collect_optout_status,
    llm_provider_status_to_dict,
    render_llm_provider_section,
)


class _ClaudeHomeMixin(unittest.TestCase):
    """Point ``Path.home()`` at a fresh tmp dir for each test.

    Also neutralises the macOS Keychain probe (the Keychain is independent
    of ``$HOME``) and clears ``ANTHROPIC_API_KEY`` so OAuth detection
    reflects only the on-disk credential file under the tmp home.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._home_patch = mock.patch.object(Path, "home", return_value=self.home)
        self._home_patch.start()
        self._keychain_patch = mock.patch(
            "agentic_swmm.agent.provider_preflight._detect_macos_keychain_credentials",
            return_value=False,
        )
        self._keychain_patch.start()
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._keychain_patch.stop()
        self._home_patch.stop()
        self._tmp.cleanup()

    def _write_oauth(self) -> None:
        claude_dir = self.home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / ".credentials.json").write_text(
            '{"token": "x"}', encoding="utf-8"
        )


class CollectLLMProviderStatusTests(_ClaudeHomeMixin):
    def test_reports_oauth_present_when_file_exists(self) -> None:
        self._write_oauth()
        status = collect_llm_provider_status()
        self.assertTrue(status.claude_oauth_present)

    def test_reports_oauth_absent_when_no_file(self) -> None:
        status = collect_llm_provider_status()
        self.assertFalse(status.claude_oauth_present)

    def test_openai_key_presence_tracked(self) -> None:
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-x"}):
            self.assertTrue(collect_llm_provider_status().openai_key_present)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            self.assertFalse(collect_llm_provider_status().openai_key_present)


class RenderLLMProviderSectionTests(_ClaudeHomeMixin):
    def test_section_shows_detected_when_oauth_exists(self) -> None:
        self._write_oauth()
        body = render_llm_provider_section(collect_llm_provider_status())
        self.assertIn("LLM provider", body)
        # Subscription row leads the section now.
        self.assertIn("Claude subscription", body)
        self.assertIn("detected", body)

    def test_section_shows_not_detected_otherwise(self) -> None:
        body = render_llm_provider_section(collect_llm_provider_status())
        self.assertIn("Claude subscription", body)
        self.assertIn("not detected", body)
        # The opt-in OpenAI row points at the login command.
        self.assertIn("aiswmm login --openai", body)

    def test_section_lists_cli_and_sdk_rows(self) -> None:
        body = render_llm_provider_section(collect_llm_provider_status())
        self.assertIn("claude CLI", body)
        self.assertIn("claude_agent_sdk", body)


class DoctorJsonCarriesProviderTests(_ClaudeHomeMixin):
    def test_json_payload_carries_oauth_flag(self) -> None:
        self._write_oauth()
        status = collect_llm_provider_status()
        payload = llm_provider_status_to_dict(status)
        self.assertIn("claude_oauth_present", payload)
        self.assertTrue(payload["claude_oauth_present"])

    def test_doctor_cli_json_includes_llm_provider_block(self) -> None:
        with TemporaryDirectory() as mem:
            with mock.patch.dict(os.environ, {"AISWMM_MEMORY_DIR": mem}):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    cli_main(["doctor", "--json"])
        payload = json.loads(out.getvalue())
        self.assertIn("llm_provider", payload)
        self.assertIn("claude_oauth_present", payload["llm_provider"])


class RuntimeKnobsAnthropicKeyTests(unittest.TestCase):
    def test_runtime_knobs_section_includes_anthropic_api_key(self) -> None:
        names = {s.env_name for s in collect_optout_status()}
        self.assertIn("ANTHROPIC_API_KEY", names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
