# Case studies

Real sessions, real networks, real deliverables. Each case records the exact natural-language prompt that was typed, what the agent did, the numbers it produced, and the artifacts it wrote, so you can replay it word for word.

| Case | Area | Upstream source | Chain | Deliverable |
| --- | --- | --- | --- | --- |
| [Downtown Victoria, BC](downtown-victoria/) | ~1 km2 municipal core | [SWMMCanada](https://github.com/Zhonghao1995/SWMMCanada) real storm network | fetch, run, audit, design review, plot, map, Word report | [sample_report.docx](downtown-victoria/sample_report.docx) |

## Ground rules for every case

- The prompt shown is the prompt used, verbatim.
- Numbers come from the run's own artifacts (SWMM report file, audit JSON, review output), not from prose.
- Failures and caveats are part of the record: an uncalibrated first-pass model is labeled as such, and a design-review FAIL on such a model is the rulebook doing its job, not a broken chain.
