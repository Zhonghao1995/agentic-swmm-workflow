# Memory runtime — engineering notes

The agent runtime reads a small set of on-disk stores before deciding
how to dispatch a SWMM workflow. This document describes the
substrate, the four confidence quadrants the runtime maps a decision
into, and the audit trail every consultation leaves behind.

The runtime never assumes the stores exist. Every read path returns
an empty result when a file is missing or malformed, so a fresh
project produces a coherent decision (defer to the LLM) instead of an
exception.

## Substrate

All memory artifacts live under `memory/modeling-memory/` in the
project root. The directory is created lazily by the audit hook, or
explicitly via `aiswmm bootstrap memory`.

### Stores

#### `parametric_memory.jsonl`

Append-only JSONL. One line per audited SWMM run. Each row carries:

* `run_id`, `case_name` — provenance keys.
* `model_structure` — INP topology snapshot (subcatchment count, node
  count, parameter ranges).
* `qa_metrics` — runoff continuity, flow continuity, mass balance.
* `performance_metrics` — wall-clock, peak flow, total volume.
* `watershed_classification` — area, dominant land use, soil group;
  used by `watershed_similarity` to rank prior cases.
* `recorded_utc` — ISO 8601 timestamp.

The writer is `agentic_swmm.memory.parametric_memory.record_parametric`.
Reads go through `recall_parametric` (filter by case, use_case, etc.)
or through the read-side adapter `gather_memory_context`.

#### `calibration_memory.jsonl`

Append-only JSONL. One line per **accepted** calibration. Tracks the
parameter set, the primary objective (NSE, KGE), secondary metrics
(PBIAS, RMSE), the algorithm (sceua, dream_zs), and the SWMM solver
version. Used by `case_adaptive_thresholds` to propose tighter
warn/fail bands when a case has accumulated enough history.

#### `negative_lessons.jsonl`

Append-only JSONL. One line per **failed** run or **divergent**
calibration. Records the parameter set that misbehaved and the
failure code (continuity_fail, calibration_diverged,
non_physical_param). The helper `is_param_set_known_bad` answers
"would this candidate land in a previously bad region" before a
calibration accept.

#### `reference_benchmarks.yaml`

Shipped with the package. Holds the library defaults for continuity
warn/fail thresholds and other QA gates. Read through
`benchmark_resolver.resolve_threshold` so the project overrides take
precedence when present.

#### `project_overrides.yaml`

Optional. Same dotted-path schema as `reference_benchmarks.yaml`;
any key set here wins over the library default for this project. The
bootstrap command creates an empty overrides file (just the
`schema_version` header) so the resolver has somewhere to look.

#### `citations.yaml`

Maps citation keys to bibliographic entries the agent surfaces in
audit notes. Read through `recall_citation` (or the `aiswmm cite`
verb).

#### `run_progress/`

Sub-directory holding long-run checkpoints. Each long-running command
writes a JSON file here so a crash or Ctrl-C does not lose the
intermediate state. The checkpoint format is private to the writing
command; the runtime treats the directory as opaque.

## The four confidence quadrants

When the runtime considers a goal that touches memory, it picks one of
four labels. The picker is the pure function
`agentic_swmm.agent.memory_informed_policy.decide_with_memory`, which
takes a `MemoryContext` snapshot and a `stakes` hint and returns a
`PolicyDecision`.

* `auto_complete` — the utterance is unambiguous against memory
  (exactly one matching case, or one explicit case-name token that
  matches a hit). The planner skips the LLM and proceeds.
* `memory_informed` — multiple candidates exist. Memory ranks them by
  recency; the planner pre-fills the confirmation prompt with the
  top-1 but still asks the user. Also produced when calibration
  intent fires against an empty parametric store but the
  cross-watershed transfer recommender has candidates from similar
  watersheds.
* `llm` — memory was consulted but not decisive (zero hits, or an
  explicit token that does not appear in memory). The planner defers
  to the existing LLM / keyword fallback.
* `hitl` — high-stakes verb with zero matching evidence. The policy
  raises `MemoryHITLRequired`, the runtime catches it, and the user
  sees a structured prompt explaining what was about to happen and
  what memory had to say.

Stakes are derived two ways. The first is the **memory verb registry**
(`agentic_swmm.agent.memory_verbs`): every memory-facing CLI verb is
registered with an explicit `stakes` label, and a goal that mentions a
high-stakes verb is treated as high stakes without any further
keyword analysis. The second is the legacy keyword sniff
(`_HIGH_STAKES_TOKENS`) covering the older accept-calibration /
promote-fact / reflect-apply verbs that predate the registry.

The four quadrants map onto the two axes in this matrix:

|              | evidence present     | evidence absent |
| ------------ | -------------------- | --------------- |
| **low stakes**  | auto_complete or memory_informed | llm |
| **high stakes** | memory_informed (transfer warm-start), then quadrant by evidence | **hitl** |

## Audit trail — `memory_trace.jsonl`

Every memory consultation lands one line in
`<session_dir>/memory_trace.jsonl`. The line is JSON with these
fields:

* `recorded_at` — ISO 8601 timestamp.
* `decision_point` — where in the pipeline the consult fired
  (`planner_intent_disambiguation`, `qa_gate`, etc.).
* `parametric_hit_count` — how many parametric rows the context
  carried.
* `decision` — the resolved case (or `"(none)"`).
* `confidence` — one of the four quadrants above.
* `summary` — the short plain-English summary from the
  `MemoryContext`.

The trace is append-only. Tests assert one line per consult; the
runtime never edits or replaces existing lines. The format is read by
the audit hook to build the final run report.

## Cross-watershed transfer

When a fresh case has zero local calibration history but the user
asks for a calibration, the runtime can rescue the prompt out of the
`hitl` branch by consulting `cross_watershed_transfer`. The pipeline
is:

1. `watershed_similarity.compare_watersheds` — vector-space compare
   the new INP against each `case_name` in `calibration_memory.jsonl`.
   The similarity metric uses area, land use, soil group, and
   subcatchment count.
2. `cross_watershed_transfer.recommend_parameters_for_new_case` —
   for the top-K most similar cases, surface the best calibration
   record (highest objective). Each recommendation is a
   `TransferRecommendation` carrying the source case, the similarity
   score, and the proposed parameter set.
3. The memory-informed policy reads the top recommendation, switches
   the decision to `memory_informed`, and the planner pre-fills the
   warm-start prompt for human confirmation.

Transfer is advisory only. The recommender never edits an INP — the
user is the only path by which a recommendation lands in a calibration
run.

## Opt-out flags

Three independent escape hatches let the user bypass the
memory-informed runtime or the SWMM pre/postflight gates without
editing project config. All three are runtime knobs — they exist
for the moment something is wrong and the user needs to move on.

### `AISWMM_DISABLE_SWMM_GATES=1`

When set, the pre-flight INP validation and the post-flight QA gate
are skipped entirely. Each runnable :class:`WorkflowMode` adapter
checks this before invoking the gate, so the SWMM run + audit + plot
pipeline executes regardless of whether the INP would have failed
preflight or the .rpt would have failed postflight. Use this when the
gate is wrong for your case (e.g. a custom non-SWMM-5 routing rule)
and you have manually verified the run.

### `AISWMM_DISABLE_MEMORY_INFORMED=1`

When set, ``gather_memory_context`` short-circuits to an empty
:class:`MemoryContext` before reading any store. Every consumer
sees the same shape as a fresh project: the policy returns ``llm``
for low-stakes goals and ``hitl`` for high-stakes goals. Use this
when memory has accumulated incorrect rows and you want a clean
"as if first run" execution while you triage the store.

### `--ignore-memory` (CLI flag)

A one-shot equivalent of ``AISWMM_DISABLE_MEMORY_INFORMED=1``: the
CLI sets the env var only for the duration of one invocation, then
restores the prior value. Subsequent commands in the same shell
session see memory again. The flag works at any position on the
command line: ``aiswmm --ignore-memory plot ...`` and
``aiswmm plot --ignore-memory ...`` are equivalent.

The two env-var flags and the CLI flag are mutually independent —
each controls one boundary. A user who wants both off sets both
variables; a user who wants only the SWMM gates off sets only
``AISWMM_DISABLE_SWMM_GATES``.

## Chat-note "Memory-informed defaults" section

When a chat session involves at least one memory-informed decision
the agent automatically inserts a section titled
**Memory-informed defaults** into ``chat_note.md``. The section is a
three-column Markdown table — Field / Value / Source — with one row
per decision. Empty trace (no memory-informed decisions) means the
section is omitted entirely, so a fresh-project chat note stays
clean. The table is rendered from ``memory_informed_decision``
events on ``agent_trace.jsonl``, which the runtime writes alongside
the existing ``memory_trace.jsonl`` per-run log.

## agent_trace.jsonl mirror events

The transparency log lives in two places. ``memory_trace.jsonl``
(per-run) carries one line per consultation for chat-time audit.
``agent_trace.jsonl`` (per-session) gains two mirror event types so
the run-time audit pipeline sees the same decisions:

* ``memory_consultation`` — fires once per workflow-mode adapter
  run, with fields ``kind``, ``case_meta``, ``evidence_count``,
  ``consensus_fields``, ``ambiguous_fields``, ``queried_at_utc``.
* ``memory_informed_decision`` — fires once per memory-informed
  default the agent picked, with fields ``field``, ``value_chosen``,
  ``rationale``, ``source_runs``.

The duplication is intentional. The chat-note generator reads the
agent trace; the audit hook reads the memory trace; both consumers
can stay loosely coupled because they never share a file.

## Honesty layer & opt-outs

The runtime gates a small set of trust-breakers so a downstream
``&&``-chained script cannot accept a manifest that looks healthy
when SWMM in fact reported `ERROR`. The substrate lives in
`agentic_swmm/agent/honesty.py` and is wired through three surfaces:

* **Postflight gate**: after every SWMM invocation, `postflight_qa`
  scans the `.rpt` for verbatim `ERROR \d+:` lines and surfaces them
  as a `swmm_solver_error` failure on the `QAReport`. `aiswmm run`
  uses this to exit non-zero with the verbatim error line on stderr;
  the manifest is still written so an audit can inspect the run.
* **STUB banner**: while the calibration solver hookup is still
  synthetic, `aiswmm calibrate` prints a prominent warning at the
  top of every invocation so the deterministic stub output cannot be
  mistaken for a real result. The verb also refuses to start when
  `--base-inp` does not exist (fail-fast with exit code 2).
* **Silent-default / silent-override notices**: `aiswmm storm`
  emits a stderr `note:` line when `--shape` is omitted (default
  `uniform`) and a stderr `warning:` line when `--depth-mm` is
  silently overridden by `--idf`.

Set `AISWMM_DISABLE_HONESTY_LAYER=1` to restore the legacy "always
exit 0" path for `aiswmm run`. The opt-out is documented in
`aiswmm doctor`'s **Runtime knobs** section so a user who set it can
see what state they're in. Set it on a per-invocation basis when
intentionally consuming partial runs (replay tooling, archival
fixers); leave it unset for interactive modeling work.

## Doctor sections

`aiswmm doctor` prints five sections in this order:

1. **Install** — Python, swmm5, MCP routing, package skills.
2. **Memory stores** — file existence, row counts, last-modified
   timestamps, and verified-entry counts for every store under
   `memory/modeling-memory/`. A fresh PyPI install shows seven
   `MISSING` rows pointing the user at `aiswmm bootstrap memory`
   or the YAML-library copy paths.
3. **Runtime knobs** — current state (set/unset and raw value) of
   the five opt-out env vars: `AISWMM_DISABLE_MEMORY_INFORMED`,
   `AISWMM_DISABLE_SWMM_GATES`, `AISWMM_DISABLE_HONESTY_LAYER`,
   `AISWMM_DISABLE_WELCOME`, `AISWMM_MEMORY_DIR`.
4. **Issues (grouped)** — WARN/MISSING rows from the install block
   with identical-cause rows collapsed into one summary. When 11
   MCP servers all drift to the same checkout, the section reports
   "11 MCP servers drift to <path>" once with one remediation line.
5. **Suggested actions** — when remediable issues exist, doctor
   lists the commands that would fix them. Pass `--fix` to walk
   each one interactively (`--yes` skips the per-action prompt).

`aiswmm doctor --json` emits the full report as JSON on stdout so
CI integrations can parse it without scraping the text layout.

## Adding a new verb

The drift bug PRD-04 warned about (three files in lockstep) is now
two registrations:

1. Add a `MemoryVerb(...)` row in
   `agentic_swmm/agent/memory_verbs.py`. Pick `mode="default"` for
   verbs every user sees and `mode="expert"` for the additive
   expert-only set. Pick `stakes="low"` for advisory reads;
   `stakes="high"` only for verbs that mutate memory or accept a
   calibration.
2. Register the argparse subparser in `agentic_swmm/cli.py` (or in a
   sub-module under `commands/`).

The planner's stakes lookup, the HITL surface, and the docs reader
all consult the registry — no third edit is required.
