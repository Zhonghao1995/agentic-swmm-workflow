"""Unified command-line entry points for Agentic SWMM."""

from __future__ import annotations

import re as _re
from importlib import metadata as _metadata
from pathlib import Path as _Path

_PROJECT_NAME = "aiswmm"


def _installed_version() -> str:
    try:
        return _metadata.version(_PROJECT_NAME)
    except _metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def _checkout_version(package_dir: _Path) -> str | None:
    """Version declared by the checkout's pyproject.toml, or None outside a checkout.

    An editable install keeps the dist-info written at install time, so after a
    version bump ``aiswmm --version``, the banner and doctor kept reporting the old
    number until ``pip install -e .`` was rerun (F-156). Next to a checkout, the
    pyproject is the truth; a wheel has no pyproject next to its package.
    """
    pyproject = package_dir.parent / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    project = _re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, _re.M | _re.S)
    if not project:
        return None
    name = _re.search(r'^name\s*=\s*"([^"]+)"', project.group(1), _re.M)
    version = _re.search(r'^version\s*=\s*"([^"]+)"', project.group(1), _re.M)
    if not name or not version or name.group(1) != _PROJECT_NAME:
        return None
    return version.group(1)


def _resolve_version(package_dir: _Path | None = None) -> str:
    return _checkout_version(package_dir or _Path(__file__).resolve().parent) or _installed_version()


__version__ = _resolve_version()
