"""SWMM INP text knowledge: rainfall wiring and FILE-directive sidecars.

A ~115-line state machine over the ``[RAINGAGES]`` and ``[TIMESERIES]``
sections that answers "which rainfall series should a plot use, and is
it intensity or cumulative depth". It lived inside the ``plot`` CLI
verb and was imported across module boundaries by the map verb, the
plot tool handler and the tool registry; the 2026-07 architecture pass
moved it next to the other INP-format knowledge (preflight).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any


def infer_rain_timeseries(inp: Path) -> tuple[str, str | None]:
    options = rainfall_timeseries_options(inp)
    for option in options:
        if option.get("used_by_raingage"):
            return str(option["name"]), option.get("rain_kind")
    if options:
        return str(options[0]["name"]), options[0].get("rain_kind")
    raise FileNotFoundError(f"Unable to infer rainfall TIMESERIES from INP: {inp}")


def rainfall_timeseries_options(inp: Path) -> list[dict[str, Any]]:
    text = inp.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    raingage_series: dict[str, dict[str, str | None]] = {}
    # SWMManywhere-style INPs reference rainfall via ``[RAINGAGES] FILE
    # storm.dat`` instead of an inline ``[TIMESERIES]`` block. We capture
    # those gages here so the plot path can fall through to reading the
    # external .dat directly (see plot_rain_runoff_si.py for the parser).
    raingage_file_entries: dict[str, dict[str, str | None]] = {}
    in_raingages = False
    for raw in lines:
        stripped = raw.strip()
        upper = stripped.upper()
        if upper == "[RAINGAGES]":
            in_raingages = True
            continue
        if in_raingages and stripped.startswith("[") and stripped.endswith("]"):
            break
        if not in_raingages or not stripped or stripped.startswith(";"):
            continue
        parts = stripped.split()
        upper_parts = [p.upper() for p in parts]
        if "TIMESERIES" in upper_parts:
            idx = upper_parts.index("TIMESERIES")
            if idx + 1 < len(parts):
                name = parts[idx + 1].strip('"')
                gage = parts[0].strip('"')
                raingage_series[name] = {
                    "gage": gage,
                    "rain_kind": "cumulative_depth_mm" if "CUMULATIVE" in upper_parts else None,
                }
        elif "FILE" in upper_parts:
            # ``rg1 INTENSITY 0:05 1.0 FILE "storm.dat"`` — the gage
            # itself acts as the rainfall identifier; no [TIMESERIES]
            # block exists. ``rain_kind`` defaults to intensity_mm_per_hr
            # because SWMM5's RAINGAGES FILE values are intensity (mm/h)
            # when ``Format == INTENSITY``; cumulative if CUMULATIVE.
            gage = parts[0].strip('"')
            if "INTENSITY" in upper_parts:
                rain_kind = "intensity_mm_per_hr"
            elif "CUMULATIVE" in upper_parts:
                rain_kind = "cumulative_depth_mm"
            else:
                rain_kind = None
            raingage_file_entries[gage] = {
                "gage": gage,
                "rain_kind": rain_kind,
            }

    options: list[dict[str, Any]] = []
    in_timeseries = False
    for raw in lines:
        stripped = raw.strip()
        upper = stripped.upper()
        if upper == "[TIMESERIES]":
            in_timeseries = True
            continue
        if in_timeseries and stripped.startswith("[") and stripped.endswith("]"):
            break
        if not in_timeseries or not stripped or stripped.startswith(";"):
            continue
        parts = stripped.split()
        if not parts:
            continue
        name = parts[0].strip('"')
        if any(option["name"] == name for option in options):
            continue
        gage_info = raingage_series.get(name, {})
        options.append(
            {
                "name": name,
                "source": "file" if len(parts) >= 3 and parts[1].upper() == "FILE" else "inline",
                "used_by_raingage": name in raingage_series,
                "gage": gage_info.get("gage"),
                "rain_kind": gage_info.get("rain_kind"),
            }
        )
    for name, gage_info in raingage_series.items():
        if not any(option["name"] == name for option in options):
            options.append(
                {
                    "name": name,
                    "source": "raingage",
                    "used_by_raingage": True,
                    "gage": gage_info.get("gage"),
                    "rain_kind": gage_info.get("rain_kind"),
                }
            )
    # RAINGAGES FILE entries surface under the gage name itself. The
    # plot script's parse_timeseries_from_inp recognises this case and
    # reads the referenced .dat file directly when no [TIMESERIES]
    # block defines ``name``.
    for gage_name, gage_info in raingage_file_entries.items():
        if not any(option["name"] == gage_name for option in options):
            options.append(
                {
                    "name": gage_name,
                    "source": "raingage_file",
                    "used_by_raingage": True,
                    "gage": gage_info.get("gage"),
                    "rain_kind": gage_info.get("rain_kind"),
                }
            )
    rainfall_options = [option for option in options if option.get("used_by_raingage")]
    return rainfall_options or options


def copy_inp_sidecar_files(inp: Path, inputs_dir: Path) -> list[Path]:
    """Copy every ``FILE``-referenced sidecar next to the run's INP copy.

    Walks the INP text for ``FILE <name>`` directives, resolves each
    relative to the INP's own directory, and copies it into
    ``inputs_dir``. A section-header token where a filename was expected
    is reported as an INP-parser error with its line number (sections in
    the wrong order), and a missing referenced file raises with the
    resolved path. Returns the copied targets in encounter order.
    """
    copied: list[Path] = []
    text = inp.read_text(encoding="utf-8", errors="ignore")
    for match in re.finditer(r"\bFILE\s+\"?([^\"\s;]+)\"?", text, flags=re.IGNORECASE):
        raw = match.group(1)
        # PRD-08 A.3 (audit #4): the previous code treated a section
        # header token like ``[OPTIONS]`` as a filename and surfaced
        # "FILE not found: /…/[OPTIONS]" — confusing for users whose
        # INP merely has sections in the wrong order. Detect a
        # section-header-shaped token and raise an INP-parser error
        # instead so the message points at the real problem.
        if raw.startswith("[") and raw.endswith("]"):
            # Find the offending line number for the error so the user
            # can locate it in the editor.
            line_no = 0
            for i, line in enumerate(text.splitlines(), start=1):
                if raw in line and "FILE" in line.upper():
                    line_no = i
                    break
            raise FileNotFoundError(
                f"INP parser error at line {line_no or '?'}: encountered "
                f"section header {raw} where a filename was expected. "
                "The INP file likely has sections in the wrong order; see "
                "the SWMM 5 manual for the canonical section order."
            )
        source = Path(raw)
        if not source.is_absolute():
            source = inp.parent / source
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"INP references an external FILE that was not found: {source}")
        target = inputs_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        copied.append(target)
    return copied


__all__ = [
    "copy_inp_sidecar_files",
    "infer_rain_timeseries",
    "rainfall_timeseries_options",
]
