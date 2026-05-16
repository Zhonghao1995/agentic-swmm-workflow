"""Unit tests for ``intent_disambiguator`` (issue #111).

Spec: when the planner's keyword fast-path is ambiguous on plot-conflict
goals (``wants_plot`` co-occurring with another action verb), the
disambiguator makes one lightweight LLM call via the fake-tool enum
mechanism and returns the LLM-picked mode string. On any failure path
(no trigger, invalid mode, timeout, arbitrary exception) it returns
``None`` so the planner can fall through to the (priority-fixed)
keyword classifier.

These tests pin the public interface — they do NOT assert on the LLM
provider payload shape so the implementation is free to reshape its
private prompt format without breaking tests.
"""

from __future__ import annotations

from typing import Any

from agentic_swmm.agent.intent_disambiguator import disambiguate
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


class _StubProvider:
    """Minimal stand-in for ``OpenAIProvider``.

    Records the calls it received so trigger-condition tests can assert
    "the provider was not invoked" without depending on internal call
    counters in the production code.
    """

    def __init__(self, response: ProviderToolResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "input_items": input_items,
                "tools": tools,
                "previous_response_id": previous_response_id,
            }
        )
        return self._response


def _mode_response(mode: str) -> ProviderToolResponse:
    return ProviderToolResponse(
        text="",
        model="stub",
        response_id="r1",
        tool_calls=[
            ProviderToolCall(
                call_id="c1",
                name="classify_workflow_mode",
                arguments={"mode": mode},
            )
        ],
        raw={},
    )


def test_compound_run_plus_plot_routes_through_llm() -> None:
    """The exact regression scenario from runs/2026-05-16/120740_todcreek_run:
    a user types "run X demo and plot the figure". ``wants_plot`` and
    ``wants_demo`` both fire — keyword fast-path is ambiguous — so the
    disambiguator calls the provider and returns ``prepared_demo``."""

    provider = _StubProvider(_mode_response("prepared_demo"))
    signals = {
        "wants_plot": True,
        "wants_demo": True,
        "wants_run": True,
        "wants_calibration": False,
        "wants_uncertainty": False,
    }

    mode = disambiguate(
        goal="run Tod Creek demo and plot the figure",
        signals=signals,
        provider=provider,
    )

    assert mode == "prepared_demo"
    assert len(provider.calls) == 1, "provider must be called exactly once on conflict"


def test_plot_only_does_not_call_provider() -> None:
    """A "plot the previous run" prompt is unambiguous: there is no
    second action verb to disambiguate against. The disambiguator must
    short-circuit to ``None`` without spending an LLM call so the
    deterministic SOP fast-path is preserved for the unambiguous case
    (user story 2: paper-grade reproducibility)."""

    provider = _StubProvider(_mode_response("prepared_demo"))
    signals = {
        "wants_plot": True,
        "wants_demo": False,
        "wants_run": False,
        "wants_calibration": False,
        "wants_uncertainty": False,
    }

    mode = disambiguate(
        goal="plot the previous run",
        signals=signals,
        provider=provider,
    )

    assert mode is None
    assert provider.calls == [], "no provider call expected when trigger does not fire"


def test_provider_returns_invalid_mode_yields_none() -> None:
    """If the LLM hallucinates a mode outside the 7-value enum the
    disambiguator must NOT return it — callers rely on the return
    value being either a valid enum value or ``None``."""

    provider = _StubProvider(_mode_response("definitely_not_a_real_mode"))
    signals = {
        "wants_plot": True,
        "wants_demo": True,
        "wants_run": True,
        "wants_calibration": False,
        "wants_uncertainty": False,
    }

    mode = disambiguate(
        goal="run demo and plot",
        signals=signals,
        provider=provider,
    )

    assert mode is None


def test_provider_emits_no_tool_call_yields_none() -> None:
    """The LLM is forced to call the fake tool, but defensive: if for
    any reason it returns plain text instead, the disambiguator must
    not crash and must return ``None``."""

    provider = _StubProvider(
        ProviderToolResponse(
            text="I think prepared_demo",
            model="stub",
            response_id="r1",
            tool_calls=[],
            raw={},
        )
    )
    signals = {
        "wants_plot": True,
        "wants_demo": True,
        "wants_run": True,
        "wants_calibration": False,
        "wants_uncertainty": False,
    }

    assert disambiguate("run demo and plot", signals, provider) is None


def test_slow_provider_times_out_and_returns_none() -> None:
    """If the LLM API hangs, the disambiguator must surface ``None`` to
    the caller within ``timeout_sec`` seconds so the planner can fall
    back to the keyword classifier rather than blocking the user's
    request indefinitely (user story 7)."""

    import time

    class _HangingProvider:
        def respond_with_tools(self, **_: Any) -> ProviderToolResponse:
            # Sleep longer than the test timeout. The disambiguator
            # owns the wall-clock guard.
            time.sleep(2.0)
            return _mode_response("prepared_demo")

    signals = {
        "wants_plot": True,
        "wants_demo": True,
        "wants_run": True,
        "wants_calibration": False,
        "wants_uncertainty": False,
    }

    start = time.monotonic()
    mode = disambiguate(
        goal="run demo and plot",
        signals=signals,
        provider=_HangingProvider(),
        timeout_sec=0.1,
    )
    elapsed = time.monotonic() - start

    assert mode is None
    assert elapsed < 1.0, f"disambiguate must respect timeout; took {elapsed:.2f}s"


def test_provider_exception_is_swallowed_returns_none() -> None:
    """Any exception inside the provider call (network blip, auth
    error, malformed JSON, …) must not bubble out of the disambiguator
    — the planner relies on a clean ``None`` so it can fall through to
    the keyword classifier instead of crashing the user's session."""

    class _RaisingProvider:
        def respond_with_tools(self, **_: Any) -> ProviderToolResponse:
            raise RuntimeError("simulated OpenAI 503")

    signals = {
        "wants_plot": True,
        "wants_demo": True,
        "wants_run": True,
        "wants_calibration": False,
        "wants_uncertainty": False,
    }

    mode = disambiguate(
        goal="run demo and plot",
        signals=signals,
        provider=_RaisingProvider(),
    )

    assert mode is None


def test_run_only_no_plot_does_not_call_provider() -> None:
    """Run-only or calibration-only goals also do not need disambiguation —
    the conflict is specifically *plot* vs another action."""

    provider = _StubProvider(_mode_response("calibration"))
    signals = {
        "wants_plot": False,
        "wants_demo": False,
        "wants_run": True,
        "wants_calibration": True,
        "wants_uncertainty": False,
    }

    mode = disambiguate(
        goal="run a calibration",
        signals=signals,
        provider=provider,
    )

    assert mode is None
    assert provider.calls == []
