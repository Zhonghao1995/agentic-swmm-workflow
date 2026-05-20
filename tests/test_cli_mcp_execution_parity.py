"""Regression guard: CLI and MCP execution paths must agree.

WHAT THIS PROVES
----------------
The ``aiswmm`` CLI exposes a SWMM run via ``agentic_swmm/commands/run.py``;
the ``swmm-runner`` MCP server exposes the same capability via the
``swmm_run`` JSON-RPC tool. This module runs the *same* Todcreek INP
through both entry points and asserts the SWMM numerical output is
byte-identical (only wall-clock lines in the ``.rpt`` may differ).

PATH-INDEPENDENCE FINDING (Step 0)
----------------------------------
The two entry points are genuinely *different invocation routes* but
they *converge on one shared SWMM runner*:

* CLI path:  ``agentic_swmm/commands/run.py`` -> ``python_command(...)``
  -> ``skills/swmm-runner/scripts/swmm_runner.py run`` (Python process)
  -> ``subprocess.run(["swmm5", inp, rpt, out])``.
* MCP path:  ``mcp/swmm-runner/server.js`` -> Node ``spawn("python3",
  [swmm_runner.py, "run", ...])`` -> the *same* ``swmm_runner.py`` ->
  the *same* ``subprocess.run(["swmm5", ...])``.

So the launchers differ (a Python ``subprocess`` invocation built by
``agentic_swmm.utils.subprocess_runner`` vs a Node.js ``child_process``
spawn over MCP stdio JSON-RPC), but both ultimately exec the identical
``swmm_runner.py`` script and the identical ``swmm5`` binary. Numerical
parity is therefore *expected*; the value of this test is guarding
against future divergence — e.g. if either path grows INP preprocessing,
a different solver flag, or a separate runner implementation, the
core hash assertion breaks immediately.

The CLI path additionally wraps the runner in the standard run-folder
layout (``00_inputs/ 04_builder/ 05_runner/ ...``); the MCP path writes
the runner artifacts flat into ``runDir``. This module compares the
runner-produced ``.rpt``/``.out`` from each, which is the SWMM output
proper, independent of the surrounding folder scaffolding.

NO LLM IS INVOLVED
------------------
The MCP path is exercised by speaking JSON-RPC (``initialize`` +
``tools/call``) to the server directly, reusing the repo's own MCP
stdio harness (``skills/swmm-end-to-end/scripts/mcp_stdio_call.py``).
The agent runtime / LLM planner is never started, so no
``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` is required.

SKIP CONDITIONS
---------------
* No ``swmm5`` binary on PATH -> skip (the solver is mandatory).
* No ``node`` on PATH, or the ``swmm-runner`` MCP server cannot start
  (e.g. ``node_modules`` not installed) -> skip the MCP-dependent tests.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TODCREEK_INP = REPO_ROOT / "examples" / "todcreek" / "model_chicago5min.inp"
MCP_HARNESS = REPO_ROOT / "skills" / "swmm-end-to-end" / "scripts" / "mcp_stdio_call.py"
MCP_SERVER_DIR = REPO_ROOT / "mcp" / "swmm-runner"
TODCREEK_OUTFALL = "O1"

# Wall-clock lines SWMM stamps into every .rpt. These legitimately
# differ between two runs of the same model and must be stripped
# before hashing the report for a parity comparison.
_WALLCLOCK_RE = re.compile(
    r"^\s*(Analysis begun on:|Analysis ended on:|Total elapsed time:)",
)


# --------------------------------------------------------------------------
# environment probes -> skip markers
# --------------------------------------------------------------------------
def _swmm5_available() -> bool:
    return shutil.which("swmm5") is not None


def _node_available() -> bool:
    return shutil.which("node") is not None


def _mcp_server_importable() -> bool:
    """True only if the swmm-runner MCP server can actually start.

    ``node_modules`` is .gitignored, so a fresh checkout (or a CI
    runner that has not run ``scripts/install_mcp_deps.sh``) cannot
    launch the server. Probe by importing ``server.js`` with
    ``--check`` (syntax + module resolution, no side effects).
    """
    if not _node_available():
        return False
    server_js = MCP_SERVER_DIR / "server.js"
    if not server_js.exists():
        return False
    try:
        proc = subprocess.run(
            ["node", "--check", str(server_js)],
            cwd=MCP_SERVER_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    # --check validates syntax but not import resolution; confirm the
    # MCP SDK dependency is actually installed.
    return (MCP_SERVER_DIR / "node_modules" / "@modelcontextprotocol").is_dir()


_needs_swmm5 = pytest.mark.skipif(
    not _swmm5_available(), reason="swmm5 binary not available on PATH"
)
_needs_mcp = pytest.mark.skipif(
    not (_swmm5_available() and _mcp_server_importable()),
    reason="swmm5 and/or the swmm-runner MCP server (node + node_modules) unavailable",
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _run_via_cli(run_dir: Path) -> Path:
    """Run Todcreek through the CLI entry point; return its ``.rpt``.

    Invokes ``agentic_swmm.cli`` exactly as the ``aiswmm run`` console
    script would. The runner artifacts land in ``05_runner/`` of the
    standard run-folder layout.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentic_swmm.cli",
            "run",
            "--inp",
            str(TODCREEK_INP),
            "--run-dir",
            str(run_dir),
            "--node",
            TODCREEK_OUTFALL,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"CLI run failed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    rpt = run_dir / "05_runner" / "model.rpt"
    assert rpt.exists(), f"CLI path produced no .rpt at {rpt}"
    return rpt


def _run_via_mcp(run_dir: Path, tmp_path: Path) -> Path:
    """Run Todcreek through the MCP ``swmm_run`` tool; return its ``.rpt``.

    Speaks JSON-RPC to ``mcp/swmm-runner/server.js`` via the repo's own
    stdio harness — no agent runtime, no LLM. The harness performs
    ``initialize`` -> ``notifications/initialized`` -> ``tools/list`` ->
    ``tools/call``.
    """
    response = tmp_path / "mcp_response.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(MCP_HARNESS),
            "--server-dir",
            "mcp/swmm-runner",
            "--tool",
            "swmm_run",
            "--arguments-json",
            json.dumps(
                {
                    "inp": str(TODCREEK_INP),
                    "runDir": str(run_dir),
                    "node": TODCREEK_OUTFALL,
                }
            ),
            "--out-response",
            str(response),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"MCP swmm_run failed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    summary = json.loads(proc.stdout)
    assert summary["transport"] == "mcp_stdio", summary
    assert summary["tool"] == "swmm_run", summary
    rpt = run_dir / "model.rpt"
    assert rpt.exists(), f"MCP path produced no .rpt at {rpt}"
    return rpt


def _strip_wallclock(rpt: Path) -> str:
    """Return the ``.rpt`` text with SWMM's wall-clock lines removed."""
    lines = rpt.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(line for line in lines if not _WALLCLOCK_RE.match(line))


def _rpt_hash_modulo_timestamps(rpt: Path) -> str:
    return hashlib.sha256(_strip_wallclock(rpt).encode("utf-8")).hexdigest()


def _continuity_errors(rpt: Path) -> dict:
    """Extract continuity-error percentages by parsing the ``.rpt``.

    Reuses the project's own runner parser so the numbers are read the
    same way production code reads them.
    """
    import importlib.util

    runner_path = REPO_ROOT / "skills" / "swmm-runner" / "scripts" / "swmm_runner.py"
    spec = importlib.util.spec_from_file_location("swmm_runner_parity", runner_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parsed = module.parse_continuity_blocks(rpt.read_text(errors="ignore"))
    return parsed["continuity_error_percent"]


def _series(out_file: Path, label: tuple[str, str, str]) -> list[float]:
    """Extract one SWMM time series from a ``.out`` binary as a value list.

    ``label`` is a ``(type, name, variable)`` triple, e.g.
    ``("node", "O1", "Total_inflow")``.
    """
    import swmmtoolbox.swmmtoolbox as swmmtoolbox

    frame = swmmtoolbox.extract(str(out_file), ",".join(label))
    return [float(v) for v in frame.iloc[:, 0].tolist()]


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
@_needs_swmm5
def test_cli_path_produces_swmm_artifacts(tmp_path: Path) -> None:
    """The CLI path runs Todcreek and yields a non-empty .rpt and .out."""
    run_dir = tmp_path / "cli"
    rpt = _run_via_cli(run_dir)
    out = run_dir / "05_runner" / "model.out"

    assert rpt.exists() and rpt.stat().st_size > 0
    assert out.exists() and out.stat().st_size > 0


@_needs_mcp
def test_mcp_path_produces_swmm_artifacts(tmp_path: Path) -> None:
    """The MCP path runs the same INP and yields a non-empty .rpt and .out."""
    run_dir = tmp_path / "mcp"
    rpt = _run_via_mcp(run_dir, tmp_path)
    out = run_dir / "model.out"

    assert rpt.exists() and rpt.stat().st_size > 0
    assert out.exists() and out.stat().st_size > 0


@_needs_mcp
def test_cli_and_mcp_rpt_identical_modulo_timestamps(tmp_path: Path) -> None:
    """Core assertion: the two .rpt files are identical once the
    wall-clock lines (analysis begun/ended, elapsed time) are stripped.
    """
    cli_rpt = _run_via_cli(tmp_path / "cli")
    mcp_rpt = _run_via_mcp(tmp_path / "mcp", tmp_path)

    cli_hash = _rpt_hash_modulo_timestamps(cli_rpt)
    mcp_hash = _rpt_hash_modulo_timestamps(mcp_rpt)

    assert cli_hash == mcp_hash, (
        "Timestamp-stripped .rpt SHA-256 differs between CLI and MCP paths.\n"
        f"  CLI: {cli_hash}\n  MCP: {mcp_hash}\n"
        "The two execution paths have diverged in SWMM numerical output."
    )


@_needs_mcp
def test_cli_and_mcp_numerical_results_identical(tmp_path: Path) -> None:
    """Continuity errors plus one node and one subcatchment series must
    be exactly equal across the two execution paths.
    """
    cli_run = tmp_path / "cli"
    mcp_run = tmp_path / "mcp"
    _run_via_cli(cli_run)
    _run_via_mcp(mcp_run, tmp_path)

    cli_rpt = cli_run / "05_runner" / "model.rpt"
    mcp_rpt = mcp_run / "model.rpt"
    cli_out = cli_run / "05_runner" / "model.out"
    mcp_out = mcp_run / "model.out"

    # continuity errors (parsed from .rpt)
    assert _continuity_errors(cli_rpt) == _continuity_errors(mcp_rpt)

    # node series + subcatchment series (extracted from .out)
    node_label = ("node", TODCREEK_OUTFALL, "Total_inflow")
    sub_label = ("subcatchment", "S1", "Runoff_rate")
    assert _series(cli_out, node_label) == _series(mcp_out, node_label)
    assert _series(cli_out, sub_label) == _series(mcp_out, sub_label)

    # the .out binary itself is byte-identical (SWMM stamps no
    # wall-clock into it, unlike the .rpt)
    assert cli_out.read_bytes() == mcp_out.read_bytes()


@_needs_mcp
def test_cli_and_mcp_are_distinct_entry_points(tmp_path: Path) -> None:
    """The CLI and MCP paths are genuinely different invocation routes.

    Step 0 finding: they converge on one shared runner
    (``skills/swmm-runner/scripts/swmm_runner.py``), but the *launchers*
    differ — the CLI builds a Python ``subprocess`` invocation while the
    MCP server is a Node.js process spawned over stdio JSON-RPC. This
    test asserts that observable difference so the "distinct entry
    points" claim is checked, not merely documented.
    """
    cli_run = tmp_path / "cli"
    mcp_run = tmp_path / "mcp"
    _run_via_cli(cli_run)
    _run_via_mcp(mcp_run, tmp_path)

    # The CLI path wraps the runner in the standard run-folder layout;
    # the MCP path writes runner artifacts flat into runDir. Different
    # output scaffolding => genuinely different entry points.
    assert (cli_run / "05_runner" / "model.rpt").exists()
    assert (cli_run / "manifest.json").exists()
    assert (cli_run / "command_trace.json").exists()
    assert not (mcp_run / "05_runner").exists()
    assert (mcp_run / "model.rpt").exists()

    # The CLI path additionally emits a 04_builder handoff stage that
    # the bare MCP runner never produces.
    assert (cli_run / "04_builder" / "manifest.json").exists()
    assert not (mcp_run / "04_builder").exists()
