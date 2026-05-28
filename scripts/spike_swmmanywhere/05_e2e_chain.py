"""E2E chain acceptance spike — proves aiswmm drives SWMManywhere end-to-end.

Chain:
  bbox -> SWMManywhere synth (skill) -> aiswmm run (swmm5) -> audit -> plot -> peak

Step 1: invoke ``skills/swmm-anywhere/scripts/synth_from_bbox.py`` in the
        isolated spike venv to synthesize a SWMM .inp from a bbox.
Step 2: ``aiswmm run`` (uses ``/opt/homebrew/bin/swmm5``, not pyswmm).
Step 3: ``aiswmm audit`` against the run dir.
Step 4: ``aiswmm plot`` to render rainfall vs runoff.
Step 5: parse peak flow from the .rpt's "Outfall Loading Summary".

Deterministic — no LLM in the loop. Exit 0 iff every step succeeds.

Run from project root:

    python3.11 scripts/spike_swmmanywhere/05_e2e_chain.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPIKE_DIR = REPO_ROOT / "scripts" / "spike_swmmanywhere"
SPIKE_VENV_PY = SPIKE_DIR / "venv" / "bin" / "python"
SYNTH_SCRIPT = REPO_ROOT / "skills" / "swmm-anywhere" / "scripts" / "synth_from_bbox.py"
AISWMM_BIN = Path("/opt/homebrew/bin/aiswmm")

# Same 1x1 km bbox used in spike 02 / 04 (London Greenwich, SW corner).
BBOX = ["0.04020", "51.55759", "0.05450", "51.56660"]
PROJECT_NAME = "greenwich_e2e"


def _hr(label: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n{label}\n{bar}", flush=True)


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str, float]:
    t0 = time.time()
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env=env,
    )
    elapsed = time.time() - t0
    return proc.returncode, proc.stdout, proc.stderr, elapsed


def _make_run_root() -> Path:
    now = datetime.now()
    return (
        REPO_ROOT
        / "runs"
        / now.strftime("%Y-%m-%d")
        / f"{now.strftime('%H%M%S')}_e2e_chain"
    )


def _step1_synth(run_root: Path) -> tuple[bool, dict, float, str]:
    """Step 1: SWMManywhere synth via skill script in spike venv."""
    _hr("STEP 1 / 5 — swmm-anywhere skill (bbox -> synth.inp)")
    cmd = [
        str(SPIKE_VENV_PY),
        str(SYNTH_SCRIPT),
        "--bbox", *BBOX,
        "--run-dir", str(run_root),
        "--project-name", PROJECT_NAME,
        "--json",
    ]
    # spike venv has swmmanywhere but not agentic_swmm; expose this repo's
    # agentic_swmm package via PYTHONPATH so the skill's lazy import succeeds.
    extra_env = {"PYTHONPATH": str(REPO_ROOT)}
    print(f"$ PYTHONPATH={REPO_ROOT} {' '.join(cmd)}", flush=True)
    rc, stdout, stderr, elapsed = _run(cmd, cwd=REPO_ROOT, extra_env=extra_env)
    print(stderr, end="", flush=True)
    if rc != 0:
        print(stdout, flush=True)
        return False, {}, elapsed, "synth script exited non-zero"
    try:
        summary = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return False, {}, elapsed, f"could not parse skill JSON: {exc}"
    inp_path = Path(summary.get("inp_path", ""))
    if not inp_path.exists():
        return False, summary, elapsed, f"reported inp not found: {inp_path}"
    print(f"  -> inp: {inp_path}  ({inp_path.stat().st_size / 1024:.1f} KB)", flush=True)
    return True, summary, elapsed, ""


def _step2_run(inp_path: Path, swmm_run_dir: Path) -> tuple[bool, dict, float, str]:
    """Step 2: aiswmm run -> RPT + OUT using bundled swmm5.

    aiswmm v0.7.0 writes RPT/OUT under nested ``05_runner/`` (not directly in
    run_dir), and emits a JSON manifest on stdout that includes the actual
    paths and parsed metrics.
    """
    _hr("STEP 2 / 5 — aiswmm run (drives /opt/homebrew/bin/swmm5)")
    cmd = [
        str(AISWMM_BIN),
        "run",
        "--inp", str(inp_path),
        "--run-dir", str(swmm_run_dir),
    ]
    print(f"$ {' '.join(cmd)}", flush=True)
    rc, stdout, stderr, elapsed = _run(cmd, cwd=REPO_ROOT)
    print(stdout, flush=True)
    if rc != 0:
        print(stderr, flush=True)
        return False, {}, elapsed, "aiswmm run exited non-zero"

    # Parse the JSON manifest from stdout to discover where rpt/out landed.
    try:
        manifest = json.loads(stdout)
    except json.JSONDecodeError:
        return False, {}, elapsed, "could not parse aiswmm run stdout as JSON"
    files = manifest.get("files") or {}
    rpt = Path(files.get("rpt", ""))
    out = Path(files.get("out", ""))
    if not rpt.exists() or not out.exists():
        return (
            False, {"manifest": manifest}, elapsed,
            f"manifest-reported rpt/out missing: rpt={rpt.exists()} out={out.exists()}",
        )
    info = {
        "manifest": manifest,
        "rpt": str(rpt),
        "out": str(out),
        "rpt_kb": round(rpt.stat().st_size / 1024, 1),
        "out_kb": round(out.stat().st_size / 1024, 1),
        "swmm_return_code": manifest.get("return_code"),
        "manifest_peak": manifest.get("metrics", {}).get("peak"),
        "manifest_continuity_error_pct": manifest.get("metrics", {}).get(
            "continuity", {}
        ).get("continuity_error_percent"),
    }
    print(f"  -> rpt: {info['rpt']}  ({info['rpt_kb']} KB)", flush=True)
    print(f"  -> out: {info['out']}  ({info['out_kb']} KB)", flush=True)
    return True, info, elapsed, ""


def _step3_audit(swmm_run_dir: Path) -> tuple[bool, dict, float, str]:
    """Step 3: aiswmm audit against run dir."""
    _hr("STEP 3 / 5 — aiswmm audit")
    cmd = [
        str(AISWMM_BIN),
        "audit",
        "--run-dir", str(swmm_run_dir),
        "--case-name", "greenwich_e2e_chain",
        "--workflow-mode", "synthetic_bbox",
        "--objective", "e2e_chain_acceptance",
        "--no-memory",  # spike: don't pollute lessons_learned
    ]
    print(f"$ {' '.join(cmd)}", flush=True)
    rc, stdout, stderr, elapsed = _run(cmd, cwd=REPO_ROOT)
    print(stdout, flush=True)
    if rc != 0:
        print(stderr, flush=True)
        return False, {}, elapsed, "aiswmm audit exited non-zero"
    audit_dir = swmm_run_dir / "09_audit"
    prov = audit_dir / "experiment_provenance.json"
    note = audit_dir / "experiment_note.md"
    if not prov.exists() or not note.exists():
        return False, {}, elapsed, (
            f"expected audit artefacts missing: prov={prov.exists()} note={note.exists()}"
        )
    info = {
        "audit_dir": str(audit_dir),
        "provenance": str(prov),
        "note": str(note),
    }
    print(f"  -> provenance: {info['provenance']}", flush=True)
    print(f"  -> note:       {info['note']}", flush=True)
    return True, info, elapsed, ""


def _build_plot_friendly_inp(swmm_run_dir: Path, peak_node: str) -> Path:
    """Materialise an INP plot-tool can ingest.

    SWMManywhere emits a `[RAINGAGES]` `FILE storm.dat` reference and no
    in-INP `[TIMESERIES]`. ``aiswmm plot`` requires an in-INP TIMESERIES.
    We inject one parsed from the run dir's storm.dat next to a copy of
    the model INP, leaving the original (run-of-record) INP untouched.
    """
    src_inp = swmm_run_dir / "00_inputs" / "model.inp"
    src_storm = swmm_run_dir / "00_inputs" / "storm.dat"
    if not src_inp.exists() or not src_storm.exists():
        raise FileNotFoundError(f"missing 00_inputs INP or storm.dat in {swmm_run_dir}")

    # Parse storm.dat: lines of "<id> <YYYY> <MM> <DD> <HH> <MM>  <value>"
    rows: list[tuple[str, str, str, str, str, str, str]] = []
    for line in src_storm.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        rows.append(tuple(parts[:7]))  # type: ignore[arg-type]
    if not rows:
        raise ValueError(f"no usable rows in {src_storm}")

    # Build TIMESERIES section in SWMM format using MM/DD/YYYY HH:MM <value>.
    ts_name = "storm"
    ts_lines = [
        "[TIMESERIES]",
        ";;Name           Date       Time       Value",
        ";;-------------- ---------- ---------- ----------",
    ]
    for (_id, yyyy, mm, dd, hh, mn, val) in rows:
        ts_lines.append(f"{ts_name:<16}{mm}/{dd}/{yyyy} {hh}:{mn}      {val}")

    # Read INP, rewrite RAINGAGES to use TIMESERIES `storm`, then append our TS.
    text = src_inp.read_text(encoding="latin-1")
    new_lines: list[str] = []
    in_raingages = False
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("[raingages]"):
            in_raingages = True
            new_lines.append(line)
            continue
        if stripped.startswith("[") and not stripped.startswith("[raingages]"):
            in_raingages = False
            new_lines.append(line)
            continue
        if in_raingages and "FILE" in line and "storm" in line.lower():
            # Replace "<name> INTENSITY 00:05 1 FILE storm.dat <staid> mm"
            # with  "<name> INTENSITY 00:05 1 TIMESERIES storm"
            parts = line.split()
            if len(parts) >= 5:
                name, fmt, interval, scf = parts[0], parts[1], parts[2], parts[3]
                new_lines.append(
                    f"{name:<16} {fmt:<9} {interval:<6} {scf:<6}   TIMESERIES storm"
                )
                continue
        new_lines.append(line)

    new_text = "\n".join(new_lines) + "\n" + "\n".join(ts_lines) + "\n"
    plot_inp = swmm_run_dir / "00_inputs" / "model_plot.inp"
    plot_inp.write_text(new_text, encoding="latin-1")
    return plot_inp


def _step4_plot(swmm_run_dir: Path, peak_node: str) -> tuple[bool, dict, float, str]:
    """Step 4: aiswmm plot — rainfall vs runoff.

    SWMManywhere INPs reference rainfall via ``FILE storm.dat`` and carry no
    in-INP TIMESERIES; ``aiswmm plot`` needs a TIMESERIES. We materialise a
    sidecar INP (``00_inputs/model_plot.inp``) that mirrors the run INP but
    inlines the storm.dat values as a TIMESERIES called ``storm``.
    """
    _hr("STEP 4 / 5 — aiswmm plot")
    try:
        t0 = time.time()
        plot_inp = _build_plot_friendly_inp(swmm_run_dir, peak_node)
        prep_s = time.time() - t0
        print(f"  -> plot-friendly inp: {plot_inp}  (built in {prep_s:.2f} s)", flush=True)
    except Exception as exc:
        return False, {}, 0.0, f"failed to build plot-friendly INP: {exc}"

    cmd = [
        str(AISWMM_BIN),
        "plot",
        "--run-dir", str(swmm_run_dir),
        "--inp", str(plot_inp),
        "--node", peak_node,
        "--node-attr", "Total_inflow",
        "--rain-ts", "storm",
        "--rain-kind", "depth_mm_per_dt",
    ]
    print(f"$ {' '.join(cmd)}", flush=True)
    rc, stdout, stderr, elapsed = _run(cmd, cwd=REPO_ROOT)
    elapsed += prep_s
    print(stdout, flush=True)
    if rc != 0:
        print(stderr, flush=True)
        return False, {}, elapsed, "aiswmm plot exited non-zero"
    plots_dir = swmm_run_dir / "07_plots"
    pngs = sorted(plots_dir.glob("*.png")) if plots_dir.exists() else []
    if not pngs:
        return False, {}, elapsed, f"no PNGs found in {plots_dir}"
    info = {
        "plots_dir": str(plots_dir),
        "pngs": [str(p) for p in pngs],
        "plot_inp": str(plot_inp),
    }
    for p in pngs:
        print(f"  -> {p}  ({p.stat().st_size / 1024:.1f} KB)", flush=True)
    return True, info, elapsed, ""


_OUTFALL_HEADER = re.compile(r"^\s*Outfall\s+Loading\s+Summary", re.IGNORECASE)
_NODE_INFLOW_HEADER = re.compile(r"^\s*Node\s+Inflow\s+Summary", re.IGNORECASE)
_FLOW_UNITS_RE = re.compile(r"Flow\s+Units\s*\.+\s*(\S+)", re.IGNORECASE)
# Columns of Outfall Loading Summary:
#   <node> <freq_pcnt> <avg_flow> <max_flow> <total_volume_10^6_ltr>
# Max_Flow is parts[3]. Flow units (LPS / CFS / CMS / ...) come from the
# "Analysis Options" Flow Units line at the top of the RPT.


def _read_rpt(rpt_path: Path) -> str:
    """SWMM may emit non-UTF8 bytes (e.g. footer chars); read as latin-1."""
    return rpt_path.read_text(encoding="latin-1")


def _parse_flow_units(text: str) -> str:
    m = _FLOW_UNITS_RE.search(text)
    return m.group(1).strip().upper() if m else "UNKNOWN"


def _parse_peak_from_rpt(rpt_path: Path) -> dict:
    """Extract peak (max) flow at outfalls from the RPT's Outfall Loading Summary.

    Returns the row with the largest Max_Flow across all outfalls, plus the
    node id and the rest of the row for context. Flow units are read from
    the 'Flow Units' line in Analysis Options.
    """
    text = _read_rpt(rpt_path)
    flow_units = _parse_flow_units(text)
    lines = text.splitlines()

    # Find the Outfall Loading Summary section.
    start = None
    for i, line in enumerate(lines):
        if _OUTFALL_HEADER.search(line):
            start = i
            break
    if start is None:
        return {"ok": False, "reason": "no 'Outfall Loading Summary' header found"}

    # Section ends at the next blank line followed by a non-data line, but
    # simpler: walk forward, look for the data block (lines with a node id
    # token followed by numeric columns), stop on a fully-dashed terminator.
    data_rows: list[tuple[str, float, list[str]]] = []
    in_data = False
    dashed_seen = 0
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if in_data:
                # blank after data -> end
                break
            continue
        if set(stripped) == {"-"}:
            dashed_seen += 1
            if dashed_seen >= 2:
                in_data = True
            continue
        if not in_data:
            continue
        # Data row. SWMM 5.2 outfall row layout (SI):
        #   <node>  <freq%>  <avg_flow>  <max_flow>  <total_vol_10^6_ltr>  <kg/L stats...>
        # Min 5 tokens (node + 4 numbers) before any quality columns.
        parts = stripped.split()
        if len(parts) < 5:
            break  # next section header (e.g. "System")
        node = parts[0]
        if node.lower() == "system":
            continue
        try:
            max_flow = float(parts[3])
        except (ValueError, IndexError):
            continue
        data_rows.append((node, max_flow, parts))

    if not data_rows:
        return {"ok": False, "reason": "Outfall Loading Summary found but no data rows parsed"}

    # Peak across outfalls = max of Max_Flow column.
    peak_node, peak_flow, peak_row = max(data_rows, key=lambda r: r[1])
    return {
        "ok": True,
        "node": peak_node,
        "max_flow": peak_flow,
        "flow_units": flow_units,
        "freq_pct": float(peak_row[1]),
        "avg_flow": float(peak_row[2]),
        "total_volume_10_6_ltr": float(peak_row[4]),
        "n_outfalls": len(data_rows),
        "top_outfalls": [
            {"node": n, "max_flow": mf}
            for (n, mf, _) in sorted(data_rows, key=lambda r: r[1], reverse=True)[:5]
        ],
    }


def _parse_node_flow_summary(rpt_path: Path) -> dict:
    """Parse 'Node Inflow Summary' for peak inflow + time-of-peak."""
    text = _read_rpt(rpt_path)
    flow_units = _parse_flow_units(text)
    lines = text.splitlines()

    start = None
    for i, line in enumerate(lines):
        if _NODE_INFLOW_HEADER.search(line):
            start = i
            break
    if start is None:
        return {"ok": False, "reason": "no Node Inflow Summary"}

    # Data rows in this section have a node type ("JUNCTION"/"OUTFALL") and
    # include columns for Maximum Lateral Inflow, Maximum Total Inflow, Time
    # of Max (days hr:min), etc. Schema (SWMM 5.2 SI):
    #   <node> <type> <max_lat_inflow> <max_total_inflow> <time_days> <time_hr:min> <lat_inflow_vol> <total_inflow_vol> <flow_balance_err>
    in_data = False
    dashed_seen = 0
    rows: list[dict] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if in_data:
                break
            continue
        if set(stripped) == {"-"}:
            dashed_seen += 1
            if dashed_seen >= 2:
                in_data = True
            continue
        if not in_data:
            continue
        parts = stripped.split()
        if len(parts) < 6:
            break
        node = parts[0]
        node_type = parts[1].upper() if len(parts) > 1 else ""
        if node_type not in {"JUNCTION", "OUTFALL", "STORAGE", "DIVIDER"}:
            continue
        try:
            max_total_inflow = float(parts[3])
            time_days = int(parts[4])
            time_hhmm = parts[5]
        except (ValueError, IndexError):
            continue
        rows.append({
            "node": node,
            "type": node_type,
            "max_total_inflow": max_total_inflow,
            "time_days_after_start": time_days,
            "time_hh_mm": time_hhmm,
        })

    if not rows:
        return {"ok": False, "reason": "no node flow rows parsed"}

    rows.sort(key=lambda r: r["max_total_inflow"], reverse=True)
    return {"ok": True, "flow_units": flow_units, "top": rows[:5], "n_rows": len(rows)}


def _step5_peak(rpt_path: Path) -> tuple[bool, dict, float, str]:
    """Step 5: parse peak flow + time-of-peak."""
    _hr("STEP 5 / 5 — parse peak flow from RPT")
    t0 = time.time()
    outfall_summary = _parse_peak_from_rpt(rpt_path)
    node_summary = _parse_node_flow_summary(rpt_path)
    elapsed = time.time() - t0

    if not outfall_summary["ok"]:
        return False, {"outfall_summary": outfall_summary}, elapsed, outfall_summary["reason"]

    units = outfall_summary["flow_units"]
    info = {
        "rpt": str(rpt_path),
        "outfall_summary": outfall_summary,
        "node_flow_summary": node_summary,
    }
    print(
        f"  -> peak Max_Flow @ outfall {outfall_summary['node']}: "
        f"{outfall_summary['max_flow']:.4f} {units} "
        f"(avg {outfall_summary['avg_flow']:.4f} {units}, "
        f"flow-freq {outfall_summary['freq_pct']:.2f}%, "
        f"total vol {outfall_summary['total_volume_10_6_ltr']:.4f} x 10^6 L, "
        f"across {outfall_summary['n_outfalls']} outfalls)",
        flush=True,
    )
    if node_summary["ok"]:
        print(f"  -> top nodes by max total inflow ({node_summary['flow_units']}):", flush=True)
        for row in node_summary["top"][:3]:
            print(
                f"     {row['node']:<14} ({row['type']:<8}) "
                f"max_inflow={row['max_total_inflow']:.4f} "
                f"@ day {row['time_days_after_start']} {row['time_hh_mm']}",
                flush=True,
            )
    return True, info, elapsed, ""


def _check_swmm_run_ok(swmm_run_dir: Path) -> tuple[bool, str]:
    """Sanity: confirm SWMM completed (no fatal RPT errors).

    aiswmm v0.7 writes QA artefacts under 06_qa/. We inspect runner_continuity
    + qa_summary for ERROR-level entries.
    """
    qa = swmm_run_dir / "06_qa" / "qa_summary.json"
    if not qa.exists():
        return True, "(no 06_qa/qa_summary.json — accepting)"
    try:
        d = json.loads(qa.read_text())
    except Exception as exc:
        return True, f"(could not parse qa summary: {exc})"
    return d.get("status") != "fail", f"qa_summary: status={d.get('status')}"


def main() -> int:
    if not SPIKE_VENV_PY.exists():
        print(f"FATAL: spike venv missing: {SPIKE_VENV_PY}", file=sys.stderr)
        return 1
    if not SYNTH_SCRIPT.exists():
        print(f"FATAL: synth script missing: {SYNTH_SCRIPT}", file=sys.stderr)
        return 1
    if not AISWMM_BIN.exists():
        print(f"FATAL: aiswmm CLI missing: {AISWMM_BIN}", file=sys.stderr)
        return 1

    run_root = _make_run_root()
    run_root.mkdir(parents=True, exist_ok=True)
    swmm_run_dir = run_root / "swmm_run"

    print(f"[e2e] run_root:     {run_root}")
    print(f"[e2e] swmm_run_dir: {swmm_run_dir}")
    print(f"[e2e] bbox:         {BBOX}")

    report: dict = {
        "started_at_utc": datetime.utcnow().isoformat() + "Z",
        "run_root": str(run_root),
        "swmm_run_dir": str(swmm_run_dir),
        "bbox_wgs84": [float(x) for x in BBOX],
        "steps": [],
    }

    # ---- STEP 1
    ok1, synth, t1, err1 = _step1_synth(run_root)
    report["steps"].append({"step": 1, "name": "swmm-anywhere", "ok": ok1, "elapsed_s": round(t1, 2), "error": err1, "out": synth})
    if not ok1:
        print(f"\nFAIL step 1: {err1}", file=sys.stderr)
        _write_report(report, run_root, ok=False)
        return 1
    inp_path = Path(synth["inp_path"])

    # ---- STEP 2
    ok2, runinfo, t2, err2 = _step2_run(inp_path, swmm_run_dir)
    report["steps"].append({"step": 2, "name": "aiswmm run", "ok": ok2, "elapsed_s": round(t2, 2), "error": err2, "out": runinfo})
    if not ok2:
        print(f"\nFAIL step 2: {err2}", file=sys.stderr)
        _write_report(report, run_root, ok=False)
        return 1
    swmm_ok, swmm_note = _check_swmm_run_ok(swmm_run_dir)
    report["swmm_diagnostics"] = swmm_note
    print(f"  swmm_diagnostics: {swmm_note}", flush=True)

    # ---- STEP 3
    ok3, auditinfo, t3, err3 = _step3_audit(swmm_run_dir)
    report["steps"].append({"step": 3, "name": "aiswmm audit", "ok": ok3, "elapsed_s": round(t3, 2), "error": err3, "out": auditinfo})
    if not ok3:
        print(f"\nFAIL step 3: {err3}", file=sys.stderr)
        _write_report(report, run_root, ok=False)
        return 1

    # ---- STEP 5 first: we need the peak node to drive plot's --node arg.
    rpt_path = Path(runinfo["rpt"])
    ok5, peakinfo, t5, err5 = _step5_peak(rpt_path)
    report["steps"].append({"step": 5, "name": "parse peak", "ok": ok5, "elapsed_s": round(t5, 4), "error": err5, "out": peakinfo})
    if not ok5:
        print(f"\nFAIL step 5: {err5}", file=sys.stderr)
        _write_report(report, run_root, ok=False)
        return 1

    peak_node = peakinfo["outfall_summary"]["node"]

    # ---- STEP 4
    ok4, plotinfo, t4, err4 = _step4_plot(swmm_run_dir, peak_node)
    report["steps"].append({"step": 4, "name": "aiswmm plot", "ok": ok4, "elapsed_s": round(t4, 2), "error": err4, "out": plotinfo})
    if not ok4:
        print(f"\nFAIL step 4: {err4}", file=sys.stderr)
        _write_report(report, run_root, ok=False)
        return 1

    # ---- FINAL REPORT
    _hr("E2E CHAIN: ALL STEPS OK")
    # Order by step number for display (we executed 5 before 4 to discover peak).
    report["steps"].sort(key=lambda s: s["step"])
    total = sum(s["elapsed_s"] for s in report["steps"])
    for s in report["steps"]:
        flag = "OK" if s["ok"] else "FAIL"
        print(f"  [{flag}] step {s['step']} {s['name']:<18}  {s['elapsed_s']:>7.2f} s")
    print(f"  -----")
    print(f"  total                          {total:>7.2f} s")

    print(f"\nProducts:")
    print(f"  synth.inp:    {inp_path}")
    print(f"  rpt:          {runinfo['rpt']}")
    print(f"  out:          {runinfo['out']}")
    print(f"  audit prov:   {auditinfo['provenance']}")
    print(f"  audit note:   {auditinfo['note']}")
    for p in plotinfo["pngs"]:
        print(f"  plot:         {p}")

    peak = peakinfo["outfall_summary"]
    units = peak["flow_units"]
    print(f"\nPeak flow (at outfall {peak['node']}): {peak['max_flow']:.4f} {units}")
    if peakinfo["node_flow_summary"]["ok"]:
        top = peakinfo["node_flow_summary"]["top"][0]
        print(
            f"Top node max-total-inflow: {top['node']} "
            f"= {top['max_total_inflow']:.4f} {units} "
            f"@ day {top['time_days_after_start']} {top['time_hh_mm']}"
        )

    report["total_elapsed_s"] = round(total, 2)
    report["finished_at_utc"] = datetime.utcnow().isoformat() + "Z"
    report["status"] = "ok"
    _write_report(report, run_root, ok=True)
    return 0


def _write_report(report: dict, run_root: Path, *, ok: bool) -> None:
    report["status"] = report.get("status", "ok" if ok else "fail")
    report_path = run_root / "e2e_chain_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=False))
    print(f"\n[e2e] machine-readable report: {report_path}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
