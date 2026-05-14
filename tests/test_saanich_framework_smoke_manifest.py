"""Validate that any locally-present Saanich smoke run produced a manifest
with the schema the framework promises (Mode 0 of swmm-end-to-end).

Behaviour:
- If the env var SAANICH_SMOKE_RUN_DIR is set, validate that specific run.
- Otherwise auto-discover the most recently-modified
  ``runs/*-saanich-framework-smoke`` directory that contains a
  ``framework_mcp_manifest.json``.
- If no such run exists locally (e.g. fresh clone, CI runner before
  any smoke), the test is skipped — runs/ is .gitignored and the
  framework-validation copy under docs/ is the canonical archive
  for those cases.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_run_dir() -> Path | None:
    env = os.environ.get("SAANICH_SMOKE_RUN_DIR")
    if env:
        candidate = Path(env)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        return candidate if (candidate / "framework_mcp_manifest.json").exists() else None
    runs_dir = REPO_ROOT / "runs"
    if not runs_dir.exists():
        return None
    matches = sorted(
        (p for p in runs_dir.glob("*-saanich-framework-smoke")
         if (p / "framework_mcp_manifest.json").exists()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def test_saanich_smoke_run_records_mcp_tool_call_audit() -> None:
    run_dir = _resolve_run_dir()
    if run_dir is None:
        pytest.skip(
            "no runs/*-saanich-framework-smoke/framework_mcp_manifest.json found locally; "
            "set SAANICH_SMOKE_RUN_DIR to validate a specific run"
        )

    manifest_path = run_dir / "framework_mcp_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["skill"] == "swmm-end-to-end"
    assert manifest["mode"] == "MCP-first framework smoke test mode"
    assert manifest["tool_transport"] == "mcp"
    assert manifest["missing_or_fallback_inputs"]
    assert manifest["framework_gaps"]

    calls = manifest["mcp_tool_calls"]
    expected = [
        "swmm-gis-mcp.qgis_area_weighted_params",
        "swmm-climate-mcp.format_rainfall",
        "swmm-network-mcp.import_city_network",
        "swmm-network-mcp.qa",
        "swmm-builder-mcp.build_inp",
        "swmm-runner-mcp.swmm_run",
    ]
    assert [call["tool"] for call in calls[: len(expected)]] == expected
    assert all(call["transport"] == "mcp_stdio" for call in calls)
    assert all(call["status"] in {"ok", "fallback_used", "missing_contract"} for call in calls)
