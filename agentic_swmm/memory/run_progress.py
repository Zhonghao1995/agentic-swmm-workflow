"""Long-run progress checkpointing primitive (PRD-06 Phase C.4).

Why this module exists
----------------------
DREAM-ZS over 5000 evaluations runs for hours. Today a crash mid-run
discards everything: there is no on-disk record of how far the
sampler got, what the best objective so far is, or which parameter
set the chain was visiting. This module is the primitive every
long-running loop will write through so the next run can resume — or
at least diagnose — what happened.

Out of scope
------------
- The calibration loop's own wiring. Phase C ships only the
  primitive; the SCE-UA / DREAM-ZS runners adopt it downstream.
- Multi-chain bookkeeping. ``ProgressCheckpoint`` carries one
  ``last_param_set`` — single-chain or chain-aggregate. Multi-chain
  bookkeeping lives in the calibrator's own state once we wire it up.
- Compression / history. We keep one checkpoint per run dir; older
  states are overwritten. A separate audit log carries the trajectory
  when callers need it.

Atomic write
------------
``write_checkpoint`` writes to ``progress.json.tmp`` and renames over
``progress.json`` so a crash mid-write cannot leave a corrupt
``progress.json`` on disk. On POSIX ``Path.replace`` is atomic; on
Windows it falls back to ``os.replace`` which is the same call.
``read_checkpoint`` returns ``None`` on any error (missing,
malformed, partially written) so the calibrator never has to special
case startup vs. resume.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"

# Stable filename inside the run dir. Picked to mirror SWMM run-dir
# conventions (``model.rpt``, ``experiment_provenance.json``) — short,
# lower-case, suffix indicates format.
_FILENAME = "progress.json"


@dataclass(frozen=True)
class ProgressCheckpoint:
    """One snapshot of a long-running optimisation's state.

    Frozen so callers can stash the same instance in multiple places
    without aliasing. ``last_param_set`` defaults to ``{}`` so the
    very first checkpoint (iteration 0, no proposals yet) does not
    fail validation.
    """

    run_id: str
    algorithm: str
    iter_index: int
    total_iters: int
    best_objective_so_far: float
    wall_time_s: float
    last_param_set: dict[str, float] = field(default_factory=dict)
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-versioned dict written to disk."""
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "algorithm": self.algorithm,
            "iter_index": int(self.iter_index),
            "total_iters": int(self.total_iters),
            "best_objective_so_far": float(self.best_objective_so_far),
            "wall_time_s": float(self.wall_time_s),
            "last_param_set": dict(self.last_param_set),
            "created_at": self.created_at
            or datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        }


def write_checkpoint(run_dir: Path, ckpt: ProgressCheckpoint) -> None:
    """Atomically write ``ckpt`` to ``<run_dir>/progress.json``.

    Overwrites any prior checkpoint. The temp-file + rename dance
    guarantees a reader never sees a partially-written file. Creates
    ``run_dir`` if missing so a first-checkpoint call from a fresh
    job does not have to pre-mkdir.
    """
    if not ckpt.run_id or not ckpt.run_id.strip():
        raise ValueError("ProgressCheckpoint.run_id must be a non-empty string")
    if not ckpt.algorithm or not ckpt.algorithm.strip():
        raise ValueError("ProgressCheckpoint.algorithm must be a non-empty string")
    if ckpt.iter_index < 0:
        raise ValueError("ProgressCheckpoint.iter_index must be non-negative")
    if ckpt.total_iters < 0:
        raise ValueError("ProgressCheckpoint.total_iters must be non-negative")

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    target = run_dir / _FILENAME
    tmp = target.with_suffix(target.suffix + ".tmp")
    payload = ckpt.to_dict()
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(target)


def read_checkpoint(run_dir: Path) -> ProgressCheckpoint | None:
    """Return the checkpoint under ``run_dir`` or ``None`` on any failure.

    Failure modes (all yield ``None`` rather than raising):
    - the file does not exist
    - the JSON is malformed (torn write, truncated disk)
    - a required field is missing
    """
    target = Path(run_dir) / _FILENAME
    if not target.is_file():
        return None
    try:
        raw = target.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    try:
        return ProgressCheckpoint(
            run_id=str(payload["run_id"]),
            algorithm=str(payload["algorithm"]),
            iter_index=int(payload["iter_index"]),
            total_iters=int(payload["total_iters"]),
            best_objective_so_far=float(payload["best_objective_so_far"]),
            wall_time_s=float(payload["wall_time_s"]),
            last_param_set=dict(payload.get("last_param_set") or {}),
            created_at=payload.get("created_at"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def summarize_progress(ckpt: ProgressCheckpoint) -> str:
    """Return a one-line human-readable summary.

    Example: ``"DREAM-ZS iter 3200/5000, best NSE 0.71, 42 min elapsed"``.

    The objective name is not carried by the checkpoint (intentionally —
    the calibrator already records it in calibration_memory) so the
    summary writes ``best objective``. Wall time renders in whichever
    unit is most legible: seconds under a minute, minutes under an
    hour, hours after that.
    """
    iter_str = f"iter {ckpt.iter_index}/{ckpt.total_iters}"
    obj_str = f"best objective {ckpt.best_objective_so_far:.3f}"
    elapsed = ckpt.wall_time_s
    if elapsed < 60:
        time_str = f"{elapsed:.1f}s elapsed"
    elif elapsed < 3600:
        time_str = f"{elapsed / 60:.0f} min elapsed"
    else:
        time_str = f"{elapsed / 3600:.1f} h elapsed"
    return f"{ckpt.algorithm} {iter_str}, {obj_str}, {time_str}"
