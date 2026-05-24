"""Shared error-boundary decorator for the agent runtime (issue #207).

The agent runtime carries 60+ ``try/except Exception: log + return X``
blocks scattered across planner, runtime_loop, mcp_pool, tool_registry,
and the memory_* modules. They all encode the same robustness
contract — an LLM provider hiccup, a malformed YAML, a flaky SQLite
connection MUST NOT crash the agent's turn — but each site rolled the
contract by hand with its own log format. That spread made the
"how many silent fallbacks fired in this session?" question a
62-grep affair.

This module consolidates the contract behind one decorator::

    @on_exception_return_default(default=[], scope="memory_recall")
    def recall_lessons(pattern: str) -> list[str]:
        ...

* When the wrapped function returns normally, the decorator is a
  transparent pass-through.
* When the wrapped function raises any ``Exception`` (subclasses
  included), the decorator catches it, logs a structured payload via
  both Python ``logging`` AND a queryable jsonl file, and returns
  ``default``.
* ``KeyboardInterrupt`` / ``SystemExit`` propagate untouched — those
  are not the "programmer / IO error" class the boundary exists for.

Structured-log channel
----------------------
Each catch appends one JSON line to
``<config_dir>/silent_fallbacks.jsonl`` with at least::

    {
      "timestamp_utc": "2026-05-24T12:34:56Z",
      "scope": "memory_recall",
      "exception_type": "ValueError",
      "exception_str": "bad input",
      "session_id": <str or null>
    }

``<config_dir>`` resolves through :func:`agentic_swmm.config.config_dir`
so the ``AISWMM_CONFIG_DIR`` env var (used by tests and CI) redirects
the file the same way the rest of the user-config layer does. Writing
to the jsonl is itself wrapped in a final try/except so a read-only
config dir never re-introduces the very crash this module exists to
prevent.

``session_id`` is included when discoverable. The runtime does not
currently expose a contextvar for the active session, so the field is
serialized as ``null`` today. When a future PR threads a
``current_session_id()`` contextvar through the planner / runtime
loop, populate :func:`_discover_session_id` with the lookup — the
on-disk schema already reserves the field.

``default_factory`` convention
------------------------------
A site whose existing default is a fresh mutable container (e.g.
``return []``) should pass ``default_factory=list`` rather than
``default=[]``. The decorator calls the factory once per catch so
each caller gets its own object identity; reusing the same empty
list across calls is a foot-gun where one caller could mutate the
"empty" default the next caller receives.
"""

from __future__ import annotations

import functools
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from agentic_swmm.config import config_dir


_log = logging.getLogger(__name__)


T = TypeVar("T")


_SILENT_FALLBACKS_FILENAME = "silent_fallbacks.jsonl"


def _now_utc_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with ``Z`` suffix.

    Centralised so every appended row uses the same format — a doctor
    row that filters by age can otherwise mis-parse the timestamp if
    one site uses ``+00:00`` and another uses ``Z``.
    """
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _discover_session_id() -> str | None:
    """Return the active session_id when discoverable, else ``None``.

    The runtime does not currently expose a session-id contextvar.
    This function is the future hook: when a PR adds e.g.
    ``agentic_swmm.agent.session_context.current_session_id()``, wire
    the lookup here so every migrated site picks up the value without
    a touch.
    """
    try:
        from agentic_swmm.agent.session_context import (  # type: ignore[import-not-found]
            current_session_id,
        )

        value = current_session_id()
    except Exception:
        # The session_context module does not exist yet; that is the
        # expected branch today. Once it lands, the import succeeds
        # and the returned value flows into the jsonl row.
        return None
    if value is None:
        return None
    return str(value)


def _append_event(payload: dict[str, Any]) -> None:
    """Append one JSON line to ``<config_dir>/silent_fallbacks.jsonl``.

    Wrapped in a broad try/except: the structured-log file is a
    best-effort transparency surface, not a critical path. A
    permission-denied write, a missing parent directory we somehow
    cannot create, or a transient I/O error MUST NOT propagate — the
    decorator's whole point is to keep the runtime alive on failure.
    """
    try:
        target = config_dir() / _SILENT_FALLBACKS_FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception:
        # If we cannot write the audit row we still want the in-process
        # logger to surface the original failure, but we never re-raise
        # — the caller asked for a silent fallback.
        _log.debug(
            "error_boundary: silent_fallbacks.jsonl write failed", exc_info=True
        )


def on_exception_return_default(
    default: Any = None,
    *,
    scope: str,
    log_level: int = logging.WARNING,
    default_factory: Callable[[], Any] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Wrap a function so any ``Exception`` becomes ``default``.

    Arguments:
        default: Value returned on catch. Ignored when
            ``default_factory`` is provided.
        scope: A short stable string identifying the boundary
            (e.g. ``"memory_recall"``, ``"mcp_tool_call"``). Written
            verbatim into the jsonl row so the developer can filter
            ``jq 'select(.scope == "memory_recall")'``. REQUIRED — a
            site without a scope name is anonymous in the structured
            log and defeats the abstraction's purpose.
        log_level: Python ``logging`` level used for the in-process
            warning. Defaults to ``WARNING``; pass ``logging.ERROR``
            for boundaries where a catch is more surprising than
            routine (e.g. an audit-write failure on a healthy box).
        default_factory: Callable returning a fresh value on each
            catch. Use this when the default is a mutable container
            (``default_factory=list`` for ``[]``, ``default_factory=dict``
            for ``{}``). Avoids the shared-default foot-gun where one
            caller's mutation leaks into the next caller's "empty"
            return.

    Returns:
        A decorator. The wrapped function preserves
        ``__name__`` / ``__doc__`` / ``__wrapped__`` via
        :func:`functools.wraps` so stack traces and ``help(fn)`` still
        point at the original site.

    Note:
        ``KeyboardInterrupt`` and ``SystemExit`` are NOT caught — they
        derive from ``BaseException`` rather than ``Exception``, and
        catching them would freeze interactive Ctrl-C handling.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                exc_type = type(exc).__name__
                # ``str(exc)`` itself can raise if the exception's
                # ``__str__`` is hostile (rare, but precisely the class
                # of failure the ``hitl_surface._safe_str`` boundary
                # exists to defend against — symmetry matters: the
                # boundary that catches ``__str__`` failures must not
                # itself re-crash on one).
                try:
                    exc_str = str(exc)
                except Exception:
                    exc_str = "(unprintable exception)"
                # In-process logger — the human-readable channel a
                # developer tailing stderr will notice.
                _log.log(
                    log_level,
                    "silent fallback in %s: %s: %s",
                    scope,
                    exc_type,
                    exc_str,
                )
                # Queryable channel — the structured row that turns
                # "how many fallbacks fired today?" into one jq line.
                _append_event(
                    {
                        "timestamp_utc": _now_utc_iso(),
                        "scope": scope,
                        "exception_type": exc_type,
                        "exception_str": exc_str,
                        "session_id": _discover_session_id(),
                    }
                )
                if default_factory is not None:
                    return default_factory()  # type: ignore[return-value]
                return default  # type: ignore[return-value]

        return wrapper

    return decorator


__all__ = ["on_exception_return_default"]
