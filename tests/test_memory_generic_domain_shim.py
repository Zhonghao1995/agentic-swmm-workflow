"""Stage-1 re-export shim test for the memory namespace split (P1-4 in #79).

The ``agentic_swmm.memory`` namespace mixes a Hermes-equivalent generic
layer (`session_db`, `facts`, `context_fence`, `session_sync`) with a
SWMM-specific domain layer. P1-4 introduces two re-export sub-packages —
``generic`` and ``domain`` — so a future caller-update pass can move
imports without changing the public surface twice.

This test contracts both halves:

1. The new sub-packages expose the expected modules.
2. The pre-existing flat imports (``from agentic_swmm.memory import
   recall``) keep working — i.e., we did not accidentally remove or
   rename anything.
3. Each module re-exported through ``generic`` / ``domain`` is the same
   object as the flat import, so there is exactly one source of truth.
"""

from __future__ import annotations

import importlib


_GENERIC_MODULES = (
    "context_fence",
    "facts",
    "session_db",
    "session_sync",
)

_DOMAIN_MODULES = (
    "audit_hook",
    "audit_to_memory",
    "case_inference",
    "lessons_lifecycle",
    "lessons_metadata",
    "moc_generator",
    "proposal_skeleton",
    "recall",
    "recall_search",
)


def test_generic_subpackage_exports_expected_modules() -> None:
    from agentic_swmm.memory import generic

    for name in _GENERIC_MODULES:
        assert hasattr(generic, name), f"agentic_swmm.memory.generic missing {name}"


def test_domain_subpackage_exports_expected_modules() -> None:
    from agentic_swmm.memory import domain

    for name in _DOMAIN_MODULES:
        assert hasattr(domain, name), f"agentic_swmm.memory.domain missing {name}"


def test_flat_imports_still_work_after_split() -> None:
    """Back-compat lock — every caller in the repo still uses these."""
    for name in _GENERIC_MODULES + _DOMAIN_MODULES:
        mod = importlib.import_module(f"agentic_swmm.memory.{name}")
        assert mod is not None


def test_subpackage_modules_are_same_objects_as_flat_imports() -> None:
    """One source of truth — the shim must not create alternate identities."""
    from agentic_swmm.memory import generic, domain

    for name in _GENERIC_MODULES:
        flat = importlib.import_module(f"agentic_swmm.memory.{name}")
        assert getattr(generic, name) is flat, f"generic.{name} drifted"
    for name in _DOMAIN_MODULES:
        flat = importlib.import_module(f"agentic_swmm.memory.{name}")
        assert getattr(domain, name) is flat, f"domain.{name} drifted"


def test_no_module_appears_in_both_subpackages() -> None:
    """A module belongs in exactly one of the two layers."""
    generic_set = set(_GENERIC_MODULES)
    domain_set = set(_DOMAIN_MODULES)
    overlap = generic_set & domain_set
    assert not overlap, f"modules in both generic and domain: {sorted(overlap)}"
