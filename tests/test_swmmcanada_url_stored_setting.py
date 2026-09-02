"""SWMMCanada's URL is a persisted setting and must resolve like one.

Live finding F-01 (2026-09-02, a real-user session driven through a pty):
``aiswmm setup`` had written ``AISWMM_SWMMCANADA_URL`` to ``~/.aiswmm/env``,
yet ``aiswmm doctor`` answered "not configured" and the first
``fetch_swmm_from_canada`` failed with ``config_missing`` in any shell that
did not source that file. The planner then copied the public address out of
the error hint and passed it as ``base_url`` itself, so the opt-in the
product puts in front of the user was made by the model instead.

Three things are pinned here:

* one resolver (environment, then the env file login/setup write) shared by
  the runner, ``doctor`` and the setup opt-in, so they can never disagree;
* the reader looks at the file the writer uses (``$AISWMM_CONFIG_DIR/env``);
* with nothing stored, a human at a keyboard gets the wizard's one consent
  question in-flow, a headless caller gets the fail-soft payload, and the
  hint tells the planner to hand the choice to the user rather than to
  invent a URL.
"""

from __future__ import annotations

import urllib.error
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

URL = "https://swmm.example.test"
VAR = "AISWMM_SWMMCANADA_URL"


@pytest.fixture
def stored_url(tmp_path, monkeypatch):
    """The state ``aiswmm setup`` leaves behind: file yes, environment no."""
    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv(VAR, raising=False)
    (tmp_path / "env").write_text(f"export {VAR}={URL}/\n", encoding="utf-8")
    return URL


@pytest.fixture
def nothing_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv(VAR, raising=False)
    return tmp_path


class TestStoredEnvValue:
    def test_environment_wins_for_one_session(self, stored_url, monkeypatch):
        from agentic_swmm.agent.provider_preflight import stored_env_value

        monkeypatch.setenv(VAR, "https://override.test")
        assert stored_env_value(VAR) == "https://override.test"

    def test_the_env_file_is_the_durable_fallback(self, stored_url):
        from agentic_swmm.agent.provider_preflight import stored_env_value

        assert stored_env_value(VAR) == f"{URL}/"

    def test_nothing_stored_is_none(self, nothing_stored):
        from agentic_swmm.agent.provider_preflight import stored_env_value

        assert stored_env_value(VAR) is None

    def test_reads_the_file_login_writes(self, nothing_stored):
        # login/setup write ``$AISWMM_CONFIG_DIR/env``; before this change the
        # reader looked at ``~/.aiswmm/env`` unconditionally, so the two
        # disagreed as soon as the config dir was redirected.
        from agentic_swmm.agent.provider_preflight import stored_env_value
        from agentic_swmm.commands.login import _write_key_to_env

        written = _write_key_to_env(VAR, URL)
        assert written == nothing_stored / "env"
        assert stored_env_value(VAR) == URL

    def test_api_keys_still_resolve_through_the_same_tiers(self, nothing_stored, monkeypatch):
        from agentic_swmm.agent.provider_preflight import provider_key_value

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (nothing_stored / "env").write_text("OPENAI_API_KEY=sk-from-file\n", encoding="utf-8")
        assert provider_key_value("openai") == "sk-from-file"


class TestResolveBaseUrl:
    def test_an_explicit_argument_wins(self, stored_url):
        from agentic_swmm.integrations.swmmcanada_runner import resolve_base_url

        assert resolve_base_url("http://localhost:8000/") == "http://localhost:8000"

    def test_the_stored_setting_is_used_without_its_trailing_slash(self, stored_url):
        from agentic_swmm.integrations.swmmcanada_runner import resolve_base_url

        assert resolve_base_url() == URL

    def test_off_is_the_empty_string(self, nothing_stored):
        from agentic_swmm.integrations.swmmcanada_runner import resolve_base_url

        assert resolve_base_url() == ""

    def test_fetch_talks_to_the_stored_url(self, stored_url, tmp_path):
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, fetch_from_aoi

        seen: list[str] = []

        def opener(req, timeout=None):
            seen.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 404, "not here", {}, None)

        with pytest.raises(CanadaFetchError) as ctx:
            fetch_from_aoi(
                "{}", date(2023, 11, 1), date(2023, 11, 4),
                run_dir=tmp_path / "run", opener=opener, sleep=lambda *_: None,
            )
        assert ctx.value.stage != "config_missing"
        assert seen and seen[0].startswith(URL)

    def test_fetch_with_nothing_stored_is_config_missing(self, nothing_stored, tmp_path):
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, fetch_from_aoi

        with pytest.raises(CanadaFetchError) as ctx:
            fetch_from_aoi(
                "{}", date(2023, 11, 1), date(2023, 11, 4),
                run_dir=tmp_path / "run", opener=lambda *a, **k: None, sleep=lambda *_: None,
            )
        assert ctx.value.stage == "config_missing"
        assert "aiswmm setup" in str(ctx.value)


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestDoctorRow:
    def test_a_stored_setting_counts_as_configured(self, stored_url, monkeypatch):
        from agentic_swmm.commands.doctor import _swmmcanada_upstream_check

        seen: dict[str, str] = {}

        def fake_open(url, timeout=None):
            seen["url"] = url
            return _Resp(b'{"status": "ok"}')

        monkeypatch.setattr("urllib.request.urlopen", fake_open)
        name, ok, detail, required = _swmmcanada_upstream_check()
        assert name == "SWMMCanada upstream"
        assert ok is True
        assert "healthy" in detail
        assert seen["url"] == f"{URL}/api/v1/healthz"

    def test_nothing_stored_is_still_a_quiet_ok(self, nothing_stored):
        from agentic_swmm.commands.doctor import _swmmcanada_upstream_check

        _, ok, detail, required = _swmmcanada_upstream_check()
        assert ok is True
        assert "not configured" in detail
        assert required is False


class TestSetupOptInAgrees:
    def test_a_stored_setting_means_configured(self, stored_url):
        from agentic_swmm.commands import upstream_optin

        assert upstream_optin.is_configured() is True

    def test_nothing_stored_means_off(self, nothing_stored):
        from agentic_swmm.commands import upstream_optin

        assert upstream_optin.is_configured() is False

    def test_the_explicit_mapping_seam_is_consulted_alone(self, stored_url):
        from agentic_swmm.commands import upstream_optin

        assert upstream_optin.is_configured({}) is False
        assert upstream_optin.is_configured({VAR: "http://x"}) is True


def _fake_result(run_dir: Path, service_url: str) -> SimpleNamespace:
    return SimpleNamespace(
        inp_path=run_dir / "05_builder" / "model.inp",
        run_dir=run_dir,
        zip_path=run_dir / "10_upstream" / "swmmcanada" / "swmm_model.zip",
        service_url=service_url,
        task_id="t1",
        mode="real",
        validation={"accepted": True},
        warnings=[],
    )


class TestInFlowConsent:
    """With nothing stored, the wizard's one question moves into the call."""

    BBOX = [-123.37, 48.425, -123.36, 48.432]

    def _call(self, tmp_path: Path):
        from agentic_swmm.agent.tool_handlers.swmm_canada import fetch_swmm_from_canada_tool
        from agentic_swmm.agent.types import ToolCall

        run_dir = tmp_path / "run"
        call = ToolCall(
            "fetch_swmm_from_canada",
            {"bbox": self.BBOX, "run_dir": str(run_dir), "start_date": "2023-11-01", "end_date": "2023-11-04"},
        )
        return fetch_swmm_from_canada_tool(call, tmp_path), run_dir

    @staticmethod
    def _fetch_that_needs_a_url(calls: list):
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError

        def fake(aoi, start, end, *, run_dir, base_url=None, infiltration=None, progress=None):
            calls.append(base_url)
            if not base_url:
                raise CanadaFetchError("config_missing", "off")
            return _fake_result(Path(run_dir), base_url)

        return fake

    def test_headless_stays_fail_soft_and_never_prompts(self, nothing_stored, tmp_path, monkeypatch):
        from agentic_swmm.agent.tool_handlers import swmm_canada

        monkeypatch.setattr(swmm_canada, "_stdin_is_tty", lambda: False)
        calls: list = []
        with mock.patch("builtins.input", side_effect=AssertionError("must not prompt headless")):
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                side_effect=self._fetch_that_needs_a_url(calls),
            ):
                payload, _ = self._call(tmp_path)
        assert payload["ok"] is False
        assert payload["stage"] == "config_missing"
        assert calls == [None]

    def test_a_yes_at_the_keyboard_enables_persists_and_continues(self, nothing_stored, tmp_path, monkeypatch):
        from agentic_swmm.agent.tool_handlers import swmm_canada
        from agentic_swmm.agent.tool_handlers.swmm_canada import HOSTED_SERVICE_URL

        monkeypatch.setattr(swmm_canada, "_stdin_is_tty", lambda: True)
        calls: list = []
        with mock.patch("builtins.input", return_value="y"):
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                side_effect=self._fetch_that_needs_a_url(calls),
            ):
                payload, run_dir = self._call(tmp_path)
        assert payload["ok"] is True
        assert payload["results"]["service_url"] == HOSTED_SERVICE_URL
        assert calls == [None, HOSTED_SERVICE_URL]
        # Persisted where every later process will find it (F-01).
        assert HOSTED_SERVICE_URL in (nothing_stored / "env").read_text(encoding="utf-8")

    def test_a_no_at_the_keyboard_leaves_it_off(self, nothing_stored, tmp_path, monkeypatch):
        from agentic_swmm.agent.tool_handlers import swmm_canada

        monkeypatch.setattr(swmm_canada, "_stdin_is_tty", lambda: True)
        calls: list = []
        with mock.patch("builtins.input", return_value="n"):
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                side_effect=self._fetch_that_needs_a_url(calls),
            ):
                payload, _ = self._call(tmp_path)
        assert payload["ok"] is False
        assert payload["stage"] == "config_missing"
        assert calls == [None]
        assert not (nothing_stored / "env").exists()

    def test_an_explicit_base_url_is_never_second_guessed(self, nothing_stored, tmp_path, monkeypatch):
        from agentic_swmm.agent.tool_handlers import swmm_canada
        from agentic_swmm.agent.tool_handlers.swmm_canada import fetch_swmm_from_canada_tool
        from agentic_swmm.agent.types import ToolCall
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError

        monkeypatch.setattr(swmm_canada, "_stdin_is_tty", lambda: True)
        call = ToolCall(
            "fetch_swmm_from_canada",
            {"bbox": self.BBOX, "run_dir": str(tmp_path / "run"), "base_url": "http://mine:8000",
             "start_date": "2023-11-01", "end_date": "2023-11-04"},
        )
        with mock.patch("builtins.input", side_effect=AssertionError("must not prompt")):
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                side_effect=CanadaFetchError("config_missing", "odd"),
            ):
                payload = fetch_swmm_from_canada_tool(call, tmp_path)
        assert payload["ok"] is False


class TestHintHandsTheChoiceToTheUser:
    def test_the_hint_no_longer_invites_the_planner_to_pass_base_url(self):
        from agentic_swmm.agent.tool_handlers.swmm_canada import HOSTED_SERVICE_URL, _stage_hint

        hint = _stage_hint("config_missing")
        assert "aiswmm setup" in hint
        assert HOSTED_SERVICE_URL in hint
        assert "sends the area" in hint
        assert "Tell the user" in hint
        assert "or pass base_url" not in hint
