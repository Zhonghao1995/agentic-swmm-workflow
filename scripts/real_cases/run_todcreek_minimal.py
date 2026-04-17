#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import rasterio
import shapefile


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "Todcreek"
DEFAULT_RUN_DIR = REPO_ROOT / "runs" / "real-todcreek-minimal"
LANDUSE_LUT = REPO_ROOT / "skills" / "swmm-params" / "references" / "landuse_class_to_subcatch_params.csv"
SOIL_LUT = REPO_ROOT / "skills" / "swmm-params" / "references" / "soil_texture_to_greenampt.csv"


@dataclass
class LanduseParams:
    imperv_pct: float
    n_imperv: float
    n_perv: float
    dstore_imperv_mm: float
    dstore_perv_mm: float
    zero_imperv_pct: float


@dataclass
class GreenAmptParams:
    suction_mm: float
    ksat_mm_per_hr: float
    imdmax: float


def read_lut(path: Path, key_field: str) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        return {str(row[key_field]).strip(): row for row in rows}


def basin_area_from_dem_ha(dem_path: Path) -> float:
    with rasterio.open(dem_path) as dataset:
        arr = dataset.read(1, masked=True)
        cell_area_m2 = abs(dataset.res[0] * dataset.res[1])
        area_m2 = int((~arr.mask).sum()) * cell_area_m2
    return area_m2 / 10000.0


def outlet_xy_from_geojson(path: Path) -> tuple[float, float]:
    feature_collection = json.loads(path.read_text(encoding="utf-8"))
    x, y = feature_collection["features"][0]["geometry"]["coordinates"]
    return float(x), float(y)


def parse_rain_daily_mm(path: Path) -> list[tuple[date, float]]:
    rows: list[tuple[date, float]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 7:
            continue
        _, year, month, day, _, _, value = parts[:7]
        rows.append((date(int(year), int(month), int(day)), float(value)))
    rows.sort(key=lambda item: item[0])
    return rows


def choose_window(rain: list[tuple[date, float]], pad_days: int = 2) -> tuple[date, date, date]:
    peak_day, _ = max(rain, key=lambda item: item[1])
    return peak_day - timedelta(days=pad_days), peak_day + timedelta(days=pad_days), peak_day


def area_weighted_landuse(landuse_shp: Path, lut_path: Path) -> tuple[LanduseParams, dict[str, float]]:
    lut = read_lut(lut_path, "landuse_class")
    reader = shapefile.Reader(str(landuse_shp))
    fields = [field[0] for field in reader.fields[1:]]
    idx_class = fields.index("CLASS")
    idx_area = fields.index("SHAPE_AREA")

    totals: dict[str, float] = {}
    area_sum = 0.0
    for record in reader.records():
        landuse_class = str(record[idx_class]).strip()
        area = float(record[idx_area])
        totals[landuse_class] = totals.get(landuse_class, 0.0) + area
        area_sum += area

    def weighted_average(column: str, default_key: str = "DEFAULT") -> float:
        total = 0.0
        for landuse_class, area in totals.items():
            row = lut.get(landuse_class) or lut[default_key]
            total += float(row[column]) * (area / area_sum)
        return total

    return (
        LanduseParams(
            imperv_pct=weighted_average("imperv_pct"),
            n_imperv=weighted_average("n_imperv"),
            n_perv=weighted_average("n_perv"),
            dstore_imperv_mm=weighted_average("dstore_imperv_in") * 25.4,
            dstore_perv_mm=weighted_average("dstore_perv_in") * 25.4,
            zero_imperv_pct=weighted_average("zero_imperv_pct"),
        ),
        totals,
    )


def area_weighted_greenampt(soil_shp: Path, lut_path: Path) -> tuple[GreenAmptParams, dict[str, float]]:
    lut = read_lut(lut_path, "texture")
    reader = shapefile.Reader(str(soil_shp))
    fields = [field[0] for field in reader.fields[1:]]
    idx_texture = fields.index("TEXTURE")
    idx_area = fields.index("SHAPE_AREA")

    totals: dict[str, float] = {}
    area_sum = 0.0
    for record in reader.records():
        texture = str(record[idx_texture]).strip()
        area = float(record[idx_area])
        totals[texture] = totals.get(texture, 0.0) + area
        area_sum += area

    def weighted_average(column: str, default_key: str = "-") -> float:
        total = 0.0
        for texture, area in totals.items():
            row = lut.get(texture) or lut[default_key]
            total += float(row[column]) * (area / area_sum)
        return total

    return (
        GreenAmptParams(
            suction_mm=weighted_average("suction_mm"),
            ksat_mm_per_hr=weighted_average("ksat_mm_per_hr"),
            imdmax=weighted_average("imdmax"),
        ),
        totals,
    )


def area_weighted_slope_pct(soil_shp: Path) -> float:
    reader = shapefile.Reader(str(soil_shp))
    fields = [field[0] for field in reader.fields[1:]]
    idx_slope = fields.index("AVG_SLOPE")
    idx_area = fields.index("SHAPE_AREA")

    area_sum = 0.0
    slope_sum = 0.0
    for record in reader.records():
        area = float(record[idx_area])
        slope = float(record[idx_slope])
        area_sum += area
        slope_sum += slope * area
    return slope_sum / area_sum if area_sum else 2.0


def build_inp(
    out_inp: Path,
    start: date,
    end: date,
    rain_series: list[tuple[date, float]],
    basin_area_ha: float,
    slope_pct: float,
    outlet_xy: tuple[float, float],
    landuse: LanduseParams,
    green_ampt: GreenAmptParams,
) -> None:
    area_m2 = basin_area_ha * 10000.0
    width_m = 2.0 * math.sqrt(area_m2)
    ds_imp_m = landuse.dstore_imperv_mm / 1000.0
    ds_perv_m = landuse.dstore_perv_mm / 1000.0

    ts_lines = [";;Name           Date       Time      Value"]
    for rain_day, rainfall_mm in rain_series:
        if start <= rain_day <= end:
            ts_lines.append(f"TS_RAIN          {rain_day.strftime('%m/%d/%Y')} 00:00     {rainfall_mm:.3f}")

    x_out, y_out = outlet_xy
    jx, jy = x_out - 500.0, y_out + 500.0

    inp = f"""[TITLE]
;;Project Title/Notes
Todcreek minimal real-data run from agentic-swmm-workflow

[OPTIONS]
;;Option             Value
FLOW_UNITS           CMS
INFILTRATION         GREEN_AMPT
FLOW_ROUTING         DYNWAVE
START_DATE           {start.strftime('%m/%d/%Y')}
START_TIME           00:00:00
REPORT_START_DATE    {start.strftime('%m/%d/%Y')}
REPORT_START_TIME    00:00:00
END_DATE             {end.strftime('%m/%d/%Y')}
END_TIME             24:00:00
DRY_DAYS             0
REPORT_STEP          01:00:00
WET_STEP             00:05:00
DRY_STEP             01:00:00
ROUTING_STEP         00:00:30
ALLOW_PONDING        NO
INERTIAL_DAMPING     PARTIAL
VARIABLE_STEP        0.75
MIN_SURFAREA         0
NORMAL_FLOW_LIMITED  BOTH
SKIP_STEADY_STATE    NO

[EVAPORATION]
;;Data Source    Parameters
CONSTANT         0.0

[RAINGAGES]
;;Name           Format    Interval SCF      Source
RG1              VOLUME    24:00    1.0      TIMESERIES TS_RAIN

[TIMESERIES]
{chr(10).join(ts_lines)}

[SUBCATCHMENTS]
;;Name           Raingage         Outlet         Area    %Imperv  Width   %Slope  CurbLen SnowPack
S1               RG1              J1             {basin_area_ha:.3f}  {landuse.imperv_pct:.2f}    {width_m:.1f}   {slope_pct:.3f}   0

[SUBAREAS]
;;Subcatchment   N-Imperv N-Perv  S-Imperv S-Perv  %Zero  RouteTo        PctRouted
S1               {landuse.n_imperv:.4f}   {landuse.n_perv:.4f}   {ds_imp_m:.4f}  {ds_perv_m:.4f}  {landuse.zero_imperv_pct:.1f}   OUTLET         100

[INFILTRATION]
;;Subcatchment   Suction  Ksat    IMDmax
S1               {green_ampt.suction_mm:.2f}  {green_ampt.ksat_mm_per_hr:.3f}  {green_ampt.imdmax:.3f}

[JUNCTIONS]
;;Name           Elevation  MaxDepth  InitDepth  SurDepth  Aponded
J1               100        10        0          0         0

[OUTFALLS]
;;Name           Elevation  Type       Stage Data       Gated   Route To
O1               90         FREE

[CONDUITS]
;;Name           From Node  To Node   Length  Roughness  InOffset  OutOffset  InitFlow  MaxFlow
C1               J1         O1        1000    0.013      0         0          0         0

[XSECTIONS]
;;Link           Shape        Geom1  Geom2  Geom3  Geom4  Barrels
C1               CIRCULAR     1.0    0      0      0      1

[REPORT]
;;Reporting Options
INPUT      NO
SUBCATCHMENTS ALL
NODES      ALL
LINKS      ALL

[COORDINATES]
;;Node           X-Coord  Y-Coord
J1               {jx:.3f}      {jy:.3f}
O1               {x_out:.3f}      {y_out:.3f}

[POLYGONS]
;;Subcatchment   X-Coord  Y-Coord
S1               {jx - 500:.3f} {jy - 500:.3f}
S1               {jx + 500:.3f} {jy - 500:.3f}
S1               {jx + 500:.3f} {jy + 500:.3f}
S1               {jx - 500:.3f} {jy + 500:.3f}
S1               {jx - 500:.3f} {jy - 500:.3f}
"""
    out_inp.parent.mkdir(parents=True, exist_ok=True)
    out_inp.write_text(inp, encoding="utf-8")


def run_swmm(inp: Path, run_dir: Path) -> tuple[Path, Path]:
    rpt = run_dir / "model.rpt"
    out = run_dir / "model.out"
    proc = subprocess.run(["swmm5", str(inp), str(rpt), str(out)], capture_output=True, text=True)
    (run_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"swmm5 failed rc={proc.returncode}: {(proc.stderr or proc.stdout).strip()[:400]}")
    return rpt, out


def extract_peak_outfall_flow_cms(rpt: Path, outfall: str = "O1") -> tuple[float | None, str | None]:
    text = rpt.read_text(encoding="utf-8", errors="ignore")
    peak_match = re.search(
        rf"^\s*{re.escape(outfall)}\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s*$",
        text,
        re.M,
    )
    peak = float(peak_match.group(3)) if peak_match else None
    time_match = re.search(
        rf"^\s*{re.escape(outfall)}\s+OUTFALL\s+\S+\s+(\d+\.\d+)\s+\d+\s+(\d\d):(\d\d)",
        text,
        re.M,
    )
    peak_time = f"{time_match.group(2)}:{time_match.group(3)}" if time_match else None
    return peak, peak_time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a minimal real-data Tod Creek SWMM case inside this repo.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--pad-days", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    run_dir = args.run_dir.resolve()

    dem_path = data_dir / "Geolayer" / "n48_w124_1arc_v3_Clip_Projec1.tif"
    landuse_shp = data_dir / "Geolayer" / "landuse.shp"
    soil_shp = data_dir / "Geolayer" / "soil.shp"
    rain_path = data_dir / "Rainfall" / "1984rain.dat"
    outlet_geojson = data_dir / "outlet_candidate.geojson"

    for path in [dem_path, landuse_shp, soil_shp, rain_path, outlet_geojson, LANDUSE_LUT, SOIL_LUT]:
        if not path.exists():
            raise FileNotFoundError(path)

    rain = parse_rain_daily_mm(rain_path)
    start, end, peak_day = choose_window(rain, pad_days=args.pad_days)
    area_ha = basin_area_from_dem_ha(dem_path)
    slope_pct = area_weighted_slope_pct(soil_shp)
    outlet_xy = outlet_xy_from_geojson(outlet_geojson)
    landuse, landuse_totals = area_weighted_landuse(landuse_shp, LANDUSE_LUT)
    green_ampt, soil_totals = area_weighted_greenampt(soil_shp, SOIL_LUT)

    run_dir.mkdir(parents=True, exist_ok=True)
    inp_path = run_dir / "model.inp"
    build_inp(inp_path, start, end, rain, area_ha, slope_pct, outlet_xy, landuse, green_ampt)
    rpt_path, out_path = run_swmm(inp_path, run_dir)
    peak_flow_cms, peak_time = extract_peak_outfall_flow_cms(rpt_path)

    manifest = {
        "run_dir": str(run_dir),
        "data_dir": str(data_dir),
        "sim_start": start.isoformat(),
        "sim_end": end.isoformat(),
        "peak_rain_day": peak_day.isoformat(),
        "basin_area_ha": area_ha,
        "slope_pct_proxy": slope_pct,
        "landuse_area_by_class": landuse_totals,
        "soil_area_by_texture": soil_totals,
        "landuse_params": asdict(landuse),
        "green_ampt_params": asdict(green_ampt),
        "qoi": {"peak_flow_cms_at_O1": peak_flow_cms, "time_of_peak_hhmm": peak_time},
        "files": {"inp": str(inp_path), "rpt": str(rpt_path), "out": str(out_path)},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
