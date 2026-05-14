#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import sqlite3
import struct
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = (
    REPO_ROOT
    / "runs/raw-case-candidates/tuflow-swmm/TUFLOW_SWMM_Tutorial_Models_QGIS_GPKG/"
    / "TUFLOW_SWMM_Module_03/Complete_Model/TUFLOW"
)
SOURCE_GPKG = CASE_ROOT / "model/swmm/sw03_001.gpkg"
RAINFALL_CSV = CASE_ROOT / "bc_dbase/rainfall_stations.csv"
RUN_DIR = REPO_ROOT / "runs/benchmarks/tuflow-swmm-module03-raw-path"
GAGE_SERIES = {"RF_G1": "RF_FC04", "RF_G2": "RF_FC07"}


def run_cmd(args: list[str]) -> None:
    subprocess.run(args, cwd=REPO_ROOT, check=True)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_table(con: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cur = con.execute(f'SELECT * FROM "{table}"')
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def gpkg_wkb(blob: bytes) -> bytes:
    if blob[:2] != b"GP":
        raise ValueError("Not a GeoPackage geometry blob")
    flags = blob[3]
    envelope_code = (flags >> 1) & 0b111
    envelope_size = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(envelope_code)
    if envelope_size is None:
        raise ValueError(f"Unsupported GeoPackage envelope code: {envelope_code}")
    return blob[8 + envelope_size :]


def parse_wkb(blob: bytes) -> dict[str, Any]:
    data = gpkg_wkb(blob)
    endian = "<" if data[0] == 1 else ">"
    geom_type = struct.unpack(endian + "I", data[1:5])[0]
    offset = 5
    if geom_type == 1:
        x, y = struct.unpack(endian + "dd", data[offset : offset + 16])
        return {"type": "Point", "coordinates": [x, y]}
    if geom_type == 2:
        n = struct.unpack(endian + "I", data[offset : offset + 4])[0]
        offset += 4
        coords = []
        for _ in range(n):
            x, y = struct.unpack(endian + "dd", data[offset : offset + 16])
            offset += 16
            coords.append([x, y])
        return {"type": "LineString", "coordinates": coords}
    if geom_type == 3:
        ring_count = struct.unpack(endian + "I", data[offset : offset + 4])[0]
        offset += 4
        rings = []
        for _ in range(ring_count):
            n = struct.unpack(endian + "I", data[offset : offset + 4])[0]
            offset += 4
            coords = []
            for _ in range(n):
                x, y = struct.unpack(endian + "dd", data[offset : offset + 16])
                offset += 16
                coords.append([x, y])
            rings.append(coords)
        return {"type": "Polygon", "coordinates": rings}
    raise ValueError(f"Unsupported WKB geometry type: {geom_type}")


def feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


def clean_max_flow(value: Any) -> float | None:
    parsed = as_float(value, 0.0)
    return parsed if parsed > 0 else None


def geometry_points(geom: dict[str, Any]) -> list[list[float]]:
    if geom["type"] == "LineString":
        return geom["coordinates"]
    if geom["type"] == "Point":
        return [geom["coordinates"]]
    return []


def line_length(coords: list[list[float]]) -> float:
    total = 0.0
    for a, b in zip(coords, coords[1:]):
        total += math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
    return total


def resolve_subcatchment_outlets(rows: list[dict[str, Any]], node_ids: set[str]) -> dict[str, str]:
    raw = {str(r["Name"]): str(r["Outlet"]) for r in rows}

    def resolve(name: str, seen: set[str] | None = None) -> str:
        seen = seen or set()
        outlet = raw.get(name, "")
        if outlet in node_ids:
            return outlet
        if outlet in raw and outlet not in seen:
            return resolve(outlet, seen | {name})
        return outlet

    return {str(r["Name"]): resolve(str(r["Name"])) for r in rows}


def decimal_hour_to_hhmm(value: str) -> str:
    minutes = int(round(float(value) * 60.0))
    hh = minutes // 60
    mm = minutes % 60
    return f"{hh:02d}:{mm:02d}"


def write_timeseries(path: Path) -> None:
    base_date = datetime(2025, 6, 1)
    with RAINFALL_CSV.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    lines = []
    for row in rows:
        hhmm = decimal_hour_to_hhmm(row["time"])
        dt = base_date + timedelta(hours=int(hhmm[:2]), minutes=int(hhmm[3:]))
        for series_name in GAGE_SERIES.values():
            lines.append(f"{series_name} {dt:%m/%d/%Y} {dt:%H:%M} {row[series_name]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def extract_raw_inputs() -> dict[str, Path]:
    con = sqlite3.connect(SOURCE_GPKG)
    raw_dir = RUN_DIR / "00_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    junction_rows = read_table(con, "Nodes--Junctions")
    outfall_rows = read_table(con, "Nodes--Outfalls")
    conduit_rows = read_table(con, "Links--Conduits")
    subcatchment_rows_all = read_table(con, "Hydrology--Subcatchments")
    subcatchment_rows = [r for r in subcatchment_rows_all if r["Rain Gage"] in GAGE_SERIES]

    node_ids = {str(r["Name"]) for r in junction_rows} | {str(r["Name"]) for r in outfall_rows}
    resolved_outlets = resolve_subcatchment_outlets(subcatchment_rows_all, node_ids)

    junction_features = []
    for row in junction_rows:
        props = {
            "node_id": row["Name"],
            "inv_el": row["Elev"],
            "max_d": row["Ymax"],
            "init_d": row["Y0"],
            "sur_d": row["Ysur"],
            "aponded": row["Apond"],
        }
        junction_features.append({"type": "Feature", "properties": props, "geometry": parse_wkb(row["geom"])})

    outfall_features = []
    for row in outfall_rows:
        props = {
            "node_id": row["Name"],
            "inv_el": row["Elev"],
            "out_type": row["Type"],
            "stage_data": row["Stage"],
            "gated": row["Gated"],
            "route_to": row["RouteTo"],
        }
        outfall_features.append({"type": "Feature", "properties": props, "geometry": parse_wkb(row["geom"])})

    conduit_features = []
    for row in conduit_rows:
        geom = parse_wkb(row["geom"])
        coords = geometry_points(geom)
        props = {
            "link_id": row["Name"],
            "from_id": row["From Node"],
            "to_id": row["To Node"],
            "len_m": row["Length"] or max(line_length(coords), 1.0),
            "n_val": row["Roughness"],
            "in_offset": row["InOffset"],
            "out_offset": row["OutOffset"],
            "init_flow": row["InitFlow"],
            "max_flow": clean_max_flow(row["MaxFlow"]),
            "shape": row["xsec_XsecType"],
            "geom1": row["xsec_Geom1"],
            "geom2": row["xsec_Geom2"],
            "geom3": row["xsec_Geom3"],
            "geom4": row["xsec_Geom4"],
            "barrels": row["xsec_Barrels"],
        }
        conduit_features.append({"type": "Feature", "properties": props, "geometry": geom})

    subcatchment_features = []
    params = {"ok": True, "sections": {"subcatchments": [], "subareas": [], "infiltration": []}}
    for row in subcatchment_rows:
        sid = str(row["Name"])
        props = {
            "subcatchment_id": sid,
            "outlet_hint": resolved_outlets[sid],
            "rain_gage": row["Rain Gage"],
            "width_m": row["Width"],
            "slope_pct": row["PctSlope"],
            "curb_length_m": row["CurbLen"],
        }
        subcatchment_features.append({"type": "Feature", "properties": props, "geometry": parse_wkb(row["geom"])})
        params["sections"]["subcatchments"].append({"id": sid, "pct_imperv": row["PctImperv"]})
        params["sections"]["subareas"].append(
            {
                "id": sid,
                "n_imperv": row["Subareas_Nimp"],
                "n_perv": row["Subareas_Nperv"],
                "dstore_imperv_in": row["Subareas_Simp"],
                "dstore_perv_in": row["Subareas_Sperv"],
                "zero_imperv_pct": row["Subareas_PctZero"],
                "route_to": row["Subareas_RouteTo"],
                "pct_routed": row["Subareas_PctRouted"] if row["Subareas_PctRouted"] is not None else 100.0,
            }
        )
        params["sections"]["infiltration"].append(
            {
                "id": sid,
                "suction_mm": row["Infiltration_p1"],
                "ksat_mm_per_hr": row["Infiltration_p2"],
                "imdmax": row["Infiltration_p3"],
            }
        )

    paths = {
        "junctions": raw_dir / "junctions.geojson",
        "outfalls": raw_dir / "outfalls.geojson",
        "conduits": raw_dir / "conduits.geojson",
        "subcatchments": raw_dir / "subcatchments.geojson",
        "mapping": raw_dir / "network_import_mapping.json",
        "params": raw_dir / "params_from_gpkg.json",
        "timeseries": raw_dir / "timeseries.txt",
        "raingage": raw_dir / "raingage.json",
        "config": raw_dir / "builder_config.json",
    }
    write_json(paths["junctions"], feature_collection(junction_features))
    write_json(paths["outfalls"], feature_collection(outfall_features))
    write_json(paths["conduits"], feature_collection(conduit_features))
    write_json(paths["subcatchments"], feature_collection(subcatchment_features))
    write_json(paths["params"], params)
    write_timeseries(paths["timeseries"])
    write_json(
        paths["raingage"],
        {
            "ok": True,
            "gages": [
                {
                    "id": gage_id,
                    "rain_format": "VOLUME",
                    "interval_min": 6,
                    "scf": 1.0,
                    "source": {"kind": "TIMESERIES", "series_name": series_name},
                }
                for gage_id, series_name in GAGE_SERIES.items()
            ],
        },
    )
    write_json(
        paths["config"],
        {
            "title": "TUFLOW SWMM Module 03 raw-path adapter benchmark",
            "options": {
                "FLOW_UNITS": "CMS",
                "INFILTRATION": "GREEN_AMPT",
                "FLOW_ROUTING": "DYNWAVE",
                "START_DATE": "06/01/2025",
                "START_TIME": "00:00:00",
                "REPORT_START_DATE": "06/01/2025",
                "REPORT_START_TIME": "00:00:00",
                "END_DATE": "06/01/2025",
                "END_TIME": "03:00:00",
                "REPORT_STEP": "00:05:00",
                "WET_STEP": "00:01:00",
                "DRY_STEP": "01:00:00",
                "ROUTING_STEP": "00:00:30",
            },
            "report": {"INPUT": "NO", "CONTROLS": "NO", "SUBCATCHMENTS": "ALL", "NODES": "ALL", "LINKS": "ALL"},
        },
    )
    write_json(
        paths["mapping"],
        {
            "meta": {"name": "tuflow-swmm-module03", "source": str(SOURCE_GPKG)},
            "junctions": {
                "format": "geojson",
                "fields": {
                    "id": "node_id",
                    "invert_elev": "inv_el",
                    "max_depth": "max_d",
                    "init_depth": "init_d",
                    "sur_depth": "sur_d",
                    "aponded": "aponded",
                },
            },
            "outfalls": {
                "format": "geojson",
                "fields": {
                    "id": "node_id",
                    "invert_elev": "inv_el",
                    "type": "out_type",
                    "stage_data": "stage_data",
                    "gated": "gated",
                    "route_to": "route_to",
                },
                "defaults": {"type": "FREE", "gated": False},
            },
            "conduits": {
                "format": "geojson",
                "fields": {
                    "id": "link_id",
                    "from_node": "from_id",
                    "to_node": "to_id",
                    "length": "len_m",
                    "roughness": "n_val",
                    "diameter": "geom1",
                    "in_offset": "in_offset",
                    "out_offset": "out_offset",
                    "init_flow": "init_flow",
                    "max_flow": "max_flow",
                    "shape": "shape",
                    "geom2": "geom2",
                    "geom3": "geom3",
                    "geom4": "geom4",
                    "barrels": "barrels",
                },
                "defaults": {
                    "shape": "CIRCULAR",
                    "roughness": 0.013,
                    "geom1": 0.5,
                    "geom2": 0.0,
                    "geom3": 0.0,
                    "geom4": 0.0,
                    "barrels": 1,
                    "in_offset": 0.0,
                    "out_offset": 0.0,
                    "init_flow": 0.0,
                    "max_flow": None,
                },
            },
        },
    )
    return paths


def main() -> None:
    if not SOURCE_GPKG.exists():
        raise FileNotFoundError(
            f"{SOURCE_GPKG}\n"
            "Download and unzip the TUFLOW SWMM Tutorial QGIS GPKG package under "
            "runs/raw-case-candidates/tuflow-swmm before running this benchmark."
        )
    paths = extract_raw_inputs()
    run_cmd(
        [
            "python3",
            "skills/swmm-network/scripts/network_import.py",
            "--conduits",
            str(paths["conduits"]),
            "--junctions",
            str(paths["junctions"]),
            "--outfalls",
            str(paths["outfalls"]),
            "--mapping",
            str(paths["mapping"]),
            "--out",
            str(RUN_DIR / "04_network/network.json"),
        ]
    )
    run_cmd(
        [
            "python3",
            "skills/swmm-network/scripts/network_qa.py",
            str(RUN_DIR / "04_network/network.json"),
            "--report-json",
            str(RUN_DIR / "04_network/network_qa.json"),
        ]
    )
    run_cmd(
        [
            "python3",
            "skills/swmm-gis/scripts/preprocess_subcatchments.py",
            "--subcatchments-geojson",
            str(paths["subcatchments"]),
            "--network-json",
            str(RUN_DIR / "04_network/network.json"),
            "--default-rain-gage",
            "RF_G1",
            "--out-csv",
            str(RUN_DIR / "01_gis/subcatchments.csv"),
            "--out-json",
            str(RUN_DIR / "01_gis/subcatchments.json"),
        ]
    )
    run_cmd(
        [
            "python3",
            "skills/swmm-builder/scripts/build_swmm_inp.py",
            "--subcatchments-csv",
            str(RUN_DIR / "01_gis/subcatchments.csv"),
            "--params-json",
            str(paths["params"]),
            "--network-json",
            str(RUN_DIR / "04_network/network.json"),
            "--timeseries-text",
            str(paths["timeseries"]),
            "--raingage-json",
            str(paths["raingage"]),
            "--config-json",
            str(paths["config"]),
            "--out-inp",
            str(RUN_DIR / "05_builder/model.inp"),
            "--out-manifest",
            str(RUN_DIR / "05_builder/manifest.json"),
        ]
    )
    run_cmd(
        [
            "python3",
            "skills/swmm-runner/scripts/swmm_runner.py",
            "run",
            "--inp",
            str(RUN_DIR / "05_builder/model.inp"),
            "--run-dir",
            str(RUN_DIR / "06_runner"),
            "--node",
            "Node20",
        ]
    )
    continuity = subprocess.check_output(
        ["python3", "skills/swmm-runner/scripts/swmm_runner.py", "continuity", "--rpt", str(RUN_DIR / "06_runner/model.rpt")],
        cwd=REPO_ROOT,
        text=True,
    )
    peak = subprocess.check_output(
        ["python3", "skills/swmm-runner/scripts/swmm_runner.py", "peak", "--rpt", str(RUN_DIR / "06_runner/model.rpt"), "--node", "Node20"],
        cwd=REPO_ROOT,
        text=True,
    )
    write_json(RUN_DIR / "07_qa/continuity.json", json.loads(continuity))
    write_json(RUN_DIR / "07_qa/peak_Node20.json", json.loads(peak))
    summary = {
        "run_dir": str(RUN_DIR.relative_to(REPO_ROOT)),
        "status": "pass",
        "source_gpkg": str(SOURCE_GPKG.relative_to(REPO_ROOT)),
        "scope": "Full Module 03 SWMM hydrology layer with RF_G1 and RF_G2 raingages.",
        "artifacts": {
            "built_inp": str((RUN_DIR / "05_builder/model.inp").relative_to(REPO_ROOT)),
            "runner_manifest": str((RUN_DIR / "06_runner/manifest.json").relative_to(REPO_ROOT)),
            "network_qa": str((RUN_DIR / "04_network/network_qa.json").relative_to(REPO_ROOT)),
            "continuity": str((RUN_DIR / "07_qa/continuity.json").relative_to(REPO_ROOT)),
            "peak": str((RUN_DIR / "07_qa/peak_Node20.json").relative_to(REPO_ROOT)),
        },
        "metrics": {"continuity": json.loads(continuity), "peak_Node20": json.loads(peak)},
    }
    write_json(RUN_DIR / "manifest.json", summary)
    run_cmd(
        [
            "python3",
            "skills/swmm-experiment-audit/scripts/audit_run.py",
            "--run-dir",
            str(RUN_DIR),
            "--workflow-mode",
            "TUFLOW SWMM Module 03 full multi-raingage raw GeoPackage adapter benchmark",
        ]
    )
    run_cmd(
        [
            "python3",
            "skills/swmm-modeling-memory/scripts/summarize_memory.py",
            "--runs-dir",
            "runs",
            "--out-dir",
            "memory/modeling-memory",
        ]
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
