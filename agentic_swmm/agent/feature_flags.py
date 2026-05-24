"""Runtime feature flags for the memory-informed agent.

The agent has two pieces of integrated behaviour that a user may want
to bypass without rebuilding: the SWMM pre/postflight gates, and the
memory-informed dispatch path. Both are exposed as environment
variables so a single ``AISWMM_DISABLE_*=1`` flips them off for one
shell session, and one ``--ignore-memory`` CLI flag (handled at the
CLI entry point) sets the memory flag for the duration of a single
invocation.

Why env vars (not config keys)
------------------------------
Both opt-outs are *runtime* knobs the user reaches for when something
goes wrong, not project settings the team commits. Env vars give a
zero-edit escape hatch — the user does not have to mutate a project
config to skip a gate for one run, and the next run picks the gate
back up automatically.

Both checks are deliberately permissive about the *value*: anything
truthy ("1", "true", "yes", "on", case-insensitive) flips the flag.
This matches the convention used by other agentic_swmm env vars
(``AISWMM_HEADLESS``, ``AISWMM_MEMORY_DIR``) and avoids the foot-gun
where the user sets ``AISWMM_DISABLE_SWMM_GATES=true`` and the gate
silently stays on because the comparison was ``== "1"``.

Failure mode
------------
Neither helper raises. A missing or unset env var means the feature
is enabled (the default for both is "memory-informed runtime active").
"""

from __future__ import annotations

import os


_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


SWMM_GATES_ENV = "AISWMM_DISABLE_SWMM_GATES"
MEMORY_INFORMED_ENV = "AISWMM_DISABLE_MEMORY_INFORMED"


def is_truthy(value: str | None) -> bool:
    """Return True for the canonical truthy strings, case-insensitive.

    Empty string and ``None`` are both False so an explicit
    ``AISWMM_DISABLE_SWMM_GATES=`` (set-but-empty) leaves the gate
    on — same as if the variable were not set at all.

    This is the single source of truth every ``AISWMM_*`` boolean env
    var consults so the truthy contract stays consistent across the
    codebase (see :mod:`agentic_swmm.agent.experimental_providers`).
    """
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY


# Backwards-compatible alias for the now-public spelling.
_is_truthy = is_truthy


def swmm_gates_disabled() -> bool:
    """Return True when SWMM pre/postflight gates should be skipped.

    Controlled by ``AISWMM_DISABLE_SWMM_GATES``. Each mode adapter
    reads this before invoking ``preflight_inp`` / ``postflight_qa``
    so the gate stays a single boolean flip rather than a per-mode
    keyword argument that has to propagate through every call site.
    """
    return is_truthy(os.environ.get(SWMM_GATES_ENV))


def memory_informed_disabled() -> bool:
    """Return True when the memory-informed runtime path is opted out.

    Controlled by ``AISWMM_DISABLE_MEMORY_INFORMED``. The check lives
    at the ``gather_memory_context`` boundary: when the flag is set,
    the function short-circuits to an empty :class:`MemoryContext`
    *before* it reads any store, so the runtime sees the same shape
    as a fresh project.
    """
    return is_truthy(os.environ.get(MEMORY_INFORMED_ENV))


__all__ = [
    "MEMORY_INFORMED_ENV",
    "SWMM_GATES_ENV",
    "is_truthy",
    "memory_informed_disabled",
    "swmm_gates_disabled",
]
