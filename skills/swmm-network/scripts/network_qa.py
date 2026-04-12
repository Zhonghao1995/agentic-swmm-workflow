#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def save_json(path: str | Path, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def add_issue(issues: list[dict], severity: str, code: str, message: str, obj_id: str | None = None) -> None:
    rec = {"severity": severity, "code": code, "message": message}
    if obj_id is not None:
        rec["id"] = obj_id
    issues.append(rec)


def summarize(network: dict) -> dict:
    return {
        "junction_count": len(network.get("junctions", [])),
        "outfall_count": len(network.get("outfalls", [])),
        "conduit_count": len(network.get("conduits", [])),
        "total_conduit_length": float(sum(c.get("length", 0) for c in network.get("conduits", []))),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("network_json", type=Path)
    ap.add_argument("--report-json", default=None, type=Path)
    args = ap.parse_args()

    network = load_json(args.network_json)
    issues: list[dict] = []

    node_ids = [j["id"] for j in network.get("junctions", [])] + [o["id"] for o in network.get("outfalls", [])]
    counts = defaultdict(int)
    for nid in node_ids:
        counts[nid] += 1
    for cid in [c["id"] for c in network.get("conduits", [])]:
        counts[cid] += 1
    for obj_id, count in counts.items():
        if count > 1:
            add_issue(issues, "error", "duplicate_id", f"Duplicate ID detected: {obj_id}", obj_id)

    all_nodes = {j["id"]: j for j in network.get("junctions", [])}
    all_nodes.update({o["id"]: o for o in network.get("outfalls", [])})

    for j in network.get("junctions", []):
        if "coordinates" not in j or "x" not in j["coordinates"] or "y" not in j["coordinates"]:
            add_issue(issues, "error", "missing_coordinates", "Junction missing valid coordinates", j["id"])
    for o in network.get("outfalls", []):
        if "coordinates" not in o or "x" not in o["coordinates"] or "y" not in o["coordinates"]:
            add_issue(issues, "error", "missing_coordinates", "Outfall missing valid coordinates", o["id"])

    incoming = defaultdict(int)
    outgoing = defaultdict(int)
    graph = defaultdict(list)
    outfalls = {o["id"] for o in network.get("outfalls", [])}

    for c in network.get("conduits", []):
        cid = c["id"]
        if c.get("from_node") not in all_nodes:
            add_issue(issues, "error", "missing_from_node", f"Conduit from_node not found: {c.get('from_node')}", cid)
        if c.get("to_node") not in all_nodes:
            add_issue(issues, "error", "missing_to_node", f"Conduit to_node not found: {c.get('to_node')}", cid)
        if c.get("length", 0) <= 0:
            add_issue(issues, "error", "non_positive_length", "Conduit length must be > 0", cid)
        if c.get("roughness", 0) <= 0:
            add_issue(issues, "error", "non_positive_roughness", "Conduit roughness must be > 0", cid)
        if not c.get("xsection"):
            add_issue(issues, "error", "missing_xsection", "Conduit missing xsection", cid)
        else:
            xs = c["xsection"]
            if xs.get("geom1", 0) <= 0:
                add_issue(issues, "error", "invalid_xsection", "Conduit xsection geom1 must be > 0", cid)
        fn = c.get("from_node")
        tn = c.get("to_node")
        if fn in all_nodes and tn in all_nodes:
            outgoing[fn] += 1
            incoming[tn] += 1
            graph[fn].append(tn)

    for j in network.get("junctions", []):
        jid = j["id"]
        if incoming[jid] == 0 and outgoing[jid] == 0:
            add_issue(issues, "warning", "isolated_node", "Junction is isolated", jid)

    for start in [j["id"] for j in network.get("junctions", [])]:
        q = deque([start])
        seen = {start}
        reaches_outfall = False
        while q:
            cur = q.popleft()
            if cur in outfalls:
                reaches_outfall = True
                break
            for nxt in graph[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        if not reaches_outfall:
            add_issue(issues, "warning", "no_outfall_path", "No downstream path from junction to any outfall", start)

    report = {
        "ok": not any(x["severity"] == "error" for x in issues),
        "summary": summarize(network),
        "issue_count": len(issues),
        "issues": issues,
    }
    if args.report_json:
        save_json(args.report_json, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
