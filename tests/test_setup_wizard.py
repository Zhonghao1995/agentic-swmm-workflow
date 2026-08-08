"""Headless drives of the interactive setup wizard (ADR-0008)."""
from __future__ import annotations

import pytest

from agentic_swmm.commands import setup
from agentic_swmm.commands.setup_wizard import (
    GATEWAY_CANDIDATES,
    detect_route,
    run_wizard,
)
from agentic_swmm.providers.routes import ROUTES


class Script:
    """Scripted ask/ask_secret with a transcript of printed lines."""

    def __init__(self, answers: list[str], secrets: list[str] | None = None):
        self._answers = list(answers)
        self._secrets = list(secrets or [])
        self.printed: list[str] = []

    def ask(self, prompt: str) -> str:
        if not self._answers:
            raise AssertionError(f"wizard asked more than scripted: {prompt!r}")
        return self._answers.pop(0)

    def ask_secret(self, prompt: str) -> str:
        if not self._secrets:
            raise AssertionError(f"wizard asked for a secret unscripted: {prompt!r}")
        return self._secrets.pop(0)

    def print_fn(self, line: str = "") -> None:
        self.printed.append(line)


def _no_probe(url, **kw):
    return None


def _verify_ok(spec, base_url, key):
    return True, "listed"


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AISWMM_CONFIG_DIR", raising=False)
    for spec in ROUTES.values():
        if spec.key_env:
            monkeypatch.delenv(spec.key_env, raising=False)


class TestHappyPaths:
    def test_default_flow_openai_with_key(self):
        # Answers: route (Enter = openai), model (Enter = default), verify
        # ("y"). No fallback question: ollama is not detected under _no_probe.
        script = Script(answers=["", "", "y"], secrets=["sk-live"])
        result = run_wizard(
            ask=script.ask,
            ask_secret=script.ask_secret,
            print_fn=script.print_fn,
            probe=_no_probe,
            verify=_verify_ok,
        )
        assert result is not None
        assert result.route == "openai"
        assert result.model == "gpt-5.5"
        assert result.api_key == "sk-live"
        assert result.base_url == ""
        assert result.fallback == ""
        assert result.verified is True

    def test_route_by_name_and_model_by_number(self):
        script = Script(answers=["openrouter", "2", "n"], secrets=["sk-or"])
        result = run_wizard(
            ask=script.ask,
            ask_secret=script.ask_secret,
            print_fn=script.print_fn,
            probe=_no_probe,
            verify=_verify_ok,
        )
        assert result is not None
        assert result.route == "openrouter"
        assert result.model == ROUTES["openrouter"].model_menu[1]
        assert result.verified is None

    def test_ollama_detected_lists_live_models_and_needs_no_key(self):
        def probe(url, **kw):
            if url == ROUTES["ollama"].detect_url:
                return {"models": [{"name": "qwen3:8b"}, {"name": "llama3.1:8b"}]}
            return None

        script = Script(answers=["ollama", "1", "n"])
        result = run_wizard(
            ask=script.ask,
            ask_secret=script.ask_secret,
            print_fn=script.print_fn,
            probe=probe,
            verify=_verify_ok,
        )
        assert result is not None
        assert result.route == "ollama"
        assert result.model == "qwen3:8b"  # from the live listing
        assert result.api_key == ""

    def test_codex_gateway_on_alternate_port_sets_base_url(self):
        alternate = GATEWAY_CANDIDATES[1]

        def probe(url, **kw):
            if url == f"{alternate}/models":
                return {"data": [{"id": "gpt-5.6-sol"}]}
            return None

        script = Script(answers=["codex", "", "n"], secrets=[""])
        result = run_wizard(
            ask=script.ask,
            ask_secret=script.ask_secret,
            print_fn=script.print_fn,
            probe=probe,
            verify=_verify_ok,
        )
        assert result is not None
        assert result.route == "codex"
        assert result.base_url == alternate
        assert result.model == "gpt-5.6-sol"

    def test_remote_primary_offers_detected_ollama_fallback(self):
        def probe(url, **kw):
            if url == ROUTES["ollama"].detect_url:
                return {"models": [{"name": "llama3.1:8b"}]}
            return None

        # route openai -> model default -> fallback yes -> verify no
        script = Script(answers=["", "", "y", "n"], secrets=["sk-live"])
        result = run_wizard(
            ask=script.ask,
            ask_secret=script.ask_secret,
            print_fn=script.print_fn,
            probe=probe,
            verify=_verify_ok,
        )
        assert result is not None
        assert result.fallback == "ollama"


class TestEdges:
    def test_custom_requires_base_url(self):
        script = Script(answers=["custom", ""])
        result = run_wizard(
            ask=script.ask,
            ask_secret=script.ask_secret,
            print_fn=script.print_fn,
            probe=_no_probe,
            verify=_verify_ok,
        )
        assert result is None

    def test_custom_full_flow(self):
        script = Script(
            answers=["custom", "http://vllm:8000/v1/", "qwen3-32b", "n"],
            secrets=[""],
        )
        result = run_wizard(
            ask=script.ask,
            ask_secret=script.ask_secret,
            print_fn=script.print_fn,
            probe=_no_probe,
            verify=_verify_ok,
        )
        assert result is not None
        assert result.route == "custom"
        assert result.base_url == "http://vllm:8000/v1"
        assert result.model == "qwen3-32b"

    def test_abort_via_eof_returns_none(self):
        def raising_ask(prompt):
            raise EOFError

        script = Script(answers=[])
        result = run_wizard(
            ask=raising_ask,
            ask_secret=script.ask_secret,
            print_fn=script.print_fn,
            probe=_no_probe,
        )
        assert result is None
        assert any("aborted" in line.lower() for line in script.printed)

    def test_missing_required_key_aborts(self):
        script = Script(answers=["deepseek", "", "y"], secrets=[""])
        result = run_wizard(
            ask=script.ask,
            ask_secret=script.ask_secret,
            print_fn=script.print_fn,
            probe=_no_probe,
            verify=_verify_ok,
        )
        assert result is None

    def test_verification_failure_still_saves(self):
        script = Script(answers=["", "", "y"], secrets=["sk-live"])
        result = run_wizard(
            ask=script.ask,
            ask_secret=script.ask_secret,
            print_fn=script.print_fn,
            probe=_no_probe,
            verify=lambda spec, base, key: (False, "HTTP 401"),
        )
        assert result is not None
        assert result.verified is False
        assert any("verification failed" in line for line in script.printed)


class TestDetection:
    def test_detect_route_reports_key_presence(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
        det = detect_route(ROUTES["openrouter"], _no_probe)
        assert det.key_found is True
        assert det.alive is None

    def test_detect_codex_probes_candidates_in_order(self):
        probed: list[str] = []

        def probe(url, **kw):
            probed.append(url)
            return None

        det = detect_route(ROUTES["codex"], probe)
        assert det.alive is False
        assert probed == [f"{base}/models" for base in GATEWAY_CANDIDATES]


class TestWizardGate:
    def _args(self, **kw):
        import argparse

        ns = argparse.Namespace(
            provider=None, model=None, fallback=None, yes=False, json=False
        )
        for key, value in kw.items():
            setattr(ns, key, value)
        return ns

    def test_any_selection_flag_suppresses_wizard(self):
        assert setup._should_run_wizard(self._args(provider="openai")) is False
        assert setup._should_run_wizard(self._args(model="m")) is False
        assert setup._should_run_wizard(self._args(fallback="ollama")) is False
        assert setup._should_run_wizard(self._args(yes=True)) is False
        assert setup._should_run_wizard(self._args(json=True)) is False

    def test_non_tty_suppresses_wizard(self, monkeypatch):
        # Under pytest stdin is not a TTY, so the bare-args gate must
        # already answer False without any patching.
        assert setup._should_run_wizard(self._args()) is False
