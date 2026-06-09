"""Memory verb registry (PRD-06 Phase D.1).

PRs #147-154 added a cluster of new memory-facing CLI verbs:
``compare``, ``cite``, ``storm``, ``transfer``, ``uncertainty plan``,
plus a handful of expert-only surfaces (``calibration-memory read``,
``negative-lessons read``, ``case-adaptive-thresholds``). The planner's
existing :func:`_consult_memory_informed_policy` decides whether a
goal is high- or low-stakes via :func:`_looks_high_stakes` (keyword
sniff). That table grows every release and is the canonical "drift bug
shape" PRD-04 warned about — three files have to be edited in lockstep
to add a new verb.

This module is the single source of truth for **what memory verb
metadata the runtime needs to know**: the verb's name, a one-sentence
description, the CLI subcommand path, the workflow mode it belongs to
(``default`` vs ``expert``), and the policy stakes hint (``low`` vs
``high``). Adding a new verb means appending one
:class:`MemoryVerb` row here — the planner, the HITL surface, and the
docs scripts all read from this registry.

Why a separate registry and not the tool registry
-------------------------------------------------
The tool registry (``AgentToolRegistry`` in ``tool_registry.py``) models
the LLM-facing flat tool surface — the planner picks tools by name from
the schemas the registry exposes. The memory verbs are CLI-level mutation
surfaces, not planner-callable tools, and conflating them would mix two
unrelated dispatch concerns. The tool registry's LLM-driven flat-dispatch
shape (see *Dispatch architecture* in ``CONTEXT.md``) is also the wrong
shape for the stakes-aware HITL routing that memory verbs require.

Failure mode
------------
Lookups for unknown verbs return ``None``; this module never raises
on a typo. The planner already treats a missing verb name as "no
stakes hint available" and falls back to its existing keyword sniff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# Valid stakes labels. Mirrors :data:`memory_informed_policy.VALID_STAKES`
# on purpose — the registry's stakes hint flows straight into
# :func:`decide_with_memory` without re-mapping.
StakesLevel = Literal["low", "high"]


# Valid workflow modes the registry distinguishes. ``default`` is the
# verb set every user sees; ``expert`` is additive — it includes the
# default verbs plus the expert-only surfaces.
WorkflowModeLabel = Literal["default", "expert"]


@dataclass(frozen=True)
class MemoryVerb:
    """One row in the memory-verb registry.

    Frozen so callers (the planner, the docs generator) cannot scribble
    on a registered row. Adding a new optional field is an additive
    schema change — the registry's consumers only read what they need.

    Attributes:
        name: Stable identifier used by the planner's stakes lookup.
            Matches the CLI subcommand (``compare``, ``cite``) for the
            simple cases; compound verbs use the dotted form
            (``uncertainty.plan``) so dispatch is unambiguous.
        description: One-sentence plain-English summary. Surfaced in
            HITL prompts and the docs generator.
        cli_path: The full CLI subcommand path, e.g. ``aiswmm compare``
            or ``aiswmm uncertainty plan``. Documentation-only; the
            registry does not invoke argparse.
        mode: ``"default"`` for verbs every user sees;
            ``"expert"`` for the additive expert-only set. The
            ``expert`` mode includes both the ``default`` and
            ``expert`` rows when listed.
        stakes: ``"low"`` (advisory / read-only) or ``"high"``
            (mutates ``memory/`` or accepts a calibration). The
            planner passes this to
            :func:`decide_with_memory` so high-stakes verbs route to
            ``hitl`` on zero evidence.
    """

    name: str
    description: str
    cli_path: str
    mode: WorkflowModeLabel
    stakes: StakesLevel


# Authoritative registry. Mutable on purpose so test fixtures can
# extend it without monkey-patching imports; production code only
# reads through the helpers below.
_REGISTRY: dict[str, MemoryVerb] = {}


def register(verb: MemoryVerb) -> MemoryVerb:
    """Insert ``verb`` into the registry, replacing any prior entry.

    Returns the registered verb so a caller can chain the call (e.g.
    ``my_verb = register(MemoryVerb(...))``). Replacement is
    deliberate — re-registering with a tweaked stakes label during a
    test must take effect without a separate ``unregister`` call.
    """
    _REGISTRY[verb.name] = verb
    return verb


def get_verb(name: str) -> MemoryVerb | None:
    """Return the verb with ``name`` or ``None`` for unknown names.

    Never raises. The planner uses ``None`` as "no registry entry
    for this verb — fall back to the keyword stakes sniff".
    """
    return _REGISTRY.get(name)


def list_verbs(mode: WorkflowModeLabel = "default") -> list[MemoryVerb]:
    """Return verbs visible in the requested mode, sorted by name.

    ``mode="default"`` returns only ``mode="default"`` rows.
    ``mode="expert"`` returns the union of default + expert rows
    (expert mode is additive — every default verb is still available).
    """
    if mode == "expert":
        rows = list(_REGISTRY.values())
    else:
        rows = [v for v in _REGISTRY.values() if v.mode == "default"]
    return sorted(rows, key=lambda v: v.name)


def stakes_for(name: str) -> StakesLevel | None:
    """Return the stakes label for ``name`` or ``None`` for unknown verbs.

    Tiny convenience wrapper around :func:`get_verb` so the planner's
    consultation site stays a single line.
    """
    verb = get_verb(name)
    return verb.stakes if verb else None


def _populate_default_registry() -> None:
    """Populate the registry with the verbs PRs #147-154 added.

    Called at import time so the registry is non-empty without any
    explicit bootstrapping. Idempotent — re-running just re-registers
    the same rows. Tests that want a clean registry use
    ``mock.patch.dict(_REGISTRY, {}, clear=True)``.
    """
    # Default-mode verbs: every user sees these.
    register(
        MemoryVerb(
            name="compare",
            description=(
                "Compare two SWMM runs on continuity metrics and return "
                "a structured verdict."
            ),
            cli_path="aiswmm compare",
            mode="default",
            stakes="low",
        )
    )
    register(
        MemoryVerb(
            name="cite",
            description=(
                "Print a citation entry from the project's citations.yaml."
            ),
            cli_path="aiswmm cite",
            mode="default",
            stakes="low",
        )
    )
    register(
        MemoryVerb(
            name="storm",
            description=(
                "Generate an algorithmic design storm in SWMM "
                "[TIMESERIES] format."
            ),
            cli_path="aiswmm storm",
            mode="default",
            stakes="low",
        )
    )
    register(
        MemoryVerb(
            name="transfer",
            description=(
                "Recommend warm-start parameters for a new INP by ranking "
                "calibrated prior cases by watershed similarity."
            ),
            cli_path="aiswmm transfer",
            mode="default",
            stakes="low",
        )
    )

    # Expert-mode verbs: additive — surfaced only when the user opts
    # into the expert verb set. High-stakes labels here flow straight
    # into the memory-informed policy's hitl branch when evidence is
    # absent.
    register(
        MemoryVerb(
            name="uncertainty.plan",
            description=(
                "Plan a parameter uncertainty scan over a base INP "
                "without running SWMM."
            ),
            cli_path="aiswmm uncertainty plan",
            mode="expert",
            stakes="low",
        )
    )
    register(
        MemoryVerb(
            name="calibration-memory.read",
            description=(
                "Read accepted calibrations from calibration_memory.jsonl "
                "to anchor new threshold proposals."
            ),
            cli_path="aiswmm calibration-memory read",
            mode="expert",
            stakes="high",
        )
    )
    register(
        MemoryVerb(
            name="negative-lessons.read",
            description=(
                "Surface known-bad parameter regions from "
                "negative_lessons.jsonl before a calibration accept."
            ),
            cli_path="aiswmm negative-lessons read",
            mode="expert",
            stakes="high",
        )
    )
    register(
        MemoryVerb(
            name="case-adaptive-thresholds",
            description=(
                "Propose case-specific WARN/FAIL thresholds from "
                "calibration history; advisory only."
            ),
            cli_path="aiswmm case-adaptive-thresholds",
            mode="expert",
            stakes="high",
        )
    )


# Populate at import time so the registry is queryable immediately.
_populate_default_registry()


__all__ = [
    "MemoryVerb",
    "StakesLevel",
    "WorkflowModeLabel",
    "get_verb",
    "list_verbs",
    "register",
    "stakes_for",
]
