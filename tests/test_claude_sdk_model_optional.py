"""P4: ``claude_sdk`` may run with no model configured.

Subscription-first — when the provider is ``claude_sdk`` and no model is
configured, ``run_openai_planner`` must NOT raise; it passes
``model=None`` to the factory and the SDK uses the subscription default.
OpenAI still requires an explicit model.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import agentic_swmm.agent.runtime_loop as runtime_loop


class _Outcome:
    ok = True
    plan: list = []
    results: list = []
    final_text = ""


def _run(provider_default: str, *, model_value, args_model=None):
    captured: dict = {}

    def _fake_make_provider(name, *, model=None):
        captured["name"] = name
        captured["model"] = model
        m = mock.MagicMock()
        m.model = model or "subscription-default"
        return m

    def _config_get(key, default=None):
        if key == "provider.default":
            return provider_default
        if key.endswith(".model"):
            return model_value
        return default

    fake_config = mock.MagicMock()
    fake_config.get.side_effect = _config_get

    with tempfile.TemporaryDirectory() as tmp:
        session_dir = Path(tmp) / "s"
        session_dir.mkdir()
        args = argparse.Namespace(
            provider=None,
            model=args_model,
            planner="llm",
            dry_run=True,
            verbose=False,
            max_steps=1,
        )
        with mock.patch.object(runtime_loop, "make_provider", side_effect=_fake_make_provider), \
            mock.patch.object(runtime_loop, "load_config", return_value=fake_config), \
            mock.patch.object(runtime_loop, "run_openai_plan", return_value=_Outcome()), \
            mock.patch.object(runtime_loop, "ensure_session_pool"):
            rc = runtime_loop.run_openai_planner(
                args,
                goal="hi",
                session_dir=session_dir,
                trace_path=session_dir / "t.jsonl",
                registry=mock.MagicMock(names={"doctor"}, sorted_names=lambda: ["doctor"]),
                chat_session=True,
            )
    return rc, captured


def test_claude_sdk_with_no_model_does_not_raise_and_passes_none():
    rc, captured = _run("claude_sdk", model_value=None)
    assert rc == 0
    assert captured["name"] == "claude_sdk"
    assert captured["model"] is None


def test_openai_with_no_model_raises():
    with pytest.raises(ValueError) as exc:
        _run("openai", model_value=None)
    assert "OpenAI model is not configured" in str(exc.value)
    assert "gpt-5.5" in str(exc.value)


def test_openai_with_model_is_fine():
    rc, captured = _run("openai", model_value="gpt-5.5")
    assert rc == 0
    assert captured["name"] == "openai"
    assert captured["model"] == "gpt-5.5"
