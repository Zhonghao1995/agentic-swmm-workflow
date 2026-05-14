"""Pattern-name dict lookup over ``lessons_learned.md`` (PRD M1).

This is a pure function: read the file once, locate the ``## <pattern>``
section heading, return everything until the next ``## `` heading or
end of file. Returns an empty string for any error path (missing file,
absent pattern), so callers can do truthy checks.

This is NOT the RAG retriever. The embedding-based hybrid retriever
lives behind ``recall_search.recall_search`` (PRD M6).
"""

from __future__ import annotations

from pathlib import Path


def recall(pattern: str, lessons_path: Path) -> str:
    """Return the Markdown section for ``pattern`` from ``lessons_path``.

    Parameters
    ----------
    pattern:
        Exact ``failure_pattern`` name to look up. Matched against
        ``## <pattern>`` headings (case-sensitive, exact text after the
        ``## ``).
    lessons_path:
        Path to a ``lessons_learned.md`` file.

    Returns
    -------
    str
        The Markdown fragment for the matching section, including its
        heading. Empty string if the file does not exist or no matching
        section is found.
    """
    if not lessons_path.is_file():
        return ""
    try:
        text = lessons_path.read_text(encoding="utf-8")
    except OSError:
        return ""

    target_heading = f"## {pattern}"
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        # Strict equality (after rstrip) to avoid prefix collisions like
        # "## peak_flow_parse_missing_old" matching "peak_flow_parse_missing".
        if line.rstrip() == target_heading:
            start = index
            break
    if start is None:
        return ""

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break

    section = "\n".join(lines[start:end]).rstrip()
    return section + ("\n" if section else "")
