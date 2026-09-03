"""review_run takes a bundled rulebook by name and says which one it applied.

Live test 2026-09-03 (S47): asked for "the plausibility rulebook", the
planner had no way to name it, the review ran the default GB 50014
template on a Canadian municipal network, and the summary never said which
rulebook had been applied.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from agentic_swmm.agent.tool_handlers import swmm_review
from agentic_swmm.agent.tool_handlers.swmm_review import resolve_rulebook, tool_specs
from agentic_swmm.agent.types import ToolCall


def test_bundled_names_resolve_to_the_bundled_files() -> None:
    assert resolve_rulebook("plausibility").name == "synth_plausibility.yaml"
    assert resolve_rulebook("synth_plausibility").name == "synth_plausibility.yaml"
    assert resolve_rulebook("gb50014").name == "gb50014_template.yaml"
    assert resolve_rulebook("custom/rules.yaml").name == "rules.yaml"


def test_the_spec_lists_the_bundled_rulebooks() -> None:
    spec = next(s for s in tool_specs() if s.name == "review_run")
    description = spec.parameters["properties"]["rules"]["description"]
    assert "gb50014_template" in description and "synth_plausibility" in description
    assert "the one the user names" in description


def test_the_tool_passes_the_named_rulebook_and_names_it_in_the_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "06_runner").mkdir(parents=True)
    seen: dict[str, list[str]] = {}

    def fake_run(call, session_dir, cli_args):
        seen["args"] = list(cli_args)
        return {"ok": True, "summary": "Design review: PASS (3 pass, 0 fail)", "return_code": 0}

    with mock.patch.object(swmm_review, "_run_script_tool", side_effect=fake_run), mock.patch.object(
        swmm_review, "_resolve_run_dir", lambda call, key: run_dir
    ):
        result = swmm_review._review_run_tool(
            ToolCall(name="review_run", args={"run_dir": str(run_dir), "rules": "plausibility"}), tmp_path
        )
    assert result["ok"] is True
    args = seen["args"]
    assert "--rules" in args and args[args.index("--rules") + 1].endswith("rulebooks/synth_plausibility.yaml")
    assert result["summary"].endswith("(rulebook=synth_plausibility)")
