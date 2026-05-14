"""Confidence-score formula (ME-1, issue #61).

``compute_confidence(evidence_count, last_seen_utc, half_life_days)``
returns ``evidence_count * exp(-age_days / half_life_days)``, where
``age_days`` is measured from ``last_seen_utc`` up to "now" (a UTC
timestamp). The formula gives:

- Maximum confidence (= ``evidence_count``) when the pattern was just
  observed (age = 0).
- Half of the confidence after exactly one ``half_life_days``.
- ``evidence_count * exp(-2)`` (~13.5%) after two half-lives.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def test_compute_confidence_today_equals_evidence_count() -> None:
    from agentic_swmm.memory.lessons_metadata import compute_confidence

    now = datetime.now(timezone.utc)
    score = compute_confidence(7, _iso(now), 90, now=now)
    assert score == pytest.approx(7.0, rel=1e-3)


def test_compute_confidence_half_life_decays_by_half() -> None:
    from agentic_swmm.memory.lessons_metadata import compute_confidence

    now = datetime.now(timezone.utc)
    one_half_life_ago = now - timedelta(days=90)
    score = compute_confidence(10, _iso(one_half_life_ago), 90, now=now)
    assert score == pytest.approx(10 * math.exp(-1.0), rel=1e-3)


def test_compute_confidence_two_half_lives_decays_to_exp_minus_two() -> None:
    from agentic_swmm.memory.lessons_metadata import compute_confidence

    now = datetime.now(timezone.utc)
    two_half_lives_ago = now - timedelta(days=180)
    score = compute_confidence(7, _iso(two_half_lives_ago), 90, now=now)
    expected = 7 * math.exp(-2.0)
    # Issue #61 calls this approximately 0.947.
    assert score == pytest.approx(expected, rel=1e-3)
    assert score == pytest.approx(0.947, abs=0.01)


def test_compute_confidence_handles_zero_evidence() -> None:
    from agentic_swmm.memory.lessons_metadata import compute_confidence

    now = datetime.now(timezone.utc)
    assert compute_confidence(0, _iso(now), 90, now=now) == 0.0


def test_compute_confidence_rejects_non_positive_half_life() -> None:
    from agentic_swmm.memory.lessons_metadata import compute_confidence

    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        compute_confidence(3, _iso(now), 0, now=now)
    with pytest.raises(ValueError):
        compute_confidence(3, _iso(now), -7, now=now)


def test_compute_confidence_accepts_offset_iso_timestamps() -> None:
    """``last_seen_utc`` may carry ``+00:00`` instead of ``Z``."""
    from agentic_swmm.memory.lessons_metadata import compute_confidence

    now = datetime.now(timezone.utc)
    one_half_life_ago = now - timedelta(days=90)
    iso_with_offset = one_half_life_ago.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    )
    assert iso_with_offset.endswith("+00:00")
    score = compute_confidence(4, iso_with_offset, 90, now=now)
    assert score == pytest.approx(4 * math.exp(-1.0), rel=1e-3)
