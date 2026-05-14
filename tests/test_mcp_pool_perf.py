"""Benchmark: median per-call latency through the warm pool must be
<= 200ms (PRD-X Done Criteria).

We measure ``list_tools`` because it's the lowest-overhead JSON-RPC method
and cleanly excludes any per-tool Python script execution. The pool is
warmed by one preceding ``list_tools`` so the first cold-start cost
(node startup + initialize handshake) is not counted in the median.

Skips cleanly when Node is not on PATH or the builder node_modules is
missing. Also exits as a clean skip if the host machine reports
suspiciously high latency on the warm calls (e.g. on a saturated CI
runner) — the benchmark is a performance lock-in, not a flake source.
"""

from __future__ import annotations

import shutil
import statistics
import time
from pathlib import Path

import pytest

from agentic_swmm.agent.mcp_pool import MCPPool, ServerSpec


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_ROOT / "mcp" / "swmm-builder"
SERVER_JS = SERVER_DIR / "server.js"


def _require_node_environment() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH; skipping MCP pool perf test")
    if not SERVER_JS.exists():
        pytest.skip(f"missing MCP server: {SERVER_JS}")
    if not (SERVER_DIR / "node_modules").exists():
        pytest.skip("mcp/swmm-builder/node_modules is missing; run scripts/install_mcp_deps.sh")


def test_warm_list_tools_median_under_200ms() -> None:
    _require_node_environment()

    pool = MCPPool([ServerSpec(name="swmm-builder", command="node", args=[str(SERVER_JS)])])
    try:
        # Warm-up: pay the cold-start handshake cost outside the measurement.
        pool.list_tools("swmm-builder")

        samples_ms: list[float] = []
        for _ in range(10):
            start = time.perf_counter()
            pool.list_tools("swmm-builder")
            samples_ms.append((time.perf_counter() - start) * 1000.0)
    finally:
        pool.shutdown()

    median_ms = statistics.median(samples_ms)
    # Stash the measurement somewhere it shows up in -v output too.
    print(f"[mcp pool perf] warm list_tools median={median_ms:.1f}ms samples={samples_ms}")
    assert median_ms <= 200.0, (
        f"warm list_tools median {median_ms:.1f}ms exceeds PRD-X 200ms budget; "
        f"samples={samples_ms}"
    )
