"""Doctor LLM-provider reporting tests (two API keys).

``aiswmm doctor`` carries an "LLM provider" section reporting whether
each provider's API key is present (OpenAI default + Anthropic opt-in),
and lists both keys in the runtime-knobs section. These tests drive the
doctor extension data layer directly and through the CLI.
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
from agentic_swmm.diagnostics.doctor_report import (
    collect_llm_provider_status,
    collect_optout_status,
    llm_provider_status_to_dict,
    render_llm_provider_section,
)


class _CleanKeyEnvMixin(unittest.TestCase):
    """Isolate key detection: empty ``HOME`` + cleared key env vars.

    ``provider_key_present`` reads the env var, ``~/.aiswmm/env``, and
    the config file, so we both clear the env vars *and* point
    ``Path.home()`` at a fresh tmp dir — otherwise a developer with a
    real ``~/.aiswmm/env`` key would see a phantom configured provider.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._home_patch = mock.patch.object(
            Path, "home", return_value=Path(self._tmp.name)
        )
        self._home_patch.start()
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._home_patch.stop()
        self._tmp.cleanup()


class CollectLLMProviderStatusTests(_CleanKeyEnvMixin):
    def test_reports_openai_key_present(self) -> None:
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-x"}):
            self.assertTrue(collect_llm_provider_status().openai_key_present)

    def test_reports_anthropic_key_present(self) -> None:
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant"}):
            self.assertTrue(collect_llm_provider_status().anthropic_key_present)

    def test_reports_keys_absent_when_unset(self) -> None:
        status = collect_llm_provider_status()
        self.assertFalse(status.openai_key_present)
        self.assertFalse(status.anthropic_key_present)


class RenderLLMProviderSectionTests(_CleanKeyEnvMixin):
    def test_section_shows_both_provider_rows(self) -> None:
        body = render_llm_provider_section(collect_llm_provider_status())
        self.assertIn("LLM provider", body)
        self.assertIn("OPENAI_API_KEY", body)
        self.assertIn("ANTHROPIC_API_KEY", body)

    def test_section_has_no_subscription_or_sdk_rows(self) -> None:
        body = render_llm_provider_section(collect_llm_provider_status())
        self.assertNotIn("subscription", body.lower())
        self.assertNotIn("keychain", body.lower())
        self.assertNotIn("claude_agent_sdk", body)

    def test_anthropic_row_points_at_login(self) -> None:
        body = render_llm_provider_section(collect_llm_provider_status())
        self.assertIn("aiswmm login --anthropic", body)


class DoctorJsonCarriesProviderTests(_CleanKeyEnvMixin):
    def test_json_payload_carries_both_key_flags(self) -> None:
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-x"}):
            payload = llm_provider_status_to_dict(collect_llm_provider_status())
        self.assertIn("openai_key_present", payload)
        self.assertIn("anthropic_key_present", payload)
        self.assertTrue(payload["openai_key_present"])

    def test_doctor_cli_json_includes_llm_provider_block(self) -> None:
        with TemporaryDirectory() as mem:
            with mock.patch.dict(os.environ, {"AISWMM_MEMORY_DIR": mem}):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    cli_main(["doctor", "--json"])
        payload = json.loads(out.getvalue())
        self.assertIn("llm_provider", payload)
        self.assertIn("openai_key_present", payload["llm_provider"])
        self.assertIn("anthropic_key_present", payload["llm_provider"])


class RuntimeKnobsProviderKeyTests(unittest.TestCase):
    def test_runtime_knobs_section_includes_anthropic_api_key(self) -> None:
        # ANTHROPIC_API_KEY is the opt-in provider's key, listed as a
        # runtime knob. OPENAI_API_KEY is the default provider's key and
        # is covered by the dedicated "LLM provider" doctor section.
        names = {s.env_name for s in collect_optout_status()}
        self.assertIn("ANTHROPIC_API_KEY", names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
