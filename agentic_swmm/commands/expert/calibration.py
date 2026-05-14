"""``aiswmm calibration accept <run_dir>`` (PRD-Z + issue #54).

Promotes a calibration candidate to canonical. The agent never patches
the canonical INP directly; instead each calibration strategy writes
three artefacts to ``<run_dir>/09_audit/``:

* ``candidate_calibration.json`` (best params + metrics + SHA of patch)
* ``candidate_inp_patch.json``  (the diff to apply)
* ``calibration_report.md``     (human-readable summary)

This subcommand is the **only** path that turns the candidate into a
real on-disk change. It:

1. Reads ``candidate_calibration.json`` and refuses if missing.
2. Reads ``candidate_inp_patch.json`` and refuses if missing.
3. Recomputes the SHA of the patch JSON, compares against the SHA the
   candidate recorded, and refuses on mismatch (tamper detection).
4. Reads the canonical INP referenced by the candidate and applies
   the patch via the shared :mod:`inp_patch` machinery.
5. Records a ``calibration_accept`` row in ``human_decisions`` whose
   ``decision_text`` quotes the applied patch SHA so an auditor can
   trace exactly which patch landed.

Steps 1–3 are pure read-checks; step 4 is the first (and only) write
to the canonical INP performed by this CLI.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from agentic_swmm.commands.expert._shared import (
    record_and_print,
    resolve_provenance_path,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
CALIBRATION_SCRIPTS_DIR = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"


def _load_calibration_module(name: str):
    """Load a module from ``skills/swmm-calibration/scripts/<name>.py``.

    The scripts directory is not part of the ``agentic_swmm`` package
    (skills are user-facing scaffolds), so we import them by path. We
    cache through ``sys.modules`` so repeated calls in one process are
    cheap.
    """
    cache_key = f"_expert_calibration_{name}"
    if cache_key in sys.modules:
        return sys.modules[cache_key]
    path = CALIBRATION_SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(cache_key, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[cache_key] = module
    spec.loader.exec_module(module)
    return module


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "calibration",
        help=(
            "Expert-only: promote calibration outcomes. "
            "Subcommand 'accept' verifies the candidate handover, applies "
            "the recorded INP patch, and records the human decision."
        ),
    )
    inner = parser.add_subparsers(dest="calibration_command", required=True)
    accept = inner.add_parser(
        "accept",
        help="Verify candidate, apply INP patch, record human_decisions entry.",
    )
    accept.add_argument(
        "run_dir",
        type=Path,
        help="Path to the run directory whose calibration is being accepted.",
    )
    accept.add_argument(
        "--note",
        default=None,
        help="Optional free-text note saved with the human_decisions record.",
    )
    accept.set_defaults(func=main)


def _print_error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _resolve_canonical_inp(run_dir: Path, candidate: dict[str, Any]) -> Path | None:
    """Resolve ``canonical_inp_ref`` to an absolute path or return ``None``.

    Candidates record the canonical INP path the writer was pointed at;
    we accept both absolute paths (when calibration was run with an
    absolute ``--base-inp``) and run-dir-relative paths (when the INP
    was inside the run dir, which is the common case).
    """
    ref = candidate.get("canonical_inp_ref")
    if not isinstance(ref, str) or not ref:
        return None
    candidate_path = Path(ref)
    if candidate_path.is_absolute() and candidate_path.is_file():
        return candidate_path
    rel_to_run = run_dir / candidate_path
    if rel_to_run.is_file():
        return rel_to_run
    if candidate_path.is_file():
        return candidate_path
    return None


def main(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir.resolve()
    provenance = resolve_provenance_path(run_dir, require_exists=True)
    if provenance is None:
        return 2

    candidate_writer = _load_calibration_module("candidate_writer")
    inp_patch = _load_calibration_module("inp_patch")

    # --- Step 1: candidate must exist --------------------------------------
    try:
        candidate = candidate_writer.read_candidate(run_dir)
    except FileNotFoundError as exc:
        _print_error(
            f"candidate_calibration.json not found at {exc}. "
            "Run a calibration strategy with --candidate-run-dir first."
        )
        return 3

    # --- Step 2: patch must exist ------------------------------------------
    try:
        patch = candidate_writer.read_patch(run_dir)
    except FileNotFoundError as exc:
        _print_error(
            f"candidate_inp_patch.json not found at {exc}; the candidate "
            "handover is incomplete."
        )
        return 3

    # --- Step 3: tamper detection ------------------------------------------
    recorded_sha = candidate.get("candidate_inp_patch_sha256")
    actual_sha = candidate_writer.sha256_of_canonical_json(patch)
    if not recorded_sha or actual_sha != recorded_sha:
        _print_error(
            "candidate_inp_patch.json sha256 mismatch — refusing accept. "
            f"recorded={recorded_sha} actual={actual_sha}. "
            "Re-run calibration to regenerate the candidate."
        )
        return 4

    # --- Step 4: apply patch to canonical INP ------------------------------
    canonical_inp = _resolve_canonical_inp(run_dir, candidate)
    if canonical_inp is None:
        _print_error(
            "canonical INP not found from candidate_calibration.json "
            f"(canonical_inp_ref={candidate.get('canonical_inp_ref')!r}). "
            "Cannot apply patch."
        )
        return 5
    patch_map: dict[str, dict[str, Any]] = {}
    params: dict[str, Any] = {}
    for edit in patch.get("edits") or []:
        param = edit["param"]
        patch_map[param] = {
            "section": edit["section"],
            "object": edit["object"],
            "field_index": edit["field_index"],
        }
        params[param] = edit["new_value"]
    if not patch_map:
        _print_error("candidate_inp_patch.json contains no edits; nothing to apply.")
        return 5
    try:
        patched_text = inp_patch.patch_inp_text(
            canonical_inp.read_text(errors="ignore"),
            patch_map,
            params,
        )
    except Exception as exc:  # noqa: BLE001
        _print_error(f"failed to apply candidate patch to {canonical_inp}: {exc}")
        return 5
    canonical_inp.write_text(patched_text, encoding="utf-8")

    # --- Step 5: record human decision -------------------------------------
    note_parts: list[str] = [f"candidate_inp_patch.json sha256={actual_sha}"]
    if args.note:
        note_parts.append(args.note)
    decision_text = "; ".join(note_parts)
    evidence_rel = "09_audit/candidate_calibration.json"
    record_and_print(
        provenance,
        action="calibration_accept",
        evidence_ref=evidence_rel,
        decision_text=decision_text,
    )
    return 0
