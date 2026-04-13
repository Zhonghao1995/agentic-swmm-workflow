# Local run outputs

`runs/` is for local generated outputs. Do not commit run artifacts from this folder.

Acceptance outputs are written to `runs/acceptance/<run-id>/`.
For the default run id, the report is:
`runs/acceptance/latest/acceptance_report.md`

Quick commands:

```sh
python3 scripts/acceptance/run_acceptance.py --run-id latest
cat runs/acceptance/latest/acceptance_report.md
```
