"""`pip install aiswmm[gis]` must be a real, installable extra (review P1-1).

The swmm-gis scripts import a heavy geospatial stack that is not in the core
dependencies. Without a declared `gis` extra, a user who is told to run
`pip install aiswmm[gis]` hits an error. This pins the extra and the modules it
must cover so the install hint stays truthful.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _extras() -> dict[str, list[str]]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]


def test_gis_extra_exists() -> None:
    assert "gis" in _extras(), "expected a [gis] extra so pip install aiswmm[gis] works"


def test_gis_extra_covers_the_heavy_stack() -> None:
    gis = " ".join(_extras()["gis"]).lower()
    for pkg in ("geopandas", "rasterio", "shapely"):
        assert pkg in gis, f"{pkg} missing from the gis extra"


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
