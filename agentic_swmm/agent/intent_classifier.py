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

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Mapping

from agentic_swmm.utils.paths import resource_path


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
    # Data-shaped inputs are answers, never smalltalk: coordinates,
    # numbers, paths, bracketed lists. A bare bbox pasted in reply to
    # a question used to be classified as a greeting because it splits
    # into "one word" (BUG-2, user live test 2026-08-09).
    if any(ch.isdigit() for ch in text) or any(
        marker in text for marker in ("[", "]", "/", "\\", ".inp", ".csv")
    ):
        return False
    if _contains_any(lowered, _OPEN_GREETING_TOKENS):
        return True
    # CJK text has no spaces, so the word-count fallback called every
    # short Chinese sentence a greeting. Count characters for CJK.
    if any("一" <= ch <= "鿿" for ch in text):
        return len(text) < 6
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


# ---------------------------------------------------------------------------
# JSON-backed intent-config helper section (was ``agent/intent_map.py``,
# merged in via issue #206). The classifier above is the Python-side
# vocabulary surface (``compute_intent_signals`` / warm-intro gate /
# continuation routing). The helpers below are the config-driven
# vocabulary surface (skill-loader / preferred-tools / required-inputs)
# read from ``agent/config/intent_map.json``. They were a separate module
# but never called by ``intent_classifier`` itself and only added module-
# discovery overhead for developers editing intent vocab — consolidated
# here so "where does this rule live" has a single answer.
# ---------------------------------------------------------------------------

INTENT_MAP_PATH = ("agent", "config", "intent_map.json")


@lru_cache(maxsize=1)
def load_intent_map() -> dict[str, Any]:
    path = resource_path(*INTENT_MAP_PATH)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"intent map must be a JSON object: {path}")
    return payload


def keywords(name: str) -> list[str]:
    values = load_intent_map().get(name, [])
    return [str(value) for value in values if str(value)]


#: Live finding F-74 (2026-09-03, scenario S25): "Which node flooded the
#: most in the downtown Victoria run I just made?" opened a run-kind folder
#: that never received a model, because the SWMM vocabulary ("run",
#: "flooded") reads as a modeling request. A question that does not start
#: with a work verb and names no model source is a chat turn.
_QUESTION_START = re.compile(
    r"^\s*(?:which|what|where|when|why|how|who|is|are|was|were|does|did|do|has|have|had)\b"
    r"|^\s*(?:哪|什么|为什么|怎么|如何|是不是|有没有|多少)",
    re.IGNORECASE,
)
_LEADING_WORK_VERB = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|would you\s+|请\s*|帮我\s*|能不能\s*|能否\s*)?"
    r"(?:run|rerun|re-run|simulate|calibrate|fetch|build|synthesi[sz]e|plot|draw|export|generate|make|create|audit|compare|check|propagate|transfer|map)\b",
    re.IGNORECASE,
)


def is_question_about_existing_work(goal: str) -> bool:
    """True for a question that asks about work already done, not for new work."""
    text = str(goal or "").strip()
    if not text:
        return False
    if _MODEL_SOURCE.search(text):
        return False
    if _LEADING_WORK_VERB.search(text):
        return False
    if not _QUESTION_START.search(text):
        return False
    return not any(marker in text.lower() for marker in _STARTS_NEW_WORK)


def looks_like_swmm_request(goal: str) -> bool:
    lowered = goal.lower()
    if _contains_any_list(lowered, keywords("excluded_swmm_keywords")):
        return False
    if is_question_about_existing_work(goal):
        return False
    return _contains_any_list(lowered, keywords("swmm_request_keywords"))


#: A pasted model source always means a fresh run folder.
_MODEL_SOURCE = re.compile(r"\S+\.inp\b|\bbbox\b|\bgeojson\b|\bpolygon\b", re.IGNORECASE)

#: Words that point back at the work already in hand.
_POINTS_BACK = re.compile(
    r"\b(?:that|this|last|previous|same|current|latest)\s+"
    r"(?:run|model|session|result|results|simulation|network|report|figure)\b"
    r"|\bit\b|\bits\b|\bagain\b"
    r"|刚才|刚刚|上一个|上次|这个|那个|重新",
    re.IGNORECASE,
)

#: Verbs that start NEW modelling work (a model that does not exist yet).
_STARTS_NEW_WORK = (
    "fetch", "download", "get me", "build", "synthesize", "synthesise", "synth ",
    "create a model", "create a swmm", "generate a model", "generate a swmm",
    "make me a model", "model for downtown", "model of downtown",
    "给我", "帮我建", "建一个", "生成一个", "抓取", "下载", "自动建模", "合成一个",
)


#: The shapes an assistant message takes when it ends by asking for input.
_ASKS_FOR_INPUT = re.compile(
    r"\?|？|\breply\b|\bplease\s+(?:provide|give|specify|confirm|choose|paste|share|tell)\b"
    r"|\blet me know\b|请提供|请回复|请告诉|请选择|请确认|请给",
    re.IGNORECASE,
)


def message_asks_for_input(text: str) -> bool:
    """True when the assistant's message ends by asking the user for something.

    Only the closing lines count: a question buried in the middle of a
    report is not an open question. The shell uses this to decide whether
    the next input is an ANSWER (which continues the same turn even when
    it names a file or a bbox) or a fresh request.
    """
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    return bool(_ASKS_FOR_INPUT.search("\n".join(lines[-2:])))


def looks_like_new_modeling_request(goal: str, *, answering_question: bool = False) -> bool:
    """True only when ``goal`` STARTS new modelling work.

    The interactive shell asks this once a turn or run is already in hand,
    to choose between continuing it and opening a fresh run folder.
    :func:`looks_like_swmm_request` is the wrong question there: it
    matches vocabulary ("node", "map", "plot"), and every follow-up about
    a finished run uses that vocabulary. Live finding F-07 (2026-09-02):
    "Draw the network map for that run." and "Which node flooded the
    most, and for how long?" each opened an empty run folder named after
    the sentence.

    Rules, in order:

    1. ``answering_question`` (the previous turn ended with a question):
       the input is the answer unless it plainly starts other work with a
       new-work verb. A bare bbox or a pasted path is an answer.
    2. A pasted model source (``.inp``, ``bbox``, GeoJSON, polygon) is
       new work.
    3. A sentence that points back at the run in hand ("that run", "it",
       "again", 刚才 ...) continues it, whatever verbs it carries:
       calibrating or forcing the model in hand stays in its folder.
    4. Otherwise only a new-work verb (fetch, download, build, synthesize,
       get me, 给我 ...) opens a folder. Bare "run the model", "plot",
       "audit", questions and confirmations all continue.
    """
    lowered = goal.lower()
    starts_work = any(marker in lowered for marker in _STARTS_NEW_WORK)
    points_back = bool(_POINTS_BACK.search(goal))
    if answering_question:
        return starts_work and not points_back
    if _MODEL_SOURCE.search(goal):
        return True
    if points_back:
        return False
    return starts_work


def looks_like_plot_request(goal: str) -> bool:
    return _contains_any_list(goal.lower(), keywords("plot_keywords"))


def select_relevant_skills(goal: str) -> list[str]:
    lowered = goal.lower()
    selected: list[str] = []
    for skill in _string_list(load_intent_map().get("always_load_skills")):
        _add(selected, skill)

    for intent in _intent_records():
        intent_keywords = _intent_keywords(intent)
        if _contains_any_list(lowered, intent_keywords):
            for skill in _string_list(intent.get("skills")):
                _add(selected, skill)

    if len(selected) == len(_string_list(load_intent_map().get("always_load_skills"))):
        for skill in _string_list(load_intent_map().get("fallback_skills")):
            _add(selected, skill)
    return selected


def select_relevant_intents(goal: str) -> list[dict[str, Any]]:
    lowered = goal.lower()
    return [intent for intent in _intent_records() if _contains_any_list(lowered, _intent_keywords(intent))]


def intent_contracts(goal: str) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for intent in select_relevant_intents(goal):
        contracts.append(
            {
                "id": str(intent.get("id") or ""),
                "required_inputs": _string_list(intent.get("required_inputs")),
                "optional_inputs": _string_list(intent.get("optional_inputs")),
                "preferred_tools": _string_list(intent.get("preferred_tools")),
                "stop_conditions": _string_list(intent.get("stop_conditions")),
                "next_user_prompt": str(intent.get("next_user_prompt") or ""),
            }
        )
    return contracts


def select_relevant_mcp_servers(skill_names: list[str]) -> list[str]:
    mcp_enabled = set(_string_list(load_intent_map().get("mcp_enabled_skills")))
    return [name for name in skill_names if name in mcp_enabled]


def _intent_records() -> list[dict[str, Any]]:
    values = load_intent_map().get("intents", [])
    return [value for value in values if isinstance(value, dict)]


def _intent_keywords(intent: dict[str, Any]) -> list[str]:
    if intent.get("keywords_from"):
        return keywords(str(intent["keywords_from"]))
    return _string_list(intent.get("keywords"))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _contains_any_list(text: str, values: list[str]) -> bool:
    """List-form variant of ``_contains_any`` for JSON-loaded vocab.

    The classifier above uses tuple-typed vocabulary (frozen at import
    time) and matches with ``_contains_any(text, tuple)``; the helpers in
    this section load vocabulary from JSON as ``list[str]``. Kept as a
    distinct function so the type contract on each side stays explicit.
    """
    return any(value.lower() in text for value in values)


def _add(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


__all__ = [
    "IntentSignals",
    "classify_intent",
    "is_negated",
    # JSON-backed intent-config helpers (merged from intent_map.py, #206).
    "INTENT_MAP_PATH",
    "load_intent_map",
    "keywords",
    "looks_like_swmm_request",
    "looks_like_new_modeling_request",
    "message_asks_for_input",
    "looks_like_plot_request",
    "select_relevant_skills",
    "select_relevant_intents",
    "intent_contracts",
    "select_relevant_mcp_servers",
]


# Live finding F-71 (2026-09-02, scenario S21): "go back to the downtown run
# and export a Word report" wrote the report into the right run but left the
# session anchored on the later James Bay run. A follow-up that names
# exactly one EARLIER run of this session, and not the current one, moves
# the anchor there; an ambiguous mention keeps the current anchor.
_RUN_SLUG_STOP = frozenset({"run", "chat", "area", "of", "the", "bc", "on", "ab", "qc", "sk", "mb", "ns", "nb", "nl", "pe", "yt", "nt", "nu"})


def _run_slug_tokens(run_dir: "Path | str") -> set[str]:
    from pathlib import Path as _Path

    name = _Path(str(run_dir)).name.lower()
    parts = name.split("_", 1)
    body = parts[1] if len(parts) == 2 and parts[0].isdigit() else name
    body = body.rsplit("_", 1)[0] if body.endswith(("_run", "_chat")) else body
    return {tok for tok in body.replace("_", "-").split("-") if len(tok) >= 4 and tok not in _RUN_SLUG_STOP}


def referenced_run_dir(prompt: str, run_dirs: "list[Path]", current: "Path | None") -> "Path | None":
    """The one earlier run of this session the prompt refers to, or None.

    Each run is known by the tokens of its slug that no other run of the
    session shares (so "victoria" does not count when both the downtown and
    the James Bay runs carry it). A prompt that names exactly one run other
    than the current one returns it; naming two, none, or only the current
    one returns None.
    """
    import re as _re

    if not run_dirs:
        return None
    lowered = str(prompt or "").lower()
    tokens_by_dir = {str(d): _run_slug_tokens(d) for d in run_dirs}
    mentioned: list = []
    for run_dir in run_dirs:
        own = tokens_by_dir[str(run_dir)]
        shared = set()
        for other, toks in tokens_by_dir.items():
            if other != str(run_dir):
                shared |= toks
        distinctive = own - shared
        if any(_re.search(r"\b" + _re.escape(tok) + r"\b", lowered) for tok in distinctive):
            mentioned.append(run_dir)
    if len(mentioned) != 1:
        return None
    target = mentioned[0]
    if current is not None and str(target) == str(current):
        return None
    return target
