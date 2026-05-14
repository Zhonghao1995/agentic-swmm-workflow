#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag_memory_lib import build_corpus, write_corpus


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an Agentic SWMM RAG memory corpus.")
    parser.add_argument("--memory-dir", type=Path, default=Path("memory/modeling-memory"))
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--out-dir", type=Path, default=Path("memory/rag-memory"))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entries = build_corpus(args.memory_dir, args.runs_dir, args.repo_root)
    write_corpus(entries, args.out_dir)
    print(
        json.dumps(
            {
                "entry_count": len(entries),
                "out_dir": str(args.out_dir),
                "embedding_backend": "local-hashed-token-char-ngram",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
