"""Regression test for issue #129.

Walks the README's Markdown link references of the form
``(path/file.md#anchor)`` and asserts that, for every such link, the
target file exists and a ``## Section Title`` whose GitHub-slug matches
``anchor`` is present.

Catches the class of bug where a README link advertises a section
that does not exist in the target doc (the browser jumps to page top,
the reader has no signal that the link is dead).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"


_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)\s]+\.md)#([^)\s]+)\)")


def _slugify_heading(heading_text: str) -> str:
    """GitHub-style slug: lowercase, strip punctuation except hyphens
    and underscores, collapse whitespace to single hyphen."""
    text = heading_text.strip().lower()
    # Remove characters that GitHub strips when generating anchors.
    text = re.sub(r"[^\w\s\-]", "", text)
    text = re.sub(r"\s+", "-", text)
    return text


def _readme_anchor_links() -> List[Tuple[str, str]]:
    text = README.read_text(encoding="utf-8")
    return [(m.group(1), m.group(2)) for m in _LINK_PATTERN.finditer(text)]


def _heading_slugs_for(md_path: Path) -> List[str]:
    text = md_path.read_text(encoding="utf-8")
    slugs: List[str] = []
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m:
            slugs.append(_slugify_heading(m.group(2)))
    return slugs


@pytest.mark.parametrize("target,anchor", _readme_anchor_links())
def test_readme_anchor_link_resolves(target: str, anchor: str) -> None:
    target_path = (REPO_ROOT / target).resolve()
    assert target_path.exists(), (
        f"README links to '{target}#{anchor}', but {target_path} does "
        f"not exist on disk"
    )
    slugs = _heading_slugs_for(target_path)
    assert anchor in slugs, (
        f"README links to '{target}#{anchor}', but no '## Section' in "
        f"{target} slugifies to '{anchor}'. Existing slugs: {slugs}"
    )
