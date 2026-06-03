"""Tests for ``agentic_swmm.integrations.raw_snapshot``.

The raw-snapshot module is the reusable hash/cache/verify layer that backs
the ``swmm-anywhere`` skill's ``00_raw/`` directory contract. It is
deliberately decoupled from SWMManywhere and operates on arbitrary
``(file_path, source_url, capture_time)`` tuples so it can be reused by any
future skill that needs to snapshot non-deterministic upstream inputs.

The public surface this test pins down:

    snapshot_to(snapshot_dir, sources) -> RawManifest
    verify_snapshot(manifest_path) -> VerifyResult
    should_refetch(manifest_path, *, refresh) -> bool
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.integrations.raw_snapshot import (
    RawSource,
    snapshot_to,
    summarize_snapshot_verification,
    verify_snapshot,
    should_refetch,
)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class SnapshotToTests(unittest.TestCase):
    """``snapshot_to`` copies inputs into the snapshot dir and records a
    ``raw_manifest.json`` with SHA-256 of each file plus its provenance."""

    def test_writes_manifest_with_expected_schema(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "incoming" / "osm.geojson"
            _write(src, b'{"type":"FeatureCollection","features":[]}')

            snap_dir = tmp_path / "00_raw"
            captured = "2026-05-27T19:18:00Z"
            manifest = snapshot_to(
                snap_dir,
                [RawSource(path=src, source_url="https://overpass-api.de/api", captured_at=captured)],
            )

            self.assertEqual(manifest.manifest_version, "1.0")
            self.assertEqual(len(manifest.sources), 1)
            entry = manifest.sources[0]
            self.assertEqual(entry.path, "osm.geojson")
            self.assertEqual(entry.source_url, "https://overpass-api.de/api")
            self.assertEqual(entry.captured_at, captured)
            self.assertEqual(
                entry.sha256,
                "afb95ef0d1d1bf90b58ff1e8a4e1574bf9b1d0e54c2d2c8b1f57e3a8c8e8be8a"[:0]
                or __import__("hashlib").sha256(b'{"type":"FeatureCollection","features":[]}').hexdigest(),
            )

    def test_copies_file_into_snapshot_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "dem.tif"
            _write(src, b"FAKE-TIFF-PAYLOAD")

            snap_dir = tmp_path / "00_raw"
            snapshot_to(
                snap_dir,
                [RawSource(path=src, source_url="https://pc.example", captured_at="2026-05-27T19:18:00Z")],
            )

            self.assertTrue((snap_dir / "dem.tif").exists())
            self.assertEqual((snap_dir / "dem.tif").read_bytes(), b"FAKE-TIFF-PAYLOAD")
            self.assertTrue((snap_dir / "raw_manifest.json").exists())

    def test_manifest_json_round_trips(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            a = tmp_path / "a.txt"
            b = tmp_path / "b.txt"
            _write(a, b"AAA")
            _write(b, b"BBB")
            snap_dir = tmp_path / "00_raw"
            snapshot_to(
                snap_dir,
                [
                    RawSource(path=a, source_url="https://a.example", captured_at="2026-05-27T19:18:00Z"),
                    RawSource(path=b, source_url="https://b.example", captured_at="2026-05-27T19:18:00Z"),
                ],
            )

            payload = json.loads((snap_dir / "raw_manifest.json").read_text())
            self.assertEqual(payload["manifest_version"], "1.0")
            self.assertEqual(len(payload["sources"]), 2)
            paths = {s["path"] for s in payload["sources"]}
            self.assertEqual(paths, {"a.txt", "b.txt"})


class VerifySnapshotTests(unittest.TestCase):
    """``verify_snapshot`` re-hashes every file and reports drift."""

    def _make_snapshot(self, tmp_path: Path) -> Path:
        src = tmp_path / "incoming.txt"
        _write(src, b"hello world")
        snap_dir = tmp_path / "00_raw"
        snapshot_to(
            snap_dir,
            [RawSource(path=src, source_url="https://h.example", captured_at="2026-05-27T19:18:00Z")],
        )
        return snap_dir / "raw_manifest.json"

    def test_clean_snapshot_verifies_ok(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest_path = self._make_snapshot(tmp_path)

            result = verify_snapshot(manifest_path)
            self.assertTrue(result.ok)
            self.assertEqual(result.missing, ())
            self.assertEqual(result.mismatched, ())

    def test_missing_file_is_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest_path = self._make_snapshot(tmp_path)
            (manifest_path.parent / "incoming.txt").unlink()

            result = verify_snapshot(manifest_path)
            self.assertFalse(result.ok)
            self.assertEqual(result.missing, ("incoming.txt",))
            self.assertEqual(result.mismatched, ())

    def test_tampered_file_is_reported_as_mismatched(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest_path = self._make_snapshot(tmp_path)
            (manifest_path.parent / "incoming.txt").write_bytes(b"NOT hello world")

            result = verify_snapshot(manifest_path)
            self.assertFalse(result.ok)
            self.assertEqual(result.missing, ())
            self.assertEqual(result.mismatched, ("incoming.txt",))


class SummarizeSnapshotVerificationTests(unittest.TestCase):
    """``summarize_snapshot_verification`` adapts ``verify_snapshot`` into a
    JSON-serialisable provenance summary the synth runner records + warns on.
    This is the wiring that makes ``verify_snapshot`` actually run on the
    synth path (it was previously dead code)."""

    def _make_snapshot(self, tmp_path: Path) -> Path:
        src = tmp_path / "incoming.txt"
        _write(src, b"hello world")
        snap_dir = tmp_path / "00_raw"
        snapshot_to(
            snap_dir,
            [RawSource(path=src, source_url="https://h.example", captured_at="2026-05-27T19:18:00Z")],
        )
        return snap_dir / "raw_manifest.json"

    def test_clean_snapshot_summary_is_ok(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest_path = self._make_snapshot(Path(tmp))
            summary = summarize_snapshot_verification(manifest_path)
            self.assertTrue(summary["ok"])
            self.assertEqual(summary["missing"], [])
            self.assertEqual(summary["mismatched"], [])

    def test_tampered_snapshot_summary_reports_drift(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest_path = self._make_snapshot(Path(tmp))
            (manifest_path.parent / "incoming.txt").write_bytes(b"changed upstream")
            summary = summarize_snapshot_verification(manifest_path)
            self.assertFalse(summary["ok"])
            self.assertEqual(summary["mismatched"], ["incoming.txt"])

    def test_summary_is_json_serialisable(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest_path = self._make_snapshot(Path(tmp))
            summary = summarize_snapshot_verification(manifest_path)
            # Must round-trip through json so it can land in provenance as-is.
            self.assertEqual(json.loads(json.dumps(summary)), summary)


class ShouldRefetchTests(unittest.TestCase):
    """``should_refetch`` is the small decision helper for first-run /
    cached / explicit-refresh cases."""

    def test_first_run_with_no_manifest_returns_true(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nonexistent" / "raw_manifest.json"
            self.assertTrue(should_refetch(missing, refresh=False))

    def test_existing_manifest_with_no_refresh_returns_false(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "x"
            _write(src, b"xxx")
            snap_dir = tmp_path / "00_raw"
            snapshot_to(
                snap_dir,
                [RawSource(path=src, source_url="https://x.example", captured_at="2026-05-27T19:18:00Z")],
            )
            self.assertFalse(should_refetch(snap_dir / "raw_manifest.json", refresh=False))

    def test_existing_manifest_with_refresh_returns_true(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "x"
            _write(src, b"xxx")
            snap_dir = tmp_path / "00_raw"
            snapshot_to(
                snap_dir,
                [RawSource(path=src, source_url="https://x.example", captured_at="2026-05-27T19:18:00Z")],
            )
            self.assertTrue(should_refetch(snap_dir / "raw_manifest.json", refresh=True))


if __name__ == "__main__":
    unittest.main()
