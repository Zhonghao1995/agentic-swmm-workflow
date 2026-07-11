"""The one chunked SHA-256-of-a-file loop.

Four in-process modules (``run_manifests.sha256_of``,
``session_header._sha256_file``, ``raw_snapshot._sha256_of``,
``memory_reflect._sha256``) and several ``scripts/`` entry points each
hand-rolled the same hashlib loop. Converged here per ADR-0006 D5; the
in-process copies now delegate to this function under their historical
names so existing callers/tests are unaffected.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


def sha256_of_file(path: Path) -> str:
    """Return the hex SHA-256 digest of ``path``'s bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
