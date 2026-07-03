"""Aligned fixed-width CLI tables: header + dashed separator + rows.

Three command surfaces used to hand-roll the same alignment mechanics
with per-file f-strings (each re-deriving column padding and a
separator line of guessed length). The alignment math lives here once;
cells arrive as pre-formatted strings because truncation style, number
formatting and placeholder dashes are domain decisions that stay at
the call sites.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    """One table column: header text, fixed width, alignment.

    ``width=None`` renders the cell at its natural width (no padding) —
    use it for a final free-running column such as a timestamp.
    """

    header: str
    width: int | None = None
    align: str = "left"  # "left" | "right"


def _cell(value: str, column: Column) -> str:
    if column.width is None:
        return value
    if column.align == "right":
        return f"{value:>{column.width}}"
    return f"{value:<{column.width}}"


def render_table(
    columns: Sequence[Column],
    rows: Iterable[Sequence[object]],
    *,
    indent: str = "",
) -> str:
    """Return the aligned table as one string ending in a newline.

    Layout: header line, a dashed separator spanning the header's
    visible width, then one line per row. Columns are joined with a
    two-space gap; every line carries ``indent``.
    """
    header = indent + "  ".join(_cell(c.header, c) for c in columns)
    separator = indent + "-" * (len(header) - len(indent))
    lines = [header, separator]
    for row in rows:
        lines.append(
            indent
            + "  ".join(_cell(str(value), c) for value, c in zip(row, columns))
        )
    return "\n".join(lines) + "\n"


__all__ = ["Column", "render_table"]
