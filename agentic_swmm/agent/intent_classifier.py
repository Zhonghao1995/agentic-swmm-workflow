"""Single source of truth for keyword-driven intent classification.

PRD #121. The agent runtime makes several keyword-driven decisions
about a user goal:

- workflow-mode selection (``wants_calibration / wants_plot / ...``),
- warm-intro gating (``is_open_shaped``),
- plot-continuation routing (``is_plot_continuation``),
- continuation classification (build vs. plot vs. new run).

Before this module, each site rolled its own bilingual vocabulary,
lowercasing convention, and priority order. The result was vocabulary
drift, uneven Chinese coverage, and N x M test cost. ``classify_intent``
absorbs the union of those sites and returns a single ``IntentSignals``
record that the call sites read named fields from.

Implementation is deliberately small and dependency-free: lower-case the
goal once, run substring and word-boundary checks against frozen
vocabulary tables, and return an immutable dataclass. ZH tokens are
matched as substrings against the lowered goal (zh case folding is a
no-op); EN tokens are matched with word boundaries when the PRD calls
for it.

The module is intentionally a thin adapter over plain Python sets —
the *deepness* is the consolidation of six previously-scattered keyword
tables into one cohesive surface, not algorithmic complexity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Vocabulary tables. The PRD plan was to source these from
# ``agent/config/intent_map.json``; for the first migration we keep them
# in Python so the behaviour parity with the previously-scattered sites
# is unambiguous (one place to diff against). Future work can move them
# into JSON without changing the classifier interface.
# ---------------------------------------------------------------------------

# ``compute_intent_signals`` vocab (was: tool_registry.py).
_CALIBRATION_TOKENS: tuple[str, ...] = (
    "calibration",
    "calibrate",
    "observed",
    "nse",
    "kge",
    "校准",
    "率定",
)
_UNCERTAINTY_TOKENS: tuple[str, ...] = (
    "uncertainty",
    "fuzzy",
    "sensitivity",
    "不确定性",
    "敏感性",
)
_AUDIT_TOKENS: tuple[str, ...] = (
    "audit",
    "comparison",
    "compare",
    "审计",
    "比较",
)
_PLOT_INTENT_TOKENS: tuple[str, ...] = (
    "plot",
    "figure",
    "graph",
    "chart",
    "作图",
    "画图",
    "出图",
    "绘图",
)
_DEMO_TOKENS: tuple[str, ...] = (
    "demo",
    "acceptance",
    "演示",
    "验收",
)
# Word-boundary EN regex + ZH substrings — kept verbatim from the
# previous ``compute_intent_signals.wants_run`` implementation so the
# behavioural contract is byte-for-byte preserved.
_RUN_EN_REGEX = re.compile(r"\b(?:run|runs|running|execute|executes|executing)\b")
_RUN_ZH_TOKENS: tuple[str, ...] = ("跑", "运行")

# ``runtime_loop._TASK_VERB_TOKENS`` (warm-intro gate).
_TASK_VERB_TOKENS: tuple[str, ...] = (
    "run",
    "build",
    "plot",
    "calibrate",
    "audit",
    "check",
    "test",
    "compare",
    "simulate",
    "execute",
    "inspect",
    "summarize",
    "show",
    "list",
    "read",
    "create",
    "generate",
    "make",
    "fix",
    "跑",
    "做",
    "建",
    "审计",
    "校准",
    "验证",
    "对比",
    "比较",
    "运行",
)

# ``runtime_loop._OPEN_GREETING_TOKENS``.
_OPEN_GREETING_TOKENS: tuple[str, ...] = (
    "你好",
    "您好",
    "hi",
    "hello",
    "hey",
    "yo",
    "what can you do",
    "what are you",
    "who are you",
    "tell me about yourself",
    "tell me what you",
    "what do you do",
    "introduce yourself",
)

# ``continuation_classifier._PLOT_KEYWORDS`` (wider than ``_PLOT_INTENT_TOKENS``
# because the continuation classifier accepts attribute/variable names as
# "plot vocab"; the workflow-mode classifier does not).
_PLOT_CONTINUATION_TOKENS: tuple[str, ...] = (
    "plot",
    "figure",
    "graph",
    "rainfall",
    "outfall",
    "inflow",
    "depth",
    "flow",
    "peak",
    "total_inflow",
    "depth_above_invert",
    "volume_stored_ponded",
    "flow_lost_flooding",
    "hydraulic_head",
    "作图",
    "画图",
    "图",
    "水深",
    "节点",
    "根据你刚才",
    "刚才的运行",
)

# ``continuation_classifier._BUILD_KEYWORDS``.
_BUILD_TOKENS: tuple[str, ...] = (
    "build",
    "create a new",
    "new model",
    "new run",
    "another run",
    "重新跑",
    "新建",
    "重新建",
)

# ``continuation_classifier._NEW_RUN_KEYWORDS`` — explicit ``.inp`` path
# or run verb. Language-driven only, no watershed names.
_NEW_RUN_TOKENS: tuple[str, ...] = (
    ".inp",
    "run swmm",
    "run examples",
    "run the model",
)

# ``runtime_loop._looks_like_run_continuation`` vocab. The continuation
# heuristic on the runtime-loop side is slightly different from the
# continuation_classifier one (uses ``node`` instead of ``节点 / 水深``).
# Preserved as its own table so the byte-for-byte parity test stays
# green; future cleanup can merge with ``_PLOT_CONTINUATION_TOKENS``.
_RUN_CONTINUATION_PLOT_TOKENS: tuple[str, ...] = (
    "plot",
    "figure",
    "graph",
    "rainfall",
    "node",
    "outfall",
    "total_inflow",
    "depth_above_invert",
    "volume_stored_ponded",
    "flow_lost_flooding",
    "hydraulic_head",
    "作图",
    "画图",
    "图",
    "节点",
    "根据你刚才",
    "刚才的运行",
)

_NODE_ID_PATTERN = re.compile(r"\b[JO]\d+\b", flags=re.IGNORECASE)

_NEGATION_MARKERS: tuple[str, ...] = ("不想要", "不要", "别画", "不是", "not ", "no ")


@dataclass(frozen=True)
class IntentSignals:
    """Named result of ``classify_intent``.

    Fields named ``wants_*`` are the legacy ``compute_intent_signals``
    contract (kept byte-for-byte to preserve workflow-mode selection
    behaviour). Other fields absorb the previously-scattered helpers
    (``is_open_shaped``, ``is_plot_continuation``, build/new-run markers
    used by ``continuation_classifier``).
    """

    # ``compute_intent_signals`` legacy fields.
    wants_calibration: bool
    wants_uncertainty: bool
    wants_audit: bool
    wants_plot: bool
    wants_demo: bool
    wants_run: bool

    # Warm-intro gate (was ``is_open_shaped_prompt`` in runtime_loop).
    is_open_shaped: bool

    # Continuation-classifier signals.
    has_build_intent: bool
    has_plot_continuation_vocab: bool
    has_node_id: bool
    has_new_run_marker: bool
    # Strictly the runtime_loop._looks_like_run_continuation result —
    # vocabulary slightly differs from has_plot_continuation_vocab.
    looks_like_run_continuation: bool

    # Workflow-state-coupled signal.
    is_plot_continuation: bool

    def as_dict(self) -> dict[str, bool]:
        """Return the ``compute_intent_signals`` legacy dict shape."""
        return {
            "wants_calibration": self.wants_calibration,
            "wants_uncertainty": self.wants_uncertainty,
            "wants_audit": self.wants_audit,
            "wants_plot": self.wants_plot,
            "wants_demo": self.wants_demo,
            "wants_run": self.wants_run,
        }


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _contains_task_verb(lowered: str, raw: str) -> bool:
    """Match the runtime_loop convention: EN word-boundary, ZH substring.

    Kept identical to the previous implementation so warm-intro gating
    behaves byte-for-byte the same after migration.
    """
    for token in _TASK_VERB_TOKENS:
        if token.isascii():
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                return True
        else:
            if token in raw:
                return True
    return False


def _is_open_shaped(text: str, lowered: str) -> bool:
    if not text:
        return True
    if _contains_task_verb(lowered, text):
        return False
    if _contains_any(lowered, _OPEN_GREETING_TOKENS):
        return True
    # Fallback: very short prompts without task verbs.
    if len(lowered.split()) < 5:
        return True
    return False


def classify_intent(
    goal: str,
    *,
    workflow_state: Mapping[str, Any] | None = None,
) -> IntentSignals:
    """Classify ``goal`` into the documented ``IntentSignals`` set.

    ``workflow_state`` is the dict shape written by
    ``state.write_session_state``; the only key consulted is
    ``active_run_dir``. ``is_plot_continuation`` is True iff
    ``active_run_dir`` is set AND the prompt matches the continuation
    classifier's plot vocabulary (or contains a SWMM node id).
    """

    text = goal if isinstance(goal, str) else ""
    stripped = text.strip()
    lowered = text.lower()

    wants_calibration = _contains_any(lowered, _CALIBRATION_TOKENS)
    wants_uncertainty = _contains_any(lowered, _UNCERTAINTY_TOKENS)
    wants_audit = _contains_any(lowered, _AUDIT_TOKENS)
    wants_plot = _contains_any(lowered, _PLOT_INTENT_TOKENS)
    wants_demo = _contains_any(lowered, _DEMO_TOKENS)
    wants_run = bool(_RUN_EN_REGEX.search(lowered)) or _contains_any(
        lowered, _RUN_ZH_TOKENS
    )

    is_open_shaped = _is_open_shaped(stripped, lowered)

    has_build_intent = _contains_any(lowered, _BUILD_TOKENS)
    has_plot_continuation_vocab = _contains_any(lowered, _PLOT_CONTINUATION_TOKENS)
    has_node_id = bool(_NODE_ID_PATTERN.search(text))
    has_new_run_marker = _contains_any(lowered, _NEW_RUN_TOKENS)
    looks_like_run_continuation = _contains_any(lowered, _RUN_CONTINUATION_PLOT_TOKENS)

    active_run_dir = None
    if isinstance(workflow_state, Mapping):
        active_run_dir = workflow_state.get("active_run_dir")
    is_plot_continuation = bool(active_run_dir) and (
        has_plot_continuation_vocab or has_node_id
    )

    return IntentSignals(
        wants_calibration=wants_calibration,
        wants_uncertainty=wants_uncertainty,
        wants_audit=wants_audit,
        wants_plot=wants_plot,
        wants_demo=wants_demo,
        wants_run=wants_run,
        is_open_shaped=is_open_shaped,
        has_build_intent=has_build_intent,
        has_plot_continuation_vocab=has_plot_continuation_vocab,
        has_node_id=has_node_id,
        has_new_run_marker=has_new_run_marker,
        looks_like_run_continuation=looks_like_run_continuation,
        is_plot_continuation=is_plot_continuation,
    )


def is_negated(lowered: str, term: str) -> bool:
    """Return True iff ``term`` is preceded by a negation marker.

    Mirrors the previous private ``planner._is_negated`` helper so
    ``_extract_plot_choice`` can stop owning its own copy.
    """
    start = lowered.find(term)
    if start < 0:
        return False
    prefix = lowered[max(0, start - 12) : start]
    return any(marker in prefix for marker in _NEGATION_MARKERS)


__all__ = [
    "IntentSignals",
    "classify_intent",
    "is_negated",
]
