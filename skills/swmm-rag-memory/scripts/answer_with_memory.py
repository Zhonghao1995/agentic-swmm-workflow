#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from rag_memory_lib import build_corpus, load_corpus, load_embedding_vectors, render_context_pack, retrieve, slugify


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an LLM-ready Agentic SWMM RAG context pack.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--memory-dir", type=Path, default=Path("memory/modeling-memory"))
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--index-dir", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--project", default=None)
    parser.add_argument("--retriever", choices=("keyword", "hybrid"), default="hybrid")
    parser.add_argument("--format", choices=("markdown",), default="markdown")
    parser.add_argument("--obsidian-dir", type=Path, default=None)
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
    context = render_context_pack(args.query, matches)
    if args.obsidian_dir:
        args.obsidian_dir.mkdir(parents=True, exist_ok=True)
        out_path = args.obsidian_dir / f"RAG Query - {slugify(args.query)}.md"
        out_path.write_text(context, encoding="utf-8")
        print(str(out_path))
    else:
        print(context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
