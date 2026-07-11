"""Manifest schemas for the prepared-input run pipeline (``aiswmm run``).

Three JSON artifacts document a prepared-input run: the QA summary
(``07_qa/qa_summary.json`` plus its peak/continuity side files), the
builder handoff manifest (``05_builder/manifest.json``) and the
top-level run manifest (``manifest.json``, the source of truth
downstream consumers read — Key invariant 3). Their schemas used to be
assembled field-by-field inside the CLI verb, which left "what does
``outputs.built_inp`` mean" answerable only by reading the whole
command function. The builders live here as plain functions —
dicts in, dicts out — so the schemas are testable without argv.

Stage numbers above are the ADR-0004 canonical ones (see
``agentic_swmm.agent.swmm_runtime.run_layout``); callers pass in the
actual ``qa_dir``/``runner_dir`` paths, so this module only embeds the
numbers in documentation and in the human-readable notes below.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent.session_header import environment_fingerprint
from agentic_swmm.agent.swmm_runtime import run_layout

from agentic_swmm.utils.hashing import sha256_of_file
from agentic_swmm.utils.paths import repo_root


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel_to_repo(path: Path) -> str:
    """Repo-relative string when inside the repo; absolute otherwise."""
    try:
        return str(path.resolve().relative_to(repo_root().resolve()))
    except ValueError:
        return str(path.resolve())


sha256_of = sha256_of_file


def source_type_of(path: Path) -> str:
    """``repository_inp`` for in-repo INPs, ``external_inp_import`` otherwise."""
    try:
        path.resolve().relative_to(repo_root().resolve())
    except ValueError:
        return "external_inp_import"
    return "repository_inp"


def parse_runner_manifest(stdout: str) -> dict[str, Any]:
    """Parse the swmm-runner script's stdout JSON; ``{}`` when malformed."""
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_qa_summary(
    runner_manifest: dict[str, Any], *, qa_dir: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Derive ``(peak, continuity, qa_summary)`` from the runner manifest.

    The five checks are the prepared-input pipeline's smoke QA: SWMM
    exited zero, both artifacts exist, and both metric families parsed.
    """
    runner_files = (
        runner_manifest.get("files")
        if isinstance(runner_manifest.get("files"), dict)
        else {}
    )
    peak = (runner_manifest.get("metrics") or {}).get("peak") or {}
    continuity = (runner_manifest.get("metrics") or {}).get("continuity") or {}
    continuity_errors = continuity.get("continuity_error_percent") or {}
    qa_checks = [
        {
            "id": "swmm_return_code_zero",
            "ok": runner_manifest.get("return_code") == 0,
            "detail": f"return_code={runner_manifest.get('return_code')}",
        },
        {
            "id": "runner_rpt_exists",
            "ok": bool(runner_files.get("rpt") and Path(runner_files["rpt"]).exists()),
            "detail": runner_files.get("rpt"),
        },
        {
            "id": "runner_out_exists",
            "ok": bool(runner_files.get("out") and Path(runner_files["out"]).exists()),
            "detail": runner_files.get("out"),
        },
        {
            "id": "peak_parsed",
            "ok": peak.get("peak") is not None,
            "detail": f"node={peak.get('node')} source={peak.get('source')} peak={peak.get('peak')}",
        },
        {
            "id": "continuity_parsed",
            "ok": any(value is not None for value in continuity_errors.values()),
            "detail": continuity_errors,
        },
    ]
    qa_summary = {
        "schema_version": "1.0",
        "status": "pass" if all(check["ok"] for check in qa_checks) else "fail",
        "checks": qa_checks,
        "peak_file": str(qa_dir / "runner_peak.json"),
        "continuity_file": str(qa_dir / "runner_continuity.json"),
    }
    return peak, continuity, qa_summary


def build_builder_manifest(
    *,
    source_inp: Path,
    run_inp: Path,
    builder_inp: Path,
    sidecar_inputs: list[Path],
    source_type: str,
) -> dict[str, Any]:
    """The ``05_builder/manifest.json`` prepared-input handoff record."""
    handoff_note = (
        "Prepared-input workflow: INP was supplied by the user/example and "
        f"copied into {run_layout.BUILDER} as the execution handoff."
    )
    return {
        "schema_version": "1.0",
        "stage": "prepared-input-handoff",
        "source_type": source_type,
        "outputs": {"inp": rel_to_repo(builder_inp)},
        "inputs": {
            "source_inp": {
                "path": rel_to_repo(source_inp),
                "sha256": sha256_of(source_inp),
                "source_type": source_type,
            },
            "copied_inp": {"path": rel_to_repo(run_inp), "sha256": sha256_of(run_inp)},
            "sidecar_files": [
                {"path": rel_to_repo(path), "sha256": sha256_of(path)}
                for path in sidecar_inputs
            ],
        },
        "validation": {
            "status": "pass",
            "notes": [
                handoff_note,
                "External INP imports are copied into the run directory before execution; SWMM runs against the run-local copy.",
            ]
            if source_type == "external_inp_import"
            else [handoff_note],
        },
    }


def build_top_manifest(
    *,
    source_inp: Path,
    run_inp: Path,
    builder_inp: Path,
    sidecar_inputs: list[Path],
    source_type: str,
    runner_manifest: dict[str, Any],
    runner_files: dict[str, Any],
    runner_dir: Path,
    qa_dir: Path,
    run_dir: Path,
    command_trace: dict[str, Any],
) -> dict[str, Any]:
    """The top-level ``manifest.json`` — the run's source of truth.

    ``case_id`` stamping stays with the CLI (it validates user input);
    callers set the key on the returned dict when a valid slug arrives.
    """
    return {
        "schema_version": "1.0",
        "generated_by": "agentic-swmm",
        "generated_at_utc": now_utc(),
        "run_id": run_dir.name,
        "pipeline": source_type if source_type == "external_inp_import" else "prepared-input-cli",
        "inputs": {
            "source_inp": {
                "path": rel_to_repo(source_inp),
                "sha256": sha256_of(source_inp),
                "source_type": source_type,
            },
            "run_inp": {
                "path": rel_to_repo(run_inp),
                "sha256": sha256_of(run_inp),
                "imported_copy": True,
            },
            "builder_inp": {
                "path": rel_to_repo(builder_inp),
                "sha256": sha256_of(builder_inp),
            },
            "sidecar_files": [
                {"path": rel_to_repo(path), "sha256": sha256_of(path)}
                for path in sidecar_inputs
            ],
        },
        "outputs": {
            "built_inp": {"path": rel_to_repo(builder_inp)},
            "runner_rpt": {"path": rel_to_repo(Path(runner_files["rpt"]))}
            if runner_files.get("rpt")
            else None,
            "runner_out": {"path": rel_to_repo(Path(runner_files["out"]))}
            if runner_files.get("out")
            else None,
            "runner_manifest": {"path": rel_to_repo(runner_dir / "manifest.json")},
            "qa_summary": {"path": rel_to_repo(qa_dir / "qa_summary.json")},
        },
        "commands": [command_trace],
        "tools": {
            "agentic_swmm_command": "run",
            "swmm5_version": (runner_manifest.get("swmm5") or {}).get("version"),
        },
        # ADR-0003 layer 3: where this run actually executed (captured,
        # not prescribed). The audit script copies this block verbatim
        # into experiment_provenance.json (pure JSON read, so the skill
        # script stays agentic_swmm-import-free).
        "environment": {
            **environment_fingerprint(),
            "swmm5_version": (runner_manifest.get("swmm5") or {}).get("version"),
        },
    }


__all__ = [
    "build_builder_manifest",
    "build_qa_summary",
    "build_top_manifest",
    "now_utc",
    "parse_runner_manifest",
    "rel_to_repo",
    "sha256_of",
    "source_type_of",
]
