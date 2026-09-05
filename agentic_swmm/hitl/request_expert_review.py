"""``request_expert_review`` tool handler (PRD-Z).

This is the agent's runtime checkpoint. When a QA threshold has been
crossed, the planner is expected to call ``request_expert_review`` with
``pattern``, ``evidence_ref``, and ``message``. The handler:

1. Verifies ``evidence_ref`` resolves to a real path under the run dir.
   If not, it returns ``ok=False`` without prompting — the agent must
   pass a real evidence reference, not a hallucinated one.
2. Loads thresholds from ``docs/hitl-thresholds.md`` so it can warn
   when the pattern's rationale is still a ``<!-- HYDROLOGY-TODO -->``
   placeholder. Loading is best-effort; the prompt still fires if the
   file is missing or malformed.
3. Prints a clearly visible block to stderr.
4. Resolves the Y/N policy:
   * interactive TTY → ``permissions.prompt_user`` (the same seam used
     by write tools).
   * non-interactive without ``AISWMM_HITL_AUTO_APPROVE=1`` →
     ``ok=False`` with the message that ``--auto-approve-hitl`` is
     required for CI.
   * non-interactive with the flag → ``approved=True`` and an
     ``auto_approve_hitl_enabled`` decision is recorded *in addition*
     to the ``expert_review_approved`` decision so the CI's bypass is
     itself provenance-tracked.
5. Appends a ``human_decisions`` record via
   :func:`agentic_swmm.hitl.decision_recorder.append_decision`.
6. Returns ``{ok, approved, decision_id}``.

The handler is wired into the tool registry as ``is_read_only=False``
so the ``QUICK`` profile never auto-approves it (PRD-Z hard
requirement).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from agentic_swmm.agent import permissions
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.hitl.decision_recorder import (
    HumanDecision,
    append_decision,
    make_decision,
    new_decision_id,
    now_utc_iso,
)
from agentic_swmm.hitl.threshold_evaluator import load_thresholds_from_md
from agentic_swmm.utils.paths import resource_root


_AUTO_APPROVE_ENV = "AISWMM_HITL_AUTO_APPROVE"
_THRESHOLDS_DOC_REL = Path("docs") / "hitl-thresholds.md"


def _repo_root() -> Path:
    # Re-import locally so tests that monkeypatch the agent package do
    # not get a stale path.
    from agentic_swmm.utils.paths import repo_root

    return repo_root()


def _resolve_run_dir(value: Any) -> Path | None:
    if not value:
        return None
    candidate = Path(str(value))
    if not candidate.is_absolute():
        candidate = (_repo_root() / candidate).resolve()
    return candidate if candidate.is_dir() else None


def _resolve_evidence(run_dir: Path, evidence_ref: str) -> Path | None:
    """Resolve ``evidence_ref`` relative to ``run_dir``.

    Absolute paths are accepted only if they resolve inside ``run_dir``
    — the handler refuses to write a provenance record pointing at
    something outside the run's evidence tree.
    """
    raw = Path(evidence_ref)
    candidate = raw if raw.is_absolute() else (run_dir / raw)
    try:
        candidate.resolve().relative_to(run_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() else None


def _placeholder_rationale_for(pattern: str) -> bool:
    """Return ``True`` if ``pattern``'s rationale is still a placeholder.

    Loading the thresholds doc is best-effort: a missing or malformed
    file silently disables the warning rather than blocking the prompt.
    The PRD specifies the system must remain functional even when the
    hydrologist has not yet filled in rationale prose.
    """
    try:
        doc = resource_root() / _THRESHOLDS_DOC_REL
        thresholds = load_thresholds_from_md(doc)
    except Exception:  # pragma: no cover - depends on disk state
        return False
    spec = thresholds.get(pattern)
    if not isinstance(spec, dict):
        return False
    rationale = spec.get("rationale")
    if not isinstance(rationale, str):
        return False
    return "HYDROLOGY-TODO" in rationale


def _print_review_banner(
    pattern: str,
    message: str,
    evidence_path: Path,
    placeholder_rationale: bool,
) -> None:
    lines = [
        "",
        "=" * 72,
        "  HITL: expert review requested",
        "-" * 72,
        f"  pattern        : {pattern}",
        f"  message        : {message}",
        f"  evidence       : {evidence_path}",
        "  answer         : y = the expert approves this result for decision use;",
        "                   n = the expert denies it. Either answer is recorded in",
        "                   09_audit/experiment_provenance.json; anything else records nothing.",
    ]
    if placeholder_rationale:
        lines.extend(
            [
                "  WARNING        : threshold rationale is still a placeholder",
                "                   (<!-- HYDROLOGY-TODO --> in docs/hitl-thresholds.md).",
            ]
        )
    lines.extend(["=" * 72, ""])
    print("\n".join(lines), file=sys.stderr)


EVIDENCE_REF_HINT = (
    "evidence_ref is ONE file path relative to run_dir (for example "
    "06_runner/model.rpt); a list of paths or a note in parentheses is not "
    "a path. Name the report sections in message instead."
)
NO_DECISION_HINT = (
    "Nothing was recorded: the expert-review prompt takes y or n only. Ask "
    "the reviewer again, or continue without decision use."
)
DECISION_REPORT_HINT = (
    "Report the decision as recorded: who decided, the decision id and the "
    "provenance file. A recorded decision is never pending."
)


def _documented_patterns() -> dict[str, str]:
    """Return ``{pattern: one-line message}`` from the thresholds doc.

    Best-effort like the rationale check: an unreadable doc returns ``{}``
    and the caller skips validation rather than blocking the prompt.
    """
    try:
        thresholds = load_thresholds_from_md(resource_root() / _THRESHOLDS_DOC_REL)
    except Exception:  # pragma: no cover - depends on disk state
        return {}
    out: dict[str, str] = {}
    for name, spec in thresholds.items():
        if isinstance(spec, dict):
            out[str(name)] = str(spec.get("message") or "")
    return out


def _pattern_hint(documented: dict[str, str]) -> str:
    listing = "; ".join(f"{name} ({message})" if message else name for name, message in documented.items())
    return (
        f"Documented patterns: {listing}. Pick the one whose evidence you hold, "
        "and say in message when the concern differs from the pattern's literal check."
    )


def _decision_summary(*, verdict: str, pattern: str, decision_id: str, record: Path, consequence: str) -> str:
    return (
        f"expert review {verdict} for pattern {pattern!r}: {consequence}; "
        f"recorded as decision {decision_id} in {record}"
    )


def _failure(call: ToolCall, summary: str, *, hint: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tool": call.name,
        "args": dict(call.args),
        "ok": False,
        "approved": False,
        "summary": summary,
    }
    if hint:
        result["hint"] = hint
    return result


def request_expert_review(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    pattern = str(call.args.get("pattern") or "").strip()
    evidence_ref = str(call.args.get("evidence_ref") or "").strip()
    message = str(call.args.get("message") or "").strip()
    if not pattern:
        return _failure(call, "pattern is required")
    if not evidence_ref:
        return _failure(call, "evidence_ref is required")
    if not message:
        return _failure(call, "message is required")

    run_dir = _resolve_run_dir(call.args.get("run_dir"))
    if run_dir is None:
        return _failure(call, "run_dir must be an existing directory")

    # Live finding F-121 (2026-09-03, S54 r2): the planner labelled a
    # peak-credibility review "continuity_error_over_threshold" after
    # spending three calls on the thresholds doc. The documented list is
    # the contract; an unknown pattern is refused with that list.
    documented = _documented_patterns()
    if documented and pattern not in documented:
        return _failure(
            call,
            f"pattern {pattern!r} is not a documented HITL threshold",
            hint=_pattern_hint(documented),
        )

    evidence_path = _resolve_evidence(run_dir, evidence_ref)
    if evidence_path is None:
        return _failure(
            call,
            f"evidence_ref does not resolve inside run_dir: {evidence_ref}",
            hint=EVIDENCE_REF_HINT,
        )

    placeholder = _placeholder_rationale_for(pattern)
    _print_review_banner(pattern, message, evidence_path, placeholder)

    provenance_path = run_dir / "09_audit" / "experiment_provenance.json"
    decision_id = new_decision_id()

    interactive = sys.stdin.isatty()
    auto_approve_env = os.environ.get(_AUTO_APPROVE_ENV, "").strip()
    auto_approve = auto_approve_env in {"1", "true", "True", "yes"}

    if not interactive and not auto_approve:
        # Live finding F-140 (2026-09-04, S62 headless): this refusal carried
        # a decision_id although nothing was written, and the answer then
        # reported "expert approval was denied, record hd-...". A refused
        # pause is neither an approval nor a denial: no reviewer was asked
        # and no record exists.
        return {
            "tool": call.name,
            "args": dict(call.args),
            "ok": False,
            "approved": False,
            "summary": (
                "no expert decision recorded: no terminal was attached, so the "
                "review pause was refused rather than run unattended (nothing "
                "written to 09_audit/experiment_provenance.json); rerun with "
                "--auto-approve-hitl (or AISWMM_HITL_AUTO_APPROVE=1) for a "
                "configuration approval"
            ),
            "hint": (
                "Run the shell interactively so a reviewer can answer, or set "
                "AISWMM_HITL_AUTO_APPROVE=1 for trusted automation, which records "
                "a configuration approval, not a reviewer's. Do not report this "
                "as a denial."
            ),
        }

    if not interactive and auto_approve:
        # CI bypass — record the bypass *and* the approval so a later
        # auditor can see both events explicitly.
        bypass = make_decision(
            action="auto_approve_hitl_enabled",
            pattern=pattern,
            evidence_ref=evidence_ref,
            decision_text=(
                "Non-interactive run with AISWMM_HITL_AUTO_APPROVE=1 set; "
                "expert review was auto-approved by configuration."
            ),
        )
        append_decision(provenance_path, bypass)
        approval = HumanDecision(
            id=decision_id,
            action="expert_review_approved",
            by=bypass.by,
            at_utc=now_utc_iso(),
            pattern=pattern,
            evidence_ref=evidence_ref,
            decision_text=message,
        )
        append_decision(provenance_path, approval)
        return {
            "tool": call.name,
            "args": dict(call.args),
            "ok": True,
            "approved": True,
            "decision_id": decision_id,
            "summary": _decision_summary(
                verdict=f"auto-approved via {_AUTO_APPROVE_ENV}=1",
                pattern=pattern,
                decision_id=decision_id,
                record=provenance_path,
                consequence="configuration, not a reviewer, accepted this result for decision use",
            ),
            "hint": DECISION_REPORT_HINT,
        }

    # Interactive TTY path. This is a recorded decision, not a tool
    # approval: the question says what y and n mean, and anything else
    # records nothing (live finding F-119, 2026-09-03).
    approved = permissions.request_decision(
        f"Expert decision on {pattern}: approve this result for decision use? [y/n] "
    )
    if approved is None:
        return {
            "tool": call.name,
            "args": dict(call.args),
            "ok": False,
            "approved": False,
            "decision_id": decision_id,
            "summary": (
                f"no expert decision recorded for pattern {pattern!r}: the "
                "prompt got neither y nor n"
            ),
            "hint": NO_DECISION_HINT,
        }
    decision = HumanDecision(
        id=decision_id,
        action="expert_review_approved" if approved else "expert_review_denied",
        by=os.environ.get("USER", "unknown"),
        at_utc=now_utc_iso(),
        pattern=pattern,
        evidence_ref=evidence_ref,
        decision_text=message,
    )
    append_decision(provenance_path, decision)
    # Live finding F-120 (2026-09-03, S54 r2): the reviewer answered y and
    # the answer still said "await the expert decision". The result says
    # what was decided, what it means and where it is recorded.
    return {
        "tool": call.name,
        "args": dict(call.args),
        "ok": True,
        "approved": approved,
        "decision_id": decision_id,
        "summary": _decision_summary(
            verdict="approved" if approved else "denied",
            pattern=pattern,
            decision_id=decision_id,
            record=provenance_path,
            consequence=(
                "the reviewer accepted this result for decision use"
                if approved
                else "the reviewer rejected this result for decision use; revise before relying on it"
            ),
        ),
        "hint": DECISION_REPORT_HINT,
    }
