"""The inspection verbs answer what a first-time user types (F-41, F-42, F-43)."""

from __future__ import annotations

import argparse

from agentic_swmm.commands import config as config_cmd
from agentic_swmm.commands import mcp as mcp_cmd
from agentic_swmm.commands import skill as skill_cmd


def _parser(register):
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="verb"))
    return parser


class TestSkillShow:
    def test_shows_a_bundled_skill(self, capsys):
        ns = _parser(skill_cmd.register).parse_args(["skill", "show", "swmm-canada"])
        assert ns.func(ns) == 0
        out = capsys.readouterr().out
        assert out.startswith("swmm-canada - ")
        assert "SKILL.md" in out.splitlines()[0]
        assert "name: swmm-canada" in out

    def test_a_typo_gets_a_suggestion(self, capsys):
        ns = _parser(skill_cmd.register).parse_args(["skill", "show", "swmm-canad"])
        assert ns.func(ns) == 2
        err = capsys.readouterr().err
        assert "unknown skill 'swmm-canad'" in err and "Did you mean 'swmm-canada'?" in err

    def test_full_prints_everything(self, capsys):
        ns = _parser(skill_cmd.register).parse_args(["skill", "show", "swmm-canada", "--full"])
        assert ns.func(ns) == 0
        assert "more lines" not in capsys.readouterr().out


class TestMcpStatus:
    def test_status_is_the_list_report(self):
        parser = _parser(mcp_cmd.register)
        ns = parser.parse_args(["mcp", "status"])
        assert ns.func is mcp_cmd.list_servers
        assert parser.parse_args(["mcp", "list"]).func is mcp_cmd.list_servers


class TestBareConfig:
    def test_bare_config_shows(self, capsys):
        ns = _parser(config_cmd.register).parse_args(["config"])
        assert ns.func is config_cmd.show_config
        assert ns.func(ns) == 0
        assert capsys.readouterr().out.strip().startswith("{")
