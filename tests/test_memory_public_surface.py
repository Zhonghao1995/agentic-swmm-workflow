"""Public-surface contract for the memory facade (PRD-03).

The ``agentic_swmm.memory`` namespace holds 13 sub-modules with 100+
public functions used internally between them. External callers should
only need a small, stable verb set; this test locks that contract.

The facade exposes four verbs:

- ``trigger_memory_refresh`` — post-audit hook (used by ``commands/audit.py``)
- ``recall_memory`` — lookup a lesson section by pattern name
  (used by ``agent/tool_handlers/swmm_memory.py``)
- ``recall_memory_search`` — RAG-backed hybrid search
  (used by ``agent/tool_handlers/swmm_memory.py``)
- ``append_fact`` — record a candidate fact to staging
  (used by ``agent/tool_handlers/swmm_memory.py``)

Internal sub-module imports keep working — this PRD is a contract
narrowing, not a code move. See ``test_memory_generic_domain_shim.py``
for the backwards-compat lock.
"""

from __future__ import annotations


EXPECTED_PUBLIC_VERBS = {
    "trigger_memory_refresh",
    "recall_memory",
    "recall_memory_search",
    "append_fact",
}


def test_memory_public_surface_is_exactly_the_documented_verbs() -> None:
    import agentic_swmm.memory as memory

    assert set(memory.__all__) == EXPECTED_PUBLIC_VERBS


def test_each_verb_is_a_callable_re_export_of_the_implementing_function() -> None:
    """The short import names must resolve to the same callables that the
    sub-modules expose. If a future refactor renames the implementation
    without updating the alias, this test fails loudly."""
    from agentic_swmm.memory import (
        append_fact,
        recall_memory,
        recall_memory_search,
        trigger_memory_refresh,
    )
    from agentic_swmm.memory.audit_hook import (
        trigger_memory_refresh as impl_refresh,
    )
    from agentic_swmm.memory.facts import record_fact_to_staging as impl_append
    from agentic_swmm.memory.recall import recall as impl_recall
    from agentic_swmm.memory.recall_search import recall_search as impl_search

    assert trigger_memory_refresh is impl_refresh
    assert recall_memory is impl_recall
    assert recall_memory_search is impl_search
    assert append_fact is impl_append


def _imports_facade_verb(source: str, verb: str) -> bool:
    """Check whether ``source`` contains ``from agentic_swmm.memory import
    <verb>`` (the facade form) for ``verb``.

    Uses AST so we don't get fooled by docstrings or commented-out code.
    Accepts ``from agentic_swmm.memory import x, verb, y`` and
    ``from agentic_swmm.memory import verb as alias`` equally.
    """
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "agentic_swmm.memory":
            for alias in node.names:
                if alias.name == verb:
                    return True
    return False


def test_commands_audit_uses_facade_for_trigger_memory_refresh() -> None:
    """``commands/audit.py`` is the canonical external caller of the
    post-audit refresh hook. After PRD-03 it must import via the facade.
    """
    from pathlib import Path

    import agentic_swmm.commands.audit as audit_mod

    source = Path(audit_mod.__file__).read_text(encoding="utf-8")
    assert _imports_facade_verb(source, "trigger_memory_refresh"), (
        "commands/audit.py should import trigger_memory_refresh from the "
        "agentic_swmm.memory facade (PRD-03), not from a sub-module."
    )


def test_tool_handler_uses_facade_for_the_three_agent_verbs() -> None:
    """``agent/tool_handlers/swmm_memory.py`` is the canonical external
    caller of recall_memory, recall_memory_search, and append_fact. After
    PRD-03 it must import all three via the facade.
    """
    from pathlib import Path

    import agentic_swmm.agent.tool_handlers.swmm_memory as handler_mod

    source = Path(handler_mod.__file__).read_text(encoding="utf-8")
    for verb in ("recall_memory", "recall_memory_search", "append_fact"):
        assert _imports_facade_verb(source, verb), (
            f"agent/tool_handlers/swmm_memory.py should import {verb} from "
            "the agentic_swmm.memory facade (PRD-03)."
        )


def test_direct_submodule_imports_still_work_for_internal_callers() -> None:
    """Backwards-compat lock: the facade is a narrowing of the public
    surface, not a code move. Existing callers that import from
    sub-modules (``from agentic_swmm.memory.session_db import ...``,
    audit tooling, expert CLIs, lessons-lifecycle scripts) keep working.
    """
    # Pick one symbol from each non-facade sub-module that has at least
    # one external production caller. If a deeper refactor moves these,
    # this test fails as a tripwire.
    from agentic_swmm.memory.case_inference import infer_case_name
    from agentic_swmm.memory.context_fence import wrap
    from agentic_swmm.memory.lessons_lifecycle import apply_decay
    from agentic_swmm.memory.lessons_metadata import read_all_patterns
    from agentic_swmm.memory.session_db import connect
    from agentic_swmm.memory.session_sync import default_db_path, sync_session_to_db

    for symbol in (
        infer_case_name,
        wrap,
        apply_decay,
        read_all_patterns,
        connect,
        default_db_path,
        sync_session_to_db,
    ):
        assert callable(symbol)
