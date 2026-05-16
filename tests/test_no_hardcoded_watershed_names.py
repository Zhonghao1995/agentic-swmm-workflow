"""Cycle 4 of PRD #118: regression guard against new hardcoded watershed names.

The agent's routing / inference / memory layers must stay portable:
adding a new watershed should never require editing if/elif chains in
Python. This guard parses each source file's AST and looks for string
literal nodes (``ast.Constant`` with ``str`` value) that contain a
known example watershed name. Module / class / function docstrings are
filtered out — those are documentation, not routing logic.

Allowed sites — files whose code legitimately references the example
watersheds. Docstring / comment references are filtered automatically
by the AST walk, so this list is small:

* ``agentic_swmm/commands/demo.py`` — the demo registry pins one entry
  per built-in case_id by design (out of scope per PRD #118).
* ``agentic_swmm/commands/setup.py`` — printed example shell commands
  the user can copy-paste after ``aiswmm setup``.
* ``agentic_swmm/agent/prompts.py`` — example text inside
  ``WARM_INTRO_TEMPLATE`` (displayed to the user on first turn).
* ``agentic_swmm/agent/welcome.py`` — example text inside the welcome
  banner.
* ``skills/swmm-modeling-memory/scripts/summarize_memory.py`` — references
  a real benchmark script filename
  (``scripts/benchmarks/run_tecnopolo_199401.py``).
* Any file under ``tests/``.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

_FORBIDDEN_NAMES = ("tecnopolo", "todcreek")

# Directories scanned by the guard. Anything in this list is in scope
# for the portability invariant.
_SCAN_DIRS = (
    REPO_ROOT / "agentic_swmm",
    REPO_ROOT / "skills",
)

# Files where code-level references are intentional and documented.
# Files whose only references are in docstrings / comments are filtered
# automatically by the AST walk and need not be listed here.
_ALLOWED_FILES = {
    REPO_ROOT / "agentic_swmm" / "commands" / "demo.py",
    REPO_ROOT / "agentic_swmm" / "commands" / "setup.py",
    REPO_ROOT / "agentic_swmm" / "agent" / "prompts.py",
    REPO_ROOT / "agentic_swmm" / "agent" / "welcome.py",
    REPO_ROOT / "skills" / "swmm-modeling-memory" / "scripts" / "summarize_memory.py",
}

# Tests are exempt — fixtures legitimately reference example cases.
_EXEMPT_DIR_NAMES = {"tests"}


def _collect_docstring_node_ids(tree: ast.AST) -> set[int]:
    """Return ``id()`` of every node that is a module/class/function docstring.

    Docstrings are the ``ast.Constant`` (str) sitting as the first
    statement of a module / class / function body. We exclude them so
    legitimate documentation examples never trip the guard.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            out.add(id(first.value))
    return out


def _scan_code_strings(text: str) -> list[tuple[int, str]]:
    """Return ``(lineno, literal)`` pairs for non-docstring string literals.

    We use Python's AST: only ``ast.Constant`` (string-valued) nodes
    are considered, and module/class/function docstrings are filtered
    out. This catches the bug class the PRD targets — string literals
    like ``"tecnopolo"`` appearing in conditionals / tuples — while
    leaving documentation untouched.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    docstring_ids = _collect_docstring_node_ids(tree)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_ids:
                continue
            out.append((getattr(node, "lineno", 0), node.value))
    return out


def _iter_python_files() -> list[Path]:
    out: list[Path] = []
    for root in _SCAN_DIRS:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if any(part in _EXEMPT_DIR_NAMES for part in path.parts):
                continue
            if path in _ALLOWED_FILES:
                continue
            out.append(path)
    return out


class NoHardcodedWatershedNamesTests(unittest.TestCase):
    def test_protected_scope_has_no_watershed_names_in_code(self) -> None:
        hits: list[str] = []
        for path in _iter_python_files():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for lineno, literal in _scan_code_strings(text):
                lowered = literal.lower()
                for name in _FORBIDDEN_NAMES:
                    if name in lowered:
                        hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {literal!r}")
        self.assertEqual(
            hits,
            [],
            "hardcoded watershed names leaked back into protected scope:\n"
            + "\n".join(hits),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
