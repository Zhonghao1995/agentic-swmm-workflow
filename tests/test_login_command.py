"""Tests for ``aiswmm login`` (the independent auth subsystem).

Covers the three surfaces:

* ``--status`` prints the auth state and never leaks a secret.
* ``--openai`` writes the key to ``~/.aiswmm/env`` with mode 0600, sets
  ``provider.default = openai`` + ``openai.model = gpt-5.5``, and never
  echoes the key.
* the bare (subscription) path shells out to ``claude login`` only when
  not already logged in, and persists ``provider.default = claude_sdk``.

The ``claude`` CLI and the subscription probe are mocked so no real
subprocess or Keychain access happens.
"""
from __future__ import annotations

import io
import os
import stat
from contextlib import redirect_stdout

import pytest

from agentic_swmm.commands import login
from agentic_swmm.config import load_config


@pytest.fixture
def isolated_cfg(tmp_path, monkeypatch):
    """Isolate ``~/.aiswmm`` via AISWMM_CONFIG_DIR and clear key env vars."""
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AISWMM_LOGIN_OPENAI_KEY", raising=False)
    return cfg


def _args(**kw):
    import argparse

    ns = argparse.Namespace(openai=False, status=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestStatus:
    def test_status_reports_default_provider_and_no_secret(
        self, isolated_cfg, monkeypatch
    ):
        monkeypatch.setattr(login, "_subscription_detected", lambda: True)
        monkeypatch.setattr(login, "_openai_key_present", lambda: False)
        monkeypatch.setattr(login.shutil, "which", lambda _name: "/usr/bin/claude")
        monkeypatch.setattr(login, "_claude_agent_sdk_importable", lambda: True)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = login.main(_args(status=True))
        out = buf.getvalue()
        assert rc == 0
        assert "default provider:" in out
        assert "claude subscription:     detected" in out
        assert "OpenAI API key present:  no" in out
        # No secret material in the status output.
        assert "sk-" not in out


class TestOpenAILogin:
    def test_openai_login_writes_env_0600_and_sets_config(
        self, isolated_cfg, monkeypatch
    ):
        monkeypatch.setenv("AISWMM_LOGIN_OPENAI_KEY", "sk-secret-value")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = login.main(_args(openai=True))
        out = buf.getvalue()
        assert rc == 0

        env_path = isolated_cfg / "env"
        assert env_path.is_file()
        # The key landed in the env file...
        body = env_path.read_text(encoding="utf-8")
        assert 'OPENAI_API_KEY="sk-secret-value"' in body
        # ...with mode 0600.
        mode = stat.S_IMODE(env_path.stat().st_mode)
        assert mode == 0o600, oct(mode)

        # Config updated.
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


class TestSubscriptionLogin:
    def test_already_logged_in_skips_claude_login_and_sets_config(
        self, isolated_cfg, monkeypatch
    ):
        monkeypatch.setattr(login.shutil, "which", lambda _name: "/usr/bin/claude")
        monkeypatch.setattr(login, "_subscription_detected", lambda: True)
        monkeypatch.setattr(login, "_claude_agent_sdk_importable", lambda: True)

        def _must_not_run(*a, **kw):  # pragma: no cover - must not fire
            raise AssertionError("claude login must not run when already logged in")

        monkeypatch.setattr(login.subprocess, "run", _must_not_run)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = login.main(_args())
        assert rc == 0
        assert "Already logged in" in buf.getvalue()
        assert load_config().get("provider.default") == "claude_sdk"

    def test_not_logged_in_shells_out_to_claude_login(self, isolated_cfg, monkeypatch):
        monkeypatch.setattr(login.shutil, "which", lambda _name: "/usr/bin/claude")
        monkeypatch.setattr(login, "_subscription_detected", lambda: False)
        monkeypatch.setattr(login, "_claude_agent_sdk_importable", lambda: True)

        captured = {}

        class _Completed:
            returncode = 0

        def _fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _Completed()

        monkeypatch.setattr(login.subprocess, "run", _fake_run)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = login.main(_args())
        assert rc == 0
        assert captured["cmd"] == ["/usr/bin/claude", "login"]
        assert load_config().get("provider.default") == "claude_sdk"

    def test_missing_claude_cli_fails_cleanly(self, isolated_cfg, monkeypatch):
        monkeypatch.setattr(login.shutil, "which", lambda _name: None)
        err = io.StringIO()
        import contextlib

        with contextlib.redirect_stderr(err):
            rc = login.main(_args())
        assert rc == 1
        assert "claude` CLI was not found" in err.getvalue()
        # Config must not be flipped when login could not proceed.
        assert load_config().get("provider.default") == "claude_sdk"  # the shipped default

    def test_sdk_missing_warns_but_succeeds(self, isolated_cfg, monkeypatch):
        monkeypatch.setattr(login.shutil, "which", lambda _name: "/usr/bin/claude")
        monkeypatch.setattr(login, "_subscription_detected", lambda: True)
        monkeypatch.setattr(login, "_claude_agent_sdk_importable", lambda: False)
        out = io.StringIO()
        err = io.StringIO()
        import contextlib

        with redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = login.main(_args())
        assert rc == 0
        assert 'pip install -e ".[claude]"' in err.getvalue()


class TestRegistryExtensibility:
    def test_login_handlers_registry_maps_known_providers(self):
        assert set(login._LOGIN_HANDLERS) == {"claude_sdk", "openai"}
        for handler in login._LOGIN_HANDLERS.values():
            assert callable(handler)
