"""Opt-in LIVE end-to-end: SWMMCanada service -> INP -> swmm5 -> audit.

Skipped unless BOTH are set:

    AISWMM_SWMMCANADA_URL=<service url>   # e.g. a local :8000 container
    AISWMM_SWMMCANADA_LIVE=1              # explicit opt-in to network use

and swmm5 is on PATH for the simulate/audit half. This encodes the
manual chain validation (first performed 2026-08-08 against the hosted
beta: Victoria downtown AOI, real-network mode) as a repeatable
artifact. It costs minutes and a server-side model build, which is why
it is opt-in instead of part of the default suite.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

from agentic_swmm.utils.paths import repo_root

LIVE = os.environ.get("AISWMM_SWMMCANADA_LIVE") == "1" and bool(
    os.environ.get("AISWMM_SWMMCANADA_URL", "").strip()
)

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="live SWMMCanada smoke is opt-in: set AISWMM_SWMMCANADA_URL and AISWMM_SWMMCANADA_LIVE=1",
)

# Small downtown-Victoria AOI: fast to build, covered by real pipes.
BBOX = [-123.370, 48.425, -123.360, 48.432]


def _aoi() -> str:
    min_lon, min_lat, max_lon, max_lat = BBOX
    ring = [
        [min_lon, min_lat],
        [max_lon, min_lat],
        [max_lon, max_lat],
        [min_lon, max_lat],
        [min_lon, min_lat],
    ]
    return json.dumps({"type": "Polygon", "coordinates": [ring]})


def test_fetch_run_audit_chain_live():
    from agentic_swmm.integrations.swmmcanada_runner import fetch_from_aoi

    run_dir = repo_root() / "runs" / "agent" / f"canada-live-{uuid.uuid4().hex[:8]}"
    result = fetch_from_aoi(
        _aoi(),
        date(2024, 6, 1),
        date(2024, 6, 2),
        run_dir=run_dir,
        timeout=900.0,
    )
    assert result.inp_path.is_file()
    assert result.mode in ("real", "synthesize", "synthesized")
    assert result.run_dir == run_dir
    assert result.zip_path.is_file()

    if shutil.which("swmm5") is None:
        pytest.skip("swmm5 not on PATH; fetch half validated, simulate half skipped")

    env = dict(os.environ, AISWMM_AUTO_APPROVE="1")
    run_proc = subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", "run", "--inp", str(result.inp_path), "--run-dir", str(run_dir)],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo_root(),
    )
    assert run_proc.returncode == 0, run_proc.stderr[-2000:]

    audit_proc = subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", "audit", "--run-dir", str(run_dir)],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo_root(),
    )
    assert audit_proc.returncode == 0, audit_proc.stderr[-2000:]
