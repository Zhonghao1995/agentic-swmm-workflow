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
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# Reproducibility is pinned to this swmm5 build (Dockerfile SWMM_REF=v5.2.4;
# docs/byte-identical-reproducibility.md). A mismatch is advisory only — it
# never fails a run, it just warns the version-identical guarantee is off.
EXPECTED_SWMM_VERSION = "5.2.4"

# Default ceiling for a single swmm5 invocation. A pathological INP must not
# hang the caller (interactive session / MCP pool) forever.
DEFAULT_SWMM_TIMEOUT_S = 600.0

# Sentinel return code for a timed-out swmm5 run (mirrors GNU ``timeout``).
SWMM_TIMEOUT_RC = 124

# Mirror of ``agentic_swmm.agent.honesty._RPT_ERROR_RE`` — kept inline so this
# skill script stays import-free / portable. The ``\d+:`` after ERROR avoids
# false-positives on the narrative word "error" in continuity summaries. Keep
# the two patterns in sync.
_RPT_ERROR_RE = re.compile(r"^\s*(ERROR\s+\d+:.*)$")


def resolve_swmm5() -> str:
    """Locate the swmm5 executable.

    The one-line installer drops a built (macOS/Linux) or downloaded (Windows)
    SWMM 5.2.4 engine at ``$AISWMM_CONFIG_DIR/swmm/`` (default ``~/.aiswmm/swmm``).
    We prefer that fixed location so a run works regardless of how the user's
    shell PATH is configured, then fall back to PATH for users who installed
    swmm5 themselves. ``AISWMM_SWMM5`` is an explicit override (also used by
    tests). Returns the bare name ``"swmm5"`` as a last resort so the subprocess
    call fails with a clear ``FileNotFoundError`` rather than this resolver
    silently guessing.
    """
    override = os.environ.get("AISWMM_SWMM5")
    if override and Path(override).exists():
        return override
    config_dir = Path(os.environ.get("AISWMM_CONFIG_DIR") or (Path.home() / ".aiswmm"))
    names = ("swmm5", "swmm5.exe", "runswmm", "runswmm.exe")
    for name in names:
        candidate = config_dir / "swmm" / name
        if candidate.exists():
            return str(candidate)
    for name in names:
        hit = shutil.which(name)
        if hit:
            return hit
    return "swmm5"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def scan_rpt_for_errors(rpt_path: Path) -> list[str]:
    """Return the verbatim ``ERROR <n>:`` lines in a SWMM ``.rpt``.

    Empty list when the file is missing/unreadable or carries no canonical
    error lines. swmm5 frequently exits 0 while writing these — they are the
    real failure signal, not the process return code.
    """
    try:
        if not rpt_path.exists():
            return []
        text = rpt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    matches: list[str] = []
    for raw in text.splitlines():
        m = _RPT_ERROR_RE.match(raw)
        if m:
            matches.append(m.group(1).rstrip())
    return matches


def check_swmm_version(detected: str | None) -> tuple[bool, str | None]:
    """Advisory version check. Returns ``(version_ok, warning_or_None)``."""
    if detected == EXPECTED_SWMM_VERSION:
        return True, None
    if detected is None:
        return False, (
            f"could not detect swmm5 version; byte-identical reproducibility "
            f"is pinned to {EXPECTED_SWMM_VERSION}"
        )
    return False, (
        f"swmm5 version {detected} != pinned {EXPECTED_SWMM_VERSION}; "
        f"byte-identical reproducibility is not guaranteed"
    )


def run_swmm(
    inp: Path,
    rpt: Path,
    out: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout: float = DEFAULT_SWMM_TIMEOUT_S,
) -> int:
    try:
        p = subprocess.run(
            [resolve_swmm5(), str(inp), str(rpt), str(out)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(
            exc.stdout or "" if isinstance(exc.stdout, str) else "",
            encoding='utf-8',
            errors='ignore',
        )
        stderr_path.write_text(
            f"swmm5 timed out after {timeout}s\n", encoding='utf-8', errors='ignore'
        )
        return SWMM_TIMEOUT_RC
    stdout_path.write_text(p.stdout, encoding='utf-8', errors='ignore')
    stderr_path.write_text(p.stderr, encoding='utf-8', errors='ignore')
    return p.returncode


AUTO_NODE = "auto"


def _outfall_loading_rows(rpt: Path) -> list[tuple[str, float, float, float, float]]:
    """(node, flow_freq, avg_flow, max_flow, total_volume) per outfall row."""
    try:
        lines = rpt.read_text(errors="ignore").splitlines()
    except OSError:
        return []
    start = next((i for i, line in enumerate(lines) if line.strip().startswith("Outfall Loading Summary")), None)
    if start is None:
        return []
    body = lines[start + 1:]
    if body and body[0].strip().startswith("***"):
        body = body[1:]  # the title box's closing row
    rows: list[tuple[str, float, float, float, float]] = []
    rules = 0
    for line in body:
        s = line.strip()
        if s.startswith("***"):
            break  # next section: this one printed no table
        if s and set(s) == {"-"}:
            rules += 1
            if rules >= 3:
                break  # the rule before the System totals row
            continue
        if rules != 2 or not s or s.startswith("System"):
            continue
        tok = s.split()
        if len(tok) < 5:
            continue
        try:
            rows.append((tok[0], float(tok[1]), float(tok[2]), float(tok[3]), float(tok[4])))
        except ValueError:
            continue
    return rows


def dominant_outfall_from_rpt(rpt: Path) -> str | None:
    """The outfall carrying the largest total volume (ties: max flow, then avg)."""
    rows = _outfall_loading_rows(rpt)
    if not rows:
        return None
    return max(rows, key=lambda r: (r[4], r[3], r[2]))[0]


def first_outfall_from_inp(inp: Path) -> str | None:
    try:
        text = inp.read_text(errors="ignore")
    except OSError:
        return None
    section = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s.upper()
            continue
        if section == "[OUTFALLS]":
            return s.split()[0]
    return None


def resolve_report_node(requested: str, rpt: Path, inp: Path | None) -> tuple[str, str]:
    """Pick the node the peak metric reports on, and say why.

    ``auto`` (the CLI, MCP and agent default since 2026-09-02) resolves
    AFTER the run to the outfall carrying the largest total volume in the
    Outfall Loading Summary. The previous default, the INP's first
    outfall, picked a dry or trivial outfall on real multi-outfall
    networks (live finding F-02: "Peak: 0.0 CMS at DOF007052" while
    OUT_DMH002395 carried 2.7 ML), and that node also became the
    hydrograph in the client report.
    """
    if requested and requested != AUTO_NODE:
        return requested, "requested"
    node = dominant_outfall_from_rpt(rpt)
    if node:
        return node, "outfall carrying the largest total volume (Outfall Loading Summary)"
    if inp is not None:
        node = first_outfall_from_inp(inp)
        if node:
            return node, "first [OUTFALLS] entry (no outfall loading rows in the report)"
    return "O1", "fallback O1 (no outfalls found)"


FLOW_UNITS_RE = re.compile(r"Flow Units\s*\.*\s*([A-Za-z]+)")


def flow_units_from_rpt_text(text: str) -> str | None:
    """The report's own flow unit (CMS, LPS, CFS, ...), or None when absent.

    Live finding F-52 (2026-09-02): the manifest carried no units, so every
    consumer assumed CMS and a Seattle model running in LPS was reported a
    thousand times too large.
    """
    match = FLOW_UNITS_RE.search(text)
    return match.group(1).upper() if match else None


def parse_peak_from_rpt(rpt: Path, node: str) -> dict:
    text = rpt.read_text(errors='ignore')
    lines = text.splitlines()
    units = flow_units_from_rpt_text(text)

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
            "units": units,
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
        return {"node": node, "peak": float(m.group(3)), "units": units, "time_hhmm": None, "source": "Outfall Loading Summary"}

    return {"node": node, "peak": None, "units": units, "time_hhmm": None, "source": None}


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
        block: list[str] = []
        for s in lines[start_idx : min(len(lines), start_idx + max_lines)]:
            block.append(s)
            if "Continuity Error (%)" in s:
                break
        return block

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
        p = subprocess.run([resolve_swmm5(), "--version"], capture_output=True, text=True)
        # swmm5 may not support --version; fall back to parsing help output
        txt = (p.stdout + "\n" + p.stderr).strip()
        m = re.search(r"(\d+\.\d+\.\d+)", txt)
        return m.group(1) if m else None
    except Exception:
        return None


def _parse_memories_applied(raw: str | None) -> list[str]:
    """Parse the ``--memories-applied`` JSON string into a list of ids.

    Accepts a JSON array string (e.g. ``'["cm-abc", "pm-xyz"]'``) or
    ``None`` / empty string (returns ``[]``).  Tolerant: any parse error
    or non-list result also returns ``[]`` so a bad arg never aborts a
    run.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
    except (ValueError, TypeError):
        pass
    return []


def _safe_output_name(name: str | None, default: str) -> str:
    """Constrain a caller-supplied run-output name to a bare filename.

    An absolute path or ``..`` segment would let the MCP caller write outside
    ``run_dir`` (review P2-1). Only a plain filename is allowed.
    """
    candidate = (name or default).strip()
    if candidate in {"", ".", ".."} or candidate != Path(candidate).name:
        raise ValueError(f"output name must be a bare filename, got {name!r}")
    return candidate


def cmd_run(args):
    inp = args.inp.resolve()
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    rpt = run_dir / _safe_output_name(args.rpt_name, "model.rpt")
    out = run_dir / _safe_output_name(args.out_name, "model.out")
    # The engine's console output is provenance, not a result. It used to sit
    # beside model.rpt, so a reader opening the runner stage met a wall of
    # "hour: 1 [][][][]" progress bars next to the report they wanted. The code
    # already classified these as CLI-wrapper noise (see reporting.py); this
    # puts them where that classification says they belong. Their paths travel
    # in the manifest, so readers follow the manifest rather than a hardcoded
    # location.
    engine_dir = run_dir / "_engine"
    engine_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = engine_dir / "stdout.txt"
    stderr_path = engine_dir / "stderr.txt"

    timeout = getattr(args, "timeout", DEFAULT_SWMM_TIMEOUT_S)
    rc = run_swmm(inp, rpt, out, stdout_path, stderr_path, timeout=timeout)

    # Honesty verdict: swmm5 exits 0 even when it writes ``ERROR <n>:`` lines,
    # so a clean exit is necessary but not sufficient. ``run_ok`` is the
    # structured source of truth both the CLI and agent paths read.
    solver_errors = scan_rpt_for_errors(rpt)
    run_ok = rc == 0 and not solver_errors

    detected_version = get_swmm5_version()
    version_ok, version_warning = check_swmm_version(detected_version)

    report_node, node_rule = resolve_report_node(args.node, rpt, inp)
    peak = parse_peak_from_rpt(rpt, report_node)
    cont = parse_continuity_blocks(rpt.read_text(errors='ignore'))
    node_selection = {"requested": args.node, "resolved": report_node, "rule": node_rule}

    # ``memories_applied`` records which modeling-memory entry ids were
    # programmatically applied to this run's inputs (e.g. calibrated priors
    # from cross-watershed transfer).  Always present — empty list means no
    # memory was applied; the field must never be absent so the audit pipeline
    # can rely on it unconditionally.  Ids are passed via ``--memories-applied``
    # as a JSON array; the default is an empty list.
    memories_applied = _parse_memories_applied(getattr(args, "memories_applied", None))

    manifest = {
        "manifest_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "swmm5": {
            "cmd": "swmm5",
            "version": detected_version,
            "version_ok": version_ok,
            "version_warning": version_warning,
        },
        "inp": str(inp),
        "inp_sha256": sha256_file(inp),
        "files": {"rpt": str(rpt), "out": str(out), "stdout": str(stdout_path), "stderr": str(stderr_path)},
        "metrics": {"peak": peak, "continuity": cont, "flow_units": peak.get("units")},
        "node_selection": node_selection,
        "return_code": rc,
        "run_ok": run_ok,
        "solver_errors": solver_errors,
        "memories_applied": memories_applied,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    # stdout stays pure JSON (``aiswmm run > result.json`` / MCP parse depend
    # on it); failure detail goes to stderr.
    print(json.dumps(manifest, indent=2))

    # ``--gate`` is passed by the swmm-runner MCP server (the agent path),
    # which only rejects on a non-zero exit. The CLI verb does NOT pass it:
    # it keeps the legacy exit-0 here and runs its own honesty scan, so the
    # pure-JSON-stdout contract in test_run_swmm_error_stream_separation is
    # untouched.
    if getattr(args, "gate", False) and not run_ok:
        if solver_errors:
            detail = solver_errors[0]
        elif rc == SWMM_TIMEOUT_RC:
            detail = f"swmm5 timed out after {timeout}s"
        else:
            detail = f"swmm5 exited with return code {rc}"
        print(f"swmm_run failed: {detail}", file=sys.stderr)
        sys.exit(1)


def cmd_peak(args):
    rpt = args.rpt.resolve()
    node, _rule = resolve_report_node(args.node, rpt, None)
    print(json.dumps(parse_peak_from_rpt(rpt, node), indent=2))


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
    ap_run.add_argument(
        '--node', default=AUTO_NODE,
        help="Node the peak metric reports on. Default 'auto': the outfall carrying "
             "the largest total volume in the run (else the first [OUTFALLS] entry).",
    )
    ap_run.add_argument('--rpt-name', default=None)
    ap_run.add_argument('--out-name', default=None)
    ap_run.add_argument(
        '--timeout', type=float, default=DEFAULT_SWMM_TIMEOUT_S,
        help='Max seconds for the swmm5 subprocess before it is killed.',
    )
    ap_run.add_argument(
        '--gate', action='store_true',
        help='Exit non-zero when the run is not ok (solver ERROR lines, '
             'non-zero return code, or timeout). The MCP server passes this '
             'so the agent path surfaces failures; the CLI verb does not.',
    )
    ap_run.add_argument(
        '--memories-applied',
        default=None,
        dest='memories_applied',
        help=(
            'JSON array of memory entry ids that were programmatically applied '
            'to this run\'s inputs (e.g. \'["cm-abc", "pm-xyz"]\'). '
            'Written verbatim into manifest.json under "memories_applied". '
            'Omitting this arg records an empty list — the field is always '
            'present in the manifest.'
        ),
    )
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
