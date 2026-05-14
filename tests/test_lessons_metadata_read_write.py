"""Tests for ME-1 lessons_metadata read/write round-trip (issue #61).

The module ``agentic_swmm.memory.lessons_metadata`` exposes four pure
helpers:

- ``read_metadata(pattern_block)`` returns the parsed YAML dict embedded
  inside the single HTML-comment fence (``<!-- aiswmm-metadata`` ...
  ``/aiswmm-metadata -->``), or ``None`` when the block has no fence
  yet.
- ``write_metadata(pattern_block, meta)`` round-trips the metadata back
  into the block. The body of the block (everything past the ``##``
  heading and the optional existing metadata fence) is preserved
  verbatim — we only rewrite the fence.
- ``read_all_patterns(markdown_text)`` returns a mapping
  ``{pattern_name: metadata_dict | None}`` for every ``## <name>``
  section in ``lessons_learned.md``.
- ``replace_pattern_block(markdown_text, pattern_name, new_block)``
  swaps a single pattern section in-place without touching the rest of
  the document, so the audit hook can rewrite one pattern at a time
  without re-rendering the whole file.
"""

from __future__ import annotations

import pytest


def _block_without_metadata(pattern: str = "peak_flow_parse_missing") -> str:
    return (
        f"## {pattern}\n"
        "\n"
        "Observed in 2 run(s): `runner-check`, `runner-fixed`.\n"
        "\n"
        "The peak flow value could not be located in the parsed RPT output.\n"
    )


def _block_with_metadata(pattern: str = "peak_flow_parse_missing") -> str:
    return (
        f"## {pattern}\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        "  first_seen_utc: 2026-03-01T10:23:00Z\n"
        "  last_seen_utc: 2026-05-12T14:08:00Z\n"
        "  evidence_count: 7\n"
        "  evidence_runs:\n"
        "    - tecnopolo-199401-prepared\n"
        "    - codex-check-peakfix\n"
        "  status: active\n"
        "  confidence_score: 4.92\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Observed in 2 run(s): `runner-check`, `runner-fixed`.\n"
        "\n"
        "The peak flow value could not be located in the parsed RPT output.\n"
    )


def test_read_metadata_returns_none_when_block_has_no_fence() -> None:
    from agentic_swmm.memory.lessons_metadata import read_metadata

    assert read_metadata(_block_without_metadata()) is None


def test_read_metadata_parses_yaml_inside_fence() -> None:
    from agentic_swmm.memory.lessons_metadata import read_metadata

    meta = read_metadata(_block_with_metadata())
    assert meta is not None
    assert meta["first_seen_utc"] == "2026-03-01T10:23:00Z"
    assert meta["last_seen_utc"] == "2026-05-12T14:08:00Z"
    assert meta["evidence_count"] == 7
    assert meta["evidence_runs"] == [
        "tecnopolo-199401-prepared",
        "codex-check-peakfix",
    ]
    assert meta["status"] == "active"
    assert meta["confidence_score"] == pytest.approx(4.92)
    assert meta["half_life_days"] == 90


def test_write_metadata_into_block_without_fence() -> None:
    from agentic_swmm.memory.lessons_metadata import read_metadata, write_metadata

    meta = {
        "first_seen_utc": "2026-05-14T00:00:00Z",
        "last_seen_utc": "2026-05-14T00:00:00Z",
        "evidence_count": 1,
        "evidence_runs": [],
        "status": "active",
        "confidence_score": 1.0,
        "half_life_days": 90,
    }
    updated = write_metadata(_block_without_metadata(), meta)

    assert "<!-- aiswmm-metadata" in updated
    assert "/aiswmm-metadata -->" in updated
    # Body content must survive verbatim.
    assert "The peak flow value could not be located" in updated
    # Round-trip through read_metadata.
    assert read_metadata(updated) == meta


def test_write_metadata_replaces_existing_fence() -> None:
    from agentic_swmm.memory.lessons_metadata import read_metadata, write_metadata

    block = _block_with_metadata()
    new_meta = {
        "first_seen_utc": "2026-03-01T10:23:00Z",
        "last_seen_utc": "2026-05-14T12:00:00Z",
        "evidence_count": 8,
        "evidence_runs": [
            "tecnopolo-199401-prepared",
            "codex-check-peakfix",
            "new-run-id",
        ],
        "status": "active",
        "confidence_score": 5.10,
        "half_life_days": 90,
    }
    updated = write_metadata(block, new_meta)

    # Only one fence should remain (we replaced, not appended).
    assert updated.count("<!-- aiswmm-metadata") == 1
    assert updated.count("/aiswmm-metadata -->") == 1
    assert "The peak flow value could not be located" in updated
    assert read_metadata(updated) == new_meta


def test_read_all_patterns_finds_every_section() -> None:
    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    doc = (
        "# Lessons Learned\n"
        "\n"
        "Some intro.\n"
        "\n"
        + _block_with_metadata("peak_flow_parse_missing")
        + "\n"
        + _block_without_metadata("missing_inp")
    )
    parsed = read_all_patterns(doc)

    assert set(parsed) == {"peak_flow_parse_missing", "missing_inp"}
    assert parsed["peak_flow_parse_missing"] is not None
    assert parsed["peak_flow_parse_missing"]["evidence_count"] == 7
    assert parsed["missing_inp"] is None


def test_replace_pattern_block_swaps_only_target_section() -> None:
    from agentic_swmm.memory.lessons_metadata import replace_pattern_block

    doc = (
        "# Lessons Learned\n"
        "\n"
        + _block_without_metadata("peak_flow_parse_missing")
        + "\n"
        + _block_without_metadata("missing_inp")
    )
    new_block = _block_with_metadata("peak_flow_parse_missing")
    updated = replace_pattern_block(doc, "peak_flow_parse_missing", new_block)

    # Target block now carries the fence.
    assert "<!-- aiswmm-metadata" in updated
    # Untouched neighbour survived.
    assert "## missing_inp" in updated
    # Heading isn't duplicated.
    assert updated.count("## peak_flow_parse_missing") == 1


def test_read_all_patterns_excludes_non_pattern_headings() -> None:
    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    doc = (
        "# Lessons Learned\n"
        "\n"
        "## Repeated Failure Patterns\n"
        "- `foo`: 1 run(s)\n"
        "\n"
        "## Successful Practices\n"
        "- bar\n"
        "\n"
        + _block_without_metadata("peak_flow_parse_missing")
    )
    parsed = read_all_patterns(doc)
    # Only the failure_pattern style sections (snake_case, lowercase
    # identifiers) qualify. Capitalised summary sections are skipped.
    assert set(parsed) == {"peak_flow_parse_missing"}
