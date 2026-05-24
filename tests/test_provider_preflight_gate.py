"""Issue #182 — env gate behaviour for ``provider_preflight``.

Two surfaces tested here:

1. **Welcome banner** (the no-provider guidance text). When the
   ``AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS`` env var is unset, the
   ``Quick fix (option 2) — Claude Pro/Max subscription`` block must
   not render; only the OpenAI block remains. When the gate is ON the
   banner keeps its full two-option shape.

2. **Runtime fallback**. A persisted ``provider.default = claude_sdk``
   in ``~/.aiswmm/config.toml`` must NOT cause the preflight to return
   ``provider_name = "claude_sdk"`` when the gate is OFF. Instead the
   preflight prints the legacy-config notice exactly once per process
   and falls through to the OpenAI tier.
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from agentic_swmm.agent import provider_preflight


_ENV_VAR = "AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(_ENV_VAR, raising=False)
    # Reset the once-per-process notice flag between tests so each
    # test starts with a clean slate.
    if hasattr(provider_preflight, "_legacy_claude_sdk_notice_emitted"):
        monkeypatch.setattr(
            provider_preflight, "_legacy_claude_sdk_notice_emitted", False, raising=False
        )
    return home


def _write_config_default(home: Path, provider: str) -> None:
    cfg_dir = home / ".aiswmm"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        f'[provider]\ndefault = "{provider}"\n', encoding="utf-8"
    )


class TestWelcomeBannerConditional:
    """The no-provider guidance banner respects the env gate."""

    def test_gate_off_omits_claude_quick_fix_block(self, isolated_home):
        result = provider_preflight.check_interactive_provider()
        msg = result.guidance_message
        assert msg, "expected guidance message when no provider configured"
        assert "claude_sdk" not in msg
        assert "Claude Pro/Max" not in msg
        assert "claude login" not in msg
        # OpenAI option remains.
        assert "OPENAI_API_KEY" in msg

    def test_gate_on_keeps_claude_quick_fix_block(self, isolated_home, monkeypatch):
        monkeypatch.setenv(_ENV_VAR, "1")
        result = provider_preflight.check_interactive_provider()
        msg = result.guidance_message
        assert msg, "expected guidance message when no provider configured"
        assert "Claude Pro/Max" in msg
        assert "claude login" in msg
        assert "OPENAI_API_KEY" in msg


class TestLegacyClaudeSdkConfigFallback:
    """A ``provider.default = claude_sdk`` config with gate OFF falls back to openai."""

    def test_gate_off_legacy_config_falls_back_to_openai(
        self, isolated_home, monkeypatch
    ):
        _write_config_default(isolated_home, "claude_sdk")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        err = io.StringIO()
        with redirect_stderr(err):
            result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is True
        assert result.provider_name == "openai"
        # The notice contains both actionable remedies.
        out = err.getvalue()
        assert "AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS" in out
        assert "aiswmm config set provider.default openai" in out

    def test_gate_on_legacy_config_still_selects_claude_sdk(
        self, isolated_home, monkeypatch
    ):
        _write_config_default(isolated_home, "claude_sdk")
        monkeypatch.setenv(_ENV_VAR, "1")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "claude_sdk"

    def test_notice_prints_once_per_process(self, isolated_home, monkeypatch):
        _write_config_default(isolated_home, "claude_sdk")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        err = io.StringIO()
        with redirect_stderr(err):
            provider_preflight.check_interactive_provider()
            provider_preflight.check_interactive_provider()
            provider_preflight.check_interactive_provider()
        out = err.getvalue()
        # The signature string appears exactly once even across three calls.
        assert out.count("AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS") == 1

    def test_falls_back_without_openai_key_present(self, isolated_home):
        # No OpenAI key, no OAuth file — gate OFF + legacy config still
        # must not return claude_sdk. The downgrade is unconditional;
        # the caller decides how to recover when no key is set.
        _write_config_default(isolated_home, "claude_sdk")
        err = io.StringIO()
        with redirect_stderr(err):
            result = provider_preflight.check_interactive_provider()
        assert result.provider_name != "claude_sdk"
