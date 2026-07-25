"""`aiswmm run` must not spend an LLM session to report a typo.

`run <prose>` routes to the natural-language planner on purpose, and that
front door stays. What was missing was a cheap gate in front of it: a
bare `aiswmm run` and a mistyped path both started a planner session,
queried the skill and MCP registries, and only then could conclude
nothing was runnable. That costs tokens and hides the ready-made
`_RUN_EXAMPLE` string the run subparser already carries.

Sibling verbs (audit, report, review, compare) have always errored
cleanly here.
"""
from __future__ import annotations

import pytest

from agentic_swmm.cli import _route_default_to_agent


def _routes_to_planner(argv: list[str]) -> bool:
    """True when ``argv`` would be handed to the LLM planner."""
    routed = _route_default_to_agent(list(argv))
    return isinstance(routed, list) and routed[:3] == ["agent", "--planner", "llm"]


def test_bare_run_is_a_request_for_help_not_a_goal(capsys):
    """`aiswmm run` alone asks how to use run. Answer it directly."""
    with pytest.raises(SystemExit) as excinfo:
        _route_default_to_agent(["run"])

    assert excinfo.value.code == 2
    printed = capsys.readouterr()
    assert "--inp" in printed.out + printed.err


def test_a_path_that_does_not_exist_is_named_immediately(capsys, tmp_path):
    """A mistyped INP costs one filesystem check, not a planner session."""
    missing = tmp_path / "typo.inp"

    with pytest.raises(SystemExit) as excinfo:
        _route_default_to_agent(["run", str(missing)])

    assert excinfo.value.code == 2
    assert str(missing) in capsys.readouterr().err


def test_an_existing_inp_still_reaches_the_planner(tmp_path):
    """The natural-language front door is unchanged for real files."""
    existing = tmp_path / "model.inp"
    existing.write_text("[TITLE]\n", encoding="utf-8")

    assert _routes_to_planner(["run", str(existing)])


@pytest.mark.parametrize(
    "goal",
    [
        ["run", "the", "downtown", "model"],
        ["run", "my latest model"],
        ["run", "看看这个模型"],
    ],
)
def test_prose_goals_naming_no_path_still_reach_the_planner(goal):
    """Only tokens with a path separator are checked; prose is left alone."""
    assert _routes_to_planner(goal)


def test_a_bare_filename_is_left_for_the_planner_to_resolve():
    """A bare ``.inp`` name is not a claim about the working directory.

    ``single_shot._find_repo_inp`` resolves bare names against
    ``examples/``, so checking them here would reject a documented
    convenience. Only explicit paths are the CLI's to verify.
    """
    assert _routes_to_planner(["run", "definitely-not-on-disk.inp"])


@pytest.mark.parametrize("flag", ["--help", "-h", "--example"])
def test_help_shaped_flags_still_reach_the_run_subparser(flag):
    """These short-circuit to the subparser and must not be intercepted."""
    assert _route_default_to_agent(["run", flag]) == ["run", flag]
