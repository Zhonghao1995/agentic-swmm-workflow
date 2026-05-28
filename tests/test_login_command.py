"""Tests for ``aiswmm login`` (the API-key auth subsystem).

Covers the surfaces:

* ``--status`` prints the default provider + which keys are present and
  never leaks a secret.
* ``--openai`` / ``--anthropic`` write the key to ``~/.aiswmm/env`` with
  mode 0600, set ``provider.default`` + the provider's model default,
  and never echo the key.
* a bare ``aiswmm login`` targets the current default provider's key.

No real network or Keychain access happens — keys are injected via the
``AISWMM_LOGIN_*_KEY`` env vars.
"""
from __future__ import annotations

import io
import stat
from contextlib import redirect_stdout

import pytest

from agentic_swmm.commands import login
from agentic_swmm.config import load_config, set_config_value


@pytest.fixture
def isolated_cfg(tmp_path, monkeypatch):
    """Isolate ``~/.aiswmm`` via AISWMM_CONFIG_DIR and clear key env vars."""
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AISWMM_LOGIN_OPENAI_KEY", raising=False)
    monkeypatch.delenv("AISWMM_LOGIN_ANTHROPIC_KEY", raising=False)
    return cfg


def _args(**kw):
    import argparse

    ns = argparse.Namespace(openai=False, anthropic=False, status=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestStatus:
    def test_status_reports_default_provider_and_no_secret(self, isolated_cfg):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = login.main(_args(status=True))
        out = buf.getvalue()
        assert rc == 0
        assert "default provider:" in out
        assert "OpenAI API key present:" in out
        assert "Anthropic API key present:" in out
        # No secret material in the status output.
        assert "sk-" not in out


class TestOpenAILogin:
    def test_openai_login_writes_env_0600_and_sets_config(self, isolated_cfg, monkeypatch):
        monkeypatch.setenv("AISWMM_LOGIN_OPENAI_KEY", "sk-secret-value")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = login.main(_args(openai=True))
        out = buf.getvalue()
        assert rc == 0

        env_path = isolated_cfg / "env"
        assert env_path.is_file()
        body = env_path.read_text(encoding="utf-8")
        assert 'OPENAI_API_KEY="sk-secret-value"' in body
        mode = stat.S_IMODE(env_path.stat().st_mode)
        assert mode == 0o600, oct(mode)

        cfg = load_config()
        assert cfg.get("provider.default") == "openai"
        assert cfg.get("openai.model") == "gpt-5.5"

        # The key value is NEVER echoed to stdout.
        assert "sk-secret-value" not in out

    def test_openai_login_rejects_empty_key(self, isolated_cfg, monkeypatch):
        monkeypatch.setenv("AISWMM_LOGIN_OPENAI_KEY", "   ")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = login.main(_args(openai=True))
        assert rc == 1
        assert not (isolated_cfg / "env").exists()

    def test_openai_login_preserves_other_env_lines(self, isolated_cfg, monkeypatch):
        cfg_dir = isolated_cfg
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "env").write_text(
            'export FOO="bar"\nexport OPENAI_API_KEY="old"\n', encoding="utf-8"
        )
        monkeypatch.setenv("AISWMM_LOGIN_OPENAI_KEY", "sk-new")
        with redirect_stdout(io.StringIO()):
            rc = login.main(_args(openai=True))
        assert rc == 0
        body = (cfg_dir / "env").read_text(encoding="utf-8")
        assert 'export FOO="bar"' in body
        assert 'OPENAI_API_KEY="sk-new"' in body
        assert "old" not in body


class TestAnthropicLogin:
    def test_anthropic_login_writes_env_0600_and_sets_config(self, isolated_cfg, monkeypatch):
        monkeypatch.setenv("AISWMM_LOGIN_ANTHROPIC_KEY", "sk-ant-secret")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = login.main(_args(anthropic=True))
        out = buf.getvalue()
        assert rc == 0

        env_path = isolated_cfg / "env"
        assert env_path.is_file()
        body = env_path.read_text(encoding="utf-8")
        assert 'ANTHROPIC_API_KEY="sk-ant-secret"' in body
        mode = stat.S_IMODE(env_path.stat().st_mode)
        assert mode == 0o600, oct(mode)

        cfg = load_config()
        assert cfg.get("provider.default") == "anthropic"
        assert cfg.get("anthropic.model") == "claude-sonnet-4-6"

        # The key value is NEVER echoed to stdout.
        assert "sk-ant-secret" not in out

    def test_anthropic_login_rejects_empty_key(self, isolated_cfg, monkeypatch):
        # Whitespace-only is truthy for the env-var read but empty after
        # strip — exercises the empty-key rejection without falling
        # through to the interactive getpass prompt.
        monkeypatch.setenv("AISWMM_LOGIN_ANTHROPIC_KEY", "   ")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = login.main(_args(anthropic=True))
        assert rc == 1
        assert not (isolated_cfg / "env").exists()


class TestBareLoginTargetsDefault:
    def test_bare_login_uses_default_provider_key(self, isolated_cfg, monkeypatch):
        # Pin the default to anthropic; a bare ``aiswmm login`` then
        # stores the anthropic key (no --anthropic flag needed).
        set_config_value("provider.default", "anthropic")
        monkeypatch.setenv("AISWMM_LOGIN_ANTHROPIC_KEY", "sk-ant-bare")
        with redirect_stdout(io.StringIO()):
            rc = login.main(_args())
        assert rc == 0
        body = (isolated_cfg / "env").read_text(encoding="utf-8")
        assert 'ANTHROPIC_API_KEY="sk-ant-bare"' in body

    def test_bare_login_defaults_to_openai_when_unset(self, isolated_cfg, monkeypatch):
        monkeypatch.setenv("AISWMM_LOGIN_OPENAI_KEY", "sk-default-openai")
        with redirect_stdout(io.StringIO()):
            rc = login.main(_args())
        assert rc == 0
        body = (isolated_cfg / "env").read_text(encoding="utf-8")
        assert 'OPENAI_API_KEY="sk-default-openai"' in body


class TestRegistryExtensibility:
    def test_login_handlers_registry_maps_known_providers(self):
        assert set(login._LOGIN_HANDLERS) == {"openai", "anthropic"}
        for handler in login._LOGIN_HANDLERS.values():
            assert callable(handler)
