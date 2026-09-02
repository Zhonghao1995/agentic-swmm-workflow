"""Only the top candidate skills are primed in full before the first LLM call (F-05).

Measured across 19 live sessions on 2026-09-02: seven SKILL.md files (67k
characters) were primed per Canada chain and re-sent on every LLM call of the
turn, making up most of the 150k to 210k input tokens. The system prompt's
skill index and select_skill's full contract make that redundant beyond the top
candidates.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.planner import DEFAULT_PRIME_SKILL_READS, Planner, _skill_priming_limit
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from tests.test_onboarding_wiring import _ScriptedProvider

GOAL = ("Fetch a SWMM model from the Canada service for downtown Victoria BC, rainfall period "
        "November 1 to November 4 2023. Run the model and audit it.")


def _prime(tmp: Path) -> list[str]:
    registry = AgentToolRegistry()
    executor = AgentExecutor(registry, session_dir=tmp, trace_path=tmp / "t.jsonl", dry_run=False, profile=Profile.QUICK)
    planner = Planner(provider=_ScriptedProvider(), registry=registry, max_steps=2, verbose=False, emit=lambda t: None)  # type: ignore[arg-type]
    plan: list = []
    planner._consult_workflow_skills(goal=GOAL, plan=plan, executor=executor, prior_session_state={})
    return [call.name + ":" + str(call.args.get("skill_name") or call.args.get("server") or "") for call in plan]


class TestLimit:
    def test_default_is_zero_because_the_reads_never_reached_the_model(self, monkeypatch):
        # F-44: planner.run builds input_items from the goal alone, so the
        # primed reads were executed and discarded. Off by default.
        monkeypatch.delenv("AISWMM_PRIME_SKILL_READS", raising=False)
        assert DEFAULT_PRIME_SKILL_READS == 0
        assert _skill_priming_limit(6) == 0 and _skill_priming_limit(1) == 0

    def test_all_restores_everything_and_integers_cap(self, monkeypatch):
        monkeypatch.setenv("AISWMM_PRIME_SKILL_READS", "all")
        assert _skill_priming_limit(6) == 6
        monkeypatch.setenv("AISWMM_PRIME_SKILL_READS", "4")
        assert _skill_priming_limit(6) == 4
        monkeypatch.setenv("AISWMM_PRIME_SKILL_READS", "nonsense")
        assert _skill_priming_limit(6) == 0


class TestPriming:
    def test_no_skill_is_read_in_full_by_default(self, monkeypatch):
        monkeypatch.delenv("AISWMM_PRIME_SKILL_READS", raising=False)
        with TemporaryDirectory() as raw:
            names = _prime(Path(raw))
        assert not [n for n in names if n.startswith("read_skill:")]
        assert "list_skills:" in names and "list_mcp_servers:" in names
        assert any(n.startswith("list_mcp_tools:") for n in names)

    def test_an_integer_restores_that_many_reads(self, monkeypatch):
        monkeypatch.setenv("AISWMM_PRIME_SKILL_READS", "2")
        with TemporaryDirectory() as raw:
            names = _prime(Path(raw))
        reads = [n for n in names if n.startswith("read_skill:")]
        assert reads == ["read_skill:swmm-end-to-end", "read_skill:swmm-canada"]

    def test_all_primes_every_relevant_skill(self, monkeypatch):
        monkeypatch.setenv("AISWMM_PRIME_SKILL_READS", "all")
        with TemporaryDirectory() as raw:
            names = _prime(Path(raw))
        reads = [n for n in names if n.startswith("read_skill:")]
        assert len(reads) >= 5 and reads[0] == "read_skill:swmm-end-to-end"
