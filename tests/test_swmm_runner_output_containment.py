"""MCP runner output names must stay inside run_dir (review P2-1).

The MCP runner accepts caller-supplied rptName/outName. An absolute path or a
``..`` segment would let the caller write outside the run directory. The Python
runner is the enforcement point.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "skills" / "swmm-runner" / "scripts" / "swmm_runner.py"


def _load():
    spec = importlib.util.spec_from_file_location("swmm_runner_containment", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load()


def test_default_names_are_allowed() -> None:
    assert _mod._safe_output_name(None, "model.rpt") == "model.rpt"
    assert _mod._safe_output_name("custom.out", "model.out") == "custom.out"


@pytest.mark.parametrize(
    "bad",
    ["/etc/passwd", "../escape.out", "sub/dir.rpt", "..", "."],
)
def test_escaping_names_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        _mod._safe_output_name(bad, "model.rpt")


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
