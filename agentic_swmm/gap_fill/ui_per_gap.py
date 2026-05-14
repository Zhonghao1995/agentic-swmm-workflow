"""Per-gap pause UI for L5 subjective judgements (PRD-GF-L5).

The L5 path differs from the batched L1/L3 form in :mod:`agentic_swmm.gap_fill.ui`:

- One judgement at a time. The modeller gives full attention to each
  pick because a subjective choice has downstream ripple (e.g.
  changing the storm event narrows the calibration window).
- The renderer surfaces the numbered candidate list with each one's
  hydrological tradeoff, plus the evidence reference (so the user
  can open the upstream QA artefact) and the LLM call_id (so the
  reviewer can chase the enumerator's full prompt+response in
  ``09_audit/llm_prompts/``).
- ``[defer]`` is an explicit, supported choice. It raises
  :class:`JudgementDeferred` so the runtime can abort the workflow
  cleanly without recording a bogus pick.
- Non-TTY contexts **always block**. L5 is the one gap-fill severity
  where no env var unlocks an automation path — judgement cannot be
  rubber-stamped, even with ``AISWMM_HITL_AUTO_APPROVE=1`` or
  ``AISWMM_GAP_REGISTRY_ONLY=1`` set.

The function returns a tuple ``(user_pick_id, user_note)`` so the
caller can build a :class:`agentic_swmm.gap_fill.protocol.GapDecision`
with the L5 fields populated. The free-form note is optional —
``None`` if the user just hits Enter.
"""

from __future__ import annotations

import sys
from typing import IO, Iterable

from agentic_swmm.gap_fill.protocol import GapCandidate


class JudgementDeferred(RuntimeError):
    """Raised when the user types ``defer`` at the prompt.

    The runtime translates this into a clean workflow abort: the agent
    reports "judgement deferred" rather than picking arbitrarily or
    blocking forever. The decision is *not* recorded — there is
    nothing to record.
    """


class JudgementBlocked(RuntimeError):
    """Raised when the prompt is requested in a non-interactive context.

    L5 has no automation fallback. Both ``AISWMM_HITL_AUTO_APPROVE``
    and ``AISWMM_GAP_REGISTRY_ONLY`` are explicitly *not* honoured
    here — that is the PRD-GF-L5 failure-path matrix, and it is the
    paper-governance story (judgement is never automated).
    """


def _format_candidates(
    candidates: Iterable[GapCandidate],
    *,
    evidence_ref: str,
    llm_call_id: str,
    gap_kind: str,
    stdout: IO[str],
) -> None:
    """Write the mock-up from the PRD to ``stdout``.

    Format::

        ⚠  Agent paused — judgement required
        ----------------------------------
        Gap: <gap_kind>

        LLM enumerates (no preference):

          (1) <summary>
                tradeoff: <tradeoff>
          (2) ...

        evidence: <evidence_ref>
        llm_call_id: <id>

        Pick [1/2/.../defer]:
        Note (optional, free-form):

    The leading sentinel character is plain ASCII (``!``) rather than
    the PRD's emoji so the UI is grep-friendly and renders cleanly in
    log captures.
    """
    stdout.write("\n")
    stdout.write("!! Agent paused -- judgement required\n")
    stdout.write("-" * 50 + "\n")
    stdout.write(f"Gap: {gap_kind}\n\n")
    stdout.write("LLM enumerates (no preference):\n\n")
    for index, cand in enumerate(candidates, start=1):
        stdout.write(f"  ({index}) {cand.summary}\n")
        stdout.write(f"        tradeoff: {cand.tradeoff}\n")
    stdout.write(f"\nevidence: {evidence_ref}\n")
    stdout.write(f"llm_call_id: {llm_call_id}\n\n")
    stdout.flush()


def prompt_judgement(
    *,
    gap_kind: str,
    candidates: list[GapCandidate],
    evidence_ref: str,
    llm_call_id: str,
    is_tty: bool,
    stdout: IO[str] | None = None,
) -> tuple[str, str | None]:
    """Show the per-gap form and return ``(user_pick_id, user_note)``.

    ``is_tty`` is an explicit parameter so tests can drive both
    branches deterministically. The runtime passes
    ``sys.stdin.isatty() and sys.stdout.isatty()``.

    Raises:

    - :class:`JudgementDeferred` if the user types ``defer``.
    - :class:`JudgementBlocked` if ``is_tty=False`` (the non-TTY
      branch — env vars do **not** unlock automation here).
    - :class:`ValueError` if ``candidates`` is empty — the caller
      should not invoke the UI without at least one candidate.
    """
    if not candidates:
        raise ValueError("prompt_judgement requires at least one candidate")

    out = stdout if stdout is not None else sys.stdout

    if not is_tty:
        # L5 has no auto-approve / registry-only fallback. The two env
        # vars exist for L1/L3 (proposer-driven) and are deliberately
        # ignored here — judgement is human-only.
        raise JudgementBlocked(
            "L5 judgement requested in non-interactive context; "
            "judgement cannot be auto-approved or registry-resolved"
        )

    _format_candidates(
        candidates,
        evidence_ref=evidence_ref,
        llm_call_id=llm_call_id,
        gap_kind=gap_kind,
        stdout=out,
    )

    valid_indices = {str(i) for i in range(1, len(candidates) + 1)}
    user_pick_id: str | None = None
    while user_pick_id is None:
        choice = input(f"Pick [1-{len(candidates)}/defer]: ").strip().lower()
        if choice in {"defer", "d"}:
            raise JudgementDeferred(
                f"user deferred L5 judgement for gap_kind={gap_kind!r}"
            )
        if choice in valid_indices:
            user_pick_id = candidates[int(choice) - 1].id
            break
        out.write(
            f"Please type 1-{len(candidates)} or 'defer'.\n"
        )
        out.flush()

    note_raw = input("Note (optional, free-form): ").strip()
    user_note = note_raw if note_raw else None
    return user_pick_id, user_note


__all__ = [
    "JudgementBlocked",
    "JudgementDeferred",
    "prompt_judgement",
]
