"""LLM enumerator for L5 subjective gap-fill (PRD-GF-L5).

This module is the LLM half of the L5 path. When the agent calls
``request_gap_judgement`` the runtime delegates the candidate
enumeration here. The contract is intentionally narrow:

- Build a prompt that asks the LLM to list N candidates with each
  one's hydrological tradeoff cited. The prompt **hard-constrains**
  the LLM with the exact phrase "do not recommend, prefer, rank, or
  score" — the enumerator must present, not choose.
- Invoke the provider through the same seam ``planner.py`` uses
  (``respond_with_tools``) but with no tools — we are asking for
  structured prose, not a tool call.
- Funnel the invocation through
  :func:`agentic_swmm.audit.llm_calls.record_llm_call` with
  ``caller="gap_fill.enumerator"`` and
  ``model_role="enumerate_options"`` so the L5 decision can later
  cross-reference the prompt dump.
- Parse the response into :class:`GapCandidate` dataclasses.
  Tolerates plain JSON, JSON wrapped in ```json``` fenced code
  blocks, or JSON embedded in prose; malformed payloads raise
  :class:`EnumeratorParseError` so the caller fails loudly rather
  than silently returning nothing.

The module deliberately does **not** know about the UI or the
recorder. It returns parsed candidates plus the ``call_id`` so the
tool handler can wire them into the per-gap UI and the L5
:class:`GapDecision`.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from agentic_swmm.audit.llm_calls import extract_usage_tokens, record_llm_call
from agentic_swmm.gap_fill.protocol import GapCandidate


class EnumeratorParseError(RuntimeError):
    """Raised when the LLM response cannot be parsed into candidates.

    The L5 path treats a parse failure as fatal: returning nothing
    would silently degrade the audit (no candidates to record) and
    leave the user looking at an empty menu. The caller must catch
    this and surface a clean error to the agent so it can either
    re-call the tool with a clearer prompt or fall back to
    ``request_expert_review``.
    """


# Hard-constraint instruction baked into the system prompt. The
# substring "do not recommend" is asserted by the test suite — moving
# it requires updating both sides at once. The wording is intentionally
# blunt: the LLM is presented with hydrological judgement options and
# its sole job is to enumerate them with tradeoffs.
_SYSTEM_PROMPT = (
    "You are presented with a hydrological judgement. "
    "Enumerate exactly N candidate options. "
    "For each, cite the hydrological tradeoff in one sentence. "
    "DO NOT recommend, prefer, rank, or score any option. "
    "Your sole job is to present candidates so the human modeller "
    "can choose. Output as structured JSON with the exact shape: "
    '{"candidates": [{"id": "cand_1", "summary": "...", "tradeoff": "..."}, ...]}.'
)


def _build_user_message(
    *,
    gap_kind: str,
    context: dict[str, Any],
    evidence_ref: str,
    n_candidates: int,
) -> str:
    """Compose the user-side prompt with the per-gap context.

    The context dict is dumped as JSON for the LLM to read; we keep the
    keys uncommented so the model sees the same shape the tool
    arguments use (``workflow``, ``step``, etc.). ``evidence_ref`` is
    surfaced verbatim so the LLM can refer to the upstream QA artefact
    in its summaries.
    """
    return (
        f"Gap kind: {gap_kind}\n"
        f"Context: {json.dumps(context, ensure_ascii=False, sort_keys=True)}\n"
        f"Evidence reference: {evidence_ref}\n"
        f"Number of candidates required: {n_candidates}\n\n"
        "Enumerate candidates and return JSON only. Remember: DO NOT recommend."
    )


def _strip_code_fences(text: str) -> str:
    """Remove a leading ```json ... ``` fence if present.

    The provider occasionally returns the JSON payload wrapped in a
    fenced code block (the chat models do this for any structured
    output). We detect the fence and pull out the inner body; if no
    fence is present we return the text unchanged.
    """
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


def _parse_response(text: str) -> list[GapCandidate]:
    """Parse the LLM response text into a list of :class:`GapCandidate`.

    Tries direct ``json.loads`` first, then strips code fences, then
    falls back to a regex that pulls the first ``{...}`` JSON object
    out of free-form prose. Any failure raises
    :class:`EnumeratorParseError` with a short diagnostic — the
    caller surfaces this back to the agent.
    """
    if not text or not text.strip():
        raise EnumeratorParseError("LLM returned empty response")

    candidates_payload = _try_json_load(text)
    if candidates_payload is None:
        candidates_payload = _try_json_load(_strip_code_fences(text))
    if candidates_payload is None:
        # Last-resort: pull the first top-level JSON object out of the
        # text. Useful when the model says "Sure! Here is the JSON:
        # {...}" without a fence.
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            candidates_payload = _try_json_load(match.group(0))
    if candidates_payload is None:
        raise EnumeratorParseError(
            "LLM response does not contain a parseable JSON object"
        )

    if not isinstance(candidates_payload, dict):
        raise EnumeratorParseError(
            "LLM response JSON is not an object with a 'candidates' key"
        )
    raw_list = candidates_payload.get("candidates")
    if not isinstance(raw_list, list) or not raw_list:
        raise EnumeratorParseError(
            "LLM response missing 'candidates' list, or list is empty"
        )

    parsed: list[GapCandidate] = []
    for index, entry in enumerate(raw_list):
        if not isinstance(entry, dict):
            raise EnumeratorParseError(
                f"candidate at index {index} is not an object"
            )
        try:
            parsed.append(GapCandidate.from_dict(entry))
        except ValueError as exc:
            raise EnumeratorParseError(
                f"candidate at index {index} is malformed: {exc}"
            ) from exc
    return parsed


def _try_json_load(text: str) -> Any | None:
    """Return parsed JSON or ``None`` if the text is not valid JSON."""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def enumerate_candidates(
    *,
    gap_kind: str,
    context: dict[str, Any],
    evidence_ref: str,
    n_candidates: int = 3,
    llm_provider: Any,
    run_dir: Path | str,
    derived_decision_ref: str | None = None,
) -> tuple[list[GapCandidate], str]:
    """Ask the LLM to enumerate ``n_candidates`` options for an L5 gap.

    Returns ``(candidates, call_id)`` where ``call_id`` is the
    :func:`record_llm_call` return value so the caller can plug it
    into the L5 :class:`GapDecision` as ``enumerator_llm_call_id``.

    Required keyword arguments:

    - ``gap_kind``: one of the L5 categories (``"pour_point"``,
      ``"storm_event_selection"``, ``"metric_weighting"``,
      ``"continuity_tolerance"``, …).
    - ``context``: free-form ``{key: value}`` map carrying the
      workflow / step labels. Surfaced to the LLM verbatim so it
      can compose hydrologically-aware summaries.
    - ``evidence_ref``: pointer to an upstream QA artefact
      (``"06_qa/foo.json"``); the LLM references it in its
      summaries.
    - ``llm_provider``: a provider with a ``respond_with_tools``
      method following the same shape ``planner.py`` uses
      (``system_prompt``, ``input_items``, ``tools=[]``).
    - ``run_dir``: where ``09_audit/`` lives. The recorder writes
      one JSONL line + one prompt dump under this dir.

    Optional:

    - ``n_candidates``: default 3 per PRD-GF-L5.
    - ``derived_decision_ref``: cross-link back into
      ``gap_decisions.json`` once the recorder has assigned the
      decision_id. ``None`` is fine — the link can be added in a
      later pass.
    """
    user_message = _build_user_message(
        gap_kind=gap_kind,
        context=dict(context or {}),
        evidence_ref=evidence_ref,
        n_candidates=n_candidates,
    )
    input_items = [{"role": "user", "content": user_message}]

    _call_start = time.monotonic()
    response = llm_provider.respond_with_tools(
        system_prompt=_SYSTEM_PROMPT,
        input_items=input_items,
        tools=[],
    )
    _duration_ms = int((time.monotonic() - _call_start) * 1000)

    tokens_in, tokens_out = extract_usage_tokens(response)
    call_id = record_llm_call(
        run_dir=run_dir,
        caller="gap_fill.enumerator",
        model_role="enumerate_options",
        prompt=(_SYSTEM_PROMPT, input_items),
        response=response,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=_duration_ms,
        derived_decision_ref=derived_decision_ref,
    )

    response_text = getattr(response, "text", None)
    if not isinstance(response_text, str):
        # Defensive: a provider that returns a raw string (some mocks)
        # falls through here.
        if isinstance(response, str):
            response_text = response
        else:
            response_text = ""
    candidates = _parse_response(response_text)
    return candidates, call_id


__all__ = [
    "EnumeratorParseError",
    "enumerate_candidates",
]
