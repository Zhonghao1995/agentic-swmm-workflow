"""Batched accept/edit/reject form for gap-fill (PRD-GF-CORE).

When the runtime intercepts one or more gaps from a single tool call,
it hands the proposer's output to :func:`review_batch`. The UI:

- Shows one combined form (not N separate prompts) — user story 3.
- For each gap the user picks ``[a]ccept`` / ``[e]dit`` / ``[r]eject``.
  Edit captures a custom ``final_value`` with ``proposer_overridden=
  True``. Reject raises :class:`GapFillRejected` so the runtime can
  abort the workflow with a clean error.
- For L1 gaps with no proposed value, prompts for the file path
  directly (no accept/edit/reject — the user must supply one).

Non-TTY behaviour follows the PRD's failure-path matrix:

- Default non-TTY: raises :class:`GapFillNonInteractive` — never
  silent-fill.
- ``AISWMM_HITL_AUTO_APPROVE=1``: each decision with a non-``None``
  ``proposed_value`` is auto-accepted (the proposer has already
  flipped ``decided_by`` to ``"auto_approve"`` and logged loudly).
  L1 paths (``proposed_value is None``) still raise — paths cannot
  be auto-approved.
- ``AISWMM_GAP_REGISTRY_ONLY=1``: registry-only auto-accepts
  (``decided_by=auto_registry``); L1 still raises.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from typing import IO, Iterable

from agentic_swmm.gap_fill.protocol import GapDecision


class GapFillRejected(RuntimeError):
    """Raised when the user rejects a gap in the interactive form.

    The runtime catches this and converts it to an abort: the
    workflow does not continue without a value. The rejected gap's
    ``decision_id`` is in the message so a session log shows which
    decision aborted the run.
    """


class GapFillNonInteractive(RuntimeError):
    """Raised when the form needs a human but none is available.

    Non-TTY contexts without a matching env-var fallback (auto-
    approve, registry-only) trip this so the workflow fails loudly
    rather than silently guessing.
    """


def _is_env_true(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no"}


def _replace(decision: GapDecision, **changes) -> GapDecision:
    """Return a copy of ``decision`` with selected fields replaced.

    ``dataclasses.replace`` does not work on frozen dataclasses that
    own a frozen sub-dataclass (``ProposerInfo``) the way Python's
    type stubs hint, so we use the same builtin and rely on dataclass
    equality semantics. The helper exists for readability.
    """
    return dataclasses.replace(decision, **changes)


def _format_proposal_line(decision: GapDecision) -> str:
    """Render one row of the batched form.

    Format: ``[N] field (severity): proposed=value source=X confidence=Y``.
    The leading index lets the user mentally pair the prompt with the
    row when multiple gaps fire.
    """
    if decision.proposed_value is None:
        proposed = "<no proposal — please supply a value>"
    else:
        proposed = repr(decision.proposed_value)
    parts = [
        f"  field      : {decision.field}",
        f"  severity   : {decision.severity}",
        f"  proposed   : {proposed}",
        f"  source     : {decision.proposer.source}",
        f"  confidence : {decision.proposer.confidence}",
    ]
    if decision.proposer.registry_ref:
        parts.append(f"  registry   : {decision.proposer.registry_ref}")
    if decision.proposer.literature_ref:
        parts.append(f"  citation   : {decision.proposer.literature_ref}")
    return "\n".join(parts)


def _prompt_for_path(decision: GapDecision, stdout: IO[str]) -> GapDecision:
    """Collect a file path for an L1 gap with no machine proposal."""

    stdout.write(f"\n[gap {decision.field}] path required:\n")
    stdout.write(_format_proposal_line(decision) + "\n")
    stdout.flush()
    value = input(f"Enter path for {decision.field}: ").strip()
    if not value:
        raise GapFillRejected(
            f"empty path for {decision.field} (decision_id={decision.decision_id})"
        )
    return _replace(
        decision,
        final_value=value,
        decided_by="human",
        proposer_overridden=False,
    )


def _prompt_for_l3(decision: GapDecision, stdout: IO[str]) -> GapDecision:
    """Run the accept/edit/reject form for one L3 gap."""

    stdout.write(f"\n[gap {decision.field}] review proposal:\n")
    stdout.write(_format_proposal_line(decision) + "\n")
    stdout.flush()
    while True:
        choice = input("[a]ccept / [e]dit / [r]eject: ").strip().lower()
        if choice in {"a", "accept", ""}:
            return _replace(
                decision,
                final_value=decision.proposed_value,
                decided_by="human",
                proposer_overridden=False,
            )
        if choice in {"e", "edit"}:
            new_value = input(f"new value for {decision.field}: ").strip()
            return _replace(
                decision,
                final_value=new_value,
                decided_by="human",
                proposer_overridden=True,
            )
        if choice in {"r", "reject"}:
            raise GapFillRejected(
                f"user rejected gap {decision.field!r} "
                f"(decision_id={decision.decision_id})"
            )
        stdout.write("Please type 'a', 'e', or 'r'.\n")
        stdout.flush()


def _summary_header(tool_name: str, count: int, stdout: IO[str]) -> None:
    stdout.write(
        f"\n=== gap-fill review ({count} gap{'s' if count != 1 else ''} "
        f"for tool '{tool_name}') ===\n"
    )
    stdout.flush()


def review_batch(
    decisions: Iterable[GapDecision],
    *,
    tool_name: str,
    is_tty: bool,
    stdout: IO[str] | None = None,
) -> list[GapDecision]:
    """Run the batched form over ``decisions`` and return the resolved list.

    ``is_tty`` is an explicit parameter (not auto-detected) so tests
    can drive both branches deterministically. The runtime passes
    ``sys.stdin.isatty() and sys.stdout.isatty()``.

    On non-TTY, the function honours the env-var matrix:

    - ``AISWMM_HITL_AUTO_APPROVE=1`` — auto-accept proposals that
      already carry a ``proposed_value``; L1 (proposed_value is None)
      still raises.
    - ``AISWMM_GAP_REGISTRY_ONLY=1`` — auto-accept registry hits
      (``decided_by=auto_registry`` from the proposer); other paths
      raise.
    - Otherwise raise :class:`GapFillNonInteractive`.
    """
    out = stdout if stdout is not None else sys.stdout
    decisions_list = list(decisions)
    if not decisions_list:
        return []

    if not is_tty:
        return _non_interactive_review(decisions_list, out)

    _summary_header(tool_name, len(decisions_list), out)
    resolved: list[GapDecision] = []
    for decision in decisions_list:
        if decision.proposed_value is None:
            resolved.append(_prompt_for_path(decision, out))
        else:
            resolved.append(_prompt_for_l3(decision, out))
    return resolved


def _non_interactive_review(
    decisions: list[GapDecision], stdout: IO[str]
) -> list[GapDecision]:
    """Resolve a batch without prompting the user.

    The env-var matrix is the only source of truth here: if no env
    var grants permission, we raise.
    """
    auto_approve = _is_env_true("AISWMM_HITL_AUTO_APPROVE")
    registry_only = _is_env_true("AISWMM_GAP_REGISTRY_ONLY")
    resolved: list[GapDecision] = []
    for decision in decisions:
        # L1 paths or any human-required gap (no proposal) cannot be
        # auto-resolved — the runtime must fail loudly.
        if decision.proposed_value is None:
            raise GapFillNonInteractive(
                f"non-TTY and no proposed value for {decision.field!r}; "
                "gap-fill cannot be resolved without a human"
            )
        if registry_only and decision.proposer.source != "registry":
            raise GapFillNonInteractive(
                f"AISWMM_GAP_REGISTRY_ONLY=1 but proposer.source="
                f"{decision.proposer.source!r} for {decision.field!r}"
            )
        if not (auto_approve or registry_only):
            raise GapFillNonInteractive(
                f"non-TTY and no env-var fallback for {decision.field!r}"
            )
        # The proposer has already stamped decided_by appropriately.
        # final_value already matches proposed_value.
        resolved.append(decision)
    return resolved


__all__ = ["review_batch", "GapFillRejected", "GapFillNonInteractive"]
