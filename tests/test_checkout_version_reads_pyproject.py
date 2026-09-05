"""F-156: on a checkout the version comes from pyproject, not the stale dist-info.

An editable install keeps the dist-info written at install time, so after the
0.9.4 bump ``aiswmm --version`` kept saying 0.9.3 on the developer's own machine
until ``pip install -e .`` was rerun. Next to a checkout the pyproject is the
truth; a wheel has no pyproject next to its package and keeps the installed
metadata.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import agentic_swmm
from agentic_swmm import _checkout_version, _resolve_version

_CHECKOUT = (
    '[build-system]\nrequires = ["setuptools"]\n\n'
    '[project]\nname = "aiswmm"\nversion = "9.9.9"\n\n'
    '[project.optional-dependencies]\nreport = ["python-docx"]\n'
)


def _package(tmp_path: Path, pyproject: str | None) -> Path:
    package_dir = tmp_path / "agentic_swmm"
    package_dir.mkdir()
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return package_dir


def test_the_checkout_pyproject_wins_over_stale_installed_metadata(tmp_path, monkeypatch):
    package_dir = _package(tmp_path, _CHECKOUT)
    monkeypatch.setattr(agentic_swmm, "_installed_version", lambda: "0.9.3")
    assert _checkout_version(package_dir) == "9.9.9"
    assert _resolve_version(package_dir) == "9.9.9"


def test_a_wheel_keeps_the_installed_metadata(tmp_path, monkeypatch):
    package_dir = _package(tmp_path, None)
    monkeypatch.setattr(agentic_swmm, "_installed_version", lambda: "0.9.3")
    assert _checkout_version(package_dir) is None
    assert _resolve_version(package_dir) == "0.9.3"


def test_another_projects_pyproject_is_ignored(tmp_path, monkeypatch):
    package_dir = _package(tmp_path, '[project]\nname = "somebody-else"\nversion = "1.0.0"\n')
    monkeypatch.setattr(agentic_swmm, "_installed_version", lambda: "0.9.3")
    assert _resolve_version(package_dir) == "0.9.3"


def test_this_checkout_reports_its_own_pyproject_version():
    tomllib = pytest.importorskip("tomllib")
    pyproject = Path(agentic_swmm.__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        pytest.skip("not running from a checkout")
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert agentic_swmm.__version__ == declared
