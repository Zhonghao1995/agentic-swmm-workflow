"""The one provider/model resolution seam (ADR-0009)."""
from __future__ import annotations

import pytest

from agentic_swmm.providers.selection import resolve_selection


@pytest.fixture()
def isolated_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".aiswmm").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AISWMM_CONFIG_DIR", raising=False)
    for var in ("AISWMM_OPENAI_MODEL", "AISWMM_GROQ_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return home


def _write_config(home, body: str) -> None:
    (home / ".aiswmm" / "config.toml").write_text(body, encoding="utf-8")


def test_explicit_arguments_win(isolated_home):
    _write_config(isolated_home, '[provider]\ndefault = "anthropic"\n')
    selection = resolve_selection("groq", "my-model")
    assert selection.route == "groq"
    assert selection.model == "my-model"


def test_config_default_and_model(isolated_home):
    _write_config(
        isolated_home,
        '[provider]\ndefault = "openrouter"\n\n[openrouter]\nmodel = "meta/llama-4"\n',
    )
    selection = resolve_selection()
    assert selection.route == "openrouter"
    assert selection.model == "meta/llama-4"


def test_route_table_supplies_model_when_config_has_none(isolated_home):
    """The UX fix: `aiswmm config set provider.default groq` alone must
    yield a usable model (the route default), not an error downstream."""
    _write_config(isolated_home, '[provider]\ndefault = "groq"\n')
    selection = resolve_selection()
    assert selection.route == "groq"
    assert selection.model == "llama-3.3-70b-versatile"


def test_env_model_override_wins_over_route_default(isolated_home, monkeypatch):
    _write_config(isolated_home, '[provider]\ndefault = "groq"\n')
    monkeypatch.setenv("AISWMM_GROQ_MODEL", "qwen/qwen3-32b")
    assert resolve_selection().model == "qwen/qwen3-32b"


def test_unknown_route_passes_through_for_downstream_error(isolated_home):
    _write_config(isolated_home, '[provider]\ndefault = "claude_sdk"\n')
    selection = resolve_selection()
    assert selection.route == "claude_sdk"
    assert selection.model is None


def test_defaults_with_no_config_at_all(isolated_home):
    selection = resolve_selection()
    assert selection.route == "openai"
    assert selection.model == "gpt-5.5"


def test_swmm_runtime_package_facade_resolves_lazily():
    import agentic_swmm.agent.swmm_runtime as pkg

    run_layout = pkg.run_layout
    assert run_layout.__name__.endswith("swmm_runtime.run_layout")
    assert "run_layout" in dir(pkg)
    with pytest.raises(AttributeError):
        pkg.not_a_module
