"""Memory layer: close the audit -> memory -> agent loop (PRD M1-M7).

P1-4 (#79) splits this namespace into two sub-packages without moving any
caller imports:

* ``agentic_swmm.memory.generic`` — domain-agnostic Hermes-equivalent
  primitives (``session_db``, ``facts``, ``context_fence``,
  ``session_sync``).
* ``agentic_swmm.memory.domain`` — SWMM-specific modules (``recall``,
  ``recall_search``, ``lessons_metadata``, ``lessons_lifecycle``,
  ``audit_hook``, ``proposal_skeleton``, ``moc_generator``,
  ``audit_to_memory``, ``case_inference``).

Both sub-packages are re-export shims; ``from agentic_swmm.memory import
recall`` still works. The eventual stage-2 (lifting generic into a
Hermes plugin) is tracked as a follow-on issue.

Public facade (PRD-03)
----------------------
External callers should import the four verbs below directly from this
namespace rather than walking 13 sub-modules to find them. Internal
sub-module imports keep working — the facade is a contract narrowing,
not a code move:

- :func:`trigger_memory_refresh` — post-audit refresh hook
- :func:`recall_memory` — pattern-name lookup in ``lessons_learned.md``
- :func:`recall_memory_search` — RAG-backed hybrid retrieval
- :func:`append_fact` — append a candidate fact to ``facts_staging.md``
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
