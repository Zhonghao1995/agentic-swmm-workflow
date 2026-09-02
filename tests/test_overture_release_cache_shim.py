"""The Overture release cache is repaired before swmmanywhere runs (F-46).

Live finding 2026-09-02 (scenario S13, a Seattle bbox): swmmanywhere 0.2.2
derives the release id as ``Path(href).parent`` of an absolute STAC URL,
caches ``https:/stac.overturemaps.org/2026-08-19.0`` for 72 hours and every
global synthesis then fails on a bogus S3 key.
"""

from __future__ import annotations

import json
from datetime import datetime

from agentic_swmm.integrations import swmmanywhere_runner as runner

CATALOG = {
    "links": [
        {"rel": "self", "href": "https://stac.overturemaps.org/catalog.json"},
        {"rel": "child", "title": "Latest Overture Release", "href": "https://stac.overturemaps.org/2026-08-19.0/catalog.json"},
        {"rel": "child", "title": "2026-07-22.0 Overture Release", "href": "https://stac.overturemaps.org/2026-07-22.0/catalog.json"},
    ]
}


def test_latest_release_is_parsed_from_absolute_hrefs():
    assert runner.latest_overture_release(CATALOG) == "2026-08-19.0"


def test_a_well_formed_cache_is_left_alone(tmp_path):
    cache = tmp_path / ".cache" / "overture_release.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"release": "2026-07-22.0", "timestamp": datetime.now().isoformat()}))
    before = cache.read_text()
    assert runner.ensure_overture_release_cache(cache, fetch_catalog=lambda: CATALOG) == "2026-07-22.0"
    assert cache.read_text() == before


def test_the_librarys_malformed_cache_is_repaired_offline(tmp_path):
    cache = tmp_path / ".cache" / "overture_release.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"release": "https:/stac.overturemaps.org/2026-08-19.0", "timestamp": datetime.now().isoformat()}))

    def no_network():
        raise AssertionError("the repair must not need the catalog")

    assert runner.ensure_overture_release_cache(cache, fetch_catalog=no_network) == "2026-08-19.0"
    assert json.loads(cache.read_text())["release"] == "2026-08-19.0"


def test_a_missing_cache_is_seeded_from_the_catalog(tmp_path):
    cache = tmp_path / ".cache" / "overture_release.json"
    assert runner.ensure_overture_release_cache(cache, fetch_catalog=lambda: CATALOG) == "2026-08-19.0"
    assert json.loads(cache.read_text())["release"] == "2026-08-19.0"


def test_no_catalog_and_no_cache_means_hands_off(tmp_path):
    cache = tmp_path / ".cache" / "overture_release.json"

    def offline():
        raise OSError("no network")

    assert runner.ensure_overture_release_cache(cache, fetch_catalog=offline) is None
    assert not cache.exists()
