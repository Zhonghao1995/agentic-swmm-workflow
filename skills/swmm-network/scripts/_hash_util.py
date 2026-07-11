"""Shared SHA-256 file hashing for skills/swmm-network/scripts/.

``prepare_storm_inputs.py`` and ``city_network_adapter.py`` each
hand-rolled the same chunked hashlib loop. Converged here per
ADR-0006 D5. Deliberately agentic_swmm-import-free: these scripts run
as standalone subprocess entry points spawned by mcp/swmm-network's
server.js, not through the agentic_swmm package.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path | None) -> str | None:
    """Hex SHA-256 digest of ``path``'s bytes, or ``None`` if ``path`` is ``None``."""
    if path is None:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
