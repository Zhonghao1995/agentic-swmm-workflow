"""Memory layer: close the audit -> memory -> agent loop.

Public facade (PRD-03)
----------------------
External callers should import the three verbs below directly from this
namespace rather than walking the sub-modules to find them. Internal
sub-module imports keep working — the facade is a contract narrowing,
not a code move:

- :func:`trigger_memory_refresh` — post-audit refresh hook
- :func:`recall_memory` — keyword recall over the store's failures and
  negative lessons (memory simplification PR 3b)
- :func:`propose_fact` — turn a candidate fact into a proposal for
  ``memory/facts.md`` (memory simplification PR 4)

Internal submodule tier (issue #359)
------------------------------------
This is a wide domain package; first-party aiswmm code (commands, the
agent runtime, diagnostics) additionally imports named submodules
directly (``run_progress``, ``session_db``, ``citations``, ...). That
tier is intentional, not a facade bypass — but its extent is pinned:
``tests/test_package_import_surfaces.py`` holds the ratchet list of
memory submodules reachable from outside this package, so growing the
surface is a conscious edit to that pin in the same PR.
"""

from __future__ import annotations

from agentic_swmm.memory.audit_hook import trigger_memory_refresh
from agentic_swmm.memory.proposals import propose_fact
from agentic_swmm.memory.recall import recall as recall_memory


__all__ = [
    "trigger_memory_refresh",
    "recall_memory",
    "propose_fact",
]
