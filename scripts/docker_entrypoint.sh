#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

cd /app

cmd="${1:-acceptance}"
shift || true

run_id="${AGENTIC_SWMM_RUN_ID:-docker}"

case "$cmd" in
  acceptance)
    exec python3 scripts/acceptance/run_acceptance.py --run-id "$run_id" "$@"
    ;;
  tecnopolo)
    exec python3 scripts/benchmarks/run_tecnopolo_199401.py "$@"
    ;;
  tuflow-raw)
    exec python3 scripts/benchmarks/run_tuflow_swmm_module03_raw_path.py "$@"
    ;;
  todcreek-minimal)
    exec python3 scripts/real_cases/run_todcreek_minimal.py "$@"
    ;;
  uncertainty-dryrun)
    exec python3 skills/swmm-uncertainty/scripts/uncertainty_propagate.py \
      --base-inp examples/todcreek/model_chicago5min.inp \
      --patch-map examples/calibration/patch_map.json \
      --fuzzy-space skills/swmm-uncertainty/examples/fuzzy_space.json \
      --config skills/swmm-uncertainty/examples/uncertainty_config.json \
      --run-root runs/docker-uncertainty \
      --summary-json runs/docker-uncertainty/uncertainty_summary.json \
      --dry-run \
      "$@"
    ;;
  audit)
    run_dir="${1:-runs/acceptance/$run_id}"
    shift || true
    exec python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir "$run_dir" "$@"
    ;;
  bash|shell)
    exec bash "$@"
    ;;
  *)
    exec "$cmd" "$@"
    ;;
esac
