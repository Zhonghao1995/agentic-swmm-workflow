"""Canonical dataclass schemas for the gap-fill state machine (PRD-GF-CORE).

Every other module in the gap-fill package — preflight, proposer, ui,
recorder — speaks one of three structures:

- :class:`GapSignal`: emitted by a tool (L3 in-band) or synthesised by
  the preflight scanner (L1). It says: "here is a gap I detected,
  here is the field name, here is the context."
- :class:`GapBatch`: groups multiple ``GapSignal`` from the same tool
  call so the UI can render one form instead of N prompts.
- :class:`GapDecision`: the recorded outcome — proposer source,
  proposed value, final value (post-edit), confidence, who decided.

The three are *deep modules* in Ousterhout's sense: callers pass
plain dicts on the JSON wire and the dataclasses validate +
materialise. ``from_dict`` raises ``ValueError`` on malformed input;
``to_dict`` round-trips back to the same payload shape.

The schemas are stable wire formats: downstream PRDs (GF-L5,
GF-PROMOTE) extend them but never break a key. The severity literal
set (``L1`` / ``L3``) reflects this PRD's scope; GF-L5 adds ``L5``.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# Severity literal — the subset of the wider 5-level taxonomy in scope
# for PRD-GF-CORE. GF-L5 will extend this with ``"L5"``.
_VALID_SEVERITIES = frozenset({"L1", "L3"})

# ``kind`` is mechanically aligned with severity: L1 means "missing
# file on disk", L3 means "missing parameter value". The pair is
# enforced at construction so a downstream consumer can branch on one
# field instead of two.
_VALID_KINDS = frozenset({"file_path", "param_value"})

# Proposer ``source`` literal — where the proposed value came from.
# Registry hits are deterministic and audit-light (no LLM call);
# llm_grounded means the LLM proposer was consulted; human means the
# user supplied the value with no machine proposal (typical for L1
# path picks).
_VALID_SOURCES = frozenset({"registry", "llm_grounded", "human"})

# Confidence tier literal. The proposer assigns these per
# PRD-GF-CORE: registry hits and LLM-with-citation are HIGH; LLM-
# grounded-but-ambiguous is MEDIUM; LLM-without-strong-grounding is
# LOW (and triggers human-required).
_VALID_CONFIDENCES = frozenset({"HIGH", "MEDIUM", "LOW"})

# How the final decision was reached. ``human`` is the interactive
# accept/edit path; ``auto_registry`` is the AISWMM_GAP_REGISTRY_ONLY
# hit-and-go path; ``auto_approve`` is the AISWMM_HITL_AUTO_APPROVE
# path where the proposer's value is auto-accepted (loud logging
# elsewhere).
_VALID_DECIDED_BY = frozenset({"human", "auto_registry", "auto_approve"})


def _now_utc_iso() -> str:
    """Return an ISO-8601 UTC timestamp with second resolution.

    The recorder stamps this onto every decision. Resolution matches
    ``decision_recorder.now_utc_iso`` (PRD-Z) so the two ledgers
    cross-reference cleanly.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_gap_id() -> str:
    """Return a short ``gap-<hex>`` identifier.

    The ``gap-`` prefix makes the wire-format readable in logs without
    a schema lookup. We trim the uuid to 12 chars — collisions inside
    one run dir are vanishingly unlikely and the shorter form is
    nicer for humans skimming a JSON file.
    """
    return f"gap-{uuid.uuid4().hex[:12]}"


def new_decision_id() -> str:
    """Return a short ``dec-<hex>`` identifier.

    Mirrors :func:`new_gap_id` so the two prefixes are visually
    distinct in mixed logs.
    """
    return f"dec-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class GapSignal:
    """One detected gap.

    The tool emits this in its result dict (``{"ok": false,
    "gap_signal": <payload>}``) for L3, or the preflight scanner
    synthesises it for L1. Either way the runtime sees the same
    structure.

    Fields:

    - ``gap_id``: unique identifier. Use :func:`new_gap_id` when
      constructing.
    - ``severity``: ``"L1"`` (missing file path) or ``"L3"`` (missing
      parameter value). The literal set is enforced at construction.
    - ``kind``: ``"file_path"`` (mechanically aligned with L1) or
      ``"param_value"`` (mechanically aligned with L3). Pairing is
      enforced.
    - ``field``: the canonical argument name the runtime needs to
      fill (e.g. ``"rainfall_file"`` or ``"manning_n_imperv"``).
    - ``context``: a free-form ``{key: value}`` map carrying the
      workflow + step + tool labels so audit / UI can render the gap
      meaningfully. Tools should set ``{"tool": "<tool_name>",
      "workflow": "...", "step": "..."}`` at minimum.
    - ``suggestion``: optional pre-proposer hint. Tools can pass a
      domain-specific shape (e.g. ``{"hint": "paved surface"}``) that
      the proposer treats as a soft input.
    """

    gap_id: str
    severity: str
    kind: str
    field: str
    context: dict[str, Any]
    suggestion: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.severity not in _VALID_SEVERITIES:
            raise ValueError(
                f"GapSignal.severity must be one of {sorted(_VALID_SEVERITIES)}; "
                f"got {self.severity!r}"
            )
        if self.kind not in _VALID_KINDS:
            raise ValueError(
                f"GapSignal.kind must be one of {sorted(_VALID_KINDS)}; "
                f"got {self.kind!r}"
            )
        # Severity and kind are paired: L1 → file_path, L3 → param_value.
        # The mismatch check protects downstream branches that switch
        # on one field assuming the other is consistent.
        expected_kind = "file_path" if self.severity == "L1" else "param_value"
        if self.kind != expected_kind:
            raise ValueError(
                f"GapSignal severity {self.severity!r} requires kind "
                f"{expected_kind!r}, got {self.kind!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GapSignal":
        required = ("gap_id", "severity", "kind", "field", "context")
        for key in required:
            if key not in payload:
                raise ValueError(f"GapSignal payload missing required key: {key!r}")
        return cls(
            gap_id=str(payload["gap_id"]),
            severity=str(payload["severity"]),
            kind=str(payload["kind"]),
            field=str(payload["field"]),
            context=dict(payload["context"] or {}),
            suggestion=(dict(payload["suggestion"]) if payload.get("suggestion") else None),
        )


@dataclass(frozen=True)
class ProposerInfo:
    """Provenance for the proposed value.

    Records *where* the proposal came from so a reviewer can defend
    each parameter value. ``registry_ref`` and ``literature_ref`` are
    free-text citations; ``llm_call_id`` cross-links into the LLM-TRACE
    ledger (``09_audit/llm_calls.jsonl``).
    """

    source: str
    confidence: str
    registry_ref: str | None = None
    literature_ref: str | None = None
    llm_call_id: str | None = None

    def __post_init__(self) -> None:
        if self.source not in _VALID_SOURCES:
            raise ValueError(
                f"ProposerInfo.source must be one of {sorted(_VALID_SOURCES)}; "
                f"got {self.source!r}"
            )
        if self.confidence not in _VALID_CONFIDENCES:
            raise ValueError(
                f"ProposerInfo.confidence must be one of "
                f"{sorted(_VALID_CONFIDENCES)}; got {self.confidence!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProposerInfo":
        return cls(
            source=str(payload["source"]),
            confidence=str(payload["confidence"]),
            registry_ref=payload.get("registry_ref"),
            literature_ref=payload.get("literature_ref"),
            llm_call_id=payload.get("llm_call_id"),
        )


@dataclass(frozen=True)
class GapDecision:
    """The recorded outcome of one gap-fill cycle.

    Written to ``<run_dir>/09_audit/gap_decisions.json``, with a
    matching entry in ``experiment_provenance.json.human_decisions``
    cross-linked via ``human_decisions_ref``.

    Fields:

    - ``decision_id``: unique identifier. Use :func:`new_decision_id`.
    - ``gap_id``: foreign key back to the originating ``GapSignal``.
    - ``severity`` / ``field``: copied from the signal so a reader of
      ``gap_decisions.json`` does not have to chase the signal.
    - ``proposer``: the :class:`ProposerInfo` with source / confidence
      / citations.
    - ``proposed_value``: what the proposer offered. ``None`` for L1
      file-path gaps where there is no machine proposal.
    - ``final_value``: what the runtime resumes with — either the
      proposed value (accept) or the user's edit (override).
    - ``proposer_overridden``: ``True`` iff ``final_value`` differs
      from ``proposed_value`` (i.e. the user clicked Edit).
    - ``decided_by``: ``"human"`` (interactive), ``"auto_registry"``
      (env-var registry-only mode), or ``"auto_approve"`` (env-var
      auto-approve mode).
    - ``decided_at``: ISO-8601 UTC stamp.
    - ``resume_mode``: always ``"tool_retry"`` for CORE; GF-L5 may
      add ``"replan"``.
    - ``human_decisions_ref``: pointer back to the matching entry in
      ``experiment_provenance.json``.
    """

    decision_id: str
    gap_id: str
    severity: str
    field: str
    proposer: ProposerInfo
    proposed_value: Any
    final_value: Any
    proposer_overridden: bool
    decided_by: str
    decided_at: str
    resume_mode: str
    human_decisions_ref: str | None

    def __post_init__(self) -> None:
        if self.severity not in _VALID_SEVERITIES:
            raise ValueError(
                f"GapDecision.severity must be one of {sorted(_VALID_SEVERITIES)}; "
                f"got {self.severity!r}"
            )
        if self.decided_by not in _VALID_DECIDED_BY:
            raise ValueError(
                f"GapDecision.decided_by must be one of "
                f"{sorted(_VALID_DECIDED_BY)}; got {self.decided_by!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "gap_id": self.gap_id,
            "severity": self.severity,
            "field": self.field,
            "proposer": self.proposer.to_dict(),
            "proposed_value": self.proposed_value,
            "final_value": self.final_value,
            "proposer_overridden": self.proposer_overridden,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "resume_mode": self.resume_mode,
            "human_decisions_ref": self.human_decisions_ref,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GapDecision":
        required = (
            "decision_id",
            "gap_id",
            "severity",
            "field",
            "proposer",
            "proposed_value",
            "final_value",
            "proposer_overridden",
            "decided_by",
            "decided_at",
            "resume_mode",
        )
        for key in required:
            if key not in payload:
                raise ValueError(f"GapDecision payload missing required key: {key!r}")
        return cls(
            decision_id=str(payload["decision_id"]),
            gap_id=str(payload["gap_id"]),
            severity=str(payload["severity"]),
            field=str(payload["field"]),
            proposer=ProposerInfo.from_dict(payload["proposer"]),
            proposed_value=payload["proposed_value"],
            final_value=payload["final_value"],
            proposer_overridden=bool(payload["proposer_overridden"]),
            decided_by=str(payload["decided_by"]),
            decided_at=str(payload["decided_at"]),
            resume_mode=str(payload["resume_mode"]),
            human_decisions_ref=payload.get("human_decisions_ref"),
        )


@dataclass(frozen=True)
class GapBatch:
    """Collection of gaps from a single tool invocation.

    The runtime groups every gap detected for one tool call (pre-flight
    L1 paths + in-band L3 params) into one batch, hands it to the
    proposer, and renders one batched form. The UX requirement (user
    story 3) is "one form, not N prompts".

    Fields:

    - ``tool``: the tool name that produced the batch.
    - ``signals``: ordered list of :class:`GapSignal`. Order is
      preserved so the UI can show pre-flight L1 paths before in-band
      L3 params if a caller chooses to.
    """

    tool: str
    signals: list[GapSignal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "signals": [s.to_dict() for s in self.signals],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GapBatch":
        if "tool" not in payload:
            raise ValueError("GapBatch payload missing required key: 'tool'")
        raw_signals = payload.get("signals") or []
        if not isinstance(raw_signals, list):
            raise ValueError("GapBatch.signals must be a list")
        return cls(
            tool=str(payload["tool"]),
            signals=[GapSignal.from_dict(s) for s in raw_signals],
        )


__all__ = [
    "GapBatch",
    "GapDecision",
    "GapSignal",
    "ProposerInfo",
    "new_decision_id",
    "new_gap_id",
]
