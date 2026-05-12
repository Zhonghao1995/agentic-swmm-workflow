from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from agentic_swmm.utils.paths import resource_path


INTENT_MAP_PATH = ("agentic-ai", "config", "intent_map.json")


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


def looks_like_swmm_request(goal: str) -> bool:
    lowered = goal.lower()
    if _contains_any(lowered, keywords("excluded_swmm_keywords")):
        return False
    return _contains_any(lowered, keywords("swmm_request_keywords"))


def looks_like_plot_request(goal: str) -> bool:
    return _contains_any(goal.lower(), keywords("plot_keywords"))


def select_relevant_skills(goal: str) -> list[str]:
    lowered = goal.lower()
    selected: list[str] = []
    for skill in _string_list(load_intent_map().get("always_load_skills")):
        _add(selected, skill)

    for intent in _intent_records():
        intent_keywords = _intent_keywords(intent)
        if _contains_any(lowered, intent_keywords):
            for skill in _string_list(intent.get("skills")):
                _add(selected, skill)

    if len(selected) == len(_string_list(load_intent_map().get("always_load_skills"))):
        for skill in _string_list(load_intent_map().get("fallback_skills")):
            _add(selected, skill)
    return selected


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


def _contains_any(text: str, values: list[str]) -> bool:
    return any(value.lower() in text for value in values)


def _add(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)
