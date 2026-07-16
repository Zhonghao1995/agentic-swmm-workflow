"""The public wheel must ship every memory file the runtime requires (review P1-1).

registry.py declares LONG_TERM_MEMORY_FILES + MODELING_MEMORY_FILES as required
resources and `aiswmm setup` marks them required=True, so a public wheel that
omits them reports incomplete after a real pip install. setup.py's
PUBLIC_MEMORY_FILES must therefore mirror the registry exactly, and each file
must pass the public-resource filter.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock

from agentic_swmm.runtime.registry import LONG_TERM_MEMORY_FILES, MODELING_MEMORY_FILES


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_setup():
    spec = importlib.util.spec_from_file_location("aiswmm_setup_under_test", REPO_ROOT / "setup.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # setup.py calls setuptools.setup() at import; stub it so import is a no-op.
    with mock.patch("setuptools.setup"):
        spec.loader.exec_module(module)
    return module


_setup = _load_setup()


def _registry_memory_paths() -> set[Path]:
    paths = {Path(source) for source, _target in LONG_TERM_MEMORY_FILES}
    paths |= {Path(p) for p in MODELING_MEMORY_FILES}
    return paths


def test_setup_memory_list_matches_registry() -> None:
    assert _setup.PUBLIC_MEMORY_FILES == _registry_memory_paths()


def test_every_registry_memory_file_passes_public_filter() -> None:
    for relative in _registry_memory_paths():
        assert _setup._include_public_resource(relative) is True, relative


def test_data_dir_still_excluded_from_public_wheel() -> None:
    assert _setup._include_public_resource(Path("data/anything.csv")) is False


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
