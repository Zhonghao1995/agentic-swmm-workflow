---
name: swmm-report
description: >
  Generate a client-deliverable Word (.docx) report from an audited SWMM run
  directory. Reads manifest.json, experiment_provenance.json,
  model_diagnostics.json, comparison.json, and any PNG figures — SWMM is
  never re-run. Supports custom YAML/JSON section templates.
---

# SWMM Report Export Skill

## Purpose

Assemble a reproducible, client-deliverable Word (.docx) report from the
artifacts produced by `swmm-experiment-audit` and `swmm-plot`.  The script
reads only existing files; it never re-runs SWMM or modifies the run
directory.

**Prerequisite:** the run directory must contain a `09_audit/` subdirectory
with at least `experiment_provenance.json`.  Run `aiswmm audit --run-dir
<path>` first if that directory is absent.

---

## CLI usage

```bash
# Standalone script
python3 skills/swmm-report/scripts/generate_report.py \
    --run-dir <path>         # required: audited run directory
    [--out <path.docx>]      # default: <run-dir>/report.docx
    [--template <path>]      # YAML or JSON template (default: built-in)

# CLI verb (registered in aiswmm CLI)
aiswmm report --run-dir <path> [--out <path.docx>] [--template <template.yaml>]
```

Exit codes: `0` = success; `1` = missing dependency, missing audit dir, or
template error; `2` = argument error.

**python-docx dependency:** install with `pip install 'aiswmm[report]'`.
The script exits immediately with a clear message if python-docx is absent.

---

## Agent tool: `generate_report`

Registered in `AgentToolRegistry`.  Direct handler (not MCP-routed) —
shells out to `generate_report.py`, writes `<run-dir>/report.docx` (or
the path supplied via `out`).

```
generate_report(run_dir="runs/my_run/")
generate_report(run_dir="runs/my_run/", out="deliverables/run_report.docx")
generate_report(run_dir="runs/my_run/", template="templates/client_a.yaml")
```

`is_read_only=False` — QUICK profile prompts the user (tool writes files).

If python-docx is not installed the tool returns a failure dict whose
`summary` carries the install hint `pip install 'aiswmm[report]'`.

---

## Executed example

```bash
# Create a minimal synthetic fixture
mkdir -p /tmp/swmm_report_fixture/09_audit
cat > /tmp/swmm_report_fixture/09_audit/experiment_provenance.json << 'EOF'
{
  "schema_version": "1.0",
  "run_id": "smoke-test-run",
  "generated_at_utc": "2025-01-01T00:00:00Z",
  "metrics": {"peak_flow": {"value": 1.23, "time_hhmm": "06:30"},
              "continuity_error": -0.5, "swmm_return_code": 0},
  "qa": {"checks": [{"id": "continuity", "ok": true, "detail": "within tolerance"}]},
  "repo": {"git_head": "abc1234", "git_branch": "main"},
  "tools": {"swmm5_version": "5.1.015", "python_version": "3.11"},
  "artifacts": {
    "model.inp": {"role": "input", "sha256": "4840dbe4abcdef", "exists": true,
                  "produced_by": "builder"}
  },
  "generated_by": "aiswmm"
}
EOF

python3 skills/swmm-report/scripts/generate_report.py \
    --run-dir /tmp/swmm_report_fixture \
    --out /tmp/swmm_report_smoke.docx
# Output: Report written to: /tmp/swmm_report_smoke.docx
```

---

## Template override

Pass `--template <path>` (YAML or JSON) to control which sections appear
and in what order.  The built-in template at
`skills/swmm-report/templates/default.yaml` uses all nine sections:

| Section ID | Content |
|---|---|
| `cover` | Title, run ID, generated-at timestamp |
| `run_summary` | Peak flow, time of peak, continuity error, return code |
| `model_description` | Basin area, simulation window, impervious %, Green-Ampt params |
| `qa_gates` | QA check table from `experiment_provenance.json` |
| `figures` | Embedded PNG figures from `07_plot/`, `08_plot/`, `network_layout.png` |
| `diagnostics` | Model diagnostics from `model_diagnostics.json` |
| `comparison` | Baseline comparison (skipped silently when unavailable) |
| `provenance` | Artifact SHA-256 hashes from `experiment_provenance.json` |
| `appendix` | Git head/branch, SWMM version, Python version |

A custom template only needs the sections it uses.  Unknown section IDs
are warned and skipped — they do not abort the build.

---

## Determinism contract

- No random state, no timestamps injected by the script itself.
- All metadata (run ID, generated-at, SHA-256) comes from existing JSON
  artifacts, not re-computed at report time.
- Identical inputs produce bit-identical `.docx` output (python-docx's
  internal XML is deterministic for a given template + data combination).

---

## Scripts

- `scripts/generate_report.py` — single entrypoint; stdlib + python-docx + PyYAML only.

## Templates

- `templates/default.yaml` — built-in nine-section template.

## Dependencies

| Package | Purpose | Install |
|---|---|---|
| `python-docx` | Write `.docx` output | `pip install 'aiswmm[report]'` |
| `PyYAML` | Parse YAML template files | `pip install PyYAML` |

---

## Part of

PRD_report_export.md — Report Export skill.
PR1: generate_report.py + default template.
PR2 (this): ToolSpec wiring + CLI verb + SKILL.md.
