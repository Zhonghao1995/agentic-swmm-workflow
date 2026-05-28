#!/usr/bin/env python3
"""CLI entrypoint for the `swmm-anywhere` skill.

Synthesizes a plausible SWMM drainage network from a bbox using
SWMManywhere, then writes the audit-pipeline-ready output under
``runs/<date>/<id>/``. The actual heavy lifting lives in
``agentic_swmm.integrations.swmmanywhere_runner``.

Usage:

    python skills/swmm-anywhere/scripts/synth_from_bbox.py \\
        --bbox 0.04020 51.55759 0.05450 51.56660 \\
        --run-dir runs/2026-05-27/231012_swmm_anywhere

If --run-dir is omitted, a fresh directory under ``runs/<today>/`` is
created with an HHMMSS prefix.

Requires `pip install aiswmm[anywhere]`.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def _default_run_dir() -> Path:
    now = datetime.now()
    return (
        Path("runs")
        / now.strftime("%Y-%m-%d")
        / (now.strftime("%H%M%S") + "_swmm_anywhere")
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Synthesize a plausible SWMM .inp from a bbox via SWMManywhere "
            "(© Imperial College London, BSD-3-Clause, "
            "https://github.com/ImperialCollegeLondon/SWMManywhere). "
            "Use only when no real pipe-network data exists for the area."
        )
    )
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        required=True,
        metavar=("min_lon", "min_lat", "max_lon", "max_lat"),
        help="Bounding box in WGS84 degrees.",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Target directory; defaults to runs/<today>/<HHMMSS>_swmm_anywhere/",
    )
    parser.add_argument(
        "--project-name",
        default="swmm_anywhere",
        help="Human-readable label embedded in the SWMManywhere config + provenance.",
    )
    parser.add_argument(
        "--refresh-raw",
        action="store_true",
        help="Reserved: re-fetch OSM/DEM even if a snapshot already exists.",
    )
    parser.add_argument(
        "--upstream-defaults",
        action="store_true",
        help=(
            "Skip the spike-04 tuned outfall_derivation overrides "
            "(method=withtopo, river_buffer_distance=300, outfall_length=200) "
            "and let SWMManywhere use its upstream parameters.py defaults "
            "(method=separate, river_buffer_distance=150, outfall_length=40). "
            "Use to reproduce SWMManywhere's extended_demo behaviour or to "
            "compare against upstream output."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the SynthRunResult summary as machine-readable JSON.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Lazy import: lets `--help` work without the `[anywhere]` extra installed.
    try:
        from agentic_swmm.integrations.swmmanywhere_runner import (
            SynthRunError,
            run_synth_from_bbox,
        )
    except ImportError as exc:  # pragma: no cover - import-time only
        print(
            f"error: {exc}\nhint: this skill requires the [anywhere] extra. "
            "Install with `pip install aiswmm[anywhere]`.",
            file=sys.stderr,
        )
        return 2

    run_dir = args.run_dir or _default_run_dir()
    print(f"[swmm-anywhere] bbox:      {args.bbox}", file=sys.stderr)
    print(f"[swmm-anywhere] run-dir:   {run_dir}", file=sys.stderr)

    try:
        result = run_synth_from_bbox(
            bbox=args.bbox,
            run_dir=run_dir,
            project_name=args.project_name,
            refresh_raw=args.refresh_raw,
            use_upstream_defaults=args.upstream_defaults,
        )
    except SynthRunError as exc:
        print(
            f"error: swmm-anywhere stage `{exc.stage}` failed",
            file=sys.stderr,
        )
        print(f"cause: {exc.original_exc!r}", file=sys.stderr)

        # Stage-specific actionable hints. The `extra_missing` branch is the
        # most common first-time failure — the user has aiswmm installed but
        # never opted into the geo-heavy [anywhere] extra. Surface the fix
        # path prominently instead of the generic "smaller bbox / refresh"
        # hint that doesn't apply.
        if exc.stage == "extra_missing":
            print(
                "hint: this skill requires the optional [anywhere] extra, which "
                "wraps SWMManywhere by Imperial College London (BSD-3-Clause).",
                file=sys.stderr,
            )
            print("      Upstream: https://github.com/ImperialCollegeLondon/SWMManywhere", file=sys.stderr)
            print("      Install with:", file=sys.stderr)
            print("        pip install aiswmm[anywhere]", file=sys.stderr)
            print(
                "      (pulls in ~27 geo dependencies — geopandas, osmnx, "
                "rasterio, … ~500 MB.\n      Opt-in by design so the default "
                "aiswmm install stays light.)",
                file=sys.stderr,
            )
        else:
            print(
                "hint: re-run with --refresh-raw if the failure was a stale "
                "OSM/DEM snapshot, or pass a smaller bbox if the failure was OOM.",
                file=sys.stderr,
            )
        return 1

    summary = {
        "inp_path": str(result.inp_path),
        "run_dir": str(result.run_dir),
        "raw_manifest_path": str(result.raw_manifest_path),
        "stage_durations_s": result.stage_durations,
        "warnings": list(result.warnings),
        "provenance": result.provenance,
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=False))
    else:
        print(f"[swmm-anywhere] DONE", file=sys.stderr)
        print(f"  inp:           {result.inp_path}")
        print(f"  raw manifest:  {result.raw_manifest_path}")
        print(f"  stages (s):    {result.stage_durations}")
        if result.warnings:
            for w in result.warnings:
                print(f"  warning:      {w}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
