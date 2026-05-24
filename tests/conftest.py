"""Test fixtures shared across the suite.

PRD-09 introduces a synthetic ``claude_agent_sdk`` module so tests can
exercise ``ClaudeSDKProvider`` without spawning the real ``claude``
CLI subprocess. The fixture installs a stub into ``sys.modules`` that
implements the slice of the SDK surface the provider actually uses:

* ``query(*, prompt, options=None)`` — async generator yielding
  scripted messages set via ``stub.script(messages)``.
* ``ClaudeAgentOptions`` — dataclass-shaped container with the
  attributes the provider reads.
* ``AssistantMessage`` / ``TextBlock`` / ``ToolUseBlock`` /
  ``ResultMessage`` / ``RateLimitEvent`` — dataclasses matching the
  real SDK fields.
* ``ClaudeSDKError`` and the four CLI/process error subclasses for
  exception-mapping tests.

Tests that exercise the *real* SDK gate behind the
``AISWMM_RUN_LIVE_CLAUDE`` env var and are otherwise skipped.
"""
from __future__ import annotations

import io
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest


class _FakeTTYStream(io.StringIO):
    """StringIO that claims to be a TTY.

    Spinner / TTY-rendering tests use this to force the carriage-return
    rendering path (instead of the newline-per-line non-TTY fallback)
    while still capturing output via ``.getvalue()``.

    Lives here (instead of being duplicated in each test module) per
    issue #190 — one definition keeps the test-side contract aligned
    with the production ``Spinner._stream_is_tty`` probe.
    """

    def isatty(self) -> bool:  # type: ignore[override]
        return True


@pytest.fixture
def isolated_home(tmp_path, monkeypatch, request):
    """Point ``Path.home()`` at a fresh tmp dir to isolate config files.

    Both provider-preflight test files (``test_provider_preflight.py``
    and ``test_provider_preflight_gate.py``) need the same isolated
    ``HOME``, cleared ``OPENAI_API_KEY``, and reset of the once-per-
    process ``_legacy_claude_sdk_notice_emitted`` flag. The only thing
    that varied was the direction of the
    ``AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS`` gate.

    The fixture honours an optional ``@pytest.mark.gate("on" | "off")``
    marker — default is ``"off"`` (the new-user default). This keeps
    each test file's gate direction explicit at the marker level
    without duplicating the 25-line scaffold.
    """
    from agentic_swmm.agent import provider_preflight

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    marker = request.node.get_closest_marker("gate")
    gate_state = (marker.args[0] if marker and marker.args else "off").lower()
    if gate_state == "on":
        monkeypatch.setenv("AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS", "1")
    else:
        monkeypatch.delenv("AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS", raising=False)

    # Reset the once-per-process legacy-notice flag so each test starts
    # from a clean slate; otherwise an earlier test that triggered the
    # notice would silence it for the rest of the session.
    if hasattr(provider_preflight, "_legacy_claude_sdk_notice_emitted"):
        monkeypatch.setattr(
            provider_preflight,
            "_legacy_claude_sdk_notice_emitted",
            False,
            raising=False,
        )
    return home


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "gate(state): set the AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS gate "
        "for ``isolated_home`` ('on' or 'off'; default 'off').",
    )


@dataclass
class _StubTextBlock:
    text: str = ""


@dataclass
class _StubToolUseBlock:
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class _StubThinkingBlock:
    thinking: str = ""


@dataclass
class _StubAssistantMessage:
    content: list = field(default_factory=list)
    model: str = ""
    parent_tool_use_id: str | None = None
    error: str | None = None
    usage: dict | None = None
    message_id: str | None = None
    stop_reason: str | None = None
    session_id: str | None = None
    uuid: str | None = None


@dataclass
class _StubRateLimitInfo:
    status: str = "rate_limited"
    resets_at: str | None = None
    rate_limit_type: str | None = None
    utilization: float | None = None
    overage_status: str | None = None
    overage_resets_at: str | None = None
    overage_disabled_reason: str | None = None
    raw: dict | None = None


@dataclass
class _StubRateLimitEvent:
    rate_limit_info: Any = None
    uuid: str | None = None
    session_id: str | None = None


@dataclass
class _StubResultMessage:
    subtype: str = "success"
    duration_ms: int = 0
    duration_api_ms: int = 0
    is_error: bool = False
    num_turns: int = 1
    session_id: str = ""
    stop_reason: str | None = None
    total_cost_usd: float | None = None
    usage: dict | None = None
    result: str | None = None
    structured_output: Any = None
    model_usage: dict | None = None
    permission_denials: list | None = None
    deferred_tool_use: Any = None
    errors: list | None = None
    api_error_status: int | None = None
    uuid: str | None = None


@dataclass
class _StubClaudeAgentOptions:
    """Mirrors the slice of ClaudeAgentOptions the provider populates."""

    tools: Any = None
    allowed_tools: list[str] = field(default_factory=list)
    system_prompt: Any = None
    mcp_servers: Any = field(default_factory=dict)
    strict_mcp_config: bool = False
    permission_mode: str | None = None
    continue_conversation: bool = False
    resume: str | None = None
    session_id: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    disallowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    fallback_model: str | None = None
    betas: list[str] = field(default_factory=list)
    permission_prompt_tool_name: str | None = None
    cwd: Any = None
    cli_path: Any = None
    settings: str | None = None
    add_dirs: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    extra_args: dict = field(default_factory=dict)


class _StubSDKError(Exception):
    pass


class _StubCLIConnectionError(_StubSDKError):
    pass


class _StubCLINotFoundError(_StubCLIConnectionError):
    pass


class _StubProcessError(_StubSDKError):
    def __init__(self, message: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


class _StubCLIJSONDecodeError(_StubSDKError):
    pass


class _SDKStub:
    """The synthetic module instance — exposes ``script`` so a test
    can inject a sequence of messages the next ``query()`` call yields,
    and ``last_call`` so the test can inspect what the provider sent.
    """

    def __init__(self) -> None:
        self._scripted: list = []
        self._raise: Exception | None = None
        self.last_call: dict[str, Any] = {}

    def script(self, messages: list) -> None:
        self._scripted = list(messages)
        self._raise = None

    def script_error(self, exc: Exception) -> None:
        self._raise = exc
        self._scripted = []


def _build_stub_module() -> types.ModuleType:
    mod = types.ModuleType("claude_agent_sdk")
    stub = _SDKStub()

    async def _query(*, prompt, options=None, transport=None):
        stub.last_call = {"prompt": prompt, "options": options, "transport": transport}
        if stub._raise is not None:
            raise stub._raise
        for msg in stub._scripted:
            yield msg

    mod.query = _query
    mod.ClaudeAgentOptions = _StubClaudeAgentOptions
    mod.AssistantMessage = _StubAssistantMessage
    mod.TextBlock = _StubTextBlock
    mod.ToolUseBlock = _StubToolUseBlock
    mod.ThinkingBlock = _StubThinkingBlock
    mod.ResultMessage = _StubResultMessage
    mod.RateLimitEvent = _StubRateLimitEvent
    mod.RateLimitInfo = _StubRateLimitInfo
    mod.ClaudeSDKError = _StubSDKError
    mod.CLIConnectionError = _StubCLIConnectionError
    mod.CLINotFoundError = _StubCLINotFoundError
    mod.ProcessError = _StubProcessError
    mod.CLIJSONDecodeError = _StubCLIJSONDecodeError
    mod._stub_handle = stub
    return mod


@pytest.fixture
def mock_claude_sdk_module(monkeypatch):
    """Install a synthetic ``claude_agent_sdk`` module into ``sys.modules``.

    Yields the stub handle for tests to script messages / errors via
    ``stub.script(...)`` and ``stub.script_error(...)``. The provider
    module under test is also cleared from ``sys.modules`` so its
    next import picks up the stub.
    """
    mod = _build_stub_module()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    # Force a fresh import of the provider so the lazy import binds to
    # the stub rather than any cached real SDK reference.
    monkeypatch.delitem(sys.modules, "agentic_swmm.providers.claude_sdk_api", raising=False)
    return mod._stub_handle
