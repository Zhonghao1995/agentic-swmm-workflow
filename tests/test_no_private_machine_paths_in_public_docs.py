"""Regression test for issue #126.

Walks ``docs/`` and ``examples/`` (excluding ``docs/framework-validation/``,
which is frozen evidence and documented as such) and grep-asserts that
no ``/Users/<username>`` or ``~/.openclaw/`` private-machine breadcrumb
appears in any public doc or example.

The point of the test is to prevent the breadcrumb-class regression
from sneaking back in via a new evidence file or repo-map edit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUBSTRINGS = ("/Users/", "~/.openclaw/")

# Frozen captured evidence — documented as containing capture-time
# absolute paths. Not a regression surface.
EXCLUDED_DIRS = {
    REPO_ROOT / "docs" / "framework-validation",
}


def _scan_roots() -> Iterable[Path]:
    yield REPO_ROOT / "docs"
    yield REPO_ROOT / "examples"


def _is_excluded(path: Path) -> bool:
    for excluded in EXCLUDED_DIRS:
        try:
            path.relative_to(excluded)
            return True
        except ValueError:
            continue
    return False


def _public_doc_files() -> List[Path]:
    found: List[Path] = []
    for root in _scan_roots():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if _is_excluded(path):
                continue
            # Only consider text-based formats
            if path.suffix.lower() not in {".md", ".txt", ".rst", ".json", ".yaml", ".yml"}:
                continue
            found.append(path)
    return sorted(found)


def test_no_private_machine_paths_in_public_docs() -> None:
    offenders: list[str] = []
    for path in _public_doc_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for needle in FORBIDDEN_SUBSTRINGS:
            if needle in text:
                rel = path.relative_to(REPO_ROOT)
                # Report which substring and how many times
                count = text.count(needle)
                offenders.append(f"{rel}: {count}x '{needle}'")
                break
    assert not offenders, (
        "Private-machine paths must not appear in public docs or "
        "examples. Replace with repo-relative paths or generic "
        "placeholders. Offenders:\n  " + "\n  ".join(offenders)
    )
