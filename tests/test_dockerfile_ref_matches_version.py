"""A bare ``docker build`` must not silently pin an old release.

The comment above the ARG in the Dockerfile already states the contract
(keep in step with the released version, review P2-6), yet v0.8.0 sat
there through three releases. This guard fails the suite whenever the
ARG default and the ``pyproject.toml`` version drift apart, so a release
bump cannot forget the Dockerfile again.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, flags=re.MULTILINE)
    assert match, "pyproject.toml has no version line"
    return match.group(1)


def test_dockerfile_default_ref_matches_release_version() -> None:
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"^ARG AGENTIC_SWMM_REF=(\S+)$", text, flags=re.MULTILINE)
    assert match, "Dockerfile has no ARG AGENTIC_SWMM_REF default"
    expected = f"v{_pyproject_version()}"
    assert match.group(1) == expected, (
        f"Dockerfile ARG AGENTIC_SWMM_REF default is {match.group(1)}, "
        f"but the release version is {expected}; a bare `docker build` "
        "would pin an old release."
    )
