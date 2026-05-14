"""``aiswmm gap promote-to-case`` + ``aiswmm gap list-case-defaults`` (PRD-GF-PROMOTE).

Expert-only CLI that turns a recorded gap-fill decision into a case-
level default. Mirrors the governance pattern of ``aiswmm calibration
accept`` (PR #47): not exposed as an agent ToolSpec, not registered as
an MCP tool. Promotion is an irreversible authority action by the
human modeller.

Two subcommands:

* ``aiswmm gap promote-to-case <run_dir> <decision_id>`` — reads the
  named decision from ``<run_dir>/09_audit/gap_decisions.json``, writes
  the corresponding entry to ``cases/<case_id>/gap_defaults.yaml``, and
  appends a ``human_decisions`` ledger entry with
  ``action: gap_promote_to_case`` to the source run.

* ``aiswmm gap list-case-defaults <case_id>`` — prints the case-defaults
  file in a human-friendly table (field | value | source | promoted_at).

The case-defaults YAML schema:

    schema_version: 1
    case_id: tod-creek
    entries:
      manning_n_imperv:
        value: 0.013
        source: "promoted from runs/.../gap_decisions.json#dec-abc123"
        promoted_at: "2026-05-14T19:50:00Z"
        promoted_by: "human (expert CLI)"
        notes: "optional free-form note"

Refusal paths (matched by the test suite):

* ``decision_id`` not present in the source run's gap-decisions ledger
  -> exit non-zero with a message naming the missing id.
* The source decision's ``proposer_overridden`` is true and
  ``--accept-override`` was not passed -> exit non-zero with a hint.
* No ``case_id`` resolves (no provenance ``case_id``, no ``--case-id``)
  -> exit non-zero with an ``aiswmm case init`` hint.
* The target case-defaults file already has an entry for the field
  whose value differs from the incoming one and ``--force`` was not
  passed -> exit non-zero with a hint.

The case-defaults reader/writer used by this module is intentionally
self-contained: it is not part of the case_registry surface because
the gap-defaults schema is owned by this PRD (the case_registry only
owns ``case_meta.yaml``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agentic_swmm.case.case_id import (
    CaseIdResolutionError,
    CaseIdValidationError,
    resolve_case_id,
)
from agentic_swmm.commands.expert._shared import (
    record_and_print,
    resolve_provenance_path,
)
from agentic_swmm.utils.paths import repo_root as default_repo_root


SCHEMA_VERSION = 1
CASE_DEFAULTS_FILENAME = "gap_defaults.yaml"


# --- Case-defaults schema (read/write helpers) --------------------------------
@dataclass(frozen=True)
class CaseDefaultEntry:
    """One promoted gap-fill default.

    The fields mirror the PRD's schema block. ``notes`` is the only
    optional field; the rest are populated by the CLI on every
    promote. All values are plain Python primitives so the YAML round-
    trip stays lossless without custom representers.
    """

    value: Any
    source: str
    promoted_at: str
    promoted_by: str
    notes: str | None = None


@dataclass(frozen=True)
class CaseDefaults:
    """In-memory view over ``cases/<id>/gap_defaults.yaml``.

    The ``entries`` map is field-name → :class:`CaseDefaultEntry`. The
    reader returns an empty :class:`CaseDefaults` when the file is
    missing so callers can treat "no promotions yet" identically to
    "an empty file on disk".
    """

    case_id: str
    schema_version: int = SCHEMA_VERSION
    entries: dict[str, CaseDefaultEntry] = field(default_factory=dict)


def _resolve_repo_root() -> Path:
    """Return the active repo root, honouring ``AISWMM_REPO_ROOT``.

    Tests inject a temporary directory through the env var so the CLI
    subprocess writes its ``cases/`` artefacts under the test fixture
    instead of the real repo. Production callers leave the env var
    unset and the function falls back to the canonical repo root.
    """
    override = os.environ.get("AISWMM_REPO_ROOT")
    if override:
        return Path(override)
    return default_repo_root()


def _case_defaults_path(repo_root: Path, case_id: str) -> Path:
    return repo_root / "cases" / case_id / CASE_DEFAULTS_FILENAME


def read_case_defaults(case_id: str, *, repo_root: Path | None = None) -> CaseDefaults:
    """Read the case-defaults file or return an empty container.

    A missing or unparseable file yields an empty :class:`CaseDefaults`
    with the requested ``case_id``. The reader is forgiving so a half-
    written file (e.g. interrupted promote) does not lock out future
    promotes — the writer will overwrite the file atomically anyway.
    """
    base = repo_root if repo_root is not None else _resolve_repo_root()
    path = _case_defaults_path(base, case_id)
    if not path.is_file():
        return CaseDefaults(case_id=case_id)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return CaseDefaults(case_id=case_id)
    if not isinstance(payload, dict):
        return CaseDefaults(case_id=case_id)
    raw_entries = payload.get("entries") or {}
    entries: dict[str, CaseDefaultEntry] = {}
    if isinstance(raw_entries, dict):
        for name, raw in raw_entries.items():
            if not isinstance(raw, dict):
                continue
            entries[str(name)] = CaseDefaultEntry(
                value=raw.get("value"),
                source=str(raw.get("source") or ""),
                promoted_at=str(raw.get("promoted_at") or ""),
                promoted_by=str(raw.get("promoted_by") or ""),
                notes=(str(raw["notes"]) if raw.get("notes") else None),
            )
    return CaseDefaults(
        case_id=str(payload.get("case_id") or case_id),
        schema_version=int(payload.get("schema_version") or SCHEMA_VERSION),
        entries=entries,
    )


def write_case_defaults(
    case_id: str,
    entries: dict[str, CaseDefaultEntry],
    *,
    repo_root: Path | None = None,
) -> Path:
    """Serialise ``entries`` to ``cases/<id>/gap_defaults.yaml``.

    The writer always emits the canonical key order
    (``schema_version`` → ``case_id`` → ``entries``) so humans diffing
    the file in PRs see a predictable shape. Returns the written path
    so callers can include it in audit messages.
    """
    base = repo_root if repo_root is not None else _resolve_repo_root()
    path = _case_defaults_path(base, case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "entries": {
            name: {
                "value": entry.value,
                "source": entry.source,
                "promoted_at": entry.promoted_at,
                "promoted_by": entry.promoted_by,
                **({"notes": entry.notes} if entry.notes else {}),
            }
            for name, entry in entries.items()
        },
    }
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


# --- CLI registration ---------------------------------------------------------
def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``gap`` parser with two subcommands.

    The PRD's CLI surface is ``aiswmm gap promote-to-case`` and
    ``aiswmm gap list-case-defaults``. A single top-level ``gap``
    parser groups them so the help renders the two together. This
    mirrors the multi-subcommand pattern used by
    ``aiswmm calibration {accept}``.
    """
    parser = subparsers.add_parser(
        "gap",
        help=(
            "Expert-only: gap-fill case-level promotion. Subcommands "
            "'promote-to-case' and 'list-case-defaults'."
        ),
    )
    inner = parser.add_subparsers(dest="gap_command", required=True)

    promote = inner.add_parser(
        "promote-to-case",
        help=(
            "Promote a gap-fill decision from a run into "
            "cases/<case_id>/gap_defaults.yaml."
        ),
    )
    promote.add_argument(
        "run_dir",
        type=Path,
        help=(
            "Path to the source run directory holding the gap_decisions.json"
            " entry to promote."
        ),
    )
    promote.add_argument(
        "decision_id",
        help="decision_id from the source run's gap_decisions.json.",
    )
    promote.add_argument(
        "--case-id",
        dest="case_id",
        default=None,
        metavar="SLUG",
        help=(
            "Override the case inferred from the run's "
            "experiment_provenance.json. Default: inferred."
        ),
    )
    promote.add_argument(
        "--accept-override",
        action="store_true",
        help=(
            "Required when the source decision has "
            "proposer_overridden=true (so promotion is a conscious "
            "three-step act of propose -> override -> promote)."
        ),
    )
    promote.add_argument(
        "--note",
        default=None,
        help="Optional free-text note saved on the case-defaults entry.",
    )
    promote.add_argument(
        "--force",
        action="store_true",
        help=(
            "Required when an entry for the same field already exists "
            "in cases/<id>/gap_defaults.yaml with a different value."
        ),
    )
    promote.set_defaults(func=promote_to_case_main)

    listing = inner.add_parser(
        "list-case-defaults",
        help="Print cases/<case_id>/gap_defaults.yaml in a human-friendly table.",
    )
    listing.add_argument(
        "case_id",
        help="Slug of the case whose gap_defaults.yaml to print.",
    )
    listing.set_defaults(func=list_case_defaults_main)


# --- Implementation: promote_to_case ------------------------------------------
def _print_error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _read_gap_decisions_ledger(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "09_audit" / "gap_decisions.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a JSON object")
    return payload


def _find_decision(ledger: dict[str, Any], decision_id: str) -> dict[str, Any] | None:
    """Return the matching decision dict or ``None``.

    The ledger schema (from ``agentic_swmm.gap_fill.recorder``) is
    ``{"schema_version": "1", "decisions": [...]}``. The function does
    not raise on a missing decision — the caller decides how to surface
    the failure (the CLI prints a hint naming the missing id).
    """
    decisions = ledger.get("decisions") or []
    if not isinstance(decisions, list):
        return None
    for entry in decisions:
        if isinstance(entry, dict) and entry.get("decision_id") == decision_id:
            return entry
    return None


def _resolve_case_for_run(
    *,
    declared: str | None,
    run_dir: Path,
) -> str | None:
    """Resolve ``case_id`` from the explicit flag or the run's provenance.

    Returns ``None`` if no case_id is available (the CLI then prints
    the ``aiswmm case init`` hint). Validates the slug — a malformed
    ``--case-id`` is a user typo we want to surface, not an inference
    failure.
    """
    provenance = run_dir / "09_audit" / "experiment_provenance.json"
    session_state: dict[str, Any] | None = None
    if provenance.is_file():
        try:
            session_state = json.loads(provenance.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            session_state = None
        if not isinstance(session_state, dict):
            session_state = None

    try:
        resolved = resolve_case_id(
            declared=declared,
            run_dir=run_dir,
            session_state=session_state,
            interactive=False,
        )
    except (CaseIdResolutionError, CaseIdValidationError):
        return None
    return resolved.value


def promote_to_case_main(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir.resolve()
    if not run_dir.is_dir():
        _print_error(f"run_dir is not a directory: {run_dir}")
        return 2

    # --- Step 1: read decision ----------------------------------------------
    try:
        ledger = _read_gap_decisions_ledger(run_dir)
    except FileNotFoundError as exc:
        _print_error(
            f"gap_decisions.json not found at {exc}; "
            "the run has no recorded gap-fill decisions to promote."
        )
        return 3
    except (json.JSONDecodeError, ValueError) as exc:
        _print_error(f"gap_decisions.json could not be parsed: {exc}")
        return 3
    decision = _find_decision(ledger, args.decision_id)
    if decision is None:
        _print_error(
            f"decision_id {args.decision_id!r} not found in "
            f"{run_dir / '09_audit' / 'gap_decisions.json'}"
        )
        return 3

    # --- Step 2: refuse if proposer_overridden without --accept-override ----
    if decision.get("proposer_overridden") and not args.accept_override:
        _print_error(
            f"decision {args.decision_id!r} has proposer_overridden=true; "
            "re-run with --accept-override to confirm you want to promote "
            "an overridden value to a case-level default."
        )
        return 4

    # --- Step 3: resolve case_id --------------------------------------------
    case_id = _resolve_case_for_run(declared=args.case_id, run_dir=run_dir)
    if case_id is None:
        _print_error(
            "no case_id could be resolved from the run's "
            "experiment_provenance.json. Pass --case-id <slug> or run "
            "`aiswmm case init <slug>` first to initialise the case."
        )
        return 5

    # --- Step 4: read existing case-defaults, check conflict ----------------
    field_name = str(decision.get("field") or "")
    if not field_name:
        _print_error(
            f"decision {args.decision_id!r} carries no field name; refusing to promote."
        )
        return 3
    new_value = decision.get("final_value")
    existing = read_case_defaults(case_id)
    if field_name in existing.entries:
        prior = existing.entries[field_name]
        if prior.value != new_value and not args.force:
            _print_error(
                f"cases/{case_id}/gap_defaults.yaml already has an entry for "
                f"{field_name!r} with value {prior.value!r}; re-run with "
                "--force to overwrite (or edit the YAML by hand)."
            )
            return 6

    # --- Step 5: write the case-defaults entry ------------------------------
    promoted_at = _now_utc_iso()
    promoted_by = os.environ.get("USER", "human") + " (expert CLI)"
    decisions_rel = (
        f"runs/{run_dir.name}/09_audit/gap_decisions.json#{args.decision_id}"
    )
    new_entry = CaseDefaultEntry(
        value=new_value,
        source=f"promoted from {decisions_rel}",
        promoted_at=promoted_at,
        promoted_by=promoted_by,
        notes=args.note,
    )
    new_entries = dict(existing.entries)
    new_entries[field_name] = new_entry
    case_path = write_case_defaults(case_id, new_entries)

    # --- Step 6: append human_decisions ledger entry ------------------------
    provenance = resolve_provenance_path(run_dir, require_exists=False)
    if provenance is None:
        # Should not happen — the run_dir was checked above. Be defensive.
        return 0
    evidence_ref = f"09_audit/gap_decisions.json#{args.decision_id}"
    target_ref = f"cases/{case_id}/gap_defaults.yaml#{field_name}"
    decision_text_parts = [f"target_ref={target_ref}"]
    if args.note:
        decision_text_parts.append(f"note={args.note}")
    record_and_print(
        provenance,
        action="gap_promote_to_case",
        evidence_ref=evidence_ref,
        decision_text="; ".join(decision_text_parts),
    )
    print(f"wrote {case_path}")
    return 0


# --- Implementation: list_case_defaults ---------------------------------------
def list_case_defaults_main(args: argparse.Namespace) -> int:
    case_id = args.case_id
    payload = read_case_defaults(case_id)
    if not payload.entries:
        print(f"no gap defaults for case {case_id!r} "
              f"(expected at cases/{case_id}/gap_defaults.yaml)")
        return 0
    print(f"case_id: {case_id}")
    print(f"{'field':<28} {'value':<14} {'promoted_at':<22} source")
    print("-" * 80)
    for name in sorted(payload.entries):
        entry = payload.entries[name]
        print(
            f"{name:<28} {str(entry.value):<14} "
            f"{entry.promoted_at:<22} {entry.source}"
        )
    return 0


__all__ = [
    "CASE_DEFAULTS_FILENAME",
    "SCHEMA_VERSION",
    "CaseDefaultEntry",
    "CaseDefaults",
    "list_case_defaults_main",
    "promote_to_case_main",
    "read_case_defaults",
    "register",
    "write_case_defaults",
]
