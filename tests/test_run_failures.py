from __future__ import annotations

from agentic_swmm.memory.run_failures import (
    classify_failure,
    read_run_failures,
    record_run_failures,
    resolve_store,
)


def _ok(tool: str = "read_file") -> dict:
    return {"tool": tool, "ok": True, "summary": "done"}


def _fail(summary: str, tool: str = "run_swmm", permission=None) -> dict:
    result = {"tool": tool, "ok": False, "summary": summary}
    if permission is not None:
        result["permission"] = permission
    return result


# --- classify_failure -------------------------------------------------------


def test_classify_success_returns_none() -> None:
    assert classify_failure(_ok()) is None


def test_classify_permission_denial_returns_none() -> None:
    # Via the executor permission seam.
    assert (
        classify_failure(
            _fail(
                "tool not approved by user",
                permission={"prompted": True, "approved": False},
            )
        )
        is None
    )
    # Via the summary fallback (stub results without the permission seam).
    assert (
        classify_failure(
            {"tool": "x", "ok": False, "summary": "tool not approved by user"}
        )
        is None
    )


def test_classify_mcp_transport() -> None:
    assert (
        classify_failure(
            _fail(
                "MCP transport failed: MCP process ended before sending a "
                "complete line."
            )
        )
        == "mcp_transport"
    )
    assert (
        classify_failure(_fail("MCP tools/list failed: boom")) == "mcp_transport"
    )
    assert (
        classify_failure(
            _fail("MCP transport failed: unknown MCP server: swmm-audit")
        )
        == "mcp_transport"
    )


def test_classify_path_resolution() -> None:
    assert (
        classify_failure(_fail("could not resolve .inp from /runs/x", tool="plot_run"))
        == "path_resolution"
    )
    assert (
        classify_failure(_fail("out_file must be an existing repository file: a"))
        == "path_resolution"
    )
    assert (
        classify_failure(_fail("directory must exist inside repository"))
        == "path_resolution"
    )
    assert (
        classify_failure(_fail("file not found: /runs/x/model.inp"))
        == "path_resolution"
    )


def test_classify_swmm_error() -> None:
    assert (
        classify_failure(_fail("ERROR 138: invalid keyword at line 11"))
        == "swmm_error"
    )


def test_classify_tool_error_fallback() -> None:
    assert classify_failure(_fail("something unexpected blew up")) == "tool_error"


# --- record / read ----------------------------------------------------------


def test_record_writes_only_real_failures(tmp_path) -> None:
    store = tmp_path / "run_failures.jsonl"
    results = [
        _ok(),
        _fail(
            "tool not approved by user",
            permission={"prompted": True, "approved": False},
        ),
        _fail(
            "MCP transport failed: MCP process ended before sending a "
            "complete line."
        ),
        _fail("could not resolve .inp from /runs/x", tool="plot_run"),
    ]
    recorded = record_run_failures(store, "run-123", results)
    assert len(recorded) == 2
    assert {f.failure_class for f in recorded} == {
        "mcp_transport",
        "path_resolution",
    }
    rows = read_run_failures(store)
    assert len(rows) == 2
    assert all(r.run_id == "run-123" for r in rows)


def test_record_clean_run_writes_nothing(tmp_path) -> None:
    store = tmp_path / "run_failures.jsonl"
    recorded = record_run_failures(store, "run-1", [_ok(), _ok("glob")])
    assert recorded == []
    assert not store.exists()


def test_read_missing_store_returns_empty(tmp_path) -> None:
    assert read_run_failures(tmp_path / "nope.jsonl") == []


def test_read_tolerates_torn_final_line(tmp_path) -> None:
    store = tmp_path / "run_failures.jsonl"
    record_run_failures(store, "r1", [_fail("ERROR 200: boom")])
    with store.open("a", encoding="utf-8") as handle:
        handle.write('{"partial": ')  # torn final line
    rows = read_run_failures(store)
    assert len(rows) == 1
    assert rows[0].failure_class == "swmm_error"


def test_summary_is_capped(tmp_path) -> None:
    store = tmp_path / "run_failures.jsonl"
    long_summary = "MCP transport failed: " + "x" * 1000
    recorded = record_run_failures(store, "r", [_fail(long_summary)])
    assert len(recorded[0].summary) <= 300


# --- resolve_store ----------------------------------------------------------


def test_resolve_store_honors_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(tmp_path))
    assert resolve_store() == tmp_path / "run_failures.jsonl"


def test_resolve_store_explicit_dir(tmp_path) -> None:
    assert resolve_store(tmp_path) == tmp_path / "run_failures.jsonl"
