"""``aiswmm storm`` — algorithmic design-storm generator (PRD-06 B.4).

A pure CLI surface over
:mod:`agentic_swmm.agent.swmm_runtime.design_storm`. Writes a SWMM
``[TIMESERIES]`` block to ``--out`` (file) or stdout when ``--out`` is
omitted.

Shapes:
  * ``uniform``, ``triangular``, ``front_loaded``, ``back_loaded`` —
    in-code primitive shapes (preserved from the original B.4 surface).
  * ``chicago`` — Chicago hyetograph, depth-driven or IDF-driven.
  * ``huff`` — Huff quartile (``--quartile 1..4``).
  * ``scs`` — SCS Type II 24-hour shape (or any duration_min divisor).

The ``--from-library <key>`` flag short-circuits the shape selection
and pulls IDF / peak_position from the storm_library.yaml entry.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_json_flag,
    register_library_entry_flag,
    register_path_flag,
    register_quiet_flag,
)
from agentic_swmm.agent.honesty import (
    emit_silent_default_warning,
    emit_silent_override_warning,
)
from agentic_swmm.agent.swmm_runtime.design_storm import (
    chicago_hyetograph,
    generate_design_storm,
    huff_hyetograph,
    scs_type_ii_hyetograph,
    to_swmm_dat,
)
from agentic_swmm.memory.storm_library import recall_chicago_spec
from agentic_swmm.utils.paths import repo_root


_STORM_EXAMPLE = (
    "aiswmm storm --shape chicago --depth-mm 25 --duration-min 60 "
    "--peak-position 0.4 --out storm.dat"
)


# Choices for ``--shape``. ``chicago``/``huff``/``scs`` are the new
# engineering shapes added in Round 2; the four primitive shapes
# remain available for back-compat.
_SHAPE_CHOICES = (
    "uniform",
    "triangular",
    "front_loaded",
    "back_loaded",
    "chicago",
    "huff",
    "scs",
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "storm",
        help="Generate an algorithmic design storm in SWMM DAT format (PRD-06 B.4).",
    )
    parser.add_argument(
        "--depth-mm",
        type=float,
        default=None,
        help=(
            "Total rainfall depth in millimetres. Required for all shapes "
            "except chicago-IDF (where it is inferred from the IDF integral)."
        ),
    )
    parser.add_argument(
        "--duration-min",
        type=int,
        default=None,
        help=(
            "Storm duration in minutes. Must be a positive multiple of "
            "--interval-min so the last step is not truncated. For "
            "``--shape scs`` defaults to 1440 (24 hours)."
        ),
    )
    parser.add_argument(
        "--shape",
        choices=_SHAPE_CHOICES,
        default=None,
        help=(
            "Hyetograph shape. ``chicago`` / ``huff`` / ``scs`` are the "
            "new engineering shapes; the others are the primitive shapes. "
            "Defaults to ``uniform`` with a stderr notice; pass --shape "
            "explicitly to silence the notice."
        ),
    )
    parser.add_argument(
        "--interval-min",
        type=int,
        default=5,
        help="Time step of the hyetograph in minutes (default 5).",
    )
    parser.add_argument(
        "--start-time",
        type=str,
        default="2000-01-01 00:00",
        help="Storm start time, YYYY-MM-DD HH:MM (default '2000-01-01 00:00').",
    )
    parser.add_argument(
        "--station-id",
        type=str,
        default="STN1",
        help="SWMM timeseries station identifier (default 'STN1').",
    )
    # Chicago-specific knobs.
    parser.add_argument(
        "--peak-position",
        type=float,
        default=0.5,
        help=(
            "Chicago peak position as a fraction of duration (default 0.5). "
            "Typical regional values: 0.4 for Vancouver-class, 0.375 for "
            "US Midwest."
        ),
    )
    parser.add_argument(
        "--idf",
        type=str,
        default=None,
        help=(
            "Chicago IDF parameters as 'a=...,b=...,c=...' for "
            "i = a / (t + b)^c. When provided, the storm depth is "
            "inferred from the IDF integral; ``--depth-mm`` is ignored."
        ),
    )
    # Huff-specific knob.
    parser.add_argument(
        "--quartile",
        type=int,
        default=None,
        choices=(1, 2, 3, 4),
        help="Huff quartile (1=front-loaded, 4=back-loaded). Required for --shape huff.",
    )
    # Storm library lookup. PRD-08 A.2: canonical names are
    # ``--storm-library-entry`` (key inside the library) and
    # ``--storm-library-path`` (path to the YAML). The historical
    # spellings ``--from-library`` and ``--storm-library`` continue
    # to work as deprecated aliases.
    register_library_entry_flag(
        parser,
        noun="storm-library",
        help_text=(
            "Storm library entry key, e.g. "
            "'example_region_100yr_3hr_5min'. Looks up the entry "
            "under chicago_hyetographs and uses its idf_params / "
            "peak_position / duration_min. ``--depth-mm`` is still "
            "required when the library entry is depth-driven."
        ),
        legacy_aliases=("--from-library",),
        dest="from_library",
    )
    register_path_flag(
        parser,
        noun="storm-library",
        help_text=(
            "Optional override for the storm_library.yaml location. "
            "Defaults to memory/modeling-memory/storm_library.yaml."
        ),
        default=None,
        legacy_aliases=("--storm-library",),
        dest="storm_library",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Write the [TIMESERIES] block here. When omitted, the "
            "block is printed to stdout so a shell redirect still works."
        ),
    )
    # PRD-08 A.2: when ``--json`` is set, stdout receives a
    # machine-readable payload (start_time, interval_min, depth_mm,
    # intensities) instead of the DAT block. The DAT block is still
    # what ``--out`` writes when ``--out`` is supplied.
    register_json_flag(
        parser,
        help_text=(
            "Emit a structured DesignStorm JSON payload on stdout "
            "(intensities + provenance) instead of the SWMM DAT block."
        ),
    )
    register_quiet_flag(parser)
    register_example_flag(parser, example_text=_STORM_EXAMPLE)
    parser.set_defaults(func=main)


def _default_library_path() -> Path:
    return repo_root() / "memory" / "modeling-memory" / "storm_library.yaml"


def _parse_idf(text: str) -> dict[str, float]:
    """Parse ``a=...,b=...,c=...`` into ``{"a": ..., "b": ..., "c": ...}``.

    Tolerant of whitespace around the equals signs and commas. Missing
    or extra keys raise a ``ValueError`` so the CLI surfaces a clear
    error instead of silently using zeros.
    """
    out: dict[str, float] = {}
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            raise ValueError(f"--idf token must look like 'a=value': {token!r}")
        key, val = token.split("=", 1)
        key = key.strip().lower()
        try:
            out[key] = float(val.strip())
        except ValueError as exc:
            raise ValueError(f"--idf value for {key!r} must be a number") from exc
    missing = [k for k in ("a", "b", "c") if k not in out]
    if missing:
        raise ValueError(
            f"--idf must define a, b and c (missing: {','.join(missing)})"
        )
    return {"a": out["a"], "b": out["b"], "c": out["c"]}


def main(args: argparse.Namespace) -> int:
    # PRD-08 A.1 (audit #11): the historical default was a silent
    # ``uniform`` shape. A modeler asking for "a 25 mm 1-hour storm"
    # usually wants a Chicago / triangular shape; surface the default
    # to stderr so the choice is at least visible. Library lookups
    # below override the shape, so the notice only fires when the user
    # truly omitted both ``--shape`` and ``--from-library``.
    shape_was_user_supplied = args.shape is not None
    if not shape_was_user_supplied and not args.from_library:
        emit_silent_default_warning(
            flag_omitted="--shape",
            default_chosen="uniform",
            hint="pass --shape chicago for IDF-driven hyetograph",
        )
    if args.shape is None:
        args.shape = "uniform"

    # ---- Resolve a storm_library override ahead of shape dispatch.
    library_overrides: dict[str, object] = {}
    if args.from_library:
        lib_path = args.storm_library or _default_library_path()
        spec = recall_chicago_spec(lib_path, args.from_library)
        if spec is None:
            # PRD-08 A.3 (audit #35): differentiate three sub-cases —
            # library file missing, entry missing, or entry present
            # but only placeholder leaves.
            from agentic_swmm.agent.error_remediation import (
                storm_library_not_found,
            )
            from agentic_swmm.memory.storm_library import load_storm_library

            if not Path(lib_path).is_file():
                failure_mode = "library_missing"
                available_keys: list[str] = []
            else:
                lib_dict = load_storm_library(Path(lib_path)) or {}
                chicago_block = lib_dict.get("chicago_hyetographs") or {}
                if not isinstance(chicago_block, dict):
                    chicago_block = {}
                available_keys = list(chicago_block.keys())
                if args.from_library in chicago_block:
                    failure_mode = "entry_placeholder"
                else:
                    failure_mode = "entry_missing"
            err = storm_library_not_found(
                entry_key=args.from_library,
                library_path=Path(lib_path),
                available_keys=available_keys,
                failure_mode=failure_mode,
            )
            sys.stderr.write(err.format_for_stderr() + "\n")
            return 1
        # Treat the spec as if the user had passed equivalent flags;
        # explicit CLI arguments still win.
        library_overrides = {
            "idf_params": spec.get("idf_params"),
            "peak_position": spec.get("peak_position"),
            "duration_min": spec.get("duration_min"),
            "interval_min": spec.get("interval_min"),
        }
        # The library always implies a Chicago shape on this key.
        args.shape = "chicago"

    # PRD-08 A.1 (audit #12): when ``--idf`` is set the runtime
    # computes the depth from the IDF integral and silently drops
    # ``--depth-mm``. A modeler who asked for 25 mm got 72.19 mm
    # without warning. Emit the override notice before computing.
    if args.shape == "chicago" and args.idf and args.depth_mm is not None:
        emit_silent_override_warning(
            flag_user_set="--depth-mm",
            flag_user_value=args.depth_mm,
            reason="--idf is set; computed depth from IDF will be used",
        )

    duration_min = args.duration_min
    if duration_min is None:
        duration_min = library_overrides.get("duration_min")
    if duration_min is None and args.shape == "scs":
        duration_min = 1440

    interval_min = args.interval_min
    if "interval_min" in library_overrides and library_overrides["interval_min"]:
        # Explicit CLI override beats library; argparse default is 5,
        # so only adopt the library value when the user did not pass
        # ``--interval-min`` explicitly. We detect that by comparing
        # to the default; agreed to be best-effort.
        if interval_min == 5 and library_overrides["interval_min"] != 5:
            interval_min = int(library_overrides["interval_min"])

    try:
        if args.shape == "chicago":
            idf_params: dict[str, float] | None = None
            if args.idf:
                idf_params = _parse_idf(args.idf)
            elif library_overrides.get("idf_params"):
                raw = library_overrides["idf_params"]
                if isinstance(raw, dict):
                    idf_params = {
                        "a": float(raw.get("a") or 0.0),
                        "b": float(raw.get("b") or 0.0),
                        "c": float(raw.get("c") or 0.0),
                    }

            peak_position = args.peak_position
            if (
                library_overrides.get("peak_position") is not None
                and peak_position == 0.5
            ):
                peak_position = float(library_overrides["peak_position"])

            if duration_min is None:
                raise ValueError("--duration-min is required for --shape chicago")

            storm = chicago_hyetograph(
                depth_mm=None if idf_params else args.depth_mm,
                idf_params=idf_params,
                duration_min=int(duration_min),
                peak_position=float(peak_position),
                interval_min=int(interval_min),
                start_time=args.start_time,
            )
        elif args.shape == "huff":
            if args.quartile is None:
                raise ValueError("--quartile is required for --shape huff")
            if args.depth_mm is None:
                raise ValueError("--depth-mm is required for --shape huff")
            if duration_min is None:
                raise ValueError("--duration-min is required for --shape huff")
            storm = huff_hyetograph(
                depth_mm=float(args.depth_mm),
                duration_min=int(duration_min),
                quartile=int(args.quartile),
                interval_min=int(interval_min),
                start_time=args.start_time,
            )
        elif args.shape == "scs":
            if args.depth_mm is None:
                raise ValueError("--depth-mm is required for --shape scs")
            storm = scs_type_ii_hyetograph(
                depth_mm=float(args.depth_mm),
                duration_min=int(duration_min),
                interval_min=int(interval_min),
                start_time=args.start_time,
            )
        else:
            # Primitive shapes.
            if args.depth_mm is None:
                raise ValueError("--depth-mm is required")
            if duration_min is None:
                raise ValueError("--duration-min is required")
            storm = generate_design_storm(
                depth_mm=float(args.depth_mm),
                duration_min=int(duration_min),
                shape=args.shape,
                interval_min=int(interval_min),
                start_time=args.start_time,
            )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    text = to_swmm_dat(storm, station_id=args.station_id)
    # PRD-08 A.2 (audit #38 etc.): when ``--json`` is set we emit the
    # structured DesignStorm payload on stdout so downstream tools
    # (the agent surface, a notebook) can consume it without parsing
    # the DAT text. ``--out`` still controls whether the DAT file is
    # written to disk.
    if getattr(args, "json", False):
        payload = storm.to_dict()
        payload["station_id"] = args.station_id
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        return 0
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(
            f"wrote {len(storm.intensities_mm_per_hr)}-step design storm "
            f"to {args.out}"
        )
    return 0
