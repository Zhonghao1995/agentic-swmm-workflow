---
name: swmm-rag-memory
description: Retrieve relevant Agentic SWMM modeling memory from audited runs, modeling-memory summaries, and Obsidian-compatible notes at query time. Use when a user asks for RAG, similar past runs, evidence-linked memory retrieval, historical QA/failure patterns, or memory-grounded answers.
---

# SWMM RAG Memory

## What this skill provides

- Query-time retrieval over Agentic SWMM audited run memory.
- A lightweight keyword/tag retriever that works without embeddings or a vector database.
- A local hybrid retriever that combines keyword matches, deterministic SWMM tags, metadata weighting, and hashed token/character n-gram embeddings.
- RAG context packs that can be passed to Codex, OpenClaw, Hermes, or another LLM.
- Source citations for each retrieved memory item, including run id, project key, source file, failure patterns, diagnostics, and matched terms.
- Retrieval-grounded `failure_advice.{json,md}` for failed or warning runs, without modifying model files.
- Explicit `resolution_memory.json` for human-reviewed and benchmark-verified repairs.
- Obsidian-compatible Markdown output for saved retrieval notes.

This skill reads existing audit and modeling-memory artifacts. It does not run SWMM, modify model inputs, rewrite skills, or claim that retrieved memory proves a modeling conclusion.

## Relationship to `swmm-modeling-memory`

`swmm-modeling-memory` summarizes audited runs after experiments have been recorded.

`swmm-rag-memory` retrieves the most relevant historical memory for a current question.

The intended loop is:

1. Run SWMM or attempt a workflow.
2. Audit the run.
3. Refresh `swmm-modeling-memory`.
4. Ask a current modeling question.
5. Retrieve relevant historical memory with `swmm-rag-memory`.
6. Answer with explicit source boundaries and citations.

## Output contract

The corpus builder writes these files to the selected RAG-memory output directory:

- `corpus.jsonl`
- `keyword_index.json`
- `embedding_index.json`

The retriever writes JSON results by default and can also write a Markdown context pack. Failure advice writes `failure_advice.json` and `failure_advice.md` into the run directory. Verified repairs can be recorded as `resolution_memory.json`.

## CLI

Build a corpus from existing memory and audited runs:

```bash
python3 skills/swmm-rag-memory/scripts/build_memory_corpus.py \
  --memory-dir memory/modeling-memory \
  --runs-dir runs \
  --out-dir memory/rag-memory
```

Retrieve relevant memory:

```bash
python3 skills/swmm-rag-memory/scripts/retrieve_memory.py \
  --query "peak flow parsing is missing" \
  --memory-dir memory/modeling-memory \
  --runs-dir runs \
  --top-k 5
```

Hybrid retrieval:

```bash
python3 skills/swmm-rag-memory/scripts/retrieve_memory.py \
  --query "peak flow was not parsed from the report" \
  --index-dir memory/rag-memory \
  --retriever hybrid \
  --top-k 5
```

Generate an LLM-ready context pack:

```bash
python3 skills/swmm-rag-memory/scripts/answer_with_memory.py \
  --query "Why does high continuity error keep recurring?" \
  --memory-dir memory/modeling-memory \
  --runs-dir runs \
  --retriever hybrid \
  --top-k 6 \
  --format markdown
```

Optional Obsidian export:

```bash
python3 skills/swmm-rag-memory/scripts/answer_with_memory.py \
  --query "How should I investigate missing peak-flow parsing?" \
  --memory-dir memory/modeling-memory \
  --runs-dir runs \
  --obsidian-dir "$HOME/Documents/Agentic-SWMM-Obsidian-Vault/10_Memory_Layer/RAG Queries"
```

Generate advice after a failed, partial, or warning run:

```bash
python3 skills/swmm-rag-memory/scripts/generate_failure_advice.py \
  --run-dir runs/<case> \
  --index-dir memory/rag-memory \
  --retriever hybrid
```

Record a repair only after review and verification:

```bash
python3 skills/swmm-rag-memory/scripts/record_resolution_memory.py \
  --run-dir runs/<case> \
  --action-taken "Updated runner parser to read Node Inflow Summary." \
  --file-changed skills/swmm-runner/scripts/run_swmm.py \
  --verification "python3 -m pytest tests/test_swmm_runner_peak_parser.py" \
  --human-reviewed \
  --benchmark-verified
```

One-command post-audit refresh:

```bash
python3 skills/swmm-rag-memory/scripts/refresh_after_run.py \
  --run-dir runs/<case> \
  --runs-dir runs \
  --memory-dir memory/modeling-memory \
  --rag-dir memory/rag-memory
```

This rebuilds the RAG corpus, generates failure advice only if trigger conditions are met, and rebuilds the corpus again if advice was written. It does not regenerate curated `memory/modeling-memory` outputs unless `--refresh-modeling-memory` is provided.

## Safety rules

- Read existing memory and audit artifacts only.
- Keep retrieval evidence-linked: every result must include a source path.
- Distinguish retrieved audit evidence from inference.
- Prefer deterministic tags such as failure patterns and diagnostic ids over unsupported free-text interpretation.
- Do not mutate `runs/`, `memory/modeling-memory/`, or existing `SKILL.md` files.
- Do not treat `failure_advice.md` as accepted knowledge. It is only retrieval-grounded advice.
- Treat `resolution_memory.json` as reusable repair memory only when `human_reviewed=true` and `benchmark_verified=true`.
- Obsidian export is optional and writes only retrieval notes.
