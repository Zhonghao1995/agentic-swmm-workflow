"""Regression tests: CRS identity must be canonical EPSG, not raw text.

Two bugs in ``skills/swmm-gis/scripts/qgis_prepare_swmm_inputs.py``
(found 2026-08-08 static sweep):

1. ``get_layer_epsg`` took the FIRST ``AUTHORITY["EPSG",...]`` match in
   a WKT string. WKT nests authority nodes (spheroid, datum, prime
   meridian, unit) before the outer CRS's own node, which is written
   LAST, so a WGS84/UTM ``.prj`` with full authorities read as the
   spheroid's code (``EPSG:7019``) instead of the CRS. The reprojection
   flow compares these codes, so the wrong code caused spurious
   mismatch decisions.
2. ``validate_crs`` compared raw (whitespace-normalized) WKT text
   across layers. Two exports of the SAME CRS routinely differ as text
   (ESRI vs OGC WKT1 spelling, WKT1 vs WKT2), so consistent inputs
   were flagged "different CRS definitions, reproject in QGIS" with
   ``ok: false``.

Fix under test: ``_epsg_from_wkt`` takes the LAST authority match
(WKT1 ``AUTHORITY`` and WKT2 ``ID`` forms); ``validate_crs`` compares
canonical keys (EPSG when extractable, including GeoJSON
``urn:ogc:def:crs:EPSG::N`` names, else normalized text).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/swmm-gis/scripts/qgis_prepare_swmm_inputs.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("_qgis_crs_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MOD = _load_module()


# OGC WKT1 for EPSG:32610 with FULL authority nodes: the spheroid
# (7019), datum (6326), prime meridian (8901), unit (9001) and
# geographic CRS (4326) authorities all appear BEFORE the outer 32610.
_WKT1_UTM10 = (
    'PROJCS["WGS 84 / UTM zone 10N",'
    'GEOGCS["WGS 84",'
    'DATUM["WGS_1984",'
    'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
    'AUTHORITY["EPSG","6326"]],'
    'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4326"]],'
    'PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",0],'
    'PARAMETER["central_meridian",-123],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AUTHORITY["EPSG","32610"]]'
)

# ESRI-style WKT1 for the same CRS: different name spelling, NO
# authority nodes at all.
_WKT1_ESRI_UTM10 = (
    'PROJCS["WGS_1984_UTM_Zone_10N",'
    'GEOGCS["GCS_WGS_1984",'
    'DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Transverse_Mercator"],'
    'PARAMETER["Central_Meridian",-123.0],'
    'UNIT["Meter",1.0]]'
)

# WKT2 (gdalinfo output style) for the same CRS: nested ID nodes with
# the outer CRS id last.
_WKT2_UTM10 = (
    'PROJCRS["WGS 84 / UTM zone 10N",'
    'BASEGEOGCRS["WGS 84",'
    'DATUM["World Geodetic System 1984",'
    'ELLIPSOID["WGS 84",6378137,298.257223563,LENGTHUNIT["metre",1],'
    'ID["EPSG",7030]]],'
    'PRIMEM["Greenwich",0,ID["EPSG",8901]],'
    'ID["EPSG",4326]],'
    'CONVERSION["UTM zone 10N",METHOD["Transverse Mercator",ID["EPSG",9807]]],'
    'CS[Cartesian,2],AXIS["easting",east],AXIS["northing",north],'
    'LENGTHUNIT["metre",1,ID["EPSG",9001]],'
    'ID["EPSG",32610]]'
)


class EpsgFromWktTests(unittest.TestCase):
    def test_wkt1_full_authorities_returns_outer_crs_not_spheroid(self) -> None:
        # Pre-fix: first match returned EPSG:7019 (the spheroid).
        self.assertEqual(_MOD._epsg_from_wkt(_WKT1_UTM10), "EPSG:32610")

    def test_wkt2_id_nodes_returns_outer_crs(self) -> None:
        self.assertEqual(_MOD._epsg_from_wkt(_WKT2_UTM10), "EPSG:32610")

    def test_wkt_without_authorities_returns_none(self) -> None:
        self.assertIsNone(_MOD._epsg_from_wkt(_WKT1_ESRI_UTM10))

    def test_get_layer_epsg_reads_prj_outer_authority(self) -> None:
        with TemporaryDirectory() as tmp:
            shp = Path(tmp) / "layer.shp"
            shp.write_bytes(b"")
            shp.with_suffix(".prj").write_text(_WKT1_UTM10, encoding="utf-8")
            self.assertEqual(_MOD.get_layer_epsg(shp), "EPSG:32610")


class ValidateCrsCanonicalTests(unittest.TestCase):
    def _manifest(self, crs_by_role: dict[str, dict]) -> dict:
        return {
            "layers": [
                {"role": role, "path": f"/data/{role}", "crs": crs}
                for role, crs in crs_by_role.items()
            ]
        }

    def _run(self, manifest: dict) -> dict:
        with TemporaryDirectory() as tmp:
            return _MOD.validate_crs(manifest, Path(tmp) / "crs.json")

    def test_same_epsg_different_wkt_flavors_is_consistent(self) -> None:
        """The false-positive case: identical CRS, three text forms."""
        report = self._run(
            self._manifest(
                {
                    "boundary": {"kind": "wkt", "text": _WKT1_UTM10},
                    "dem": {"kind": "wkt", "text": _WKT2_UTM10},
                    "landuse": {
                        "kind": "geojson_crs",
                        "text": json.dumps(
                            {"properties": {"name": "urn:ogc:def:crs:EPSG::32610"}},
                            sort_keys=True,
                        ),
                    },
                }
            )
        )
        self.assertTrue(report["ok"])
        self.assertFalse(
            [i for i in report["issues"] if i["severity"] == "error"]
        )

    def test_genuinely_different_epsg_still_errors(self) -> None:
        wkt_4326_tail = _WKT1_UTM10.replace(
            'AUTHORITY["EPSG","32610"]', 'AUTHORITY["EPSG","26910"]'
        )
        report = self._run(
            self._manifest(
                {
                    "boundary": {"kind": "wkt", "text": _WKT1_UTM10},
                    "dem": {"kind": "wkt", "text": wkt_4326_tail},
                }
            )
        )
        self.assertFalse(report["ok"])
        errors = [i for i in report["issues"] if i["severity"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(
            errors[0]["distinct_crs"], ["EPSG:26910", "EPSG:32610"]
        )

    def test_non_epsg_wkt_text_mismatch_still_errors(self) -> None:
        """No EPSG identity extractable: text comparison remains."""
        report = self._run(
            self._manifest(
                {
                    "boundary": {"kind": "wkt", "text": _WKT1_ESRI_UTM10},
                    "dem": {
                        "kind": "wkt",
                        "text": _WKT1_ESRI_UTM10.replace("Zone_10N", "Zone_11N"),
                    },
                }
            )
        )
        self.assertFalse(report["ok"])

    def test_records_carry_canonical_key(self) -> None:
        report = self._run(
            self._manifest({"boundary": {"kind": "wkt", "text": _WKT1_UTM10}})
        )
        self.assertEqual(report["crs_records"][0]["canonical"], "EPSG:32610")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
