#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag_memory_lib import build_corpus, load_corpus, load_embedding_vectors, render_context_pack, retrieve


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retrieve relevant Agentic SWMM memory for a query.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--memory-dir", type=Path, default=Path("memory/modeling-memory"))
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--index-dir", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--project", default=None)
    parser.add_argument("--retriever", choices=("keyword", "hybrid"), default="keyword")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entries = []
    embedding_vectors = []
    if args.index_dir:
        entries = load_corpus(args.index_dir / "corpus.jsonl")
        embedding_vectors = load_embedding_vectors(args.index_dir / "embedding_index.json")
    if not entries:
        entries = build_corpus(args.memory_dir, args.runs_dir, args.repo_root)
    matches = retrieve(
        entries,
        args.query,
        args.top_k,
        project=args.project,
        retriever=args.retriever,
        embedding_vectors=embedding_vectors,
    )
    if args.format == "markdown":
        print(render_context_pack(args.query, matches))
    else:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "generated_by": "swmm-rag-memory",
                    "query": args.query,
                    "retriever": args.retriever,
                    "project_filter": args.project,
                    "match_count": len(matches),
                    "matches": matches,
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
