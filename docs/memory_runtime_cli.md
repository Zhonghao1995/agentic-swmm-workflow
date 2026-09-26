# Memory runtime CLI verbs

Quick reference for the memory-facing CLI surfaces added across
PRD-06 and PRD-07. Each verb is a deterministic surface over a pure
function, and none of them invokes the LLM. The default-mode verbs are
visible to every user; the expert-mode verbs are listed under the
"expert" set in `agentic_swmm.agent.memory_verbs`.

For the underlying substrate, see
[docs/memory_runtime.md](memory_runtime.md).

## `aiswmm memory`

What aiswmm remembers, and the decisions only a human makes.

```bash
aiswmm memory show <case>                 # the memory card for one case: parameters, calibrations, known-bad regions
aiswmm memory proposals [--all] [--json]  # proposals awaiting a decision (--all includes promoted and rejected)
aiswmm memory promote <id>                # apply one proposal to its target file
aiswmm memory reject <id> --reason "..."  # decline it; the same proposal is never made again
aiswmm memory rebuild [--runs-dir DIR]    # set memory.sqlite aside and rebuild it from the ledgers and runs/
aiswmm memory repair-sessions [--yes]     # rebuild the sessions from runs/**/agent_trace.jsonl
aiswmm memory health                      # the application outcome ledger
aiswmm memory archive / restore           # move an entry out of, or back into, the live store
```

`promote` re-derives the diff before writing and refuses a target that
changed since the proposal was made. A proposal for a `SKILL.md` or the
initial memory is promoted only in a source checkout (never inside an
installed package), and the result is an ordinary `git diff` to commit
or open a pull request from; a proposal for `memory/facts.md` promotes
anywhere.

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

Print a citation entry from the shipped `memory/initial/citations.yaml` (or a copy in `memory/store/`).

```bash
aiswmm cite huber_dickinson_1988_t4_5
```

Exits non-zero when the citation key is not present in the YAML.
Use `--json` for machine-readable output.

## `aiswmm storm`

Generate an algorithmic design storm in SWMM `[TIMESERIES]` format.
No IDF lookup: the shape primitives are uniform, triangular,
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
verb is advisory only: it never writes to the new INP.

## `aiswmm bootstrap memory`

Scaffold a project's `memory/store/` skeleton with empty
JSONL stores, an empty `project_overrides.yaml`, and a README that
points at the substrate doc. Idempotent: re-running never overwrites
an existing file.

```bash
aiswmm bootstrap memory
```

Use `--dir <path>` to override the default location.

After running, you'll see something like:

```
target_dir: memory/store
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
target_dir: memory/store
created: (none)
skipped (5):
  = parametric_memory.jsonl
  = calibration_memory.jsonl
  = negative_lessons.jsonl
  = project_overrides.yaml
  = README.md
```

This is safe to run in CI as an "ensure-present" step.
