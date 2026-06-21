"""Path/file-resolution hints: the ``file_resolution_error`` builder, the
``_failure`` hint/cause passthrough, the ``output_for_model`` whitelist, and
one end-to-end handler check (a missing read_file path lists its siblings).
"""

from __future__ import annotations

from agentic_swmm.agent.error_remediation import file_resolution_error
from agentic_swmm.agent.tool_handlers._shared import _failure
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


# --- builder ----------------------------------------------------------------


def test_lists_matching_files(tmp_path) -> None:
    (tmp_path / "model.inp").write_text("x")
    (tmp_path / "other.inp").write_text("x")
    (tmp_path / "model.out").write_bytes(b"\x00")
    err = file_resolution_error(
        "could not resolve .inp", search_dir=tmp_path, suffixes=(".inp",)
    )
    assert err.summary == "could not resolve .inp"  # preserved verbatim
    assert err.hint is not None
    assert "model.inp" in err.hint and "other.inp" in err.hint
    assert "model.out" not in err.hint  # filtered to .inp


def test_falls_back_to_all_files_when_no_match(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("x")
    err = file_resolution_error("no inp", search_dir=tmp_path, suffixes=(".inp",))
    assert err.cause is not None and "no .inp file" in err.cause
    assert err.hint is not None and "a.txt" in err.hint  # show what IS there


def test_did_you_mean_on_typo(tmp_path) -> None:
    (tmp_path / "model.inp").write_text("x")
    err = file_resolution_error(
        "file not found", requested=tmp_path / "modle.inp", search_dir=tmp_path
    )
    assert err.hint is not None
    assert "did you mean" in err.hint and "model.inp" in err.hint


def test_missing_directory(tmp_path) -> None:
    err = file_resolution_error("file not found", search_dir=tmp_path / "nope")
    assert err.cause is not None and "does not exist" in err.cause


def test_no_search_dir_keeps_summary_only() -> None:
    err = file_resolution_error("bad path")
    assert err.summary == "bad path"
    assert err.hint is None and err.cause is None


# --- _failure passthrough ---------------------------------------------------


def test_failure_includes_hint_and_cause() -> None:
    call = ToolCall("read_file", {"path": "x"})
    payload = _failure(call, "nope", hint="try y", cause="missing")
    assert payload["hint"] == "try y"
    assert payload["cause"] == "missing"


def test_failure_legacy_shape_unchanged() -> None:
    call = ToolCall("read_file", {"path": "x"})
    payload = _failure(call, "nope")
    assert set(payload) == {"tool", "args", "ok", "summary"}


# --- output_for_model whitelist ---------------------------------------------


def test_output_for_model_passes_hint_and_cause() -> None:
    reg = AgentToolRegistry()
    filtered = reg.output_for_model(
        {
            "tool": "t",
            "ok": False,
            "summary": "s",
            "hint": "h",
            "cause": "c",
            "secret": "x",
        }
    )
    assert filtered["hint"] == "h"
    assert filtered["cause"] == "c"
    assert "secret" not in filtered


# --- handler integration ----------------------------------------------------


def test_read_file_tool_hints_siblings(tmp_path, monkeypatch) -> None:
    import agentic_swmm.agent.tool_handlers._shared as shared
    import agentic_swmm.agent.tool_handlers.runtime_ops as ro

    monkeypatch.setattr(shared, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(ro, "repo_root", lambda: tmp_path)
    (tmp_path / "real.txt").write_text("hi")

    call = ToolCall("read_file", {"path": "reel.txt"})  # typo + missing
    result = ro._read_file_tool(call, tmp_path)

    assert result["ok"] is False
    assert "hint" in result
    assert "real.txt" in result["hint"]
