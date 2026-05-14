#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from rag_memory_lib import extract_run_problem, now_utc, read_json, read_run_evidence, relpath, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record a verified Agentic SWMM failure resolution memory.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--action-taken", required=True, help="Human-readable repair action.")
    parser.add_argument("--verification", action="append", default=[], help="Verification command or evidence. Repeatable.")
    parser.add_argument("--file-changed", action="append", default=[], help="Changed file path. Repeatable.")
    parser.add_argument("--retrieved-source", action="append", default=[], help="Retrieved memory source path used for the repair. Repeatable.")
    parser.add_argument("--human-reviewed", action="store_true")
    parser.add_argument("--benchmark-verified", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args()


def git_head(repo_root: Path) -> str | None:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def load_retrieved_memory(run_dir: Path, explicit_sources: list[str]) -> list[dict[str, Any]]:
    advice = read_json(run_dir / "failure_advice.json")
    memories: list[dict[str, Any]] = []
    if isinstance(advice, dict):
        for item in advice.get("retrieved_memory") or []:
            if isinstance(item, dict):
                memories.append(
                    {
                        "source_path": item.get("source_path"),
                        "run_id": item.get("run_id"),
                        "matched_terms": item.get("matched_terms", []),
                    }
                )
    for source in explicit_sources:
        if source and source not in {str(item.get("source_path")) for item in memories}:
            memories.append({"source_path": source, "run_id": None, "matched_terms": []})
    return memories


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    run_dir = args.run_dir.resolve()
    evidence = read_run_evidence(run_dir)
    problem = extract_run_problem(evidence, run_dir, repo_root)
    status = "verified" if args.human_reviewed and args.benchmark_verified else "draft"
    resolution = {
        "schema_version": "1.0",
        "generated_by": "swmm-rag-memory",
        "generated_at_utc": now_utc(),
        "run_id": problem.get("run_id"),
        "run_dir": relpath(run_dir, repo_root),
        "problem": {
            "failure_patterns": problem.get("failure_patterns", []),
            "diagnostics": problem.get("model_diagnostic_ids", []),
            "missing_evidence": problem.get("missing_evidence", []),
            "stderr_excerpt": problem.get("stderr_excerpt", ""),
        },
        "retrieved_memory_used": load_retrieved_memory(run_dir, args.retrieved_source),
        "resolution": {
            "action_taken": args.action_taken,
            "files_changed": args.file_changed,
            "verification": args.verification,
            "status": status,
            "source_commit": git_head(repo_root),
        },
        "retrieval_grounded": bool(load_retrieved_memory(run_dir, args.retrieved_source)),
        "human_reviewed": bool(args.human_reviewed),
        "benchmark_verified": bool(args.benchmark_verified),
        "boundary": "This records a repair for this workflow. It is not a universal SWMM modeling rule.",
    }
    out_path = args.out or (run_dir / "resolution_memory.json")
    write_json(out_path, resolution)
    print(json.dumps({"ok": True, "resolution_memory": str(out_path), "status": status}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
