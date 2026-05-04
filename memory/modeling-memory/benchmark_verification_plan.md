# Benchmark Verification Plan

Generated at UTC: `2026-05-04T17:31:26+00:00`

Use this checklist before accepting any skill refinement proposed by modeling memory.

- Identify the exact proposed skill or workflow change and the runs that motivated it.
- Review the source audit artifacts manually, including `experiment_provenance.json`, `comparison.json`, and `experiment_note.md`.
- Confirm the proposal does not change scientific modeling rules without human approval.
- Run the existing acceptance check when available:

```bash
python3 scripts/acceptance/run_acceptance.py --run-id latest
```

- Run relevant benchmark commands when the proposed change touches benchmark behavior:

```bash
python3 scripts/benchmarks/run_tuflow_swmm_module03_raw_path.py
python3 scripts/benchmarks/run_tecnopolo_199401.py
```

- Re-run experiment audit on affected runs before treating the change as evidence-backed.
- Re-run modeling-memory summarization and check whether the repeated failure pattern is reduced without hiding missing evidence.
- Accept the skill refinement only after human review confirms the benchmark and audit outputs remain interpretable.
