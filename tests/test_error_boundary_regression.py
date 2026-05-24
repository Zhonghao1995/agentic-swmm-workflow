"""Regression test for one site migrated to ``@on_exception_return_default``.

Issue #207 migrates the ~10 most-critical tier-1 ``try/except Exception:
return X`` sites in the agent runtime to the new
``@on_exception_return_default`` decorator. This file pins one of them
(``skill_router._on_disk_skill_names``) as a worked example so a future
refactor that accidentally removes the boundary fails loudly rather
than silently re-introducing a crash path.

Why ``_on_disk_skill_names``
----------------------------
* It is a tier-1 site (skill discovery during planner bootstrap; a
  crash here would abort every chat turn).
* It returns a constant default (``[]``) so the migration is a pure
  consolidation — no semantic change to assert.
* Its dependency surface (``runtime.registry.discover_skills``) is
  small and stub-friendly, so the regression test does not need an
  on-disk fixture skill tree.

The behaviour pinned here:

1. Before migration AND after migration, the function returns ``[]``
   when ``discover_skills`` raises.
2. After migration, the catch ALSO appends one row to
   ``<config_dir>/silent_fallbacks.jsonl`` carrying
   ``scope == "skill_discovery"``. This assertion is what makes the
   test "red until migration lands" — a hand-rolled try/except does
   not touch the structured-log file.

Companion abstract tests live in ``test_error_boundary.py``.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.conftest import read_silent_fallback_events as _read_events

# NOTE: ``isolated_config_dir`` fixture comes from ``tests/conftest.py``.


def test_on_disk_skill_names_returns_empty_when_discover_raises(
    isolated_config_dir,
):
    """Crash inside ``discover_skills`` collapses to ``[]`` (no propagation).

    This pins the legacy contract the hand-rolled try/except gave us;
    the migration to ``@on_exception_return_default`` must preserve it
    bit-for-bit. The shape of the planner bootstrap depends on
    ``_on_disk_skill_names`` never raising — if it did, every chat turn
    would die during ``SkillRouter`` construction.
    """
    from agentic_swmm.agent import skill_router

    with patch(
        "agentic_swmm.runtime.registry.discover_skills",
        side_effect=RuntimeError("discovery exploded"),
    ):
        assert skill_router._on_disk_skill_names() == []


def test_on_disk_skill_names_logs_structured_fallback_event(
    isolated_config_dir,
):
    """The migrated site appends a ``skill_discovery`` row to the jsonl.

    This is the assertion that flips the test from "trivially green
    against the hand-rolled try/except" to "red until the migration
    lands". A developer who later strips the decorator (e.g. while
    refactoring imports) would see this test fail and notice the
    fallback channel is no longer wired up.
    """
    from agentic_swmm.agent import skill_router

    with patch(
        "agentic_swmm.runtime.registry.discover_skills",
        side_effect=RuntimeError("discovery exploded"),
    ):
        skill_router._on_disk_skill_names()

    events = _read_events(isolated_config_dir / "silent_fallbacks.jsonl")
    assert len(events) == 1
    event = events[0]
    assert event["scope"] == "skill_discovery"
    assert event["exception_type"] == "RuntimeError"
    assert event["exception_str"] == "discovery exploded"


def test_on_disk_skill_names_happy_path_unchanged(isolated_config_dir):
    """Successful discovery still returns the list — no decorator noise.

    The decorator must be transparent on the happy path; a regression
    that started returning a truncated list or writing a fallback row
    on success would break planner bootstrap silently.
    """
    from agentic_swmm.agent import skill_router

    fake_records = [{"name": "swmm-builder"}, {"name": "swmm-runner"}]
    with patch(
        "agentic_swmm.runtime.registry.discover_skills",
        return_value=fake_records,
    ):
        names = skill_router._on_disk_skill_names()

    assert names == ["swmm-builder", "swmm-runner"]
    # No exception → no jsonl row.
    assert _read_events(isolated_config_dir / "silent_fallbacks.jsonl") == []
