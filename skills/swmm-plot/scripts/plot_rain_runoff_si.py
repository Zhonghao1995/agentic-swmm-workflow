#!/usr/bin/env python3
"""Plot rainfall (inverted) vs runoff/outfall hydrograph with publication formatting.

Requirements from Zhonghao:
- SI units
- Inverted rainfall axis
- Hydrograph shape preserved (assumes output time step is sufficiently fine, e.g., 5-min)
- Ticks inward
- Font: Arial 12
- No title

Inputs:
- INP path (to read TIMESERIES for rainfall)
- OUT path (to read node total inflow from SWMM output)

Output:
- PNG (and optionally PDF later)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _warn_if_cold_start() -> None:
    """Emit a one-line stderr hint if matplotlib's font cache is missing.

    The MCP server preheats matplotlib + swmmtoolbox at boot (see
    issue #109) so the user normally never sees this. If the preheat
    failed (no Python, no deps) or hasn't finished yet, this warning
    tells the user why the first plot call is taking a while instead
    of leaving them staring at a silent ``you>`` prompt.
    """
    try:
        import matplotlib  # cheap; just reads metadata
        cachedir = Path(matplotlib.get_cachedir())
    except Exception:
        return
    # matplotlib names the cache ``fontlist-vNNN.json``; if any file
    # matching that glob exists we treat the cache as warm.
    if not any(cachedir.glob('fontlist-v*.json')):
        sys.stderr.write(
            '[swmm-plot] First plot warms up matplotlib + swmmtoolbox '
            '(~60-90s). Subsequent plots are fast.\n'
        )
        sys.stderr.flush()


_warn_if_cold_start()

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from swmmtoolbox import extract


def parse_timeseries_file(path: Path) -> tuple[list[datetime], list[float]]:
    times: list[datetime] = []
    vals: list[float] = []
    for raw in path.read_text(errors='ignore').splitlines():
        s = raw.strip()
        if not s or s.startswith(';'):
            continue
        parts = s.split()
        if len(parts) < 3:
            continue
        dt = datetime.strptime(parts[0] + ' ' + parts[1], '%m/%d/%Y %H:%M')
        times.append(dt)
        vals.append(float(parts[2]))
    if not times:
        raise SystemExit(f'No timeseries values found in {path}')
    return times, vals


def parse_timeseries_from_inp(inp_path: Path, ts_name: str) -> tuple[list[datetime], list[float]]:
    """Return (times, values) from [TIMESERIES]. Values are whatever units the INP encodes."""
    times: list[datetime] = []
    vals: list[float] = []
    reading = False
    for line in inp_path.read_text(errors='ignore').splitlines():
        s = line.strip()
        if s.upper() == '[TIMESERIES]':
            reading = True
            continue
        if reading:
            if s.startswith('[') and s.endswith(']'):
                break
            if (not s) or s.startswith(';;'):
                continue
            parts = s.split()
            if parts[0] != ts_name:
                continue
            if len(parts) >= 3 and parts[1].upper() == 'FILE':
                return parse_timeseries_file(inp_path.parent / parts[2].strip('"'))
            dt = datetime.strptime(parts[1] + ' ' + parts[2], '%m/%d/%Y %H:%M')
            times.append(dt)
            vals.append(float(parts[3]))
    if not times:
        raise SystemExit(f'No TIMESERIES values found for {ts_name} in {inp_path}')
    return times, vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--inp', required=True, type=Path)
    ap.add_argument('--out', dest='out_file', required=True, type=Path)
    ap.add_argument('--rain-ts', default='TS_RAIN')
    ap.add_argument('--rain-kind', choices=['intensity_mm_per_hr', 'depth_mm_per_dt', 'cumulative_depth_mm'], default='depth_mm_per_dt',
                    help='How to interpret TIMESERIES values for plotting. Use depth_mm_per_dt for (mm/Δt) hyetograph (inverted).')
    ap.add_argument('--dt-min', type=float, default=5.0, help='Used only when rain-kind=depth_mm_per_dt or to convert intensity to depth.')
    ap.add_argument('--node', default='O1')
    ap.add_argument('--node-attr', default='Total_inflow')
    ap.add_argument('--out-png', required=True, type=Path)
    ap.add_argument('--dpi', type=int, default=300)
    ap.add_argument('--focus-day', type=str, default=None,
                    help='If set (YYYY-MM-DD), base day for x-axis formatting.')
    ap.add_argument('--window-start', type=str, default=None,
                    help='Optional HH:MM. If provided with --focus-day, x-axis will be limited to this time window within the day.')
    ap.add_argument('--window-end', type=str, default=None,
                    help='Optional HH:MM. If provided with --focus-day, x-axis will be limited to this time window within the day.')
    ap.add_argument('--pad-hours', type=float, default=2.0,
                    help='When focus-day is not set, auto-window uses nonzero rainfall extent ± pad-hours.')
    ap.add_argument('--rain-ymax-factor', type=float, default=3.0,
                    help='Multiplier applied to the plotted rainfall maximum so inverted bars stay in the upper part of the panel.')
    ap.add_argument('--flow-ymax-factor', type=float, default=2.5,
                    help='Multiplier applied to the plotted flow maximum so the hydrograph does not visually collide with rainfall bars.')
    args = ap.parse_args()

    # Matplotlib styling: Arial 12, ticks inward
    plt.rcParams.update({
        'font.family': 'Arial',
        'font.size': 12,
        'axes.titlesize': 12,
        'axes.labelsize': 12,
        'xtick.labelsize': 12,
        'ytick.labelsize': 12,
        'legend.fontsize': 12,
        'xtick.direction': 'in',
        'ytick.direction': 'in',
    })

    rain_t, rain_v = parse_timeseries_from_inp(args.inp, args.rain_ts)
    rain_v = np.asarray(rain_v, dtype=float)

    # For hyetograph we usually show intensity (mm/hr) inverted.
    if args.rain_kind == 'intensity_mm_per_hr':
        rain_plot = rain_v
        rain_ylabel = 'Rainfall intensity (mm/h)'
    elif args.rain_kind == 'cumulative_depth_mm':
        rain_plot = np.diff(rain_v, prepend=rain_v[0])
        rain_plot = np.where(rain_plot < 0, 0.0, rain_plot)
        rain_ylabel = f'Rainfall depth (mm/{int(args.dt_min)} min)'
    else:
        # values are assumed intensity mm/hr by our generator; convert to mm per dt for bar area readability
        rain_plot = rain_v * (args.dt_min / 60.0)
        rain_ylabel = f'Rainfall depth (mm/{int(args.dt_min)} min)'

    # Flow series (SI): CMS = m^3/s
    key = f'node,{args.node},{args.node_attr}'
    flow_df = extract(str(args.out_file), key)
    flow_t = flow_df.index.to_pydatetime()
    flow_v = flow_df.iloc[:, 0].to_numpy(dtype=float)

    # Figure
    fig, ax_rain = plt.subplots(figsize=(9, 3.8), dpi=args.dpi)

    # Rain bars
    bar_width_days = (args.dt_min / 60.0) / 24.0
    ax_rain.bar(
        rain_t,
        rain_plot,
        width=bar_width_days,
        color='#4C78A8',
        alpha=0.45,
        edgecolor='none',
        label='Rain',
        zorder=1,
    )
    ax_rain.set_ylabel(rain_ylabel)
    ax_rain.set_xlabel('Time')

    # invert rain axis (hyetograph convention)
    ax_rain.invert_yaxis()

    # Flow line (draw above rain)
    ax_flow = ax_rain.twinx()
    ax_flow.plot(flow_t, flow_v, color='#F58518', linewidth=1.8, label='Flow', zorder=3)
    ax_flow.set_ylabel('Flow (m³/s)')

    rain_max = float(np.nanmax(rain_plot)) if rain_plot.size else 0.0
    if rain_max > 0:
        ax_rain.set_ylim(rain_max * max(args.rain_ymax_factor, 1.0), 0.0)
    flow_max = float(np.nanmax(flow_v)) if flow_v.size else 0.0
    if flow_max > 0:
        ax_flow.set_ylim(0.0, flow_max * max(args.flow_ymax_factor, 1.0))

    # Focus x-axis: one day or auto-window
    import matplotlib.dates as mdates
    if args.focus_day:
        d0 = datetime.strptime(args.focus_day, '%Y-%m-%d')
        if args.window_start and args.window_end:
            ws = datetime.strptime(args.window_start, '%H:%M').time()
            we = datetime.strptime(args.window_end, '%H:%M').time()
            t0 = d0.replace(hour=ws.hour, minute=ws.minute)
            t1 = d0.replace(hour=we.hour, minute=we.minute)
            ax_rain.set_xlim(t0, t1)
            ax_rain.xaxis.set_major_locator(mdates.HourLocator(interval=1))
            ax_rain.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        else:
            ax_rain.set_xlim(d0, d0 + timedelta(hours=24))
            ax_rain.xaxis.set_major_locator(mdates.HourLocator(interval=3))
            ax_rain.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    else:
        nz = np.where(np.asarray(rain_plot) > 0)[0]
        if nz.size:
            tmin = rain_t[int(nz.min())]
            tmax = rain_t[int(nz.max())]
            pad = timedelta(hours=float(args.pad_hours))
            ax_rain.set_xlim(tmin - pad, tmax + pad)
            ax_rain.xaxis.set_major_locator(mdates.HourLocator(interval=2))
            ax_rain.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d\n%H:%M'))

    # Ticks inward on both axes
    ax_rain.tick_params(direction='in', which='both', top=True, right=False)
    ax_flow.tick_params(direction='in', which='both', top=True, right=True)

    # No title (per spec)

    # Legend: combine
    h1, l1 = ax_rain.get_legend_handles_labels()
    h2, l2 = ax_flow.get_legend_handles_labels()
    ax_flow.legend(h1 + h2, l1 + l2, loc='upper left', framealpha=0.9)

    fig.tight_layout()
    args.out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out_png, dpi=args.dpi)


if __name__ == '__main__':
    main()
