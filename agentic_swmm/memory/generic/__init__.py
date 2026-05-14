"""Generic agent-memory layer — domain-agnostic primitives.

This sub-package re-exports the Hermes-equivalent generic memory modules
(`session_db`, `facts`, `context_fence`, `session_sync`) so they can be
lifted out of the SWMM-domain layer in a future refactor. Today every
caller still imports from `agentic_swmm.memory.<name>` directly; the
re-exports here are stage-1 shims (P1-4 in #79) — caller updates land in
a follow-on issue.

Add ``from agentic_swmm.memory.generic import facts`` to your imports if
you want the explicit Hermes-namespacing now; both forms are stable.
"""

from __future__ import annotations

from agentic_swmm.memory import context_fence as context_fence
from agentic_swmm.memory import facts as facts
from agentic_swmm.memory import session_db as session_db
from agentic_swmm.memory import session_sync as session_sync

__all__ = [
    "context_fence",
    "facts",
    "session_db",
    "session_sync",
]
