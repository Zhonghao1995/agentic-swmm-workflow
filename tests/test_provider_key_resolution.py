"""The key `aiswmm login` stores must be the key the runtime uses.

Before this suite, `login` wrote `~/.aiswmm/env`, `doctor` and
`login --status` read it, and the providers read only `os.environ`. A
user who completed the documented onboarding got "OPENAI_API_KEY is not
set" from the runtime while every diagnostic reported the key present.

These tests pin the resolver both sides now share.
"""
from __future__ import annotations

import pytest

from agentic_swmm.agent.provider_preflight import (
    provider_key_present,
    provider_key_value,
)
from agentic_swmm.providers.factory import make_provider


def _write_env_file(home, body: str) -> None:
    """Write ``~/.aiswmm/env`` under a fake ``home``."""
    aiswmm_dir = home / ".aiswmm"
    aiswmm_dir.mkdir(parents=True, exist_ok=True)
    (aiswmm_dir / "env").write_text(body, encoding="utf-8")


def test_key_stored_by_login_is_readable_by_the_runtime(monkeypatch, tmp_path):
    """A key present only in ``~/.aiswmm/env`` resolves to its value.

    This is the exact state `aiswmm login` leaves behind.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _write_env_file(home, "OPENAI_API_KEY=sk-from-login-file\n")

    assert provider_key_value("openai") == "sk-from-login-file"


def test_key_stored_in_config_toml_is_readable(monkeypatch, tmp_path):
    """The setup wizard's ``config.toml`` is the third supported home."""
    home = tmp_path / "home"
    (home / ".aiswmm").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (home / ".aiswmm" / "config.toml").write_text(
        '[openai]\napi_key = "sk-from-config"\n', encoding="utf-8"
    )

    assert provider_key_value("openai") == "sk-from-config"


@pytest.mark.parametrize(
    ("provider", "var_name", "stored"),
    [
        ("openai", "OPENAI_API_KEY", "sk-openai-from-login"),
        ("anthropic", "ANTHROPIC_API_KEY", "sk-ant-from-login"),
    ],
)
def test_make_provider_uses_the_key_login_stored(
    monkeypatch, tmp_path, provider, var_name, stored
):
    """The regression this whole suite exists for.

    `aiswmm login` stores the key in ``~/.aiswmm/env``. Constructing a
    provider must pick it up, or the user gets "<VAR> is not set" from a
    runtime that a passing `doctor` just told them was ready.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _write_env_file(home, f"{var_name}={stored}\n")

    assert make_provider(provider, model="test-model").api_key == stored


@pytest.mark.parametrize(
    ("description", "env_body", "config_body", "exported"),
    [
        ("nothing stored anywhere", None, None, None),
        ("stored in the login env file", "OPENAI_API_KEY=sk-file\n", None, None),
        ("stored in config.toml", None, '[openai]\napi_key = "sk-cfg"\n', None),
        ("exported in the shell only", None, None, "sk-shell"),
        ("exported and stored", "OPENAI_API_KEY=sk-file\n", None, "sk-shell"),
    ],
)
def test_doctor_and_the_runtime_never_disagree(
    monkeypatch, tmp_path, description, env_body, config_body, exported
):
    """`provider_key_present` must answer for the value the runtime gets.

    `doctor` and `login --status` report presence; the runtime consumes
    the value. If those can be computed differently, a diagnostic can
    pass while the run fails. Asserting the two agree in every storage
    combination makes that class of drift a test failure.
    """
    home = tmp_path / "home"
    (home / ".aiswmm").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if env_body:
        _write_env_file(home, env_body)
    if config_body:
        (home / ".aiswmm" / "config.toml").write_text(config_body, encoding="utf-8")
    if exported:
        monkeypatch.setenv("OPENAI_API_KEY", exported)

    assert provider_key_present("openai") == (provider_key_value("openai") is not None), (
        f"presence and value disagree when {description}"
    )


def test_an_exported_key_overrides_the_stored_one(monkeypatch, tmp_path):
    """A shell ``export`` wins, so a session can be redirected without
    editing files."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _write_env_file(home, "OPENAI_API_KEY=sk-stored\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported")

    assert provider_key_value("openai") == "sk-exported"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_missing_key_error_points_at_login(monkeypatch, tmp_path, provider):
    """The key-missing error must name the command that fixes it.

    It previously pointed at ``aiswmm config set openai.model``, which
    sets a model and not a key, and at ``AISWMM_OPENAI_MOCK_TOOL_CALLS``,
    which is test-only machinery no end user should be told about.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = make_provider(provider, model="test-model")

    with pytest.raises(RuntimeError) as excinfo:
        client.complete(system_prompt="s", prompt="p")

    message = str(excinfo.value)
    assert "aiswmm login" in message
    assert "MOCK" not in message.upper()
    assert "config set" not in message
