"""Domain agent-memory layer — SWMM-specific modules.

This sub-package re-exports the SWMM-specific memory modules (recall,
recall_search, lessons_metadata, lessons_lifecycle, audit_hook,
proposal_skeleton, moc_generator, audit_to_memory, case_inference) so the
generic Hermes-equivalent layer can be cleanly separated. Today every
caller still imports from `agentic_swmm.memory.<name>` directly; the
re-exports here are stage-1 shims (P1-4 in #79) — caller updates land in
a follow-on issue.

Add ``from agentic_swmm.memory.domain import recall`` to your imports if
you want the explicit SWMM-namespacing now; both forms are stable.
"""

from __future__ import annotations

from agentic_swmm.memory import audit_hook as audit_hook
from agentic_swmm.memory import audit_to_memory as audit_to_memory
from agentic_swmm.memory import case_inference as case_inference
from agentic_swmm.memory import lessons_lifecycle as lessons_lifecycle
from agentic_swmm.memory import lessons_metadata as lessons_metadata
from agentic_swmm.memory import moc_generator as moc_generator
from agentic_swmm.memory import proposal_skeleton as proposal_skeleton
from agentic_swmm.memory import recall as recall
from agentic_swmm.memory import recall_search as recall_search

__all__ = [
    "audit_hook",
    "audit_to_memory",
    "case_inference",
    "lessons_lifecycle",
    "lessons_metadata",
    "moc_generator",
    "proposal_skeleton",
    "recall",
    "recall_search",
]
