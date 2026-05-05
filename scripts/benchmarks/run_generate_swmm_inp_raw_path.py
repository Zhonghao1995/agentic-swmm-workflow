#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_REPO_URL = "https://github.com/Jannik-Schilling/generate_swmm_inp.git"
SOURCE_REPO_COMMIT = "cd881da2df14898a00e3794b010a26c9b8cff8e8"
SOURCE_REPO_DIR = REPO_ROOT / "runs/raw-case-candidates/generate_swmm_inp_repo"
SOURCE_INP = SOURCE_REPO_DIR / "test_data/swmm_data/Test_5_2.inp"
RUN_DIR = REPO_ROOT / "runs/benchmarks/generate-swmm-inp-raw-path"


def run_cmd(args: list[str]) -> None:
    subprocess.run(args, cwd=REPO_ROOT, check=True)


def ensure_source_inp() -> None:
    """Fetch the public upstream fixture when the local ignored copy is absent."""
    if SOURCE_INP.exists():
        return
    SOURCE_REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    if not (SOURCE_REPO_DIR / ".git").exists():
        subprocess.run(["git", "init", str(SOURCE_REPO_DIR)], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "remote", "add", "origin", SOURCE_REPO_URL], cwd=SOURCE_REPO_DIR, check=True)
    subprocess.run(["git", "fetch", "--depth", "1", "origin", SOURCE_REPO_COMMIT], cwd=SOURCE_REPO_DIR, check=True)
    subprocess.run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=SOURCE_REPO_DIR, check=True)
    if not SOURCE_INP.exists():
        raise FileNotFoundError(f"Missing source input after fetch: {SOURCE_INP}")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def section_rows(path: Path) -> dict[str, list[list[str]]]:
    sections: dict[str, list[list[str]]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current.upper(), [])
            continue
        if current is None or line.startswith(";"):
            continue
        sections.setdefault(current.upper(), []).append(line.split())
    return sections


def fnum(value: str | None, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def parse_interval_min(token: str) -> int:
    hh, mm = token.split(":", 1)
    return int(hh) * 60 + int(mm)


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def safe_swmm_shape(shape: str, geom1: float) -> tuple[str, float]:
    token = shape.upper()
    if token in {"IRREGULAR", "STREET"}:
        return "CIRCULAR", geom1 if geom1 > 0 else 0.5
    return token, geom1 if geom1 > 0 else 0.5


def normalize_step_value(value: str) -> str:
    if ":" in value:
        return value
    seconds = max(1, int(round(float(value))))
    return f"00:00:{seconds:02d}"


def feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def point_feature(node_id: str, coords: dict[str, tuple[float, float]], props: dict[str, Any]) -> dict[str, Any]:
    x, y = coords[node_id]
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [x, y]},
    }


def line_feature(
    link_id: str,
    from_node: str,
    to_node: str,
    coords: dict[str, tuple[float, float]],
    vertices: dict[str, list[tuple[float, float]]],
    props: dict[str, Any],
) -> dict[str, Any]:
    line = [coords[from_node], *vertices.get(link_id, []), coords[to_node]]
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "LineString", "coordinates": [[x, y] for x, y in line]},
    }


def extract_raw_inputs(inp: Path, raw_dir: Path) -> dict[str, Path]:
    rows = section_rows(inp)

    coords = {r[0]: (fnum(r[1]), fnum(r[2])) for r in rows.get("COORDINATES", []) if len(r) >= 3}
    vertices: dict[str, list[tuple[float, float]]] = {}
    for r in rows.get("VERTICES", []):
        if len(r) >= 3:
            vertices.setdefault(r[0], []).append((fnum(r[1]), fnum(r[2])))

    xsections = {
        r[0]: {
            "shape": r[1],
            "geom1": fnum(r[2], 0.5),
            "geom2": fnum(r[3]) if len(r) > 3 else 0.0,
            "geom3": fnum(r[4]) if len(r) > 4 else 0.0,
            "geom4": fnum(r[5]) if len(r) > 5 else 0.0,
            "barrels": int(float(r[6])) if len(r) > 6 else 1,
        }
        for r in rows.get("XSECTIONS", [])
        if len(r) >= 3
    }

    junction_features = []
    for r in rows.get("JUNCTIONS", []):
        if len(r) >= 6 and r[0] in coords:
            junction_features.append(
                point_feature(
                    r[0],
                    coords,
                    {
                        "node_id": r[0],
                        "inv_el": fnum(r[1]),
                        "max_d": fnum(r[2]),
                        "init_d": fnum(r[3]),
                        "sur_d": fnum(r[4]),
                        "aponded": fnum(r[5]),
                    },
                )
            )

    outfall_features = []
    for r in rows.get("OUTFALLS", []):
        if len(r) >= 3 and r[0] in coords:
            out_type = r[2].upper()
            stage_data = r[3] if len(r) > 3 and r[3] != "*" else ""
            if out_type == "TIDAL":
                out_type = "FREE"
                stage_data = ""
            outfall_features.append(
                point_feature(
                    r[0],
                    coords,
                    {
                        "node_id": r[0],
                        "inv_el": fnum(r[1]),
                        "out_type": out_type,
                        "stage_data": stage_data,
                        "gated": r[4] if len(r) > 4 else "NO",
                        "route_to": r[5] if len(r) > 5 else "",
                    },
                )
            )

    conduit_features = []
    referenced_nodes: set[str] = set()
    for r in rows.get("CONDUITS", []):
        if len(r) < 5:
            continue
        link_id, from_node, to_node = r[0], r[1], r[2]
        referenced_nodes.update([from_node, to_node])
        if from_node not in coords or to_node not in coords:
            continue
        xs = xsections.get(link_id, {})
        shape, geom1 = safe_swmm_shape(str(xs.get("shape", "CIRCULAR")), fnum(str(xs.get("geom1", 0.5)), 0.5))
        max_flow = fnum(r[8]) if len(r) > 8 else None
        if max_flow is not None and max_flow <= 0:
            max_flow = None
        conduit_features.append(
            line_feature(
                link_id,
                from_node,
                to_node,
                coords,
                vertices,
                {
                    "link_id": link_id,
                    "from_id": from_node,
                    "to_id": to_node,
                    "len_m": fnum(r[3], 1.0),
                    "n_val": fnum(r[4], 0.013),
                    "in_offset": fnum(r[5]) if len(r) > 5 else 0.0,
                    "out_offset": fnum(r[6]) if len(r) > 6 else 0.0,
                    "init_flow": fnum(r[7]) if len(r) > 7 else 0.0,
                    "max_flow": max_flow,
                    **{**xs, "shape": shape, "geom1": geom1},
                },
            )
        )

    for section_name in ("PUMPS", "ORIFICES", "WEIRS", "OUTLETS"):
        for r in rows.get(section_name, []):
            if len(r) < 3:
                continue
            link_id, from_node, to_node = r[0], r[1], r[2]
            referenced_nodes.update([from_node, to_node])
            if from_node not in coords or to_node not in coords:
                continue
            length = max(distance(coords[from_node], coords[to_node]), 1.0)
            conduit_features.append(
                line_feature(
                    link_id,
                    from_node,
                    to_node,
                    coords,
                    vertices,
                    {
                        "link_id": link_id,
                        "from_id": from_node,
                        "to_id": to_node,
                        "len_m": length,
                        "n_val": 0.013,
                        "in_offset": 0.0,
                        "out_offset": 0.0,
                        "init_flow": 0.0,
                        "max_flow": None,
                        "shape": "CIRCULAR",
                        "geom1": 0.5,
                        "geom2": 0.0,
                        "geom3": 0.0,
                        "geom4": 0.0,
                        "barrels": 1,
                        "derived_from_link_section": section_name,
                    },
                )
            )

    known_junction_ids = {f["properties"]["node_id"] for f in junction_features}
    known_outfall_ids = {f["properties"]["node_id"] for f in outfall_features}
    for node_id in sorted(referenced_nodes - known_junction_ids - known_outfall_ids):
        if node_id in coords:
            junction_features.append(
                point_feature(
                    node_id,
                    coords,
                    {
                        "node_id": node_id,
                        "inv_el": 0.0,
                        "max_d": 2.0,
                        "init_d": 0.0,
                        "sur_d": 0.0,
                        "aponded": 0.0,
                        "derived_from": "referenced_non_junction_node",
                    },
                )
            )

    polygons: dict[str, list[tuple[float, float]]] = {}
    for r in rows.get("POLYGONS", []):
        if len(r) >= 3:
            polygons.setdefault(r[0], []).append((fnum(r[1]), fnum(r[2])))

    sub_rows = {r[0]: r for r in rows.get("SUBCATCHMENTS", []) if len(r) >= 8}
    subarea_rows = {r[0]: r for r in rows.get("SUBAREAS", []) if len(r) >= 7}
    infiltration_rows = {r[0]: r for r in rows.get("INFILTRATION", []) if len(r) >= 4}

    subcatchment_features = []
    landuse_rows = []
    soil_rows = []
    landuse_lookup_rows = []
    soil_lookup_rows = []
    params_direct = {"ok": True, "sections": {"subcatchments": [], "subareas": [], "infiltration": []}}

    for sid, r in sorted(sub_rows.items()):
        poly = polygons.get(sid)
        if not poly:
            continue
        if poly[0] != poly[-1]:
            poly = [*poly, poly[0]]
        landuse_class = f"source_{sid}_landuse"
        soil_texture = f"source_{sid}_soil"
        subcatchment_features.append(
            {
                "type": "Feature",
                "properties": {
                    "subcatchment_id": sid,
                    "outlet_hint": r[2],
                    "rain_gage": r[1],
                    "source_area_ha": fnum(r[3]),
                    "source_pct_imperv": fnum(r[4]),
                    "width_m": fnum(r[5]),
                    "slope_pct": fnum(r[6]),
                    "curb_length_m": fnum(r[7]),
                    "landuse_class": landuse_class,
                    "soil_texture": soil_texture,
                },
                "geometry": {"type": "Polygon", "coordinates": [[[x, y] for x, y in poly]]},
            }
        )
        landuse_rows.append({"subcatchment_id": sid, "landuse_class": landuse_class})
        soil_rows.append({"subcatchment_id": sid, "soil_texture": soil_texture})

        sa = subarea_rows[sid]
        pct_routed = fnum(sa[7], 100.0) if len(sa) > 7 else 100.0
        landuse_lookup_rows.append(
            {
                "landuse_class": landuse_class,
                "imperv_pct": fnum(r[4]),
                "n_imperv": fnum(sa[1]),
                "n_perv": fnum(sa[2]),
                "dstore_imperv_in": fnum(sa[3]),
                "dstore_perv_in": fnum(sa[4]),
                "zero_imperv_pct": fnum(sa[5]),
                "route_to": sa[6],
                "pct_routed": pct_routed,
                "notes": "extracted from Generate_SWMM_inp Test_5_2.inp for raw-path adapter test",
            }
        )

        infil = infiltration_rows[sid]
        soil_lookup_rows.append(
            {
                "texture": soil_texture,
                "suction_mm": fnum(infil[1]),
                "ksat_mm_per_hr": fnum(infil[2]),
                "imdmax": fnum(infil[3]),
                "notes": "extracted from Generate_SWMM_inp Test_5_2.inp for raw-path adapter test",
            }
        )

        params_direct["sections"]["subcatchments"].append({"id": sid, "pct_imperv": fnum(r[4])})
        params_direct["sections"]["subareas"].append(
            {
                "id": sid,
                "n_imperv": fnum(sa[1]),
                "n_perv": fnum(sa[2]),
                "dstore_imperv_in": fnum(sa[3]),
                "dstore_perv_in": fnum(sa[4]),
                "zero_imperv_pct": fnum(sa[5]),
                "route_to": sa[6],
                "pct_routed": pct_routed,
            }
        )
        params_direct["sections"]["infiltration"].append(
            {"id": sid, "suction_mm": fnum(infil[1]), "ksat_mm_per_hr": fnum(infil[2]), "imdmax": fnum(infil[3])}
        )

    raw_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "junctions": raw_dir / "junctions.geojson",
        "outfalls": raw_dir / "outfalls.geojson",
        "conduits": raw_dir / "conduits.geojson",
        "subcatchments": raw_dir / "subcatchments.geojson",
        "landuse": raw_dir / "landuse.csv",
        "soil": raw_dir / "soil.csv",
        "landuse_lookup": raw_dir / "landuse_lookup.csv",
        "soil_lookup": raw_dir / "soil_lookup.csv",
        "params_direct": raw_dir / "params_direct_from_source_sections.json",
        "timeseries": raw_dir / "timeseries.txt",
        "raingage": raw_dir / "raingage.json",
        "config": raw_dir / "builder_config.json",
        "mapping": raw_dir / "network_import_mapping.json",
    }

    write_json(paths["junctions"], feature_collection(junction_features))
    write_json(paths["outfalls"], feature_collection(outfall_features))
    write_json(paths["conduits"], feature_collection(conduit_features))
    write_json(paths["subcatchments"], feature_collection(subcatchment_features))
    write_csv(paths["landuse"], landuse_rows, ["subcatchment_id", "landuse_class"])
    write_csv(paths["soil"], soil_rows, ["subcatchment_id", "soil_texture"])
    write_csv(
        paths["landuse_lookup"],
        landuse_lookup_rows,
        [
            "landuse_class",
            "imperv_pct",
            "n_imperv",
            "n_perv",
            "dstore_imperv_in",
            "dstore_perv_in",
            "zero_imperv_pct",
            "route_to",
            "pct_routed",
            "notes",
        ],
    )
    write_csv(paths["soil_lookup"], soil_lookup_rows, ["texture", "suction_mm", "ksat_mm_per_hr", "imdmax", "notes"])
    write_json(paths["params_direct"], params_direct)

    raingage = rows["RAINGAGES"][0]
    series_name = raingage[5]
    ts_lines = [" ".join(r) for r in rows.get("TIMESERIES", []) if r and r[0] == series_name]
    paths["timeseries"].write_text("\n".join(ts_lines) + "\n", encoding="utf-8")
    write_json(
        paths["raingage"],
        {
            "ok": True,
            "gage": {
                "id": raingage[0],
                "rain_format": raingage[1],
                "interval_min": parse_interval_min(raingage[2]),
                "scf": fnum(raingage[3], 1.0),
                "source": {"kind": "TIMESERIES", "series_name": series_name},
            },
        },
    )

    option_rows = {r[0].upper(): r[1] for r in rows.get("OPTIONS", []) if len(r) >= 2}
    options_config = {
        key: option_rows[key]
        for key in (
            "FLOW_UNITS",
            "INFILTRATION",
            "FLOW_ROUTING",
            "LINK_OFFSETS",
            "MIN_SLOPE",
            "ALLOW_PONDING",
            "SKIP_STEADY_STATE",
            "START_DATE",
            "START_TIME",
            "REPORT_START_DATE",
            "REPORT_START_TIME",
            "END_DATE",
            "END_TIME",
            "SWEEP_START",
            "SWEEP_END",
            "DRY_DAYS",
            "REPORT_STEP",
            "WET_STEP",
            "DRY_STEP",
            "ROUTING_STEP",
        )
        if key in option_rows
    }
    for step_key in ("REPORT_STEP", "WET_STEP", "DRY_STEP", "ROUTING_STEP"):
        if step_key in options_config:
            options_config[step_key] = normalize_step_value(str(options_config[step_key]))

    write_json(
        paths["config"],
        {
            "title": "Generate_SWMM_inp raw-path adapter benchmark",
            "options": options_config,
            "report": {"INPUT": "NO", "CONTROLS": "NO", "SUBCATCHMENTS": "ALL", "NODES": "ALL", "LINKS": "ALL"},
        },
    )

    write_json(
        paths["mapping"],
        {
            "meta": {"name": "generate-swmm-inp-raw-path", "source": str(inp)},
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
    ensure_source_inp()

    raw_dir = RUN_DIR / "00_raw"
    paths = extract_raw_inputs(SOURCE_INP, raw_dir)

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
            "RG_1",
            "--out-csv",
            str(RUN_DIR / "01_gis/subcatchments.csv"),
            "--out-json",
            str(RUN_DIR / "01_gis/subcatchments.json"),
        ]
    )
    run_cmd(
        [
            "python3",
            "skills/swmm-params/scripts/landuse_to_swmm_params.py",
            "--input",
            str(paths["landuse"]),
            "--lookup",
            str(paths["landuse_lookup"]),
            "--output",
            str(RUN_DIR / "02_params/landuse.json"),
            "--strict",
        ]
    )
    run_cmd(
        [
            "python3",
            "skills/swmm-params/scripts/soil_to_greenampt.py",
            "--input",
            str(paths["soil"]),
            "--lookup",
            str(paths["soil_lookup"]),
            "--output",
            str(RUN_DIR / "02_params/soil.json"),
            "--strict",
        ]
    )
    run_cmd(
        [
            "python3",
            "skills/swmm-params/scripts/merge_swmm_params.py",
            "--landuse-json",
            str(RUN_DIR / "02_params/landuse.json"),
            "--soil-json",
            str(RUN_DIR / "02_params/soil.json"),
            "--output",
            str(RUN_DIR / "02_params/merged_params.json"),
            "--strict",
        ]
    )
    run_cmd(
        [
            "python3",
            "skills/swmm-builder/scripts/build_swmm_inp.py",
            "--subcatchments-csv",
            str(RUN_DIR / "01_gis/subcatchments.csv"),
            "--params-json",
            str(RUN_DIR / "02_params/merged_params.json"),
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
            "J_4",
        ]
    )

    continuity = subprocess.check_output(
        ["python3", "skills/swmm-runner/scripts/swmm_runner.py", "continuity", "--rpt", str(RUN_DIR / "06_runner/model.rpt")],
        cwd=REPO_ROOT,
        text=True,
    )
    peak = subprocess.check_output(
        ["python3", "skills/swmm-runner/scripts/swmm_runner.py", "peak", "--rpt", str(RUN_DIR / "06_runner/model.rpt"), "--node", "J_4"],
        cwd=REPO_ROOT,
        text=True,
    )
    write_json(RUN_DIR / "07_qa/continuity.json", json.loads(continuity))
    write_json(RUN_DIR / "07_qa/peak_J_4.json", json.loads(peak))
    continuity_metrics = json.loads(continuity)
    peak_metrics = json.loads(peak)
    qa_warnings = []
    flow_error = continuity_metrics.get("continuity_error_percent", {}).get("flow_routing")
    if isinstance(flow_error, (int, float)) and abs(flow_error) > 5.0:
        qa_warnings.append(
            {
                "kind": "flow_routing_continuity_error",
                "value_percent": flow_error,
                "boundary": "Adapter executed successfully, but this result should not be treated as hydrologic validation.",
            }
        )
    if peak_metrics.get("peak") == 0.0:
        qa_warnings.append(
            {
                "kind": "zero_target_node_peak",
                "node": peak_metrics.get("node"),
                "boundary": "The selected node peak is useful as a parser smoke check, not as a calibration or validation metric.",
            }
        )

    summary = {
        "run_dir": str(RUN_DIR.relative_to(REPO_ROOT)),
        "status": "pass",
        "qa_warning_count": len(qa_warnings),
        "qa_warnings": qa_warnings,
        "source": str(SOURCE_INP.relative_to(REPO_ROOT)),
        "source_repository": SOURCE_REPO_URL,
        "source_commit": SOURCE_REPO_COMMIT,
        "note": "The upstream repository currently provides INP files and QGIS style files, not the paper-described GPKG/XLSX raw layers. This adapter extracts raw-like GIS/CSV inputs from the open Test_5_2.inp to exercise the Agentic SWMM full modular path.",
        "artifacts": {
            "raw_subcatchments": str(paths["subcatchments"].relative_to(REPO_ROOT)),
            "raw_conduits": str(paths["conduits"].relative_to(REPO_ROOT)),
            "raw_junctions": str(paths["junctions"].relative_to(REPO_ROOT)),
            "raw_outfalls": str(paths["outfalls"].relative_to(REPO_ROOT)),
            "network_json": str((RUN_DIR / "04_network/network.json").relative_to(REPO_ROOT)),
            "subcatchments_csv": str((RUN_DIR / "01_gis/subcatchments.csv").relative_to(REPO_ROOT)),
            "params_json": str((RUN_DIR / "02_params/merged_params.json").relative_to(REPO_ROOT)),
            "built_inp": str((RUN_DIR / "05_builder/model.inp").relative_to(REPO_ROOT)),
            "runner_manifest": str((RUN_DIR / "06_runner/manifest.json").relative_to(REPO_ROOT)),
            "continuity": str((RUN_DIR / "07_qa/continuity.json").relative_to(REPO_ROOT)),
            "peak": str((RUN_DIR / "07_qa/peak_J_4.json").relative_to(REPO_ROOT)),
        },
        "metrics": {
            "continuity": continuity_metrics,
            "peak_J_4": peak_metrics,
        },
    }
    write_json(RUN_DIR / "manifest.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
