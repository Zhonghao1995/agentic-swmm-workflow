"""The welcome text's example commands survive a paste into a shell (F-49).

Live finding 2026-09-02 (fresh-user scenario S15): the first example case is
"tecnopolo (rome 1994)", and the suggested `aiswmm transfer --inp
examples/tecnopolo (rome 1994)/scenario.inp` broke on the parentheses and
spaces when pasted. The canned greeting also carried an em dash.
"""

from __future__ import annotations

import shlex

from agentic_swmm.agent import welcome
from agentic_swmm.agent.prompts import WARM_INTRO_TEMPLATE


def _transfer_line(monkeypatch, case_name):
    monkeypatch.setattr(welcome, "_first_case_display_name", lambda: case_name)
    text = welcome.render_extended_welcome()
    lines = [line for line in text.splitlines() if "transfer --inp" in line]
    assert len(lines) == 1, lines
    return lines[0]


def test_a_case_name_with_spaces_and_parentheses_is_quoted(monkeypatch):
    line = _transfer_line(monkeypatch, "Tecnopolo (Rome 1994)")
    command = line.strip().strip('"').lstrip("- ").strip('"')
    argv = shlex.split(command)
    assert argv[:3] == ["aiswmm", "transfer", "--inp"]
    assert argv[3] == "examples/tecnopolo (rome 1994)/scenario.inp"


def test_a_plain_case_name_stays_unquoted(monkeypatch):
    line = _transfer_line(monkeypatch, "saanich")
    assert "examples/saanich/scenario.inp" in line
    assert "'" not in line


def test_the_greeting_has_no_em_dash():
    assert "\u2014" not in WARM_INTRO_TEMPLATE
