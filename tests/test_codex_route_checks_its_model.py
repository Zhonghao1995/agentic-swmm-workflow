"""The codex route checks the model it pins against what the gateway offers.

Live test 2026-09-03 (S38): the pinned gpt-5.6-sol vanished from the local
gateway while two menu siblings stayed; every session failed with a raw
HTTP 404 model_not_found and doctor still said the route was ready.
"""

from __future__ import annotations

from typing import Any

from agentic_swmm.diagnostics.doctor_report import LLMProviderStatus, render_configured_model_line
from agentic_swmm.providers.model_check import (
    model_not_found_hint,
    models_from_listing,
    offered_models,
    reconcile_model,
)
from agentic_swmm.providers.routes import ROUTES

CODEX = ROUTES["codex"]
LISTING = {"data": [{"id": "gpt-5.4"}, {"id": "gpt-5.6-terra"}, {"id": "gpt-5.6-luna"}, {"id": "gpt-image-2"}]}


def _probe_returning(payload: Any):
    seen: dict[str, Any] = {}

    def probe(url: str, headers: dict[str, str], timeout: float) -> Any:
        seen["url"] = url
        seen["headers"] = dict(headers)
        return payload

    probe.seen = seen  # type: ignore[attr-defined]
    return probe


def test_listing_ids_are_extracted() -> None:
    assert models_from_listing(LISTING) == ["gpt-5.4", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-image-2"]
    assert models_from_listing({"error": "Missing API key"}) == []
    assert models_from_listing(None) == []


def test_offered_models_sends_the_key_and_reads_the_listing() -> None:
    probe = _probe_returning(LISTING)
    offered = offered_models(CODEX, key="secret", probe=probe)
    assert offered == ("gpt-5.4", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-image-2")
    assert probe.seen["url"] == CODEX.detect_url
    assert probe.seen["headers"]["Authorization"] == "Bearer secret"


def test_offered_models_is_none_when_the_gateway_cannot_be_asked() -> None:
    assert offered_models(CODEX, key=None, probe=_probe_returning({"error": "Missing API key"})) is None
    assert offered_models(CODEX, key="k", probe=_probe_returning(None)) is None
    assert offered_models(ROUTES["openai"], key="k", probe=_probe_returning(LISTING)) is None


def test_reconcile_keeps_the_configured_model_when_offered_or_unknown() -> None:
    assert reconcile_model(CODEX, "gpt-5.6-terra", ("gpt-5.6-terra",)) == ("gpt-5.6-terra", None)
    assert reconcile_model(CODEX, "gpt-5.6-sol", None) == ("gpt-5.6-sol", None)
    assert reconcile_model(CODEX, "gpt-5.6-sol", ()) == ("gpt-5.6-sol", None)


def test_reconcile_swaps_to_an_offered_menu_sibling_and_says_so() -> None:
    model, note = reconcile_model(CODEX, "gpt-5.6-sol", ("gpt-5.4", "gpt-5.6-terra", "gpt-5.6-luna"))
    assert model == "gpt-5.6-terra"
    assert note is not None
    assert "gpt-5.6-sol is not offered" in note
    assert "using gpt-5.6-terra" in note
    assert "aiswmm setup" in note


def test_reconcile_falls_back_to_the_first_offered_model_without_a_sibling() -> None:
    model, note = reconcile_model(CODEX, "gpt-5.6-sol", ("gpt-5.4", "gpt-5.5"))
    assert model == "gpt-5.4"
    assert note is not None and "using gpt-5.4" in note


def test_model_not_found_hint_only_for_a_missing_model() -> None:
    assert "aiswmm setup" in model_not_found_hint(404, '{"error":{"code":"model_not_found"}}')
    assert model_not_found_hint(404, "no such route") == ""
    assert model_not_found_hint(500, '{"error":{"code":"model_not_found"}}') == ""


def _status(**overrides: Any) -> LLMProviderStatus:
    base = dict(
        default_provider="codex",
        openai_key_present=True,
        anthropic_key_present=False,
        default_route_ready=True,
        default_key_env="AISWMM_CODEX_API_KEY",
        configured_model="gpt-5.6-sol",
    )
    base.update(overrides)
    return LLMProviderStatus(**base)


def test_doctor_line_says_not_offered_and_names_the_offered_models() -> None:
    line = render_configured_model_line(_status(offered_models=("gpt-5.4", "gpt-5.6-terra")))
    assert "NOT offered by the gateway" in line
    assert "gpt-5.6-terra" in line
    assert "aiswmm setup" in line


def test_doctor_line_for_offered_unprobed_and_empty() -> None:
    assert "offered by the gateway" in render_configured_model_line(
        _status(offered_models=("gpt-5.6-sol", "gpt-5.6-terra"))
    )
    assert "not probed" in render_configured_model_line(_status(offered_models=None))
    assert "lists no models" in render_configured_model_line(_status(offered_models=()))


def test_session_start_swaps_the_model_and_prints_the_note(monkeypatch) -> None:
    from agentic_swmm.agent import runtime_loop
    from agentic_swmm.providers import model_check

    said: list[str] = []
    monkeypatch.setattr(runtime_loop, "_agent_say", lambda text: said.append(text))
    monkeypatch.setattr(runtime_loop, "_SWAP_NOTES_SAID", set())
    monkeypatch.setattr(
        model_check, "offered_models", lambda spec, key=None, **kw: ("gpt-5.4", "gpt-5.6-luna")
    )
    monkeypatch.setattr("agentic_swmm.agent.provider_preflight.provider_key_value", lambda name: "k")
    assert runtime_loop._reconcile_model_with_gateway("codex", "gpt-5.6-sol") == "gpt-5.6-luna"
    assert said and "using gpt-5.6-luna" in said[0]
    assert runtime_loop._reconcile_model_with_gateway("openai", "gpt-5.5") == "gpt-5.5"


def test_the_swap_note_is_said_once_per_process(monkeypatch) -> None:
    """S40: the shell reconciles per turn; the note repeated on every turn."""
    from agentic_swmm.agent import runtime_loop
    from agentic_swmm.providers import model_check

    said: list[str] = []
    monkeypatch.setattr(runtime_loop, "_agent_say", lambda text: said.append(text))
    monkeypatch.setattr(runtime_loop, "_SWAP_NOTES_SAID", set())
    monkeypatch.setattr(model_check, "offered_models", lambda spec, key=None, **kw: ("gpt-5.6-terra",))
    monkeypatch.setattr("agentic_swmm.agent.provider_preflight.provider_key_value", lambda name: "k")
    for _ in range(3):
        assert runtime_loop._reconcile_model_with_gateway("codex", "gpt-5.6-sol") == "gpt-5.6-terra"
    assert len(said) == 1
