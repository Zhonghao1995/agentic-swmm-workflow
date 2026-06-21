"""The run_swmm_inp preflight gate: FAIL blocks before the SWMM run with an
actionable summary, WARN/PASS proceed, and a user-only env flag bypasses it.
"""

from __future__ import annotations

from agentic_swmm.agent.tool_handlers.swmm_runner import (
    _preflight_gate,
    _run_swmm_inp_args,
)
from agentic_swmm.agent.types import ToolCall

# A CONDUITS row with length=0 -> preflight FAIL (zero_length_conduit).
_FAIL_INP = "[CONDUITS]\nC1  J1  J2  0  0.01  0  0  0\n"
# No checks trigger -> PASS.
_PASS_INP = "[TITLE]\nok\n"
# Metric FLOW_UNITS + a FILE raingage declaring US units -> WARN only.
_WARN_INP = (
    "[OPTIONS]\nFLOW_UNITS  CMS\n\n"
    '[RAINGAGES]\nRG1  INTENSITY  0:05  1.0  FILE  "rain.dat"  STA  in\n'
)


def _call() -> ToolCall:
    return ToolCall("run_swmm_inp", {"inp": "x.inp"})


def test_fail_blocks_with_actionable_summary(tmp_path) -> None:
    inp = tmp_path / "bad.inp"
    inp.write_text(_FAIL_INP)
    blocked = _preflight_gate(_call(), inp)
    assert blocked is not None
    assert blocked["ok"] is False
    assert "preflight blocked" in blocked["summary"]
    assert "length" in blocked["summary"]  # the specific failure detail


def test_pass_proceeds(tmp_path) -> None:
    inp = tmp_path / "ok.inp"
    inp.write_text(_PASS_INP)
    assert _preflight_gate(_call(), inp) is None


def test_warn_proceeds(tmp_path) -> None:
    inp = tmp_path / "warn.inp"
    inp.write_text(_WARN_INP)
    # WARN must not block — gate returns None (warning is logged separately).
    assert _preflight_gate(_call(), inp) is None


def test_env_flag_bypasses_gate(tmp_path, monkeypatch) -> None:
    inp = tmp_path / "bad.inp"
    inp.write_text(_FAIL_INP)
    monkeypatch.setenv("AISWMM_SKIP_PREFLIGHT", "1")
    assert _preflight_gate(_call(), inp) is None


def test_missing_inp_is_blocked(tmp_path) -> None:
    # preflight_inp reports inp_unreadable (FAIL) for a non-existent file.
    blocked = _preflight_gate(_call(), tmp_path / "nope.inp")
    assert blocked is not None and blocked["ok"] is False


def test_args_mapper_blocks_before_run(tmp_path, monkeypatch) -> None:
    """_run_swmm_inp_args returns the block (never reaching the MCP call)."""
    import agentic_swmm.agent.tool_registry as tr

    bad = tmp_path / "bad.inp"
    bad.write_text(_FAIL_INP)
    monkeypatch.setattr(tr, "_resolve_inp_for_run", lambda call: bad)

    result = _run_swmm_inp_args(
        ToolCall("run_swmm_inp", {"inp": str(bad)}), tmp_path
    )
    assert result["ok"] is False
    assert "preflight blocked" in result["summary"]
