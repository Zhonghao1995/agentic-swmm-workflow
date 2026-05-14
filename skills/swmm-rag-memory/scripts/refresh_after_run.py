#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SUMMARIZE_MEMORY = REPO_ROOT / "skills" / "swmm-modeling-memory" / "scripts" / "summarize_memory.py"
BUILD_CORPUS = REPO_ROOT / "skills" / "swmm-rag-memory" / "scripts" / "build_memory_corpus.py"
GENERATE_ADVICE = REPO_ROOT / "skills" / "swmm-rag-memory" / "scripts" / "generate_failure_advice.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Agentic SWMM modeling memory and RAG advice after an audited run.")
    parser.add_argument("--run-dir", type=Path, required=True, help="Audited run directory to inspect for failure advice.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--memory-dir", type=Path, default=Path("memory/modeling-memory"))
    parser.add_argument("--rag-dir", type=Path, default=Path("memory/rag-memory"))
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--retriever", choices=("keyword", "hybrid"), default="hybrid")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--refresh-modeling-memory", action="store_true", help="Regenerate memory/modeling-memory before building the RAG corpus. Off by default to avoid overwriting curated memory records.")
    parser.add_argument("--no-advice", action="store_true", help="Only refresh RAG indexes.")
    return parser.parse_args()


def run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    steps: list[dict[str, object]] = []

    if args.refresh_modeling_memory:
        summarize = run_command(
            [
                sys.executable,
                str(SUMMARIZE_MEMORY),
                "--runs-dir",
                str(args.runs_dir),
                "--out-dir",
                str(args.memory_dir),
            ],
            repo_root,
        )
        steps.append({"step": "summarize_memory", "ok": True, "stdout": summarize.stdout.strip()})
    else:
        steps.append(
            {
                "step": "summarize_memory",
                "ok": True,
                "skipped": True,
                "reason": "Use --refresh-modeling-memory to regenerate curated modeling-memory outputs.",
            }
        )

    build = run_command(
        [
            sys.executable,
            str(BUILD_CORPUS),
            "--memory-dir",
            str(args.memory_dir),
            "--runs-dir",
            str(args.runs_dir),
            "--out-dir",
            str(args.rag_dir),
            "--repo-root",
            str(repo_root),
        ],
        repo_root,
    )
    steps.append({"step": "build_rag_corpus", "ok": True, "stdout": build.stdout.strip()})

    advice_written = False
    if not args.no_advice:
        advice = run_command(
            [
                sys.executable,
                str(GENERATE_ADVICE),
                "--run-dir",
                str(args.run_dir),
                "--memory-dir",
                str(args.memory_dir),
                "--runs-dir",
                str(args.runs_dir),
                "--index-dir",
                str(args.rag_dir),
                "--repo-root",
                str(repo_root),
                "--retriever",
                args.retriever,
                "--top-k",
                str(args.top_k),
            ],
            repo_root,
        )
        advice_payload = json.loads(advice.stdout)
        advice_written = bool(advice_payload.get("advice_written"))
        steps.append(
            {
                "step": "generate_failure_advice",
                "ok": True,
                "advice_written": advice_written,
                "triggered": advice_payload.get("triggered"),
                "trigger_reasons": advice_payload.get("trigger_reasons", []),
            }
        )

    if advice_written:
        rebuild = run_command(
            [
                sys.executable,
                str(BUILD_CORPUS),
                "--memory-dir",
                str(args.memory_dir),
                "--runs-dir",
                str(args.runs_dir),
                "--out-dir",
                str(args.rag_dir),
                "--repo-root",
                str(repo_root),
            ],
            repo_root,
        )
        steps.append({"step": "rebuild_rag_corpus_after_advice", "ok": True, "stdout": rebuild.stdout.strip()})

    print(
        json.dumps(
            {
                "ok": True,
                "run_dir": str(args.run_dir),
                "memory_dir": str(args.memory_dir),
                "rag_dir": str(args.rag_dir),
                "steps": steps,
                "boundary": "Post-run memory refresh only. No SWMM model files, workflow code, or skill definitions were modified.",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
