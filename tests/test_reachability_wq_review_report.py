"""Wiring tests for the water-quality, design-review, and report-export tools.

Covers the three tool families landed in the combined wiring PR:

  WQ — read_wq_loads: in registry, is_read_only=True, missing rpt_path
       returns failure, _read_wq_loads_tool resolves path correctly.
  DR — review_run: in registry, is_read_only=False, _review_run_tool
       resolves run_dir and optional args.
  RE — generate_report: in registry, is_read_only=False,
       _generate_report_tool resolves run_dir and optional args.
  SKR — skill_router._DETERMINISTIC_BINDINGS has entries for all three.
  CLI — 'review' and 'report' verbs are reachable from the CLI parser.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.tool_handlers.swmm_wq import _read_wq_loads_tool
from agentic_swmm.agent.tool_handlers.swmm_review import _review_run_tool
from agentic_swmm.agent.tool_handlers.swmm_report import _generate_report_tool
from agentic_swmm.agent.types import ToolCall


_SESSION = Path("/tmp/test_session_wq_review_report")


@pytest.fixture(scope="module")
def registry() -> AgentToolRegistry:
    return AgentToolRegistry()


def _call(name: str, args: dict) -> ToolCall:
    c = MagicMock(spec=ToolCall)
    c.name = name
    c.args = args
    return c


# ---------------------------------------------------------------------------
# WQ — read_wq_loads
# ---------------------------------------------------------------------------


def test_wq_read_wq_loads_in_registry(registry: AgentToolRegistry) -> None:
    assert "read_wq_loads" in registry.names


def test_wq_read_wq_loads_is_read_only(registry: AgentToolRegistry) -> None:
    spec = registry._tools["read_wq_loads"]
    assert spec.is_read_only is True


def test_wq_read_wq_loads_schema_has_rpt_path(registry: AgentToolRegistry) -> None:
    spec = registry._tools["read_wq_loads"]
    props = spec.parameters.get("properties", {})
    req = spec.parameters.get("required", [])
    assert "rpt_path" in props
    assert "rpt_path" in req


def test_wq_read_wq_loads_missing_rpt_path_returns_failure() -> None:
    call = _call("read_wq_loads", {})
    result = _read_wq_loads_tool(call, _SESSION)
    assert result.get("ok") is False
    error_text = result.get("error", "") or result.get("summary", "")
    assert "rpt_path" in error_text


def test_wq_read_wq_loads_nonexistent_rpt_returns_failure(tmp_path: Path) -> None:
    call = _call("read_wq_loads", {"rpt_path": str(tmp_path / "nonexistent.rpt")})
    result = _read_wq_loads_tool(call, _SESSION)
    assert result.get("ok") is False


def test_wq_read_wq_loads_relative_path_resolves_to_repo_root(tmp_path: Path) -> None:
    """A relative rpt_path is resolved against repo_root, not cwd."""
    import agentic_swmm.agent.tool_handlers.swmm_wq as wq_mod
    from agentic_swmm.utils.paths import repo_root as orig_repo_root

    # Point repo_root to tmp_path and put a dummy rpt there.
    fake_rpt = tmp_path / "smoke.rpt"
    fake_rpt.write_text("dummy\n", encoding="utf-8")

    wq_mod_repo_root_orig = wq_mod.repo_root
    wq_mod.repo_root = lambda: tmp_path  # type: ignore[assignment]
    try:
        call = _call("read_wq_loads", {"rpt_path": "smoke.rpt"})
        result = _read_wq_loads_tool(call, _SESSION)
        # The script itself won't parse a dummy file cleanly, but the path
        # resolution succeeded if we don't get the "rpt file not found" failure.
        error_text = result.get("error", "") or result.get("summary", "")
        assert "rpt file not found" not in error_text
    finally:
        wq_mod.repo_root = wq_mod_repo_root_orig  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# DR — review_run
# ---------------------------------------------------------------------------


def test_dr_review_run_in_registry(registry: AgentToolRegistry) -> None:
    assert "review_run" in registry.names


def test_dr_review_run_is_not_read_only(registry: AgentToolRegistry) -> None:
    spec = registry._tools["review_run"]
    assert spec.is_read_only is False


def test_dr_review_run_schema_has_run_dir(registry: AgentToolRegistry) -> None:
    spec = registry._tools["review_run"]
    props = spec.parameters.get("properties", {})
    req = spec.parameters.get("required", [])
    assert "run_dir" in props
    assert "run_dir" in req


def test_dr_review_run_schema_has_optional_rules_and_out_dir(
    registry: AgentToolRegistry,
) -> None:
    spec = registry._tools["review_run"]
    props = spec.parameters.get("properties", {})
    assert "rules" in props
    assert "out_dir" in props
    # Neither is in required
    req = spec.parameters.get("required", [])
    assert "rules" not in req
    assert "out_dir" not in req


def test_dr_review_run_missing_run_dir_returns_failure() -> None:
    call = _call("review_run", {})
    result = _review_run_tool(call, _SESSION)
    assert result.get("ok") is False


def test_dr_review_run_nonexistent_run_dir_returns_failure() -> None:
    call = _call("review_run", {"run_dir": "/tmp/this_does_not_exist_abcxyz"})
    result = _review_run_tool(call, _SESSION)
    assert result.get("ok") is False


# ---------------------------------------------------------------------------
# RE — generate_report
# ---------------------------------------------------------------------------


def test_re_generate_report_in_registry(registry: AgentToolRegistry) -> None:
    assert "generate_report" in registry.names


def test_re_generate_report_is_not_read_only(registry: AgentToolRegistry) -> None:
    spec = registry._tools["generate_report"]
    assert spec.is_read_only is False


def test_re_generate_report_schema_has_run_dir(registry: AgentToolRegistry) -> None:
    spec = registry._tools["generate_report"]
    props = spec.parameters.get("properties", {})
    req = spec.parameters.get("required", [])
    assert "run_dir" in props
    assert "run_dir" in req


def test_re_generate_report_schema_has_optional_out_template_title(
    registry: AgentToolRegistry,
) -> None:
    spec = registry._tools["generate_report"]
    props = spec.parameters.get("properties", {})
    assert "out" in props
    assert "template" in props
    assert "title" in props
    req = spec.parameters.get("required", [])
    for k in ("out", "template", "title"):
        assert k not in req


def test_re_generate_report_missing_run_dir_returns_failure() -> None:
    call = _call("generate_report", {})
    result = _generate_report_tool(call, _SESSION)
    assert result.get("ok") is False


def test_re_generate_report_nonexistent_run_dir_returns_failure() -> None:
    call = _call("generate_report", {"run_dir": "/tmp/this_does_not_exist_abcxyz"})
    result = _generate_report_tool(call, _SESSION)
    assert result.get("ok") is False


# ---------------------------------------------------------------------------
# build_inp water_quality_json optional arg
# ---------------------------------------------------------------------------


def test_build_inp_schema_has_water_quality_json(registry: AgentToolRegistry) -> None:
    spec = registry._tools["build_inp"]
    props = spec.parameters.get("properties", {})
    req = spec.parameters.get("required", [])
    assert "water_quality_json" in props, "water_quality_json must be in build_inp schema"
    assert "water_quality_json" not in req, "water_quality_json must be optional"


def test_build_inp_optional_paths_dict_includes_water_quality_json() -> None:
    """The optional_paths dict inside _build_inp_args must map water_quality_json
    to the camelCase key waterQualityJsonPath.  We verify this by inspecting the
    source rather than calling the full mapper (which requires repo-relative files).
    """
    import inspect
    from agentic_swmm.agent.tool_handlers.swmm_builder import _build_inp_args

    src = inspect.getsource(_build_inp_args)
    assert "water_quality_json" in src
    assert "waterQualityJsonPath" in src


# ---------------------------------------------------------------------------
# SKR — skill_router _DETERMINISTIC_BINDINGS
# ---------------------------------------------------------------------------


def test_skr_read_wq_loads_in_deterministic_bindings() -> None:
    from agentic_swmm.agent.skill_router import _DETERMINISTIC_BINDINGS

    assert "read_wq_loads" in _DETERMINISTIC_BINDINGS
    assert _DETERMINISTIC_BINDINGS["read_wq_loads"] == "swmm-water-quality"


def test_skr_review_run_in_deterministic_bindings() -> None:
    from agentic_swmm.agent.skill_router import _DETERMINISTIC_BINDINGS

    assert "review_run" in _DETERMINISTIC_BINDINGS
    assert _DETERMINISTIC_BINDINGS["review_run"] == "swmm-design-review"


def test_skr_generate_report_in_deterministic_bindings() -> None:
    from agentic_swmm.agent.skill_router import _DETERMINISTIC_BINDINGS

    assert "generate_report" in _DETERMINISTIC_BINDINGS
    assert _DETERMINISTIC_BINDINGS["generate_report"] == "swmm-report"


def test_skr_skill_router_includes_wq_tool_in_bundle(
    registry: AgentToolRegistry,
) -> None:
    from agentic_swmm.agent.skill_router import SkillRouter

    router = SkillRouter(registry)
    bundle = router.tools_for("swmm-water-quality")
    assert "read_wq_loads" in bundle.tool_names()


def test_skr_skill_router_includes_review_tool_in_bundle(
    registry: AgentToolRegistry,
) -> None:
    from agentic_swmm.agent.skill_router import SkillRouter

    router = SkillRouter(registry)
    bundle = router.tools_for("swmm-design-review")
    assert "review_run" in bundle.tool_names()


def test_skr_skill_router_includes_report_tool_in_bundle(
    registry: AgentToolRegistry,
) -> None:
    from agentic_swmm.agent.skill_router import SkillRouter

    router = SkillRouter(registry)
    bundle = router.tools_for("swmm-report")
    assert "generate_report" in bundle.tool_names()


# ---------------------------------------------------------------------------
# CLI — 'review' and 'report' verbs
# ---------------------------------------------------------------------------


def _build_parser():
    """Build the full aiswmm CLI parser."""
    from agentic_swmm.cli import build_parser

    return build_parser()


def test_cli_review_verb_registered() -> None:
    parser = _build_parser()
    # Parsing with --help raises SystemExit; parse_known_args with a dummy
    # --run-dir will fail because the path is fake but the verb must parse.
    # We only need to confirm the subcommand is registered (no SystemExit from
    # "invalid choice").
    import argparse

    with pytest.raises(SystemExit) as exc_info:
        # parse with no args produces a help/usage exit
        parser.parse_args(["review", "--run-dir", "/fake/path", "--help"])
    # --help exits with 0
    assert exc_info.value.code == 0


def test_cli_report_verb_registered() -> None:
    import argparse

    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["report", "--run-dir", "/fake/path", "--help"])
    assert exc_info.value.code == 0


def test_cli_review_verb_has_rules_flag() -> None:
    """The --rules flag must be accepted by the review subparser."""
    parser = _build_parser()
    # parse_known_args: unknown args won't error; known args are returned
    args, _ = parser.parse_known_args(
        ["review", "--run-dir", "/fake/path", "--rules", "rules.yaml"]
    )
    assert args.rules is not None


def test_cli_report_verb_has_out_and_template_flags() -> None:
    """The --out and --template flags must be accepted by the report subparser."""
    parser = _build_parser()
    args, _ = parser.parse_known_args(
        [
            "report",
            "--run-dir", "/fake/path",
            "--out", "report.docx",
            "--template", "template.yaml",
        ]
    )
    assert args.out is not None
    assert args.template is not None
