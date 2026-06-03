"""Hash/cache/verify utilities for raw upstream snapshots.

Reusable across any aiswmm skill that needs to record non-deterministic
upstream inputs (OSM extracts, DEM tiles, climate grids) under a per-run
``00_raw/`` directory. The first user is the ``swmm-anywhere`` skill, but
the module is deliberately decoupled from SWMManywhere so it can host
future skills that face the same "the upstream source can change underneath
us" problem.

Public surface:

* ``RawSource(path, source_url, captured_at)`` — describes one input file
  the caller wants snapshotted.
* ``snapshot_to(snapshot_dir, sources) -> RawManifest`` — copies each
  ``RawSource.path`` into ``snapshot_dir``, computes its SHA-256, writes
  ``snapshot_dir/raw_manifest.json``, and returns the manifest.
* ``verify_snapshot(manifest_path) -> VerifyResult`` — re-hashes every
  file recorded in the manifest and reports missing/mismatched paths.
* ``summarize_snapshot_verification(manifest_path) -> dict`` — JSON-friendly
  adapter over ``verify_snapshot`` for recording snapshot integrity in run
  provenance.
* ``should_refetch(manifest_path, *, refresh) -> bool`` — three-way
  decision helper for first-run / cached / explicit-refresh.

The manifest file uses ``manifest_version = "1.0"``; bump when the schema
changes incompatibly.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_MANIFEST_FILENAME = "raw_manifest.json"
_MANIFEST_VERSION = "1.0"
_HASH_CHUNK = 64 * 1024


@dataclass(frozen=True)
class RawSource:
    path: Path
    source_url: str
    captured_at: str


@dataclass(frozen=True)
class RawManifestEntry:
    path: str
    source_url: str
    captured_at: str
    sha256: str


@dataclass(frozen=True)
class RawManifest:
    manifest_version: str
    sources: tuple[RawManifestEntry, ...]


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    missing: tuple[str, ...]
    mismatched: tuple[str, ...]


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_to(snapshot_dir: Path, sources: Sequence[RawSource]) -> RawManifest:
    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    entries: list[RawManifestEntry] = []
    for source in sources:
        src_path = Path(source.path)
        dest_path = snapshot_dir / src_path.name
        if src_path.resolve() != dest_path.resolve():
            shutil.copyfile(src_path, dest_path)
        entries.append(
            RawManifestEntry(
                path=src_path.name,
                source_url=source.source_url,
                captured_at=source.captured_at,
                sha256=_sha256_of(dest_path),
            )
        )

    manifest = RawManifest(manifest_version=_MANIFEST_VERSION, sources=tuple(entries))
    _write_manifest(snapshot_dir / _MANIFEST_FILENAME, manifest)
    return manifest


def verify_snapshot(manifest_path: Path) -> VerifyResult:
    manifest_path = Path(manifest_path)
    manifest = _read_manifest(manifest_path)
    base = manifest_path.parent

    missing: list[str] = []
    mismatched: list[str] = []
    for entry in manifest.sources:
        candidate = base / entry.path
        if not candidate.exists():
            missing.append(entry.path)
            continue
        if _sha256_of(candidate) != entry.sha256:
            mismatched.append(entry.path)

    return VerifyResult(
        ok=not missing and not mismatched,
        missing=tuple(missing),
        mismatched=tuple(mismatched),
    )


def summarize_snapshot_verification(manifest_path: Path) -> dict:
    """Verify a raw snapshot and return a JSON-serialisable provenance summary.

    A thin adapter over :func:`verify_snapshot` so the synth runner can record
    snapshot integrity in its provenance and warn on drift. Returns lists (not
    tuples) so the result drops straight into a JSON provenance record.
    """
    result = verify_snapshot(manifest_path)
    return {
        "ok": result.ok,
        "missing": list(result.missing),
        "mismatched": list(result.mismatched),
    }


def should_refetch(manifest_path: Path, *, refresh: bool) -> bool:
    if not Path(manifest_path).exists():
        return True
    return bool(refresh)


def _write_manifest(target: Path, manifest: RawManifest) -> None:
    payload = {
        "manifest_version": manifest.manifest_version,
        "sources": [
            {
                "path": e.path,
                "source_url": e.source_url,
                "captured_at": e.captured_at,
                "sha256": e.sha256,
            }
            for e in manifest.sources
        ],
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=False))


def _read_manifest(manifest_path: Path) -> RawManifest:
    payload = json.loads(Path(manifest_path).read_text())
    return RawManifest(
        manifest_version=payload["manifest_version"],
        sources=tuple(
            RawManifestEntry(
                path=item["path"],
                source_url=item["source_url"],
                captured_at=item["captured_at"],
                sha256=item["sha256"],
            )
            for item in payload["sources"]
        ),
    )
