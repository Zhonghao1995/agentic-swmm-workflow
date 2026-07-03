"""Contract tests for the shared CLI table renderer (utils/tables.py)."""
from __future__ import annotations

from agentic_swmm.utils.tables import Column, render_table


def test_alignment_and_padding() -> None:
    out = render_table(
        (Column("id", 4, "right"), Column("name", 8), Column("note")),
        [(1, "alpha", "first"), (22, "beta", "second note")],
    )
    lines = out.splitlines()
    assert lines[0] == "  id  name      note"
    assert set(lines[1]) == {"-"}
    assert len(lines[1]) == len(lines[0])
    assert lines[2] == "   1  alpha     first"
    assert lines[3] == "  22  beta      second note"
    assert out.endswith("\n")


def test_indent_applies_to_every_line_and_separator_excludes_it() -> None:
    out = render_table(
        (Column("k", 5), Column("v", 3, "right")),
        [("a", 1)],
        indent="  ",
    )
    header, sep, row = out.splitlines()
    assert header.startswith("  ") and row.startswith("  ")
    assert sep == "  " + "-" * (len(header) - 2)


def test_zero_rows_renders_header_and_separator_only() -> None:
    out = render_table((Column("only", 6),), [])
    assert len(out.splitlines()) == 2


def test_none_width_column_is_natural_width() -> None:
    out = render_table(
        (Column("a", 2), Column("free")),
        [("x", "unpadded tail")],
    )
    assert out.splitlines()[2].endswith("unpadded tail")
