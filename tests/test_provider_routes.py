"""Invariants of the ADR-0008 route table and its resolution helpers."""
from __future__ import annotations

import pytest

from agentic_swmm.providers.routes import (
    ROUTES,
    WIRE_FORMATS,
    base_url_env_var,
    get_route,
    model_env_var,
    resolved_base_url,
    resolved_model,
    route_names,
)


class TestTableInvariants:
    def test_names_are_keys(self):
        for name, spec in ROUTES.items():
            assert spec.name == name

    def test_every_wire_is_known(self):
        for spec in ROUTES.values():
            assert spec.wire in WIRE_FORMATS, spec.name

    def test_base_url_present_except_custom(self):
        for spec in ROUTES.values():
            if spec.name == "custom":
                assert spec.base_url == ""
            else:
                assert spec.base_url.startswith("http"), spec.name
                assert not spec.base_url.endswith("/"), spec.name

    def test_default_model_present_except_open_ended_routes(self):
        open_ended = {"custom", "lmstudio"}
        for spec in ROUTES.values():
            if spec.name in open_ended:
                assert spec.default_model == ""
            else:
                assert spec.default_model, spec.name

    def test_keyed_routes_declare_key_env(self):
        for spec in ROUTES.values():
            if not spec.keyless:
                assert spec.key_env, f"{spec.name} requires a key but has no key_env"

    def test_openai_is_first_and_default_shaped(self):
        assert route_names()[0] == "openai"

    def test_get_route_unknown_raises(self):
        with pytest.raises(ValueError):
            get_route("nonsense")


class TestResolutionHelpers:
    def test_env_var_names(self):
        assert model_env_var("openai") == "AISWMM_OPENAI_MODEL"
        assert base_url_env_var("openrouter") == "AISWMM_OPENROUTER_BASE_URL"

    def test_model_precedence_env_over_config_over_table(self, monkeypatch):
        monkeypatch.delenv("AISWMM_GROQ_MODEL", raising=False)
        config = {"groq.model": "config-model"}
        assert resolved_model("groq", lambda k, d=None: config.get(k, d)) == "config-model"
        monkeypatch.setenv("AISWMM_GROQ_MODEL", "env-model")
        assert resolved_model("groq", lambda k, d=None: config.get(k, d)) == "env-model"
        monkeypatch.delenv("AISWMM_GROQ_MODEL")
        assert resolved_model("groq", lambda k, d=None: None) == ROUTES["groq"].default_model

    def test_base_url_precedence_and_slash_normalisation(self, monkeypatch):
        monkeypatch.delenv("AISWMM_DEEPSEEK_BASE_URL", raising=False)
        config = {"deepseek.base_url": "https://proxy.example/v1/"}
        assert (
            resolved_base_url("deepseek", lambda k, d=None: config.get(k, d))
            == "https://proxy.example/v1"
        )
        monkeypatch.setenv("AISWMM_DEEPSEEK_BASE_URL", "http://gw:1234/v1/")
        assert (
            resolved_base_url("deepseek", lambda k, d=None: config.get(k, d))
            == "http://gw:1234/v1"
        )
