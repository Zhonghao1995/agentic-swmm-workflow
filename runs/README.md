# runs/ layout

Every aiswmm execution lands here. `runs/` is for local generated
outputs; do not commit run artifacts from this folder. Two levels of
organization apply: this page covers the ROOT level (which folder a
run lands in); the numbered stages INSIDE each run dir
(00_inputs .. 11_review) are the canonical run layout.

## Where new runs land

| Path | What it holds |
| --- | --- |
| `<YYYY-MM-DD>/<HHMMSS>_<case>_run/` | One natural-language goal executed end to end (the agent ran tools and finished). |
| `<YYYY-MM-DD>/<HHMMSS>_<case>_chat/` | A conversational turn that produced no SWMM run (question answered, nothing simulated). |
| CLI verbs (`aiswmm run --run-dir ...`) | Wherever `--run-dir` points; pick a dated folder or a purpose folder below. |

The date-first scheme is the single home for sessions: interactive
turns and one-shot goals use the same naming, so "when did I do that"
is one sorted listing. The `<case>` slug is inferred from your goal
(for example `tecnopolo`), and the suffix tells you whether SWMM
actually ran.

## Reserved purpose folders

| Path | Purpose |
| --- | --- |
| `benchmarks/` | Repeatable benchmark harnesses driven by `scripts/benchmarks/`. Excluded from modeling memory. |
| `acceptance/` | Acceptance and CI verification runs (`scripts/acceptance/run_acceptance.py --run-id latest`; report at `acceptance/latest/acceptance_report.md`). Excluded from modeling memory. |
| `archive/` | Cold storage. `aiswmm runs tidy` moves stale unaudited legacy sessions here; nothing inside is ever rewritten. |

## Legacy folders

Anything else at the root (named case experiments, demo folders,
`agent/` with `agent-<timestamp>` sessions) predates this layout.
Legacy runs are read-only forever: tools keep resolving them, audits
keep reading them, and nothing migrates them in place. New sessions
never land there.

Housekeeping: `aiswmm runs tidy --dry-run` previews what would be
archived; drop `--dry-run` to apply.
