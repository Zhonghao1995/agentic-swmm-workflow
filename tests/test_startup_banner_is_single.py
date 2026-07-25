"""The first screen must not repeat itself.

Two renderers both claimed to be the startup banner and both fired:

    AISWMM v0.7.7  (session-225942, profile=quick)
    Last session: 8 days ago -- case "unknown"
    (/help  /exit  /new-session  --safe)
    aiswmm> aiswmm interactive (session-225942, runs/2026-07-24, profile=quick)

Session label, profile, and the slash-command hints each printed twice,
and the second block landed after the first prompt had been drawn.

The duplicate also ignored AISWMM_DISABLE_WELCOME, whose documented
acceptance is that the agent boots "with no banner / welcome / logo
printed" so scripted invocations keep their logs scoped to the run.
"""
from __future__ import annotations

import io
from unittest import mock

import pytest

from agentic_swmm.agent import runtime_loop, welcome


SESSION = "session-225942"
RUN_DIR = "runs/2026-07-24"


def _returning_banner() -> str:
    return welcome.render_returning_banner(
        session_label=SESSION,
        profile_name="quick",
        run_dir_display=RUN_DIR,
        last_session={"case_name": "demo", "end_utc": "2026-07-16T00:00:00Z"},
    )


@pytest.mark.parametrize("fact", [SESSION, "profile=quick", "/new-session"])
def test_each_fact_is_stated_once(fact):
    assert _returning_banner().count(fact) == 1


def test_the_run_directory_survived_the_merge():
    """It was the one fact only the second renderer carried."""
    assert RUN_DIR in _returning_banner()


def test_only_one_renderer_remains():
    """A second startup banner is how the duplication happened."""
    assert not hasattr(runtime_loop, "format_startup_banner")


def test_disabling_the_welcome_really_prints_nothing(monkeypatch, tmp_path):
    """The second banner used to leak through the opt-out."""
    monkeypatch.setenv("AISWMM_DISABLE_WELCOME", "1")
    buffer = io.StringIO()
    with mock.patch.object(
        welcome, "first_run_marker_path", return_value=tmp_path / "first_run.json"
    ):
        welcome.print_welcome(
            session_label=SESSION,
            profile_name="quick",
            run_dir_display=RUN_DIR,
            stream=buffer,
        )

    assert buffer.getvalue() == ""
