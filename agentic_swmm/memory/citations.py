"""Citation library loader (PRD-06 Phase B.2).

The project's reference benchmarks (``reference_benchmarks.yaml``) only
populates a numeric leaf when the matching citation has been verified
against an original source. ``citations.yaml`` is that hand-edited
bibliographic substrate; this module is the typed reader.

Three verbs, mirroring the small-facade pattern used elsewhere in
``agentic_swmm.memory``:

- :func:`load_citations` — parse the YAML, tolerant of missing /
  malformed files
- :func:`recall_citation` — single-entry lookup by citation token
- :class:`Citation` — typed dataclass for a single entry

Values land in ``reference_benchmarks.yaml`` only after the
corresponding citation entry has been verified (``verified_by`` and
``verified_on`` populated). Until that happens the numeric leaves stay
``null`` and ``classify_metric`` correctly returns ``"UNKNOWN"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Citation:
    """A single bibliographic entry from ``citations.yaml``.

    Fields mirror the YAML schema 1:1 so a caller can render the entry
    verbatim without juggling key aliases. ``url`` and the two
    ``verified_*`` fields are empty strings (not ``None``) when absent
    so the renderer can always format them without an existence check.
    """

    key: str
    authors: str
    year: int
    title: str
    work: str
    locator: str
    url: str = ""
    verified_by: str = ""
    verified_on: str = ""

    @property
    def is_verified(self) -> bool:
        """Return ``True`` when both ``verified_by`` and ``verified_on`` are populated.

        Unverified entries are useful as schema placeholders but must not
        be used to authorise a numeric backfill into
        ``reference_benchmarks.yaml``.
        """
        return bool(self.verified_by.strip()) and bool(self.verified_on.strip())

    def to_dict(self) -> dict[str, Any]:
        """Return the entry as a plain dict (display / serialization)."""
        return {
            "key": self.key,
            "authors": self.authors,
            "year": self.year,
            "title": self.title,
            "work": self.work,
            "locator": self.locator,
            "url": self.url,
            "verified_by": self.verified_by,
            "verified_on": self.verified_on,
            "is_verified": self.is_verified,
        }


def _coerce_entry(key: str, raw: Any) -> Citation | None:
    """Coerce a raw YAML entry into a :class:`Citation`.

    Returns ``None`` when ``raw`` is not a dict — schema_version and
    other top-level scalars must not be misread as citation entries.
    Unknown / missing fields fall back to empty strings (or ``0`` for
    ``year``) so the reader never raises on a partial entry.
    """
    if not isinstance(raw, dict):
        return None
    try:
        year_value = int(raw.get("year") or 0)
    except (TypeError, ValueError):
        year_value = 0
    return Citation(
        key=key,
        authors=str(raw.get("authors", "")),
        year=year_value,
        title=str(raw.get("title", "")),
        work=str(raw.get("work", "")),
        locator=str(raw.get("locator", "")),
        url=str(raw.get("url", "")),
        verified_by=str(raw.get("verified_by", "")),
        verified_on=str(raw.get("verified_on", "")),
    )


def load_citations(path: Path) -> dict[str, Citation]:
    """Load all citations from ``path``; return ``{}`` on any failure.

    Missing files and malformed YAML both yield ``{}`` so a fresh
    project (no citations yet) does not need a special-case branch.
    ``schema_version`` and any other top-level scalar keys are skipped
    — only dict-valued top-level keys are treated as citation entries.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, Citation] = {}
    for raw_key, raw_value in data.items():
        key = str(raw_key)
        if key == "schema_version":
            continue
        entry = _coerce_entry(key, raw_value)
        if entry is not None:
            out[key] = entry
    return out


def recall_citation(path: Path, key: str) -> Citation | None:
    """Return the entry for ``key`` or ``None`` if absent.

    Single-entry lookup so the CLI ``aiswmm cite <key>`` and the
    audit-note renderer share one verb. Missing files behave the same
    as missing keys — the caller decides whether to surface "library
    not initialised" or "unknown citation".
    """
    if not key or not key.strip():
        return None
    return load_citations(path).get(key)
