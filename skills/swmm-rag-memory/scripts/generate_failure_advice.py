#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag_memory_lib import (
    build_corpus,
    build_failure_advice_query,
    extract_run_problem,
    load_corpus,
    load_embedding_vectors,
    now_utc,
    read_run_evidence,
    render_failure_advice,
    retrieve,
    should_generate_failure_advice,
    suggested_checks,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate retrieval-grounded failure advice for an audited Agentic SWMM run.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--memory-dir", type=Path, default=Path("memory/modeling-memory"))
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--index-dir", type=Path, default=Path("memory/rag-memory"))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--retriever", choices=("keyword", "hybrid"), default="hybrid")
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    parser.add_argument("--force", action="store_true", help="Write advice even if trigger conditions are not met.")
    parser.add_argument("--no-write", action="store_true", help="Print JSON result without writing failure_advice files.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    run_dir = args.run_dir.resolve()
    evidence = read_run_evidence(run_dir)
    problem = extract_run_problem(evidence, run_dir, repo_root)
    should_trigger, trigger_reasons = should_generate_failure_advice(problem)

    if not should_trigger and not args.force:
        result = {
            "schema_version": "1.0",
            "generated_by": "swmm-rag-memory",
            "generated_at_utc": now_utc(),
            "run_id": problem.get("run_id"),
            "advice_written": False,
            "triggered": False,
            "trigger_reasons": [],
            "boundary": "No failure advice was generated because trigger conditions were not met.",
        }
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0

    entries = load_corpus(args.index_dir / "corpus.jsonl")
    embedding_vectors = load_embedding_vectors(args.index_dir / "embedding_index.json") if entries else []
    if not entries:
        entries = build_corpus(args.memory_dir, args.runs_dir, repo_root)
    query = build_failure_advice_query(problem)
    matches = retrieve(
        entries,
        query,
        args.top_k,
        project=problem.get("project_key"),
        retriever=args.retriever,
        embedding_vectors=embedding_vectors,
    )

    advice = {
        "schema_version": "1.0",
        "generated_by": "swmm-rag-memory",
        "generated_at_utc": now_utc(),
        "retrieval_grounded": True,
        "human_reviewed": False,
        "benchmark_verified": False,
        "advice_written": not args.no_write,
        "triggered": should_trigger or args.force,
        "trigger_reasons": trigger_reasons if should_trigger else ["forced"],
        "retriever": args.retriever,
        "query": query,
        "current_run_problem": problem,
        "retrieved_memory": matches,
        "suggested_next_checks": suggested_checks(problem, matches),
        "boundary": "Retrieval-grounded advice only. No model files, workflow code, or skill definitions were modified.",
    }

    if not args.no_write:
        out_json = args.out_json or (run_dir / "failure_advice.json")
        out_md = args.out_md or (run_dir / "failure_advice.md")
        write_json(out_json, advice)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(render_failure_advice(advice), encoding="utf-8")
        advice["failure_advice_json"] = str(out_json)
        advice["failure_advice_md"] = str(out_md)

    print(json.dumps(advice, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
