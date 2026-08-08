# Two interfaces, one engine

aiswmm has exactly two front doors, and they drive the same machinery. Every
capability below runs the same deterministic scripts and lands the same
artifacts in the same canonical run-directory layout, whichever door you use.

**Natural language** (`aiswmm "..."`) hands your goal to the LLM planner,
which picks from the same typed tools and chains them for you. **CLI verbs**
(`aiswmm run`, `aiswmm climate`, ...) call the same operations directly, with
no LLM, no tokens, and byte-reproducible behavior.

Rule of thumb: explore, chain steps, or forget flags in natural language;
script, reproduce, and publish with CLI verbs. Anything the planner did can
be replayed as CLI verbs from the run's `agent_trace.jsonl`.

## The mapping

| You say (example) | The planner calls | CLI equivalent | What lands |
| --- | --- | --- | --- |
| "Get me a model for downtown Ottawa, June 1 to 2" | `fetch_swmm_from_canada` | (agent-only; service fetch) | `05_builder/model.inp`, `10_upstream/swmmcanada/` |
| "Synthesize a network for this bbox" | `synth_swmm_from_bbox` | (agent-only; synthesis) | `05_builder/`, `10_upstream/swmmanywhere/` |
| "Run this model" | `run_swmm_inp` | `aiswmm run --inp ... --run-dir ...` | `06_runner/model.rpt`, `.out` |
| "Audit that run" | `audit_run` | `aiswmm audit --run-dir ...` | `09_audit/` provenance, note, diagnostics |
| "Calibrate it against this observed CSV" | `swmm_calibrate_search` / `swmm_calibrate_sceua` | `aiswmm calibrate ...` | `calibration_summary.json`, `best_params.json` |
| "Compare it under 20% more rain" | `run_climate_scenarios` | `aiswmm climate --inp ... --factors "1.0,1.2"` | `03_climate/` scenarios + summary |
| "Plot the outfall hydrograph" | `plot_run` | `aiswmm plot --run-dir ... --node ...` | `08_plot/*.png` |
| "Draw the network map" | `map_run` | `aiswmm map --run-dir ...` | `08_plot/network_map.png` |
| "What did the busiest pipe do?" | `read_rpt_summary` | (read-only rpt query) | answer from `model.rpt`, no writes |
| "Check this network JSON" | `network_qa` | (skill script via agent) | QA report JSON |
| "Design a 2-year Chicago storm" | `generate_design_storm` | `aiswmm storm ...` | timeseries text + metadata JSON |
| "Review the run against the rulebook" | `review_run` | `aiswmm review --run-dir ...` | `11_review/` findings |
| "Export a client report" | `generate_report` | `aiswmm report --run-dir ...` | `report.docx` |

Chinese phrasing works the same way for the natural-language door: the
planner's intent vocabulary is bilingual (for example, "给我渥太华市中心的模型,
跑一遍然后审计" walks the first three rows in one goal).

## Why both exist

The natural-language door is for humans mid-thought: it chains steps, asks
when inputs are missing, and pauses for approval at consequential decisions.
The CLI door is for machines and papers: deterministic, inspectable, and free
of model calls. Because both converge on the same scripts, evidence never
depends on which door produced it: the run directory is the record, and
`aiswmm audit` treats every run identically.

Providers and login (`aiswmm setup`, `aiswmm login`) configure only the
natural-language door; CLI verbs never need an LLM key.
