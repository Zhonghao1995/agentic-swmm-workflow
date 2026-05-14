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
        doc = _repo_root() / _THRESHOLDS_DOC_REL
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


def _failure(call: ToolCall, summary: str) -> dict[str, Any]:
    return {
        "tool": call.name,
        "args": dict(call.args),
        "ok": False,
        "approved": False,
        "summary": summary,
    }


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

    evidence_path = _resolve_evidence(run_dir, evidence_ref)
    if evidence_path is None:
        return _failure(
            call,
            f"evidence_ref does not resolve inside run_dir: {evidence_ref}",
        )

    placeholder = _placeholder_rationale_for(pattern)
    _print_review_banner(pattern, message, evidence_path, placeholder)

    provenance_path = run_dir / "09_audit" / "experiment_provenance.json"
    decision_id = new_decision_id()

    interactive = sys.stdin.isatty()
    auto_approve_env = os.environ.get(_AUTO_APPROVE_ENV, "").strip()
    auto_approve = auto_approve_env in {"1", "true", "True", "yes"}

    if not interactive and not auto_approve:
        return {
            "tool": call.name,
            "args": dict(call.args),
            "ok": False,
            "approved": False,
            "decision_id": decision_id,
            "summary": (
                "non-interactive HITL pause refused; rerun with "
                "--auto-approve-hitl (or AISWMM_HITL_AUTO_APPROVE=1) to "
                "continue."
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
            "summary": (
                f"expert review auto-approved via {_AUTO_APPROVE_ENV}=1 "
                f"for pattern {pattern!r}"
            ),
        }

    # Interactive TTY path — use the same prompt seam as write tools.
    approved = bool(permissions.prompt_user(f"expert_review:{pattern}"))
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
    return {
        "tool": call.name,
        "args": dict(call.args),
        "ok": True,
        "approved": approved,
        "decision_id": decision_id,
        "summary": (
            f"expert review {'approved' if approved else 'denied'} for "
            f"pattern {pattern!r}"
        ),
    }
