#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
GIS_DIR = Path(__file__).resolve().parents[1]
PARAMS_DIR = REPO_ROOT / "skills/swmm-params"
NETWORK_DIR = REPO_ROOT / "skills/swmm-network"

DEFAULT_QGIS_PROCESS = "/Applications/QGIS-final-4_0_2.app/Contents/MacOS/qgis_process"
DEFAULT_PROJ_LIB = "/Applications/QGIS-final-4_0_2.app/Contents/Resources/qgis/proj"
DEFAULT_GISBASE = "/Applications/GRASS-8.4.app/Contents/Resources"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def run_python(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    text = proc.stdout.strip()
    if not text:
        return {"stdout": ""}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"stdout": text}


def qgis_env(*, proj_lib: Path | None, gisbase: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    if proj_lib:
        env["PROJ_LIB"] = str(proj_lib)
    if gisbase:
        env["GISBASE"] = str(gisbase)
    return env


def run_qgis(
    qgis_process: Path,
    algorithm: str,
    params: list[tuple[str, Any]],
    *,
    env: dict[str, str],
    cwd: Path,
    audit: list[dict[str, Any]],
) -> str:
    cmd = [str(qgis_process), "run", algorithm, "--"]
    cmd.extend(f"{key}={value}" for key, value in params if value is not None)
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    audit.append(
        {
            "algorithm": algorithm,
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
        }
    )
    if proc.returncode != 0:
        raise RuntimeError(f"QGIS algorithm failed rc={proc.returncode}: {algorithm}\n{proc.stderr}")
    return proc.stdout


def layer_sidecars(path: Path) -> list[str]:
    if path.suffix.lower() != ".shp":
        return []
    sidecars = []
    for suffix in [".shx", ".dbf", ".prj"]:
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            sidecars.append(str(candidate))
    return sidecars


def read_crs_hint(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".shp":
        prj = path.with_suffix(".prj")
        if prj.exists():
            return {"source": str(prj), "kind": "wkt", "text": prj.read_text(encoding="utf-8", errors="ignore").strip()}
        return {"source": None, "kind": "missing_prj", "text": ""}
    if suffix in {".geojson", ".json"}:
        try:
            obj = load_json(path)
        except Exception:
            return {"source": str(path), "kind": "unknown_json", "text": ""}
        crs = obj.get("crs")
        if crs:
            return {"source": str(path), "kind": "geojson_crs", "text": json.dumps(crs, sort_keys=True)}
        return {"source": str(path), "kind": "geojson_default_or_unspecified", "text": ""}
    return {"source": str(path), "kind": "not_inspected", "text": ""}


def get_layer_epsg(path: Path) -> str | None:
    """Return 'EPSG:NNNNN' for a layer file, or None if undetermined."""
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        try:
            result = subprocess.run(
                ["gdalinfo", "-json", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                info = json.loads(result.stdout)
                wkt = info.get("coordinateSystem", {}).get("wkt", "")
                m = re.search(r'ID\["EPSG",(\d+)\]', wkt)
                if m:
                    return f"EPSG:{m.group(1)}"
        except Exception:
            pass
        return None
    if suffix == ".shp":
        prj = path.with_suffix(".prj")
        if prj.exists():
            wkt = prj.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'AUTHORITY\["EPSG","(\d+)"\]', wkt)
            if m:
                return f"EPSG:{m.group(1)}"
        return None
    if suffix in {".geojson", ".json"}:
        try:
            obj = load_json(path)
            name = obj.get("crs", {}).get("properties", {}).get("name", "")
            m = re.search(r'EPSG[::]+(\d+)', name)
            if m:
                return f"EPSG:{m.group(1)}"
        except Exception:
            pass
        return None
    return None


def resolve_target_epsg(target_crs_ref: str) -> str | None:
    """Return 'EPSG:NNNNN' from an EPSG string like 'EPSG:32610' or a file path."""
    epsg_match = re.match(r'epsg:(\d+)', target_crs_ref, re.IGNORECASE)
    if epsg_match:
        return f"EPSG:{epsg_match.group(1)}"
    p = Path(target_crs_ref)
    return get_layer_epsg(p) if p.exists() else None


def build_layers_manifest(layers: dict[str, str | None], out_path: Path) -> dict[str, Any]:
    records = []
    issues = []
    for role, raw_path in layers.items():
        if raw_path is None:
            continue
        path = Path(raw_path)
        record = {
            "role": role,
            "path": str(path),
            "exists": path.exists(),
            "suffix": path.suffix.lower(),
            "sidecars": layer_sidecars(path),
            "crs": read_crs_hint(path) if path.exists() else None,
        }
        if not path.exists():
            issues.append({"severity": "error", "role": role, "message": f"Layer path does not exist: {path}"})
        if path.suffix.lower() == ".shp":
            missing = [suffix for suffix in [".shx", ".dbf"] if not path.with_suffix(suffix).exists()]
            for suffix in missing:
                issues.append({"severity": "error", "role": role, "message": f"Missing shapefile sidecar {suffix}: {path}"})
            if not path.with_suffix(".prj").exists():
                issues.append({"severity": "warning", "role": role, "message": f"Missing shapefile CRS sidecar .prj: {path}"})
        records.append(record)

    manifest = {
        "ok": not any(issue["severity"] == "error" for issue in issues),
        "skill": "swmm-gis",
        "adapter": "qgis_data_prep",
        "layers": records,
        "issue_count": len(issues),
        "issues": issues,
    }
    write_json(out_path, manifest)
    return manifest


def validate_crs(layers_manifest: dict[str, Any], out_path: Path) -> dict[str, Any]:
    comparable = []
    issues = []
    for layer in layers_manifest["layers"]:
        crs = layer.get("crs") or {}
        text = " ".join(str(crs.get("text") or "").split())
        comparable.append({"role": layer["role"], "path": layer["path"], "kind": crs.get("kind"), "text": text})
        if not text and crs.get("kind") not in {"geojson_default_or_unspecified", "not_inspected"}:
            issues.append({"severity": "warning", "role": layer["role"], "message": "CRS could not be confirmed from the source layer."})

    explicit = [item for item in comparable if item["text"]]
    unique = sorted({item["text"] for item in explicit})
    if len(unique) > 1:
        issues.append(
            {
                "severity": "error",
                "message": "Input layers expose different CRS definitions. Reproject in QGIS before export.",
                "explicit_crs_count": len(unique),
            }
        )

    report = {
        "ok": not any(issue["severity"] == "error" for issue in issues),
        "skill": "swmm-gis",
        "adapter": "qgis_data_prep",
        "crs_records": comparable,
        "issue_count": len(issues),
        "issues": issues,
        "assumption": "GeoJSON without explicit CRS is treated as already exported in the project CRS.",
    }
    write_json(out_path, report)
    return report


def is_raster(path: Path) -> bool:
    return path.suffix.lower() in {".tif", ".tiff", ".img", ".vrt", ".asc"}


def is_vector(path: Path) -> bool:
    return path.suffix.lower() in {".shp", ".geojson", ".json", ".gpkg"}


def normalize_vector_layer(
    *,
    qgis_process: Path,
    source: Path,
    boundary: Path,
    target_crs: str,
    out_path: Path,
    work_dir: Path,
    env: dict[str, str],
    audit: list[dict[str, Any]],
    skip_reproject: bool = False,
) -> dict[str, str]:
    work_dir.mkdir(parents=True, exist_ok=True)
    if skip_reproject:
        vector_for_clip = source
        reprojected_str = str(source)
    else:
        reprojected = work_dir / f"{out_path.stem}_reprojected.shp"
        run_qgis(
            qgis_process,
            "native:reprojectlayer",
            [
                ("INPUT", source),
                ("TARGET_CRS", target_crs),
                ("OUTPUT", reprojected),
            ],
            env=env,
            cwd=REPO_ROOT,
            audit=audit,
        )
        vector_for_clip = reprojected
        reprojected_str = str(reprojected)

    run_qgis(
        qgis_process,
        "native:clip",
        [
            ("INPUT", vector_for_clip),
            ("OVERLAY", boundary),
            ("OUTPUT", out_path),
        ],
        env=env,
        cwd=REPO_ROOT,
        audit=audit,
    )
    return {"reprojected": reprojected_str, "clipped": str(out_path)}


def normalize_raster_layer(
    *,
    qgis_process: Path,
    source: Path,
    boundary: Path,
    target_crs: str,
    out_path: Path,
    work_dir: Path,
    resampling: int,
    target_resolution: float | None,
    env: dict[str, str],
    audit: list[dict[str, Any]],
    skip_reproject: bool = False,
) -> dict[str, str]:
    work_dir.mkdir(parents=True, exist_ok=True)
    if skip_reproject:
        raster_for_clip = source
        reprojected_str = str(source)
    else:
        reprojected = work_dir / f"{out_path.stem}_reprojected.tif"
        warp_params: list[tuple[str, Any]] = [
            ("INPUT", source),
            ("TARGET_CRS", target_crs),
            ("RESAMPLING", resampling),
            ("MULTITHREADING", "true"),
            ("OUTPUT", reprojected),
        ]
        if target_resolution:
            warp_params.insert(3, ("TARGET_RESOLUTION", target_resolution))
        run_qgis(qgis_process, "gdal:warpreproject", warp_params, env=env, cwd=REPO_ROOT, audit=audit)
        raster_for_clip = reprojected
        reprojected_str = str(reprojected)

    clip_params: list[tuple[str, Any]] = [
        ("INPUT", raster_for_clip),
        ("MASK", boundary),
        ("TARGET_CRS", target_crs),
        ("CROP_TO_CUTLINE", "true"),
        ("KEEP_RESOLUTION", "true"),
        ("OUTPUT", out_path),
    ]
    run_qgis(qgis_process, "gdal:cliprasterbymasklayer", clip_params, env=env, cwd=REPO_ROOT, audit=audit)
    return {"reprojected": reprojected_str, "clipped": str(out_path)}


def normalize_layers(args: argparse.Namespace) -> dict[str, Any]:
    out_dir: Path = args.out_dir
    work_dir = out_dir / "_work"
    out_dir.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []
    env = qgis_env(proj_lib=args.proj_lib, gisbase=args.gisbase)

    sources = {
        "dem": args.dem,
        "boundary": args.boundary,
        "landuse": args.landuse,
        "soil": args.soil,
    }
    for role, path in sources.items():
        if not path.exists():
            raise FileNotFoundError(f"Missing {role}: {path}")

    target_crs = args.target_crs or str(args.boundary)
    target_epsg = resolve_target_epsg(target_crs)

    boundary_out = out_dir / "boundary.shp"
    boundary_epsg = get_layer_epsg(args.boundary)
    boundary_needs_reproject = not (target_epsg and boundary_epsg and boundary_epsg == target_epsg)
    if boundary_needs_reproject:
        run_qgis(
            args.qgis_process,
            "native:reprojectlayer",
            [
                ("INPUT", args.boundary),
                ("TARGET_CRS", target_crs),
                ("OUTPUT", boundary_out),
            ],
            env=env,
            cwd=REPO_ROOT,
            audit=commands,
        )
        commands.append({"action": "reprojectlayer", "role": "boundary", "source": str(args.boundary), "output": str(boundary_out)})
    else:
        shutil.copy2(args.boundary, boundary_out)
        for sc in layer_sidecars(args.boundary):
            shutil.copy2(sc, boundary_out.with_suffix(Path(sc).suffix))
        commands.append({"action": "copy_same_crs", "role": "boundary", "source": str(args.boundary), "output": str(boundary_out)})
    boundary_crs_ref = str(boundary_out) if not args.target_crs else args.target_crs

    outputs: dict[str, str] = {"boundary": str(boundary_out)}
    stage_outputs: dict[str, Any] = {"boundary": {"reprojected": str(boundary_out), "clipped": str(boundary_out)}}

    dem_out = out_dir / "dem.tif"
    dem_epsg = get_layer_epsg(args.dem)
    dem_skip = bool(target_epsg and dem_epsg and dem_epsg == target_epsg)
    stage_outputs["dem"] = normalize_raster_layer(
        qgis_process=args.qgis_process,
        source=args.dem,
        boundary=boundary_out,
        target_crs=boundary_crs_ref,
        out_path=dem_out,
        work_dir=work_dir,
        resampling=args.dem_resampling,
        target_resolution=args.target_resolution,
        env=env,
        audit=commands,
        skip_reproject=dem_skip,
    )
    outputs["dem"] = str(dem_out)

    for role, source in [("landuse", args.landuse), ("soil", args.soil)]:
        src_epsg = get_layer_epsg(source)
        skip = bool(target_epsg and src_epsg and src_epsg == target_epsg)
        if is_raster(source):
            out_path = out_dir / f"{role}.tif"
            stage_outputs[role] = normalize_raster_layer(
                qgis_process=args.qgis_process,
                source=source,
                boundary=boundary_out,
                target_crs=boundary_crs_ref,
                out_path=out_path,
                work_dir=work_dir,
                resampling=args.categorical_resampling,
                target_resolution=args.target_resolution,
                env=env,
                audit=commands,
                skip_reproject=skip,
            )
        elif is_vector(source):
            out_path = out_dir / f"{role}.shp"
            stage_outputs[role] = normalize_vector_layer(
                qgis_process=args.qgis_process,
                source=source,
                boundary=boundary_out,
                target_crs=boundary_crs_ref,
                out_path=out_path,
                work_dir=work_dir,
                env=env,
                audit=commands,
                skip_reproject=skip,
            )
        else:
            raise ValueError(f"Unsupported {role} layer type: {source}")
        outputs[role] = str(out_path)

    manifest = {
        "ok": True,
        "skill": "swmm-gis",
        "adapter": "qgis_normalize_layers",
        "qgis_process": str(args.qgis_process),
        "target_crs": target_crs,
        "target_crs_policy": "explicit target CRS" if args.target_crs else "boundary CRS",
        "target_resolution": args.target_resolution,
        "resampling": {
            "dem": args.dem_resampling,
            "categorical": args.categorical_resampling,
        },
        "inputs": {role: str(path) for role, path in sources.items()},
        "outputs": outputs,
        "stage_outputs": stage_outputs,
        "processing_commands": commands,
        "evidence_boundary": (
            "This step standardizes CRS and clips input GIS layers to the study boundary. "
            "It does not delineate streams, choose pour points, or run SWMM."
        ),
    }
    write_json(out_dir / "qgis_normalized_layers_manifest.json", manifest)
    return manifest


def extract_overlay_tables(
    subcatchments_geojson: Path,
    *,
    landuse_field: str,
    soil_field: str,
    id_field: str,
    out_landuse_csv: Path,
    out_soil_csv: Path,
) -> dict[str, Any]:
    obj = load_json(subcatchments_geojson)
    if obj.get("type") != "FeatureCollection":
        raise ValueError(f"Expected FeatureCollection: {subcatchments_geojson}")

    landuse_rows: list[dict[str, str]] = []
    soil_rows: list[dict[str, str]] = []
    issues = []
    seen: set[str] = set()
    for idx, feature in enumerate(obj.get("features") or [], start=1):
        props = feature.get("properties") or {}
        sid = str(props.get(id_field) or feature.get("id") or "").strip()
        if not sid:
            raise ValueError(f"Feature {idx} missing subcatchment id field '{id_field}'")
        if sid in seen:
            raise ValueError(f"Duplicate subcatchment id: {sid}")
        seen.add(sid)

        landuse = str(props.get(landuse_field) or "DEFAULT").strip() or "DEFAULT"
        soil = str(props.get(soil_field) or "-").strip() or "-"
        if landuse == "DEFAULT":
            issues.append({"severity": "warning", "id": sid, "message": f"Missing land use field '{landuse_field}', using DEFAULT."})
        if soil == "-":
            issues.append({"severity": "warning", "id": sid, "message": f"Missing soil field '{soil_field}', using fallback '-'."})
        landuse_rows.append({"subcatchment_id": sid, "landuse_class": landuse})
        soil_rows.append({"subcatchment_id": sid, "soil_texture": soil})

    write_csv(out_landuse_csv, landuse_rows, ["subcatchment_id", "landuse_class"])
    write_csv(out_soil_csv, soil_rows, ["subcatchment_id", "soil_texture"])

    return {
        "ok": True,
        "landuse_csv": str(out_landuse_csv),
        "soil_csv": str(out_soil_csv),
        "subcatchment_count": len(seen),
        "issue_count": len(issues),
        "issues": issues,
    }


def copy_network_and_qa(network_json: Path, out_network_json: Path, out_qa_json: Path) -> dict[str, Any]:
    out_network_json.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(network_json, out_network_json)
    qa = run_python(
        [
            str(NETWORK_DIR / "scripts/network_qa.py"),
            str(out_network_json),
            "--report-json",
            str(out_qa_json),
        ]
    )
    return {"network_json": str(out_network_json), "network_qa_json": str(out_qa_json), "qa": qa}


def export_swmm_intermediates(args: argparse.Namespace) -> dict[str, Any]:
    run_dir: Path = args.run_dir
    raw_dir = run_dir / "00_raw"
    gis_dir = run_dir / "01_gis"
    params_dir = run_dir / "02_params"
    network_dir = run_dir / "04_network"

    layers = {
        "dem": str(args.dem) if args.dem else None,
        "subcatchments": str(args.subcatchments_geojson),
        "landuse": str(args.landuse_layer) if args.landuse_layer else None,
        "soil": str(args.soil_layer) if args.soil_layer else None,
        # outlet is a node ID string, not a file path — exclude from file validation
        "rainfall": str(args.rainfall) if args.rainfall else None,
        "network": str(args.network_json),
    }
    layers_manifest = build_layers_manifest(layers, raw_dir / "qgis_layers_manifest.json")
    crs_report = validate_crs(layers_manifest, raw_dir / "qgis_crs_report.json")
    if not layers_manifest["ok"]:
        raise ValueError(f"Layer validation failed. See {raw_dir / 'qgis_layers_manifest.json'}")
    if args.strict_crs and not crs_report["ok"]:
        raise ValueError(f"CRS validation failed. See {raw_dir / 'qgis_crs_report.json'}")

    subcatchments_work = gis_dir / "subcatchments.geojson"
    subcatchments_work.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.subcatchments_geojson, subcatchments_work)

    preprocess = run_python(
        [
            str(GIS_DIR / "scripts/preprocess_subcatchments.py"),
            "--subcatchments-geojson",
            str(subcatchments_work),
            "--network-json",
            str(args.network_json),
            "--out-csv",
            str(gis_dir / "subcatchments.csv"),
            "--out-json",
            str(gis_dir / "subcatchments.json"),
            "--id-field",
            args.id_field,
            "--outlet-hint-field",
            args.outlet_hint_field,
            "--default-rain-gage",
            args.default_rain_gage,
            "--default-slope-pct",
            str(args.default_slope_pct),
            "--min-slope-pct",
            str(args.min_slope_pct),
            "--min-width-m",
            str(args.min_width_m),
        ]
    )

    overlay = extract_overlay_tables(
        subcatchments_work,
        landuse_field=args.landuse_field,
        soil_field=args.soil_field,
        id_field=args.id_field,
        out_landuse_csv=params_dir / "landuse.csv",
        out_soil_csv=params_dir / "soil.csv",
    )

    landuse = run_python(
        [
            str(PARAMS_DIR / "scripts/landuse_to_swmm_params.py"),
            "--input",
            str(params_dir / "landuse.csv"),
            "--output",
            str(params_dir / "landuse.json"),
        ]
    )
    soil = run_python(
        [
            str(PARAMS_DIR / "scripts/soil_to_greenampt.py"),
            "--input",
            str(params_dir / "soil.csv"),
            "--output",
            str(params_dir / "soil.json"),
        ]
    )
    merged = run_python(
        [
            str(PARAMS_DIR / "scripts/merge_swmm_params.py"),
            "--landuse-json",
            str(params_dir / "landuse.json"),
            "--soil-json",
            str(params_dir / "soil.json"),
            "--output",
            str(params_dir / "merged_params.json"),
        ]
    )

    network = copy_network_and_qa(args.network_json, network_dir / "network.json", network_dir / "network_qa.json")

    report = {
        "ok": True,
        "skill": "swmm-gis",
        "adapter": "qgis_data_prep",
        "case_id": args.case_id,
        "run_dir": str(run_dir),
        "workflow_boundary": (
            "MVP expects QGIS to provide delineated subcatchment polygons and overlay attributes; "
            "this script validates sources and exports Agentic SWMM-ready intermediates."
        ),
        "outputs": {
            "layers_manifest": str(raw_dir / "qgis_layers_manifest.json"),
            "crs_report": str(raw_dir / "qgis_crs_report.json"),
            "subcatchments_geojson": str(subcatchments_work),
            "subcatchments_csv": str(gis_dir / "subcatchments.csv"),
            "subcatchments_json": str(gis_dir / "subcatchments.json"),
            "landuse_csv": str(params_dir / "landuse.csv"),
            "soil_csv": str(params_dir / "soil.csv"),
            "landuse_json": str(params_dir / "landuse.json"),
            "soil_json": str(params_dir / "soil.json"),
            "merged_params_json": str(params_dir / "merged_params.json"),
            "network_json": str(network_dir / "network.json"),
            "network_qa_json": str(network_dir / "network_qa.json"),
            "qgis_export_manifest": str(run_dir / "qgis_export_manifest.json"),
        },
        "stage_summaries": {
            "layers": layers_manifest,
            "crs": crs_report,
            "preprocess": preprocess,
            "overlay": overlay,
            "landuse": landuse,
            "soil": soil,
            "merged_params": merged,
            "network": network,
        },
    }
    write_json(run_dir / "qgis_export_manifest.json", report)
    return report


def add_common_export_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--case-id", default="qgis-case")
    ap.add_argument("--subcatchments-geojson", type=Path, required=True)
    ap.add_argument("--network-json", type=Path, required=True)
    ap.add_argument("--dem", type=Path, default=None)
    ap.add_argument("--landuse-layer", type=Path, default=None)
    ap.add_argument("--soil-layer", type=Path, default=None)
    ap.add_argument("--outlet", type=Path, default=None)
    ap.add_argument("--rainfall", type=Path, default=None)
    ap.add_argument("--id-field", default="subcatchment_id")
    ap.add_argument("--outlet-hint-field", default="outlet_hint")
    ap.add_argument("--landuse-field", default="landuse_class")
    ap.add_argument("--soil-field", default="soil_texture")
    ap.add_argument("--default-rain-gage", default="RG1")
    ap.add_argument("--default-slope-pct", type=float, default=1.0)
    ap.add_argument("--min-slope-pct", type=float, default=0.1)
    ap.add_argument("--min-width-m", type=float, default=10.0)
    ap.add_argument("--strict-crs", action="store_true")


def main() -> None:
    parser = argparse.ArgumentParser(description="QGIS-oriented raw GIS to Agentic SWMM intermediate exporter.")
    sub = parser.add_subparsers(dest="command", required=True)

    load_ap = sub.add_parser("load-layers", help="Validate raw/QGIS-exported layer paths and sidecars.")
    load_ap.add_argument("--out", type=Path, required=True)
    load_ap.add_argument("--dem", type=Path)
    load_ap.add_argument("--boundary", type=Path)
    load_ap.add_argument("--subcatchments", type=Path)
    load_ap.add_argument("--landuse", type=Path)
    load_ap.add_argument("--soil", type=Path)
    load_ap.add_argument("--outlet", type=Path)
    load_ap.add_argument("--rainfall", type=Path)
    load_ap.add_argument("--network", type=Path)

    crs_ap = sub.add_parser("validate-crs", help="Check source CRS hints from a layer manifest.")
    crs_ap.add_argument("--layers-manifest", type=Path, required=True)
    crs_ap.add_argument("--out", type=Path, required=True)

    normalize_ap = sub.add_parser("normalize-layers", help="Reproject DEM/boundary/landuse/soil to one CRS and clip them by the boundary.")
    normalize_ap.add_argument("--dem", type=Path, required=True)
    normalize_ap.add_argument("--boundary", type=Path, required=True)
    normalize_ap.add_argument("--landuse", type=Path, required=True)
    normalize_ap.add_argument("--soil", type=Path, required=True)
    normalize_ap.add_argument("--out-dir", type=Path, required=True)
    normalize_ap.add_argument("--target-crs", help="Target CRS auth id, WKT/PROJ string, or layer path. Defaults to the boundary CRS.")
    normalize_ap.add_argument("--target-resolution", type=float, help="Optional target raster resolution in target CRS units.")
    normalize_ap.add_argument("--dem-resampling", type=int, default=1, help="GDAL resampling enum for DEM reprojection; default 1 is bilinear.")
    normalize_ap.add_argument("--categorical-resampling", type=int, default=0, help="GDAL resampling enum for categorical rasters; default 0 is nearest.")
    normalize_ap.add_argument("--qgis-process", type=Path, default=Path(DEFAULT_QGIS_PROCESS))
    normalize_ap.add_argument("--proj-lib", type=Path, default=Path(DEFAULT_PROJ_LIB))
    normalize_ap.add_argument("--gisbase", type=Path, default=Path(DEFAULT_GISBASE))

    overlay_ap = sub.add_parser("overlay-landuse-soil", help="Extract subcatchment_id, landuse_class, and soil_texture from a QGIS overlay export.")
    overlay_ap.add_argument("--subcatchments-geojson", type=Path, required=True)
    overlay_ap.add_argument("--out-landuse-csv", type=Path, required=True)
    overlay_ap.add_argument("--out-soil-csv", type=Path, required=True)
    overlay_ap.add_argument("--id-field", default="subcatchment_id")
    overlay_ap.add_argument("--landuse-field", default="landuse_class")
    overlay_ap.add_argument("--soil-field", default="soil_texture")

    network_ap = sub.add_parser("import-drainage-assets", help="Copy a prepared network JSON into 04_network and run network QA.")
    network_ap.add_argument("--network-json", type=Path, required=True)
    network_ap.add_argument("--out-network-json", type=Path, required=True)
    network_ap.add_argument("--out-qa-json", type=Path, required=True)

    export_ap = sub.add_parser("export-swmm-intermediates", help="Export 01_gis, 02_params, and 04_network artifacts from QGIS-prepared sources.")
    add_common_export_args(export_ap)

    args = parser.parse_args()
    if args.command == "load-layers":
        layers = {
            "dem": str(args.dem) if args.dem else None,
            "boundary": str(args.boundary) if args.boundary else None,
            "subcatchments": str(args.subcatchments) if args.subcatchments else None,
            "landuse": str(args.landuse) if args.landuse else None,
            "soil": str(args.soil) if args.soil else None,
            "outlet": str(args.outlet) if args.outlet else None,
            "rainfall": str(args.rainfall) if args.rainfall else None,
            "network": str(args.network) if args.network else None,
        }
        result = build_layers_manifest(layers, args.out)
    elif args.command == "validate-crs":
        result = validate_crs(load_json(args.layers_manifest), args.out)
    elif args.command == "normalize-layers":
        result = normalize_layers(args)
    elif args.command == "overlay-landuse-soil":
        result = extract_overlay_tables(
            args.subcatchments_geojson,
            landuse_field=args.landuse_field,
            soil_field=args.soil_field,
            id_field=args.id_field,
            out_landuse_csv=args.out_landuse_csv,
            out_soil_csv=args.out_soil_csv,
        )
    elif args.command == "import-drainage-assets":
        result = copy_network_and_qa(args.network_json, args.out_network_json, args.out_qa_json)
    elif args.command == "export-swmm-intermediates":
        result = export_swmm_intermediates(args)
    else:
        raise ValueError(f"Unknown command: {args.command}")

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
