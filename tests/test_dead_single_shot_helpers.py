"""Regression guard against dead-code creep in ``single_shot.py``.

Issue #127 deleted ~600 LOC of historical tool-dispatch helpers from
``agentic_swmm/agent/single_shot.py``. This test ensures the module
stays lean and that its three public symbols continue to import.

If you find yourself wanting to raise ``MAX_LOC`` to fit something new,
the helper probably belongs in ``tool_registry.py`` (the live path) or
``agentic_swmm/utils/`` (cross-module utilities), not in ``single_shot.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent import single_shot

# Allow modest headroom over the post-deletion ~200 LOC target.
MAX_LOC = 250


def _module_loc() -> int:
    src = Path(single_shot.__file__).read_text(encoding="utf-8")
    return len(src.splitlines())


def test_single_shot_module_is_lean() -> None:
    """Guard against the historical 600-LOC dead-code block creeping back."""
    loc = _module_loc()
    assert loc <= MAX_LOC, (
        f"agentic_swmm/agent/single_shot.py grew to {loc} LOC (limit {MAX_LOC}). "
        "If you need a new helper here, ask: should it live in tool_registry.py "
        "(live tool-dispatch path) or agentic_swmm/utils/ instead?"
    )


def test_public_symbols_still_importable() -> None:
    """``_find_repo_inp``, ``_safe_name``, and ``run_single_shot`` are the
    three names imported from this module by other code/tests.
    """
    from agentic_swmm.agent.single_shot import (  # noqa: F401
        _find_repo_inp,
        _safe_name,
        run_single_shot,
    )


@pytest.mark.parametrize(
    "name",
    [
        "_openai_tool_schemas",
        "_validated_openai_call",
        "_provider_call_payload",
        "_tool_output_for_model",
        "_execute_tool",
        "_plan",  # superseded by agentic_swmm.agent.planner.rule_plan
    ],
)
def test_dead_helpers_are_gone(name: str) -> None:
    """The historical helpers must not reappear in this module."""
    assert not hasattr(single_shot, name), (
        f"{name} reappeared in single_shot.py. It was deleted in #127; "
        "the live equivalent lives in agentic_swmm/agent/tool_registry.py."
    )
