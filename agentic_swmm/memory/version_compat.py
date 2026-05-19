"""Schema-version migration registry (PRD-06 Phase C.3).

Why this module exists
----------------------
``parametric_memory``, ``calibration_memory``, and ``negative_lessons``
all carry ``SCHEMA_VERSION = "1.0"`` today. As soon as one of them
adds, renames, or types-coerces a field the on-disk JSONL will carry a
mixture of schemas — older rows can outlive the code that wrote them
by years on a long-running project.

This module is the migration mechanism. ``recall_*`` verbs pipe every
row through :func:`migrate_record` before returning it, so callers
always see fully-populated current-schema rows regardless of when the
row was written. Phase C ships the wiring + a no-op migration as a
worked example; real migrations land when a schema actually evolves.

Why explicit registry vs. ``if schema_version == "1.0"`` branches
-----------------------------------------------------------------
A registry of chained migration functions matches what every long-lived
serialization layer eventually grows. The alternative is dozens of
sprinkled ``if`` blocks across the recall verbs — they accumulate fast
and become impossible to test in isolation. The registry's migrations
are pure dict-in/dict-out functions; they unit-test cleanly.

How a new migration lands
-------------------------
1. Bump ``SCHEMA_VERSION`` in the writing module to e.g. ``"1.1"``.
2. Append a function to the store's list in :data:`MIGRATIONS` that
   takes a ``"1.0"`` row and returns a ``"1.1"`` row.
3. Make sure the function sets ``row["schema_version"] = "1.1"`` so
   chaining works.
4. Add tests covering at least one ``"1.0"`` row migrating cleanly to
   the current schema.

Out of scope
------------
- Downgrade (read-only newer than writer): callers running an old
  release should refuse the row rather than silently dropping fields.
  That refusal lives in the calling module, not here.
- Schema evolution of the JSONL filename itself; the migration acts
  on parsed records only.
"""

from __future__ import annotations

from typing import Any, Callable


# Pure-function alias for readability.
MigrationFn = Callable[[dict[str, Any]], dict[str, Any]]


def _identity_migration_1_0(row: dict[str, Any]) -> dict[str, Any]:
    """No-op ``1.0 -> 1.0`` worked example.

    Real migrations bump ``schema_version``; this one leaves the row
    untouched so chaining a future ``1.0 -> 1.1`` migration becomes a
    one-line append rather than a refactor of the recall pipeline.
    """
    return row


# Migration registry keyed by store name. Each value is an ordered list
# of migration functions: ``MIGRATIONS[store][i]`` upgrades a row from
# schema version ``versions[i]`` to ``versions[i+1]``. ``migrate_record``
# applies as many as needed to reach the head of the list.
#
# The first entry in each list is the ``1.0`` worked-example identity
# migration. Append new entries in order — each function is responsible
# for setting ``row["schema_version"]`` to its output version so the
# loop terminates.
MIGRATIONS: dict[str, list[MigrationFn]] = {
    "parametric_memory": [_identity_migration_1_0],
    "calibration_memory": [_identity_migration_1_0],
    "negative_lessons": [_identity_migration_1_0],
}


def migrate_record(store_name: str, record: dict[str, Any]) -> dict[str, Any]:
    """Bring ``record`` from its on-disk schema to the current schema.

    ``store_name`` indexes :data:`MIGRATIONS`. Unknown stores pass the
    record through unchanged — a future store can register lazily
    without breaking older callers. Records with no ``schema_version``
    field are treated as ``"1.0"`` (the first version ever shipped).

    The function is pure: it returns a new dict rather than mutating
    the input, so recall pipelines can keep the on-disk JSONL intact
    for re-reading.
    """
    if not isinstance(record, dict):
        return record  # type: ignore[return-value]

    migrations = MIGRATIONS.get(store_name)
    if not migrations:
        return dict(record)

    # Treat a missing schema_version as 1.0 — that is what the original
    # writer emitted before this module landed.
    working = dict(record)
    working.setdefault("schema_version", "1.0")

    # For Phase C every entry is the identity migration. Once a real
    # 1.0 -> 1.1 lands, this loop applies it whenever the row carries
    # ``schema_version == "1.0"`` and stops once it reaches the head.
    for migration in migrations:
        working = migration(working)
    return working
