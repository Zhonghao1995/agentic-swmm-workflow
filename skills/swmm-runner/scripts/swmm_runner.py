#!/usr/bin/env python3
"""SWMM runner + metrics extraction.

This script is intentionally generic (not Todcreek-specific).
It wraps `swmm5` and parses SWMM's own `.rpt` for peak + continuity.

Subcommands:
- run: run swmm5 and emit manifest.json
- peak: parse peak flow/time from rpt
- continuity: parse continuity blocks from rpt
- compare: compare two rpt files (GUI vs CLI)

"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def run_swmm(inp: Path, rpt: Path, out: Path, stdout_path: Path, stderr_path: Path) -> int:
    p = subprocess.run(["swmm5", str(inp), str(rpt), str(out)], capture_output=True, text=True)
    stdout_path.write_text(p.stdout, encoding='utf-8', errors='ignore')
    stderr_path.write_text(p.stderr, encoding='utf-8', errors='ignore')
    return p.returncode


def parse_peak_from_rpt(rpt: Path, node: str) -> dict:
    text = rpt.read_text(errors='ignore')
    lines = text.splitlines()

    def extract_section(title: str) -> str:
        start_idx = None
        for i, line in enumerate(lines):
            if title.lower() in line.lower():
                start_idx = i + 1
                break
        if start_idx is None:
            return ""

        block: list[str] = []
        for line in lines[start_idx:]:
            if line.strip().startswith("*****") and block:
                break
            block.append(line)
        return "\n".join(block)

    # Prefer Node Inflow Summary because it includes time of maximum total inflow.
    inflow_block = extract_section("Node Inflow Summary")
    mt = re.search(
        rf"^\s*{re.escape(node)}\s+\S+\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s+\d+\s+(\d\d):(\d\d)",
        inflow_block,
        re.M,
    )
    if mt:
        return {
            "node": node,
            "peak": float(mt.group(2)),
            "time_hhmm": f"{mt.group(3)}:{mt.group(4)}",
            "source": "Node Inflow Summary",
        }

    # Fallback to Outfall Loading Summary for outfalls when no timed inflow entry exists.
    outfall_block = extract_section("Outfall Loading Summary")
    m = re.search(
        rf"^\s*{re.escape(node)}\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s*$",
        outfall_block,
        re.M,
    )
    if m:
        return {"node": node, "peak": float(m.group(3)), "time_hhmm": None, "source": "Outfall Loading Summary"}

    return {"node": node, "peak": None, "time_hhmm": None, "source": None}


def parse_continuity_blocks(text: str) -> dict:
    out: dict = {
        "runoff_quantity": {},
        "flow_routing": {},
        "continuity_error_percent": {"runoff_quantity": None, "flow_routing": None},
    }

    lines = text.splitlines()

    def find_section_idx(needle: str) -> int | None:
        for i, s in enumerate(lines):
            if needle.lower() in s.lower():
                return i
        return None

    def scan_table(start_idx: int, max_lines: int = 200) -> list[str]:
        return lines[start_idx : min(len(lines), start_idx + max_lines)]

    def parse_table(tbl_lines: list[str]) -> dict:
        d = {}
        for s in tbl_lines:
            m2 = re.search(r"^\s*([A-Za-z][A-Za-z0-9\s\-\(\)%/]+?)\.{2,}\s*([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s*$", s)
            if m2:
                label = re.sub(r"\s+", " ", m2.group(1)).strip(" .")
                d[label] = {"col1": float(m2.group(2)), "col2": float(m2.group(3))}
                continue
            m1 = re.search(r"^\s*(Continuity Error \(\%\))\s*\.{2,}\s*([-+]?\d+(?:\.\d+)?)\s*$", s)
            if m1:
                d[m1.group(1)] = float(m1.group(2))
        return d

    rq_i = find_section_idx("Runoff Quantity Continuity")
    if rq_i is not None:
        rq_tbl = parse_table(scan_table(rq_i))
        out["runoff_quantity"] = rq_tbl
        ce = rq_tbl.get("Continuity Error (%)")
        if isinstance(ce, (int, float)):
            out["continuity_error_percent"]["runoff_quantity"] = float(ce)

    fr_i = find_section_idx("Flow Routing Continuity")
    if fr_i is not None:
        fr_tbl = parse_table(scan_table(fr_i))
        out["flow_routing"] = fr_tbl
        ce = fr_tbl.get("Continuity Error (%)")
        if isinstance(ce, (int, float)):
            out["continuity_error_percent"]["flow_routing"] = float(ce)

    return out


def get_swmm5_version() -> str | None:
    try:
        p = subprocess.run(["swmm5", "--version"], capture_output=True, text=True)
        # swmm5 may not support --version; fall back to parsing help output
        txt = (p.stdout + "\n" + p.stderr).strip()
        m = re.search(r"(\d+\.\d+\.\d+)", txt)
        return m.group(1) if m else None
    except Exception:
        return None


def cmd_run(args):
    inp = args.inp.resolve()
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    rpt = run_dir / (args.rpt_name or "model.rpt")
    out = run_dir / (args.out_name or "model.out")
    stdout_path = run_dir / "stdout.txt"
    stderr_path = run_dir / "stderr.txt"

    rc = run_swmm(inp, rpt, out, stdout_path, stderr_path)

    peak = parse_peak_from_rpt(rpt, args.node)
    cont = parse_continuity_blocks(rpt.read_text(errors='ignore'))

    manifest = {
        "manifest_version": "1.0",
        "created_at": datetime.now().isoformat(),
        "swmm5": {
            "cmd": "swmm5",
            "version": get_swmm5_version(),
        },
        "inp": str(inp),
        "inp_sha256": sha256_file(inp),
        "files": {"rpt": str(rpt), "out": str(out), "stdout": str(stdout_path), "stderr": str(stderr_path)},
        "metrics": {"peak": peak, "continuity": cont},
        "return_code": rc,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    print(json.dumps(manifest, indent=2))


def cmd_peak(args):
    rpt = args.rpt.resolve()
    print(json.dumps(parse_peak_from_rpt(rpt, args.node), indent=2))


def cmd_continuity(args):
    rpt = args.rpt.resolve()
    print(json.dumps(parse_continuity_blocks(rpt.read_text(errors='ignore')), indent=2))


def cmd_compare(args):
    a = parse_continuity_blocks(Path(args.rpt).read_text(errors='ignore'))
    b = parse_continuity_blocks(Path(args.rpt2).read_text(errors='ignore'))
    out = {
        "a": args.rpt,
        "b": args.rpt2,
        "a_err": a.get("continuity_error_percent"),
        "b_err": b.get("continuity_error_percent"),
    }
    print(json.dumps(out, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    ap_run = sub.add_parser('run')
    ap_run.add_argument('--inp', type=Path, required=True)
    ap_run.add_argument('--run-dir', type=Path, required=True)
    ap_run.add_argument('--node', default='O1')
    ap_run.add_argument('--rpt-name', default=None)
    ap_run.add_argument('--out-name', default=None)
    ap_run.set_defaults(func=cmd_run)

    ap_peak = sub.add_parser('peak')
    ap_peak.add_argument('--rpt', type=Path, required=True)
    ap_peak.add_argument('--node', default='O1')
    ap_peak.set_defaults(func=cmd_peak)

    ap_c = sub.add_parser('continuity')
    ap_c.add_argument('--rpt', type=Path, required=True)
    ap_c.set_defaults(func=cmd_continuity)

    ap_cmp = sub.add_parser('compare')
    ap_cmp.add_argument('--rpt', required=True)
    ap_cmp.add_argument('--rpt2', required=True)
    ap_cmp.set_defaults(func=cmd_compare)

    args = ap.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
