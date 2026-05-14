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
"""

from __future__ import annotations
