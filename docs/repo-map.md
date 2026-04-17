# Repository map

This public repository is the **publishable, cleaned-up workflow layer** for the broader Tod Creek / SWMM development work.

## Public repo (this repository)
Path:
- `/Users/zhonghao/.openclaw/workspace/publish/agentic-swmm-workflow`

Purpose:
- package the workflow as reusable **Skills + MCP scaffolds**
- keep the public-facing examples small and auditable
- support a paper-friendly reproducible architecture story
- provide a top-level `swmm-end-to-end` orchestration skill for OpenClaw-facing runs

Key folders:
- `skills/swmm-gis/` → DEM outlet selection
- `skills/swmm-runner/` → reproducible SWMM execution + manifests
- `skills/swmm-plot/` → publication plotting
- `skills/swmm-calibration/` → calibration / validation / sensitivity scaffold
- `skills/swmm-end-to-end/` → top-level orchestration contract across module skills
- `examples/todcreek/` → minimal example INP
- `examples/calibration/` → minimal calibration example inputs
- `docs/` → figures, repo documentation, and OpenClaw execution-path notes

## Working project (larger local development directory)
Path:
- `/Users/zhonghao/.openclaw/workspace/projects/swmm-mcp`

Purpose:
- richer experimental workspace used during model development
- contains Tod Creek data, exploratory scripts, batch experiments, paper tables, and run artifacts

Notable contents:
- `data/Todcreek/Flow/1984Rflow.dat` → real observed flow time series
- `todcreek/` → generation, plotting, sensitivity, lookup tables, and historical run outputs
- `experiments/equivalence_batch/` → Scenario A/B scripts and post-processing
- `paper/` → draft manuscript assets and tables

## How to think about the two directories
- `projects/swmm-mcp/` = messy but rich working lab bench
- `publish/agentic-swmm-workflow/` = smaller, cleaner public repository

If something seems to be "missing" from the public repo, it may still exist in the larger working project.
