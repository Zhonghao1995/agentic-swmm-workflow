"""``aiswmm map`` — render the spatial layout of a SWMM model.

Companion verb to ``aiswmm plot``. ``plot`` produces the time-series
view (rainfall vs runoff hydrograph); ``map`` produces the spatial
view (subcatchment polygons + conduit network + node markers). Every
reviewer of a SWMM model asks both questions, so the two verbs sit at
the same CLI level and route through the same skill (``swmm-plot``).

Data discovery (run-dir convention introduced by PRD
swmmanywhere_integration):

* If ``<run-dir>/10_swmmanywhere/`` contains ``nodes.geoparquet`` +
  ``edges.geoparquet`` + ``subcatchments.geoparquet`` (the artefacts
  the swmm-anywhere runner copies out of the SWMManywhere pipeline),
  the renderer reads those directly — the geometries are real WGS84
  shapes downloaded from OSM.
* Otherwise the renderer parses ``[COORDINATES]``, ``[VERTICES]``,
  ``[Polygons]``, ``[SUBCATCHMENTS]``, ``[JUNCTIONS]``, ``[OUTFALLS]``,
  and ``[CONDUITS]`` directly out of the INP. This path needs nothing
  beyond matplotlib and works on a bare aiswmm install.

The actual rendering lives in
``skills/swmm-plot/scripts/plot_network_layout.py`` — this module is a
thin CLI surface that resolves discovery, then ``run_command``s the
script. Same architecture as ``plot.py`` so the two verbs share the
same trace shape under ``command_trace.json``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.commands.plot import _find_inp, _read_manifest
from agentic_swmm.utils.paths import require_dir, require_file, script_path
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command


_MAP_EXAMPLE = "aiswmm map --run-dir runs/2026-05-27/195456_e2e_chain"


def _find_synth_dir(run_dir: Path, manifest: dict[str, Any]) -> Path | None:
    """Locate the SWMManywhere geoparquet directory, if present.

    SWMManywhere's runner writes its three geoparquet artefacts under
    ``<run-dir>/10_swmmanywhere/``. We don't require all three to be
    present — the script will gracefully fall back to INP parsing
    when any of the files (or geopandas itself) is missing.
    """
    candidate = run_dir / "10_swmmanywhere"
    if candidate.is_dir():
        # Only return when at least one of the three artefacts is
        # actually there. An empty directory means SWMManywhere never
        # finished, so we should fall back to INP parsing.
        for name in ("nodes.geoparquet", "edges.geoparquet", "subcatchments.geoparquet"):
            if (candidate / name).exists():
                return candidate
    return None


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``aiswmm map``.

    Mirrors the ``aiswmm plot`` surface: ``--run-dir`` is the load-
    bearing input (drives auto-discovery of the INP + the optional
    SWMManywhere geoparquet trio). ``--inp`` lets the user point at a
    specific INP outside the run directory (useful when the INP lives
    in ``examples/`` but the rendering should land under a run dir).
    """
    parser = subparsers.add_parser(
        "map",
        help="Render the spatial layout (subcatchments + network + outfalls).",
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        type=Path,
        help="Run directory containing the INP (and optionally the SWMManywhere geoparquet trio).",
    )
    parser.add_argument(
        "--inp",
        type=Path,
        default=None,
        help="Explicit INP file. Defaults to auto-discovery from --run-dir.",
    )
    parser.add_argument(
        "--out-png",
        type=Path,
        default=None,
        help="Output PNG path. Defaults to <run-dir>/07_plots/network_map.png.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Output resolution (default 200 — print-friendly).",
    )
    parser.add_argument(
        "--no-subcatchments",
        action="store_true",
        help="Skip the subcatchment polygon layer.",
    )
    parser.add_argument(
        "--no-vertices",
        action="store_true",
        help=(
            "Draw conduits as straight lines (ignore [VERTICES]). "
            "Useful when [VERTICES] are noisy or missing."
        ),
    )
    register_example_flag(parser, example_text=_MAP_EXAMPLE)
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    """Resolve run-dir / INP / geoparquet, then invoke the renderer."""
    run_dir = require_dir(args.run_dir, "run directory")
    manifest = _read_manifest(run_dir)

    inp = require_file(args.inp, "INP file") if args.inp else _find_inp(run_dir, manifest)
    # The INP is required for the fallback path; on the geoparquet
    # path it is unused. We only fail-fast when *neither* source is
    # available, so users with a non-SWMManywhere run that has no
    # INP get a clear error.
    synth_dir = _find_synth_dir(run_dir, manifest)
    if inp is None and synth_dir is None:
        raise FileNotFoundError(
            f"Unable to find an INP file in run directory: {run_dir}. "
            "Pass --inp explicitly or run aiswmm against a directory containing one."
        )

    out_png = (
        args.out_png.expanduser().resolve()
        if args.out_png
        else run_dir / "07_plots" / "network_map.png"
    )

    script = script_path("skills", "swmm-plot", "scripts", "plot_network_layout.py")
    command = python_command(
        script,
        "--out-png",
        str(out_png),
        "--dpi",
        str(args.dpi),
    )
    if inp is not None:
        command.extend(["--inp", str(inp)])
    if synth_dir is not None:
        command.extend(["--synth-dir", str(synth_dir)])
    if args.no_subcatchments:
        command.append("--no-subcatchments")
    if args.no_vertices:
        command.append("--no-vertices")

    result = run_command(command)
    append_trace(run_dir / "command_trace.json", result, stage="map")
    print(f"map: {out_png}")
    return result.return_code


__all__ = ["main", "register"]
