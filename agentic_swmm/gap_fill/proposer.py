"""Layered proposer for gap-fill (PRD-GF-CORE).

Three-layer pipeline:

1. **Registry lookup** — ``defaults_table.yaml`` at the repo root.
   A hit produces a ``GapDecision`` with ``proposer.source=registry``,
   ``confidence=HIGH``, and ``registry_ref`` / ``literature_ref``
   filled from the entry. No LLM call is made.
2. **LLM-grounded** — when the registry misses and ``AISWMM_GAP_REGISTRY_ONLY``
   is unset, the proposer invokes a small ``LLMProposalFn`` callable
   that knows how to wire up the planner LLM. Every invocation is
   recorded via :func:`agentic_swmm.audit.llm_calls.record_llm_call`
   with ``caller="gap_fill.proposer"``. A HIGH-confidence response
   becomes a ``llm_grounded`` proposal; LOW-confidence falls through.
3. **Human fallthrough** — when no machine proposal is available
   (L1 paths; LLM low-confidence; LLM disabled) the decision is
   returned with ``source=human``, ``proposed_value=None`` so the UI
   collects the value.

Environment variables:

- ``AISWMM_GAP_REGISTRY_ONLY=1`` — L3 lookups are restricted to the
  registry. A miss raises :class:`GapFillRegistryOnlyMiss` so CI
  fails loudly instead of guessing. ``decided_by`` is
  ``"auto_registry"`` on a hit.
- ``AISWMM_HITL_AUTO_APPROVE=1`` — once the proposer has a value
  (registry or LLM), it auto-accepts with ``decided_by="auto_approve"``
  and a loud ``AUTO_APPROVE`` log line on stderr (per PR #47
  governance).

The returned :class:`agentic_swmm.gap_fill.protocol.GapDecision` is
*not* recorded by this function — the recorder is a separate
concern. The proposer's contract is "decide the value and the
source", the recorder's contract is "persist the decision".
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from agentic_swmm.gap_fill.protocol import (
    GapDecision,
    GapSignal,
    ProposerInfo,
    new_decision_id,
)
from agentic_swmm.utils.paths import repo_root


class GapFillRegistryOnlyMiss(RuntimeError):
    """Raised when ``AISWMM_GAP_REGISTRY_ONLY=1`` and no registry hit.

    The error message names the missed field so a CI failure points
    directly at the cold-start defaults that need a new entry.
    """


@dataclass(frozen=True)
class LLMProposal:
    """The shape an ``LLMProposalFn`` returns.

    Fields:

    - ``value``: the proposed value, or ``None`` if the LLM declined
      to propose (low confidence / out of scope).
    - ``literature_ref``: the LLM's cited reference (free text).
      ``None`` when no citation was produced.
    - ``confidence``: ``"HIGH"``, ``"MEDIUM"``, or ``"LOW"``.
    - ``call_id``: the ``record_llm_call`` return value so the
      proposer can wire it into ``ProposerInfo.llm_call_id``.
    """

    value: Any
    literature_ref: str | None
    confidence: str
    call_id: str | None


LLMProposalFn = Callable[..., LLMProposal]


# Alias map from common SWMM tool-arg names to canonical registry
# entry names. Kept tight on purpose — the test suite (and the PRD's
# Done Criterion 5) asserts that a known alias resolves to a registry
# hit. Adding aliases is the responsibility of GF-PROMOTE; we ship
# only the few needed for the CORE E2E scenarios.
_REGISTRY_ALIASES: dict[str, str] = {
    # Manning's n
    "manning_n_imperv": "manning_n_paved",
    "manning_n_perv": "manning_n_grass",
    "n_imperv": "manning_n_paved",
    "n_perv": "manning_n_grass",
    # Horton infiltration
    "max_rate": "horton_max_infiltration_rate",
    "min_rate": "horton_min_infiltration_rate",
    "maxrate": "horton_max_infiltration_rate",
    "minrate": "horton_min_infiltration_rate",
    "decay": "horton_decay_constant",
    "decay_constant": "horton_decay_constant",
    # Depression storage
    "s_imperv": "depression_storage_impervious",
    "s_perv": "depression_storage_pervious",
}


def _defaults_table_path() -> Path:
    """Resolve the path to ``defaults_table.yaml`` at the repo root.

    Tests can override via ``AISWMM_DEFAULTS_TABLE`` so they don't
    have to mutate the shipped file. Production callers pick the
    repo-root copy.
    """
    override = os.environ.get("AISWMM_DEFAULTS_TABLE")
    if override:
        return Path(override)
    return repo_root() / "defaults_table.yaml"


def _load_registry() -> dict[str, dict[str, Any]]:
    """Read ``defaults_table.yaml`` into a name→entry map.

    A missing / unparseable file yields an empty registry (no rows).
    The proposer then behaves as if every L3 field misses the
    registry and falls through to the LLM. We do not raise here
    because a tools-only contributor without the YAML file should
    still get a working LLM path (with a loud audit trail).
    """
    path = _defaults_table_path()
    if not path.is_file():
        return {}
    try:
        import yaml  # local import — pyyaml is an indirect dep
    except ImportError:  # pragma: no cover - defensive
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {str(name): dict(entry) for name, entry in entries.items() if isinstance(entry, dict)}


def _resolve_alias(field: str) -> str:
    """Map a tool-arg name to its canonical registry entry name.

    Falls through to the original name when no alias is known —
    callers then look up that name directly in the registry.
    """
    return _REGISTRY_ALIASES.get(field, field)


def _registry_lookup(field: str) -> tuple[str, dict[str, Any]] | None:
    """Return ``(entry_name, entry_payload)`` for ``field`` or ``None``.

    Tries the direct field name first, then falls back to the alias
    map. Returns ``None`` when neither hits — the caller routes that
    to the LLM / human path.
    """
    registry = _load_registry()
    if field in registry:
        return field, registry[field]
    alias = _resolve_alias(field)
    if alias in registry:
        return alias, registry[alias]
    return None


def _registry_hit_to_decision(
    signal: GapSignal,
    entry_name: str,
    entry: dict[str, Any],
    *,
    decided_by: str,
) -> GapDecision:
    """Convert a registry entry to a :class:`GapDecision`."""

    value = entry.get("value")
    decision_id = new_decision_id()
    decided_at = _now_iso()
    return GapDecision(
        decision_id=decision_id,
        gap_id=signal.gap_id,
        severity=signal.severity,
        field=signal.field,
        proposer=ProposerInfo(
            source="registry",
            confidence="HIGH",
            registry_ref=f"defaults_table.yaml#{entry_name}",
            literature_ref=entry.get("source"),
            llm_call_id=None,
        ),
        proposed_value=value,
        # final_value is auto-set to proposed_value for registry-hit
        # / auto-approve paths. The UI layer overwrites this when a
        # human reviews + edits.
        final_value=value,
        proposer_overridden=False,
        decided_by=decided_by,
        decided_at=decided_at,
        resume_mode="tool_retry",
        human_decisions_ref=None,
    )


def _llm_to_decision(
    signal: GapSignal,
    proposal: LLMProposal,
    *,
    decided_by: str,
) -> GapDecision:
    """Convert a HIGH-confidence LLM proposal to a :class:`GapDecision`."""

    decision_id = new_decision_id()
    decided_at = _now_iso()
    return GapDecision(
        decision_id=decision_id,
        gap_id=signal.gap_id,
        severity=signal.severity,
        field=signal.field,
        proposer=ProposerInfo(
            source="llm_grounded",
            confidence=proposal.confidence,
            registry_ref=None,
            literature_ref=proposal.literature_ref,
            llm_call_id=proposal.call_id,
        ),
        proposed_value=proposal.value,
        final_value=proposal.value,
        proposer_overridden=False,
        decided_by=decided_by,
        decided_at=decided_at,
        resume_mode="tool_retry",
        human_decisions_ref=None,
    )


def _human_required_decision(
    signal: GapSignal,
    llm_call_id: str | None = None,
    confidence: str = "LOW",
) -> GapDecision:
    """Build a ``source=human`` decision with no proposed value.

    The UI layer overwrites ``final_value`` and flips
    ``decided_by`` to ``"human"`` when the user supplies a value.
    Returning this shape from the proposer keeps the UI's contract
    simple: it always receives a partially-filled decision and only
    needs to fill in the user's pick.
    """

    decision_id = new_decision_id()
    decided_at = _now_iso()
    return GapDecision(
        decision_id=decision_id,
        gap_id=signal.gap_id,
        severity=signal.severity,
        field=signal.field,
        proposer=ProposerInfo(
            source="human",
            confidence=confidence,
            registry_ref=None,
            literature_ref=None,
            llm_call_id=llm_call_id,
        ),
        proposed_value=None,
        final_value=None,
        proposer_overridden=False,
        decided_by="human",
        decided_at=decided_at,
        resume_mode="tool_retry",
        human_decisions_ref=None,
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_env_true(name: str) -> bool:
    """Return True iff env var ``name`` is set to a truthy value.

    The truthy set matches the rest of the codebase
    (``"1"``, ``"true"``, ``"yes"`` …) and is intentionally narrow so
    a value like ``"0"`` is False even when the var is present.
    """
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no"}


def _log_auto_approve(decision: GapDecision) -> None:
    """Emit a loud stderr line whenever ``AUTO_APPROVE`` fires.

    The PR #47 contract requires auto-approval to be loud — silent
    auto-approval is the failure mode this whole governance layer
    was added to prevent. The shape mirrors ``LLM_TRACE_DROPPED:``
    so a CI log scanner can spot both kinds of events with one regex.
    """
    sys.stderr.write(
        f"AUTO_APPROVE:{decision.decision_id} "
        f"field={decision.field!r} value={decision.final_value!r} "
        f"source={decision.proposer.source}\n"
    )
    sys.stderr.flush()


def _resolve_decided_by(default: str) -> str:
    """Resolve the ``decided_by`` value given the env-var matrix.

    ``AISWMM_HITL_AUTO_APPROVE=1`` always wins (the explicit auto-
    approve switch). Otherwise the caller's ``default`` is used
    (``"human"`` for interactive, ``"auto_registry"`` for the
    registry-only branch).
    """
    if _is_env_true("AISWMM_HITL_AUTO_APPROVE"):
        return "auto_approve"
    return default


def propose(
    *,
    signal: GapSignal,
    run_dir: Path | str,
    llm_proposal_fn: LLMProposalFn | None,
) -> GapDecision:
    """Decide the value for one gap and return the partial decision.

    The returned :class:`GapDecision` carries everything except —
    when the path is interactive — the user's edited ``final_value``.
    The UI layer takes care of that handoff. For the registry-only
    and auto-approve paths the final value is already set (registry
    hit) or set to the LLM's proposed value (auto-approve).

    Parameters:

    - ``signal``: the :class:`GapSignal` we are filling.
    - ``run_dir``: the per-session run directory (used to scope LLM
      audit writes when the LLM path fires).
    - ``llm_proposal_fn``: a callable that knows how to consult the
      LLM and return an :class:`LLMProposal`. May be ``None`` when
      no LLM is wired (registry-only mode, tests). When ``None`` and
      the registry misses, the decision falls through to ``source=
      human``.
    """

    # L1 paths bypass the registry entirely. There is no defensible
    # way to propose a file path from a textbook table.
    if signal.severity == "L1":
        decision = _human_required_decision(signal, confidence="HIGH")
        return decision

    # L3: registry first.
    hit = _registry_lookup(signal.field)
    if hit is not None:
        entry_name, entry = hit
        decided_by = _resolve_decided_by("auto_registry" if _is_env_true("AISWMM_GAP_REGISTRY_ONLY") else "human")
        decision = _registry_hit_to_decision(
            signal, entry_name, entry, decided_by=decided_by
        )
        if decided_by == "auto_approve":
            _log_auto_approve(decision)
        return decision

    # Registry miss.
    if _is_env_true("AISWMM_GAP_REGISTRY_ONLY"):
        raise GapFillRegistryOnlyMiss(
            f"AISWMM_GAP_REGISTRY_ONLY=1 but no registry entry for "
            f"field {signal.field!r} (gap_id={signal.gap_id})"
        )

    # LLM-grounded path.
    if llm_proposal_fn is None:
        return _human_required_decision(signal, confidence="LOW")

    proposal = llm_proposal_fn(signal=signal, run_dir=Path(run_dir))
    if proposal.value is None or proposal.confidence == "LOW":
        return _human_required_decision(
            signal, llm_call_id=proposal.call_id, confidence="LOW"
        )

    decided_by = _resolve_decided_by("human")
    decision = _llm_to_decision(signal, proposal, decided_by=decided_by)
    if decided_by == "auto_approve":
        _log_auto_approve(decision)
    return decision


def propose_batch(
    *,
    signals: Iterable[GapSignal],
    run_dir: Path | str,
    llm_proposal_fn: LLMProposalFn | None,
) -> list[GapDecision]:
    """Run :func:`propose` over a batch of gaps.

    The runtime owns the batching boundary (one form per tool call);
    this helper keeps the call site clean.
    """
    return [
        propose(signal=s, run_dir=run_dir, llm_proposal_fn=llm_proposal_fn)
        for s in signals
    ]


__all__ = [
    "GapFillRegistryOnlyMiss",
    "LLMProposal",
    "LLMProposalFn",
    "propose",
    "propose_batch",
]
