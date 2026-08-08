"""`aiswmm plot` default-node resolution and missing-id errors.

Found live (2026-08-08 maintenance sweep): plot without --node forwarded
the literal O1 to the plot script, which exploded inside swmmtoolbox
with a raw pandas "No objects to concatenate" on any model that names
its outfalls differently (the example case included). Same defect class
as the `aiswmm run` default fixed in #361, plus the script now turns a
missing id into an actionable error listing real ids.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import uuid

import pytest

from agentic_swmm.utils.paths import repo_root

pytestmark = pytest.mark.skipif(
    shutil.which("swmm5") is None, reason="swmm5 binary not available on PATH"
)

EXAMPLE_INP = repo_root() / "examples" / "tecnopolo" / "tecnopolo_r1_199401.inp"


@pytest.fixture(scope="module")
def completed_run(tmp_path_factory):
    run_dir = repo_root() / "runs" / "agent" / f"plot-default-test-{uuid.uuid4().hex[:8]}"
    proc = subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", "run", "--inp", str(EXAMPLE_INP), "--run-dir", str(run_dir)],
        capture_output=True,
        text=True,
        cwd=repo_root(),
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    yield run_dir
    shutil.rmtree(run_dir, ignore_errors=True)


def test_plot_without_node_resolves_a_real_outfall(completed_run):
    proc = subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", "plot", "--run-dir", str(completed_run)],
        capture_output=True,
        text=True,
        cwd=repo_root(),
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert (completed_run / "08_plot" / "fig_rain_runoff.png").is_file()


def test_plot_with_missing_node_reports_available_ids(completed_run):
    proc = subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", "plot", "--run-dir", str(completed_run), "--node", "NOPE"],
        capture_output=True,
        text=True,
        cwd=repo_root(),
    )
    assert proc.returncode != 0
    combined = proc.stderr + proc.stdout
    assert "not found" in combined
    assert "Available node ids" in combined
    assert "No objects to concatenate" not in combined
