"""Unit tests for ``agentic_swmm.agent.error_boundary`` (issue #207).

The decorator ``@on_exception_return_default`` consolidates the 60+
hand-rolled ``try/except Exception: log; return default`` blocks that
the agent runtime uses to keep an LLM/IO/parser failure from
propagating into a user-visible crash. The decorator owes three
guarantees its callers rely on:

1. When the wrapped function returns normally, its result is returned
   verbatim — the decorator is invisible on the happy path.
2. When the wrapped function raises ``Exception`` (or a subclass), the
   decorator catches it and returns the configured ``default`` value;
   the exception NEVER propagates to the caller.
3. Every catch appends one structured JSON line to
   ``<config_dir>/silent_fallbacks.jsonl`` carrying at least
   ``scope``, ``exception_type``, and ``exception_str`` so a developer
   can grep the log and answer "how many silent fallbacks fired in
   this session?" with one ``jq`` invocation.

A regression test for one migrated production site lives in
``test_error_boundary_regression.py`` — keep this file focused on the
abstract decorator contract.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agentic_swmm.agent.error_boundary import on_exception_return_default


def _read_events(jsonl_path: Path) -> list[dict]:
    """Read every JSON object from ``jsonl_path`` in line order."""
    if not jsonl_path.exists():
        return []
    return [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    """Point ``config_dir()`` at a fresh tmpdir so the jsonl is local."""
    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(tmp_path))
    yield tmp_path


def test_returns_function_result_on_success(isolated_config_dir):
    """Happy path: decorator is a transparent pass-through."""

    @on_exception_return_default(default=42, scope="unit_test")
    def doubled(x):
        return x * 2

    assert doubled(7) == 14
    # And nothing was logged because nothing was caught.
    assert _read_events(isolated_config_dir / "silent_fallbacks.jsonl") == []


def test_returns_default_on_exception(isolated_config_dir):
    """When the wrapped function raises, the default is returned."""

    @on_exception_return_default(default="fallback", scope="unit_test")
    def explodes():
        raise RuntimeError("kaboom")

    # Must not propagate.
    assert explodes() == "fallback"


def test_logs_structured_payload_with_required_fields(isolated_config_dir):
    """The jsonl line carries scope, exception_type, exception_str."""

    @on_exception_return_default(default=None, scope="memory_recall")
    def explodes():
        raise ValueError("bad input")

    explodes()

    events = _read_events(isolated_config_dir / "silent_fallbacks.jsonl")
    assert len(events) == 1
    event = events[0]
    assert event["scope"] == "memory_recall"
    assert event["exception_type"] == "ValueError"
    assert event["exception_str"] == "bad input"
    # A timestamp is also required so a doctor row can filter by age.
    assert "timestamp_utc" in event


def test_each_catch_appends_one_line(isolated_config_dir):
    """Repeated catches append; nothing is overwritten or batched."""

    @on_exception_return_default(default=0, scope="unit_test")
    def explodes():
        raise RuntimeError("boom")

    for _ in range(3):
        explodes()

    events = _read_events(isolated_config_dir / "silent_fallbacks.jsonl")
    assert len(events) == 3


def test_does_not_swallow_keyboardinterrupt(isolated_config_dir):
    """``KeyboardInterrupt`` is not an ``Exception`` and must propagate.

    The contract is "never crash the user's turn on a programmer/IO
    error"; it is NOT "swallow Ctrl-C". Catching ``BaseException``
    would freeze interactive sessions.
    """

    @on_exception_return_default(default=None, scope="unit_test")
    def interrupts():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        interrupts()


def test_log_level_emits_via_python_logger(isolated_config_dir, caplog):
    """In addition to the jsonl, the decorator emits via ``logging``.

    Callers can dial the level per-site (``log_level=logging.ERROR``
    for surprises that warrant more attention). The default is WARNING
    so the runtime's structured stderr stays readable.
    """

    @on_exception_return_default(
        default=None, scope="audit_hook", log_level=logging.ERROR
    )
    def explodes():
        raise RuntimeError("audit-write IO failure")

    with caplog.at_level(logging.ERROR, logger="agentic_swmm.agent.error_boundary"):
        explodes()

    assert any(
        "audit_hook" in record.message and "RuntimeError" in record.message
        for record in caplog.records
    )


def test_default_factory_is_called_per_invocation(isolated_config_dir):
    """``default_factory`` lets a site return a fresh empty container.

    Sharing one mutable default across calls would let one caller
    mutate the next caller's "empty" list — a foot-gun the
    ``default_factory`` callable convention sidesteps. The factory is
    invoked on every catch so each call gets its own object identity.
    """

    @on_exception_return_default(
        default=None,
        default_factory=list,
        scope="unit_test",
    )
    def explodes():
        raise RuntimeError("boom")

    a = explodes()
    b = explodes()
    assert a == [] and b == []
    # Distinct object identity — the factory ran twice.
    assert a is not b


def test_preserves_function_metadata(isolated_config_dir):
    """``functools.wraps`` keeps ``__name__`` / ``__doc__`` intact.

    Without this, stack traces and ``help(fn)`` would show
    ``wrapped`` for every migrated site, which would make the
    structured-log scope the only handle the developer has on which
    site fired — a regression we explicitly do not want.
    """

    @on_exception_return_default(default=None, scope="unit_test")
    def my_recall(pattern: str) -> str | None:
        """Recall a thing."""
        return pattern

    assert my_recall.__name__ == "my_recall"
    assert my_recall.__doc__ == "Recall a thing."


def test_only_writes_log_when_exception_is_caught(isolated_config_dir):
    """Successful calls do not append jsonl rows.

    The structured-log file would otherwise grow unbounded on a
    healthy session — exactly the opposite of "queryable channel".
    """

    @on_exception_return_default(default="ok", scope="unit_test")
    def always_ok():
        return "ok"

    for _ in range(50):
        always_ok()

    assert _read_events(isolated_config_dir / "silent_fallbacks.jsonl") == []
