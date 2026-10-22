"""Drafting: a model writes a proposal's body at review time (ADR-0014, #564).

The session end writes a template proposal and never calls a model. When
the user lists proposals, the undrafted open ones are drafted through the
configured route: the model gets the evidence, excerpts of the evidence
sessions (from the ``tool_events`` table), the target's current text and
the dedupe finding, and returns JSON with the condition, the action, the
why, the addition and, for a SKILL.md target only, an optional unified
diff that edits existing text. The program validates the draft (the note
cap, a diff that applies strictly, the skill validator) and otherwise
keeps the template, recording why. The template stays in the front
matter as the fallback and the comparison; the call's cost and transcript
go to a ``.draft.json`` sidecar next to the proposal.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.memory import proposals as _proposals
from agentic_swmm.memory.proposals import Proposal, apply_addition

#: Characters of prompt the drafter may receive.
DRAFT_INPUT_CAP = 30_000
#: Drafts per listing, so a rebuild never triggers a burst of model calls.
MAX_DRAFTS_PER_LISTING = 5
#: Below this confidence the listing marks the proposal weak.
WEAK_CONFIDENCE = 0.3
#: Steps of trace kept on each side of the failing call.
EXCERPT_STEPS = 2
MAX_EXCERPT_SESSIONS = 3
#: A drafted bullet for a SKILL.md or the operating memory may run this long.
MAX_BULLET_CHARS = 400

SYSTEM_PROMPT = """You write one short, reviewable change to an agent's tool note, skill text or operating rule, from recorded evidence of runs. The agent plans SWMM stormwater modelling work by calling tools; a failure that was fixed the same way in several runs is the evidence. Write for the planner that will read the text later, in the imperative, general enough for the whole class of cases and never one run's literal path. Say when the rule applies (the condition), what to do (the action) and why it works (the reason, inferred from the trace). Return JSON only, with these keys: condition, action, why, addition, diff, confidence, verdict, retarget_skill. `addition` is the finished text in the form the target takes (a Markdown bullet starting with "- "); `diff` is a unified diff of the target that edits existing text, or null (allowed only when the target is a SKILL.md and the existing text is wrong); `confidence` is a number from 0 to 1 for how sure you are the text is right and worth keeping; `verdict` is one sentence; `retarget_skill` is the name of the skill whose procedure this really concerns, or null."""

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)
_HUNK_RE = re.compile(r"^@@ .* @@.*$", re.M)


@dataclass
class Draft:
    """The outcome of one drafting call."""

    ok: bool
    reason: str = ""
    proposal_id: str = ""
    model: str = ""
    condition: str = ""
    action: str = ""
    why: str = ""
    addition: str = ""
    diff: str = ""
    confidence: float = 0.0
    verdict: str = ""
    retarget_skill: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    prompt: str = ""
    response: str = ""


# --- what the drafter sees ------------------------------------------------


def trace_excerpts(store_dir: Path, proposal: Proposal, *, steps: int = EXCERPT_STEPS) -> list[dict[str, Any]]:
    """The failing call and its neighbours in each evidence session, from the sessions database."""
    from agentic_swmm.memory import session_db
    from agentic_swmm.memory.store import db_path_for

    db_path = db_path_for(Path(store_dir))
    if not db_path.is_file():
        return []
    evidence = proposal.evidence or {}
    tool = str(evidence.get("pattern", "")).split(":", 1)[0]
    out: list[dict[str, Any]] = []
    try:
        with session_db.connect(db_path) as conn:
            for run_id in list(evidence.get("runs") or [])[:MAX_EXCERPT_SESSIONS]:
                escaped = run_id.replace("_", "\\_")
                rows = conn.execute(
                    "SELECT step, kind, tool_name, args_json, ok, summary FROM tool_events "
                    "WHERE session_id = ? OR session_id LIKE ? ESCAPE '\\' ORDER BY step",
                    (run_id, f"%\\_{escaped}"),
                ).fetchall()
                if not rows:
                    continue
                failing = [i for i, row in enumerate(rows) if row[1] == "tool_result" and row[2] == tool and row[4] == 0]
                if not failing:
                    continue
                start, end = max(0, failing[0] - 2 * steps), min(len(rows), failing[-1] + 2 * steps + 1)
                selected = [
                    {"step": row[0], "kind": row[1], "tool": row[2], "args": _short_json(row[3]), "ok": row[4], "summary": str(row[5] or "")[:200]}
                    for row in rows[start:end]
                ]
                skills = [
                    _skill_arg(row[3]) for row in rows if row[1] == "tool_call" and row[2] == "select_skill"
                ]
                out.append({"session": run_id, "events": selected, "selected_skills": [s for s in skills if s]})
    except Exception:  # noqa: BLE001 - excerpts are a convenience; the evidence rows suffice
        return out
    return out


def _short_json(text: Any, cap: int = 300) -> Any:
    try:
        value = json.loads(text) if isinstance(text, str) and text else text
    except json.JSONDecodeError:
        value = text
    dumped = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
    return dumped if len(dumped) <= cap else dumped[: cap - 3] + "..."


def _skill_arg(args_json: Any) -> str:
    try:
        return str((json.loads(args_json) or {}).get("skill_name") or "")
    except (TypeError, json.JSONDecodeError):
        return ""


def build_prompt(proposal: Proposal, target_text: str, excerpts: list[dict[str, Any]]) -> str:
    """The user prompt of the drafting call, capped at :data:`DRAFT_INPUT_CAP` characters."""
    evidence = proposal.evidence or {}
    parts = [
        f"Proposal {proposal.id}, kind {proposal.kind}, target {proposal.target}" + (f", section {proposal.section}" if proposal.section else ""),
        "",
        "Evidence:",
        json.dumps({k: evidence.get(k) for k in ("pattern", "fix", "changes", "runs", "cases", "count")}, ensure_ascii=False, indent=1),
        "",
        "Template text written without a model (improve on it, keep what is true):",
        proposal.addition,
    ]
    found = (proposal.dedupe or {}).get("found") or []
    if found:
        parts += ["", "The same knowledge already exists in: " + ", ".join(f"{hit['file']} line {hit['line']}" for hit in found) + ". Do not duplicate prose; say the rule in the form the target takes."]
    if excerpts:
        parts += ["", "Trace excerpts (the failing call and its neighbours, per evidence session):", json.dumps(excerpts, ensure_ascii=False, indent=1)]
    limits = {
        "tool": "The addition is one Markdown bullet of at most 200 characters, appended under the tool's section of the tool notes; no diff.",
        "skill": "The addition is one Markdown bullet of at most 400 characters appended under '## Learned from runs'; a diff is allowed instead when existing text in the SKILL.md is wrong.",
        "rule": "The addition is one Markdown bullet of at most 400 characters appended under '## Learned from runs' of the operating memory; no diff.",
    }
    parts += ["", limits.get(proposal.kind, ""), "", f"Current text of the target ({proposal.target}):", "<<<", target_text, ">>>"]
    prompt = "\n".join(parts)
    if len(prompt) > DRAFT_INPUT_CAP:
        cut = DRAFT_INPUT_CAP - len(">>>\n[target text cut at the input cap]")
        prompt = prompt[:cut] + ">>>\n[target text cut at the input cap]"
    return prompt


# --- what comes back ---------------------------------------------------------


def parse_draft(text: str) -> dict[str, Any]:
    """The JSON object in a model reply (code fences tolerated); raises ValueError otherwise."""
    body = _FENCE_RE.sub("", (text or "").strip()).strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"reply is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not str(data.get("addition") or "").strip():
        raise ValueError("reply has no addition")
    return data


def apply_unified_diff(text: str, diff: str) -> str:
    """Apply ``diff`` to ``text`` strictly: each hunk's old lines must occur exactly once.

    Line numbers in the hunk headers are ignored (models miscount); the
    context and removed lines must match exactly, with no fuzz, or the
    whole application is refused with ``ValueError``.
    """
    lines = text.splitlines(keepends=True)
    hunks = _parse_hunks(diff)
    if not hunks:
        raise ValueError("the diff has no hunks")
    for old, new in hunks:
        positions = [i for i in range(len(lines) - len(old) + 1) if lines[i : i + len(old)] == old] if old else []
        if len(positions) != 1:
            raise ValueError("a hunk's context does not match the target exactly once")
        at = positions[0]
        lines = lines[:at] + new + lines[at + len(old) :]
    return "".join(lines)


def _parse_hunks(diff: str) -> list[tuple[list[str], list[str]]]:
    hunks: list[tuple[list[str], list[str]]] = []
    old: list[str] = []
    new: list[str] = []
    in_hunk = False
    for raw in diff.splitlines():
        if raw.startswith(("--- ", "+++ ")):
            continue
        if raw.startswith("@@"):
            if in_hunk:
                hunks.append((old, new))
            old, new, in_hunk = [], [], True
            continue
        if not in_hunk or raw.startswith("\\"):
            continue
        marker, content = (raw[0], raw[1:]) if raw else (" ", "")
        line = content + "\n"
        if marker == " ":
            old.append(line)
            new.append(line)
        elif marker == "-":
            old.append(line)
        elif marker == "+":
            new.append(line)
        else:
            raise ValueError(f"unexpected diff line: {raw!r}")
    if in_hunk:
        hunks.append((old, new))
    return hunks


def _skill_validator_problems(skill_text: str, skill_name: str) -> list[str]:
    """Run skill-author's validator on a candidate SKILL.md text."""
    import importlib.util
    import tempfile

    from agentic_swmm.utils.paths import resource_root

    script = resource_root() / "skills" / "skill-author" / "scripts" / "validate_skill.py"
    if not script.is_file():
        return []
    spec = importlib.util.spec_from_file_location("aiswmm_validate_skill", script)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / skill_name
        folder.mkdir()
        (folder / "SKILL.md").write_text(skill_text, encoding="utf-8")
        return list(module.validate(folder))


def validate_draft(proposal: Proposal, data: dict[str, Any], target_text: str) -> tuple[str | None, str, str]:
    """``(reason, addition, diff)``: the reason the draft is refused, else the texts to keep."""
    addition = str(data.get("addition") or "").strip()
    if not addition.startswith("- "):
        addition = "- " + addition
    if "\n" in addition.strip():
        return "the addition spans several lines", "", ""
    diff = str(data.get("diff") or "").strip()
    if proposal.kind == "tool":
        from agentic_swmm.memory.tool_notes import MAX_NOTE_CHARS

        if len(addition) - 2 > MAX_NOTE_CHARS:
            return f"the note is {len(addition) - 2} characters; the cap is {MAX_NOTE_CHARS}", "", ""
        if diff:
            return "a tool note cannot edit existing text", "", ""
        return None, addition, ""
    if len(addition) - 2 > MAX_BULLET_CHARS:
        return f"the bullet is {len(addition) - 2} characters; the cap is {MAX_BULLET_CHARS}", "", ""
    if diff:
        if proposal.kind != "skill":
            return "only a SKILL.md target may be edited in place", "", ""
        try:
            after = apply_unified_diff(target_text, diff)
        except ValueError as exc:
            return f"the diff does not apply: {exc}", "", ""
        skill_name = Path(proposal.target).parent.name
        problems = _skill_validator_problems(after, skill_name)
        if problems:
            return "the edited SKILL.md fails the validator: " + "; ".join(problems), "", ""
        return None, addition, _proposals._unified_diff(proposal.target, target_text, after)
    return None, addition, ""


# --- the call ----------------------------------------------------------------


def _usage(raw: Any) -> tuple[int, int]:
    usage = raw.get("usage") if isinstance(raw, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    return int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0), int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sidecar_path(proposal: Proposal) -> Path:
    assert proposal.path is not None
    return proposal.path.with_name(proposal.path.stem + ".draft.json")


def draft_proposal(proposal: Proposal, *, provider: Any, store_dir: Path, directory: Path | None = None) -> Draft:
    """Draft one open proposal through ``provider`` (``complete(system_prompt, prompt)``) and rewrite its file.

    On success the drafted addition replaces the body (the template stays in
    the front matter); on any failure the template stays and the reason is
    recorded. The call's transcript and cost always go to the sidecar.
    """
    if proposal.status != "open":
        return Draft(ok=False, proposal_id=proposal.id, reason=f"proposal {proposal.id} is {proposal.status}")
    if proposal.kind == "fact":
        return Draft(ok=False, proposal_id=proposal.id, reason="a fact is written by the planner and never drafted")
    target_text = _proposals._read_target(proposal.target)
    excerpts = trace_excerpts(store_dir, proposal)
    prompt = build_prompt(proposal, target_text, excerpts)
    started = time.monotonic()
    draft = Draft(ok=False, proposal_id=proposal.id, prompt=prompt)
    try:
        result = provider.complete(system_prompt=SYSTEM_PROMPT, prompt=prompt)
        draft.response = str(getattr(result, "text", "") or "")
        draft.model = str(getattr(result, "model", "") or "")
        draft.tokens_in, draft.tokens_out = _usage(getattr(result, "raw", None))
    except Exception as exc:  # noqa: BLE001 - the provider's failure is the draft's reason
        draft.reason = f"the model call failed: {exc}"
    draft.duration_ms = int((time.monotonic() - started) * 1000)
    if not draft.reason:
        try:
            data = parse_draft(draft.response)
        except ValueError as exc:
            draft.reason = str(exc)
        else:
            draft.condition = str(data.get("condition") or "")
            draft.action = str(data.get("action") or "")
            draft.why = str(data.get("why") or "")
            draft.verdict = str(data.get("verdict") or "")
            draft.retarget_skill = str(data.get("retarget_skill") or "")
            try:
                draft.confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
            except (TypeError, ValueError):
                draft.confidence = 0.0
            _maybe_retarget(proposal, draft, excerpts)
            if proposal.target != _proposals.TOOL_TARGET and proposal.kind == "tool":
                target_text = _proposals._read_target(proposal.target)
            reason, addition, diff = validate_draft(proposal, data, _proposals._read_target(proposal.target))
            if reason:
                draft.reason = reason
            else:
                draft.ok = True
                draft.addition, draft.diff = addition, diff
    _record(proposal, draft, directory)
    return draft


def _maybe_retarget(proposal: Proposal, draft: Draft, excerpts: list[dict[str, Any]]) -> None:
    """A drafted proposal may move to the SKILL.md of a skill the evidence sessions selected."""
    name = draft.retarget_skill.strip()
    if not name or proposal.kind == "skill":
        return
    selected = {skill for item in excerpts for skill in item.get("selected_skills") or []}
    if name not in selected or name in _proposals.PRIVATE_SKILLS:
        draft.retarget_skill = ""
        return
    root = _proposals._tracked_root()
    if not (root / "skills" / name / "SKILL.md").is_file():
        draft.retarget_skill = ""
        return
    proposal.kind, proposal.target, proposal.section = "skill", f"skills/{name}/SKILL.md", _proposals.SECTION


def _record(proposal: Proposal, draft: Draft, directory: Path | None) -> None:
    """Write the sidecar and the proposal file: the drafted body, or the template with the reason."""
    stamp = _utcnow()
    sidecar = {
        "id": proposal.id, "ok": draft.ok, "reason": draft.reason, "model": draft.model, "drafted_utc": stamp,
        "tokens_in": draft.tokens_in, "tokens_out": draft.tokens_out, "duration_ms": draft.duration_ms,
        "confidence": draft.confidence, "verdict": draft.verdict, "retarget_skill": draft.retarget_skill,
        "prompt": draft.prompt, "response": draft.response,
    }
    sidecar_path(proposal).write_text(json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")
    if not proposal.template_addition:
        proposal.template_addition = proposal.addition
    if draft.ok:
        proposal.addition = draft.addition
        before = _proposals._read_target(proposal.target)
        proposal.diff = draft.diff or _proposals._unified_diff(proposal.target, before, apply_addition(before, draft.addition, section=_proposals._section_for(proposal)))
        proposal.drafted_by = draft.model or "model"
        proposal.drafted_utc = stamp
        proposal.confidence = draft.confidence
        proposal.verdict = draft.verdict
        proposal.why = draft.why
        proposal.draft_failed = ""
    else:
        proposal.draft_failed = draft.reason
        proposal.drafted_utc = stamp
    _proposals.rerender(proposal)


def draft_pending(
    store_dir: Path,
    *,
    provider_factory: Callable[[], Any],
    directory: Path | None = None,
    limit: int = MAX_DRAFTS_PER_LISTING,
    only_id: str | None = None,
) -> list[Draft]:
    """Draft the open proposals that have no draft yet (or one proposal, again, when ``only_id``)."""
    wanted = only_id.strip().zfill(3) if only_id else None
    candidates = [
        proposal
        for proposal in _proposals.list_proposals(directory)
        if proposal.kind != "fact"
        and (proposal.id == wanted if wanted else not (proposal.drafted_by or proposal.draft_failed))
    ]
    if not candidates:
        return []
    provider = provider_factory()
    drafts: list[Draft] = []
    for proposal in candidates[: limit if not wanted else 1]:
        draft = draft_proposal(proposal, provider=provider, store_dir=store_dir, directory=directory)
        draft.reason = draft.reason or ""
        drafts.append(draft)
    return drafts


def configured_provider(model: str | None = None) -> Any:
    """The configured route (the user's subscription when that is the default) and model."""
    from agentic_swmm.providers.factory import make_provider
    from agentic_swmm.providers.selection import resolve_selection

    selection = resolve_selection(explicit_model=model)
    return make_provider(selection.route, model=selection.model)
