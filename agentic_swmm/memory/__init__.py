"""Memory layer: close the audit -> memory -> agent loop (PRD M1-M7).

Public facade (PRD-03)
----------------------
External callers should import the four verbs below directly from this
namespace rather than walking the 30+ sub-modules to find them. Internal
sub-module imports keep working — the facade is a contract narrowing,
not a code move:

- :func:`trigger_memory_refresh` — post-audit refresh hook
- :func:`recall_memory` — pattern-name lookup in ``lessons_learned.md``
- :func:`recall_memory_search` — RAG-backed hybrid retrieval
- :func:`append_fact` — append a candidate fact to ``facts_staging.md``

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
from agentic_swmm.memory.facts import record_fact_to_staging as append_fact
from agentic_swmm.memory.recall import recall as recall_memory
from agentic_swmm.memory.recall_search import recall_search as recall_memory_search


__all__ = [
    "trigger_memory_refresh",
    "recall_memory",
    "recall_memory_search",
    "append_fact",
]
