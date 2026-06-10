# Modeling memory

This directory holds the project's modeling memory:

* `parametric_memory.jsonl` — append-only log of run-level
  parameters and QA metrics.
* `calibration_memory.jsonl` — append-only log of accepted
  calibrations and goodness-of-fit metrics.
* `negative_lessons.jsonl` — append-only log of known-bad
  parameter regions and failure codes.
* `project_overrides.yaml` — per-project overlay on the library
  reference benchmarks.

See [docs/memory_runtime.md](../../docs/memory_runtime.md) for
the substrate contract and the four confidence quadrants the
runtime uses to decide between auto-complete, memory-informed,
LLM, and HITL.
