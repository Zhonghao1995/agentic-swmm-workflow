"""Memory-application provenance helpers (P0-3).

This module provides the id-derivation scheme for modeling-memory entries
and the utility function that stamps ``memories_applied`` into a run
manifest.  It is the single seam between apply surfaces and the manifest
writer so the field is always spelled consistently.

ID scheme
---------
Neither the calibration-memory store nor the parametric-memory store
carries a dedicated ``memory_id`` column; their stable primary key is
``run_id``.  We derive a namespaced id on read, without mutating stored
files:

- Calibration memory entry  →  ``cm-<run_id>``
- Parametric memory entry   →  ``pm-<run_id>``

The prefix unambiguously identifies the store when ids from different
stores appear in the same ``memories_applied`` list.

Provenance boundary
-------------------
This module records **programmatic** memory application only — cases
where a stored entry's parameter values or calibrated priors are
injected directly into run inputs.

When memory influences a run solely through the LLM reading recalled
content in conversational context (e.g. the session-start memory block
rendered by ``memory_context.py``), there is **no stamp**.  That path
does not modify run inputs deterministically; no entry id can be
attributed with confidence.  This is an honest provenance statement, not
a gap: the stamp's value is its precision, and an imprecise stamp would
undermine Phase 1's ledger.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# ── ID derivation ──────────────────────────────────────────────────────────


def calibration_memory_id(run_id: str) -> str:
    """Return the stable provenance id for a calibration-memory entry.

    The id is deterministic and collision-free within the store because
    ``run_id`` is already a required, non-empty field (enforced by
    :func:`agentic_swmm.memory.calibration_memory.record_calibration_run`).
    """
    return f"cm-{run_id}"


def parametric_memory_id(run_id: str) -> str:
    """Return the stable provenance id for a parametric-memory entry.

    Mirrors :func:`calibration_memory_id` but uses the ``pm-`` prefix so
    ids from the two stores never collide in a shared ``memories_applied``
    list.
    """
    return f"pm-{run_id}"


# ── Manifest stamping ──────────────────────────────────────────────────────


def stamp_memories_applied(manifest_path: Path, memory_ids: list[str]) -> None:
    """Write ``memories_applied`` into an existing ``manifest.json``.

    Reads the current manifest, sets the field, and writes it back.
    Always writes the field — even an empty list — so readers can rely on
    its presence rather than treating absence as ``[]``.

    Rules:
    - If the manifest already carries ``memories_applied``, the new ids
      are *merged* (union, deduplicated, order-preserved).  This handles
      the case where two apply surfaces both stamp the same manifest (e.g.
      a run seeded from a calibration prior AND from parametric history).
    - The manifest file must already exist; this function never creates it.

    Arguments:
        manifest_path: Absolute path to the ``manifest.json`` to update.
        memory_ids: List of memory ids to record.  May be empty.

    Raises:
        FileNotFoundError: The manifest does not exist.
        json.JSONDecodeError: The manifest contains invalid JSON.
    """
    import json

    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))

    existing: list[str] = manifest.get("memories_applied") or []
    # Merge: keep existing order, append new ids not already present.
    seen: set[str] = set(existing)
    merged: list[str] = list(existing)
    for mid in memory_ids:
        if mid not in seen:
            merged.append(mid)
            seen.add(mid)

    manifest["memories_applied"] = merged
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def ensure_memories_applied_present(manifest_path: Path) -> None:
    """Ensure ``memories_applied`` key exists in a manifest (default ``[]``).

    Used by the runner to guarantee the field is always present, even for
    runs where no memory was applied.  A no-op when the field is already
    set.

    Arguments:
        manifest_path: Absolute path to ``manifest.json``.  Must exist.

    Raises:
        FileNotFoundError: The manifest does not exist.
    """
    import json

    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    if "memories_applied" not in manifest:
        manifest["memories_applied"] = []
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


__all__ = [
    "calibration_memory_id",
    "parametric_memory_id",
    "stamp_memories_applied",
    "ensure_memories_applied_present",
]
