# Memory runtime CLI verbs

Quick reference for the memory-facing CLI surfaces added across
PRD-06 and PRD-07. Each verb is a deterministic surface over a pure
function — none of them invoke the LLM. The default-mode verbs are
visible to every user; the expert-mode verbs are listed under the
"expert" set in `agentic_swmm.agent.memory_verbs`.

For the underlying substrate, see
[docs/memory_runtime.md](memory_runtime.md).

## `aiswmm compare`

Compare two SWMM runs on continuity metrics. Returns a structured
verdict (`A_better`, `B_better`, `tie`, `incomparable`).

```bash
aiswmm compare \
  --run-a runs/saanich-b8/2026-05-12-12-00 \
  --run-b runs/saanich-b8/2026-05-15-09-30
```

Use `--json` to emit the comparison as JSON for downstream piping.
Restrict to specific metrics with repeated `--metric` flags.

## `aiswmm cite`

Print a citation entry from `memory/modeling-memory/citations.yaml`.

```bash
aiswmm cite huber_dickinson_1988_t4_5
```

Exits non-zero when the citation key is not present in the YAML.
Use `--json` for machine-readable output.

## `aiswmm storm`

Generate an algorithmic design storm in SWMM `[TIMESERIES]` format.
No IDF lookup — the shape primitives are uniform, triangular,
front_loaded, back_loaded.

```bash
aiswmm storm --depth-mm 25 --duration-min 60 --shape triangular
```

Pipe to a file via shell redirect, or use `--out` to write directly.

## `aiswmm uncertainty plan`

Plan a parameter uncertainty scan over a base INP without actually
running SWMM. Returns a sample list (Morris or Sobol').

```bash
aiswmm uncertainty plan \
  --base-inp examples/tecnopolo/tecnopolo_r1_199401.inp \
  --param manning_n=0.01,0.03 \
  --param soil_k=0.1,5.0 \
  --method morris \
  --n-samples 50 \
  --out plans/tecnopolo_morris.json
```

The output JSON carries provenance (base INP hash, seed, method) so a
later `aiswmm run` invocation can reproduce the sweep deterministically.

## `aiswmm transfer`

Recommend warm-start parameters for a fresh INP by ranking calibrated
prior cases by watershed similarity.

```bash
aiswmm transfer --inp examples/new_case/new_case.inp --top-k 3
```

Each recommendation surfaces the source case, similarity score, the
calibration's primary objective, and the proposed parameter set. The
verb is advisory only — it never writes to the new INP.

## `aiswmm bootstrap memory`

Scaffold a project's `memory/modeling-memory/` skeleton with empty
JSONL stores, an empty `project_overrides.yaml`, and a README that
points at the substrate doc. Idempotent: re-running never overwrites
an existing file.

```bash
aiswmm bootstrap memory
```

Use `--dir <path>` to override the default location.

After running, you'll see something like:

```
target_dir: memory/modeling-memory
created (5):
  + parametric_memory.jsonl
  + calibration_memory.jsonl
  + negative_lessons.jsonl
  + project_overrides.yaml
  + README.md
skipped: (none)
```

Re-running on the same directory:

```
target_dir: memory/modeling-memory
created: (none)
skipped (5):
  = parametric_memory.jsonl
  = calibration_memory.jsonl
  = negative_lessons.jsonl
  = project_overrides.yaml
  = README.md
```

This is safe to run in CI as an "ensure-present" step.
