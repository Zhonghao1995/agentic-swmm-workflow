"""LLM call observer (PRD-LLM-TRACE).

Every LLM API invocation across the agent runtime is funnelled through
:func:`record_llm_call` so the run directory captures a symmetric
LLM-side trace next to the existing ``human_decisions`` ledger. The
contract is intentionally narrow: one observable per call, producing
two artefacts under ``<run_dir>/09_audit/``:

- one JSONL line appended to ``llm_calls.jsonl``
- one full prompt dump at ``llm_prompts/<call_id>.txt``

The observer is a *deep* module — callers funnel here regardless of
caller name (``planner``, ``gap_fill.proposer``, ``memory_reflect``,
…). The schema and atomic-append behaviour live in one place so future
callers do not reinvent their own logging.

Failure modes are deliberately soft. A disk-full / permission /
JSON-encode failure during recording logs ``LLM_TRACE_DROPPED:<call_id>``
to stderr and returns the call_id without raising. The agent workflow
never crashes because audit could not write — losing one trace line is
strictly better than aborting an in-flight SWMM run.

Atomic-append uses a per-line ``open(..., "a")`` pattern; the line is
JSON-encoded (no embedded newlines), written, flushed, and ``fsync``'d
before the file handle closes. JSONL has no header / footer so a
concurrent reader observing a partial write would see a torn last
line, not corrupt earlier records — the prior-art
``decision_recorder.py`` uses tmp-file + ``os.replace`` because its
file is a single JSON document, which has a different concurrency
profile. JSONL append is the right primitive here.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Maximum characters of the response text we inline in the JSONL line
# before the line itself becomes too unwieldy to grep. Long tails are
# truncated; the full prompt is always available via the sibling
# prompt-dump file. We do not truncate the prompt dump.
RESPONSE_INLINE_MAX_CHARS = 4000

# First-N chars of the prompt we copy into ``prompt_summary`` for
# at-a-glance triage. The full prompt is in ``prompt_full_ref``.
PROMPT_SUMMARY_MAX_CHARS = 200


def _now_utc_iso() -> str:
    """Return an ISO-8601 UTC timestamp with second resolution."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _serialise_prompt(prompt: Any) -> str:
    """Render an LLM prompt to a single string for the dump file.

    Callers pass heterogeneous shapes:
    - a plain string (the system_prompt)
    - a list of ``{"role": ..., "content": ...}`` dicts (input_items)
    - a tuple ``(system_prompt, input_items)`` carrying both halves

    We accept all three and produce a deterministic UTF-8 string.
    Anything we cannot recognise falls through to ``json.dumps`` so
    the dump never silently drops data.
    """
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, tuple) and len(prompt) == 2:
        system_prompt, input_items = prompt
        lines = []
        if system_prompt:
            lines.append("=== SYSTEM ===")
            lines.append(str(system_prompt))
        if input_items:
            lines.append("=== INPUT ===")
            lines.append(_serialise_prompt(input_items))
        return "\n".join(lines)
    if isinstance(prompt, list):
        parts = []
        for item in prompt:
            if isinstance(item, dict):
                role = item.get("role") or item.get("type") or "item"
                content = item.get("content") or item.get("output") or ""
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False, sort_keys=True)
                parts.append(f"--- {role} ---\n{content}")
            else:
                parts.append(str(item))
        return "\n".join(parts)
    try:
        return json.dumps(prompt, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(prompt)


def _extract_response_text(response: Any) -> str:
    """Pull a plain-text representation out of a provider response."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    # ``ProviderToolResponse`` dataclass exposes ``.text``.
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(response, dict):
        for key in ("text", "output_text", "response_text"):
            value = response.get(key)
            if isinstance(value, str):
                return value
    return ""


def _extract_tool_calls(response: Any) -> list[str]:
    """Return the list of tool names the response emitted."""
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls is None and isinstance(response, dict):
        tool_calls = response.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []
    names: list[str] = []
    for call in tool_calls:
        name = getattr(call, "name", None)
        if name is None and isinstance(call, dict):
            name = call.get("name")
        if isinstance(name, str):
            names.append(name)
    return names


def _extract_model_alias(response: Any) -> str:
    """Return the provider's reported model alias (best-effort)."""
    model = getattr(response, "model", None)
    if model is None and isinstance(response, dict):
        model = response.get("model")
    return str(model) if isinstance(model, str) else ""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def _write_prompt_dump(dump_path: Path, content: str) -> None:
    """Write the full prompt to its dump file.

    Parent dir creation is the caller's responsibility (we do it in
    :func:`record_llm_call`). We use a direct write rather than the
    tmp-file + rename pattern because each dump path is unique per
    ``call_id``: there is no concurrent writer to fight with.
    """
    dump_path.write_text(content, encoding="utf-8")


def _append_jsonl_line(jsonl_path: Path, payload: dict[str, Any]) -> None:
    """Append a single JSON line to ``jsonl_path``.

    Per-call flush + ``fsync`` so a crashed session preserves the
    trace up to (and not past) the crash point — user story 8 in the
    PRD.
    """
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if "\n" in encoded:
        # Defensive: a stray newline in the payload would split the
        # JSONL line in two. ``json.dumps`` never emits one for
        # in-spec data, but a user-supplied free-text field could
        # in principle smuggle one in. Replace with the escape form.
        encoded = encoded.replace("\n", "\\n")
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def record_llm_call(
    *,
    run_dir: Path | str,
    caller: str,
    model_role: str,
    prompt: Any,
    response: Any,
    model_version: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    duration_ms: int | None = None,
    derived_decision_ref: str | None = None,
) -> str:
    """Record one LLM API invocation.

    Returns the generated ``call_id`` (a uuid4 hex string) so the
    caller can cross-reference the audit entry from elsewhere in the
    trace if they want. The function never raises: a filesystem error
    is logged to stderr as ``LLM_TRACE_DROPPED:<call_id>`` and the
    call_id is still returned so the caller's downstream logic keeps
    working.

    Required keyword arguments:

    - ``run_dir``: the per-session run directory. The observer writes
      under ``<run_dir>/09_audit/`` and auto-creates that dir if
      missing.
    - ``caller``: a short tag identifying the calling module (e.g.
      ``"planner"``, ``"gap_fill.proposer"``).
    - ``model_role``: what the LLM was being asked to do (e.g.
      ``"decide_next_tool"``, ``"propose_param_value"``).
    - ``prompt``: the prompt as seen by the LLM. Accepts a string, a
      list of role/content dicts, or a tuple ``(system_prompt,
      input_items)``.
    - ``response``: the provider's response object. The observer pulls
      ``.text``, ``.tool_calls``, and ``.model`` defensively — if the
      object does not have those attributes the corresponding fields
      land empty rather than raising.

    Optional:

    - ``model_version``: dated checkpoint string (e.g.
      ``"claude-opus-4-7-20260420"``). Defaults to the alias from
      ``response.model`` when omitted.
    - ``tokens_in`` / ``tokens_out``: usage counts. ``None`` is fine —
      the provider may not surface them.
    - ``duration_ms``: wall-clock duration of the API call.
    - ``derived_decision_ref``: optional cross-link into another
      ledger (e.g. ``"09_audit/gap_decisions.json#<id>"``). ``None``
      for planner-level calls.
    """
    call_id = uuid.uuid4().hex
    try:
        run_dir_path = Path(run_dir)
        audit_dir = run_dir_path / "09_audit"
        prompts_dir = audit_dir / "llm_prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)

        prompt_text = _serialise_prompt(prompt)
        response_text = _extract_response_text(response)
        tool_calls = _extract_tool_calls(response)
        model_alias = _extract_model_alias(response)
        if not model_version:
            model_version = model_alias or ""

        prompt_dump_path = prompts_dir / f"{call_id}.txt"
        _write_prompt_dump(prompt_dump_path, prompt_text)

        prompt_summary = _truncate(prompt_text, PROMPT_SUMMARY_MAX_CHARS)
        response_text_field = _truncate(response_text, RESPONSE_INLINE_MAX_CHARS)

        payload: dict[str, Any] = {
            "call_id": call_id,
            "timestamp_utc": _now_utc_iso(),
            "caller": caller,
            "model_role": model_role,
            "model_alias": model_alias,
            "model_version": model_version,
            "prompt_summary": prompt_summary,
            "prompt_full_ref": f"09_audit/llm_prompts/{call_id}.txt",
            "response_text": response_text_field,
            "tool_calls_emitted": tool_calls,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "duration_ms": duration_ms,
            "derived_decision_ref": derived_decision_ref,
        }

        jsonl_path = audit_dir / "llm_calls.jsonl"
        _append_jsonl_line(jsonl_path, payload)
    except Exception as exc:  # pragma: no cover - defensive guard
        # Fail-soft: workflow must never crash because audit could not
        # write. We surface the drop to stderr so a human looking at
        # the session log knows a trace line is missing, and we keep
        # going.
        print(
            f"LLM_TRACE_DROPPED:{call_id} ({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
    return call_id


def extract_usage_tokens(response: Any) -> tuple[int | None, int | None]:
    """Pull ``(input_tokens, output_tokens)`` out of a provider response.

    Tries the Anthropic SDK shape first (``response.usage.input_tokens`` /
    ``output_tokens``) and falls back to the OpenAI shape
    (``prompt_tokens`` / ``completion_tokens``). Also handles the case
    where the response is a dict (e.g. ``raw`` payload). Returns
    ``(None, None)`` if neither shape matches — callers must not
    crash because token counts are unavailable.
    """
    def _coerce(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        return None

    def _from_obj(obj: Any) -> tuple[int | None, int | None]:
        if obj is None:
            return None, None
        # Anthropic SDK: input_tokens / output_tokens
        in_val = getattr(obj, "input_tokens", None)
        out_val = getattr(obj, "output_tokens", None)
        if in_val is not None or out_val is not None:
            return _coerce(in_val), _coerce(out_val)
        # OpenAI: prompt_tokens / completion_tokens
        in_val = getattr(obj, "prompt_tokens", None)
        out_val = getattr(obj, "completion_tokens", None)
        if in_val is not None or out_val is not None:
            return _coerce(in_val), _coerce(out_val)
        if isinstance(obj, dict):
            in_val = obj.get("input_tokens") or obj.get("prompt_tokens")
            out_val = obj.get("output_tokens") or obj.get("completion_tokens")
            return _coerce(in_val), _coerce(out_val)
        return None, None

    usage = getattr(response, "usage", None)
    if usage is None:
        raw = getattr(response, "raw", None)
        if isinstance(raw, dict):
            usage = raw.get("usage")
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    return _from_obj(usage)


__all__ = ["record_llm_call", "extract_usage_tokens"]
