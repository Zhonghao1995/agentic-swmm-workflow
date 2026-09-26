"""Memory layout (2026-09-06): one folder, three kinds, one hard rule.

``memory/initial/`` is hand-written and ships; ``memory/store/`` is what the
program writes in the workspace and is never committed; ``memory/facts.md``
is promoted by a human. The hard rule: a file the program writes in normal
use is not in the repository, and never lands under the resource root of a
pip install.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentic_swmm.utils import paths

REPO = Path(__file__).resolve().parents[1]


def _snapshot(root: Path) -> dict[str, float]:
    return {str(p): p.stat().st_mtime for p in root.rglob("*") if p.is_file()}


def test_store_and_proposals_are_ignored_and_initial_is_tracked() -> None:
    def ignored(rel: str) -> bool:
        return subprocess.run(["git", "check-ignore", "-q", rel], cwd=REPO).returncode == 0

    assert ignored("memory/store/anything.jsonl")
    assert ignored("memory/proposals/001-anything.md")
    assert ignored("memory/facts_staging.md")
    assert not ignored("memory/initial/soul.md")
    assert not ignored("memory/facts.md")


def test_nothing_program_written_is_tracked() -> None:
    tracked = subprocess.run(["git", "ls-files", "memory"], cwd=REPO, capture_output=True, text=True).stdout.split()
    assert tracked, "memory/ must ship the initial memory"
    for rel in tracked:
        assert rel.startswith("memory/initial/") or rel == "memory/facts.md", rel


def test_the_shipped_initial_memory_is_complete() -> None:
    from agentic_swmm.runtime.registry import LONG_TERM_MEMORY_FILES, REFERENCE_TABLE_FILES

    for preferred, _fallback in LONG_TERM_MEMORY_FILES:
        assert (REPO / preferred).is_file(), preferred
    for rel in REFERENCE_TABLE_FILES:
        assert (REPO / rel).is_file(), rel


def test_a_pip_install_never_writes_under_the_resource_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the writers a session touches (run failures, facts staging, the
    session database, bootstrap) on a pip-shaped install and assert the
    packaged root is untouched."""
    site = tmp_path / "site-packages"
    site.mkdir()
    packaged = tmp_path / "aiswmm"
    for rel in ("memory/initial", "skills/swmm-runner", "agent"):
        (packaged / rel).mkdir(parents=True)
    (packaged / "memory" / "initial" / "citations.yaml").write_text('schema_version: "1.0"\n', encoding="utf-8")
    monkeypatch.setattr(paths, "repo_root", lambda: site)
    monkeypatch.setattr(paths, "packaged_resource_root", lambda: packaged)
    for var in ("AISWMM_MEMORY_DIR", "AISWMM_FACTS_DIR", "AISWMM_SESSION_DB", "AISWMM_RUNS_ROOT"):
        monkeypatch.delenv(var, raising=False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    before = _snapshot(packaged)

    from agentic_swmm.commands import bootstrap_memory
    from agentic_swmm.memory import facts, run_failures, session_db
    from agentic_swmm.memory.session_sync import default_db_path

    result = bootstrap_memory.bootstrap_memory_dir(None)
    assert result.target_dir == workspace / "memory" / "store"
    store = run_failures.resolve_store()
    assert store == workspace / "memory" / "store" / "run_failures.jsonl"
    facts.record_fact_to_staging("the outfall is OUT_0")
    fp = facts.resolve_paths()
    assert fp.facts_md == workspace / "memory" / "facts.md"
    assert fp.staging_md == workspace / "memory" / "facts_staging.md"
    db = default_db_path()
    assert db == workspace / "memory" / "store" / "memory.sqlite"
    with session_db.connect(db):
        pass
    assert db.is_file()

    assert _snapshot(packaged) == before, "a pip install wrote under its packaged resource root"
    assert not (site / "memory").exists()


def test_the_legacy_session_database_is_adopted_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic_swmm.memory.session_sync import default_db_path

    monkeypatch.delenv("AISWMM_SESSION_DB", raising=False)
    workspace = tmp_path / "ws"
    (workspace / "runs").mkdir(parents=True)
    legacy = workspace / "runs" / "sessions.sqlite"
    legacy.write_bytes(b"legacy")
    db = default_db_path(workspace)
    assert db == workspace / "memory" / "store" / "memory.sqlite"
    assert db.read_bytes() == b"legacy"
    assert not legacy.exists()
    # A second call leaves the adopted file alone.
    db.write_bytes(b"newer")
    assert default_db_path(workspace).read_bytes() == b"newer"


def test_a_stale_memory_registry_falls_back_to_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ~/.aiswmm/memory.json written before the move names files that no
    longer exist; the shell must still load its startup memory."""
    from agentic_swmm.runtime import registry

    stale = tmp_path / "memory.json"
    stale.write_text(json.dumps({"memory_files": [
        {"name": "soul", "path": str(tmp_path / "agent" / "memory" / "soul.md"), "enabled": True, "load_at_startup": True},
    ]}), encoding="utf-8")
    monkeypatch.setattr(registry, "memory_registry_path", lambda: stale)
    files = registry.enabled_startup_memory_files()
    assert files, "stale registry must fall back to discovery"
    assert all(p.parent.name == "initial" for p in files)


def test_reference_table_lookup_prefers_the_store_copy(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    shipped = paths.reference_table_path("storm_library.yaml", store)
    assert shipped == paths.initial_memory_dir() / "storm_library.yaml"
    assert shipped.is_file()
    override = store / "storm_library.yaml"
    override.write_text("schema_version: \"1.0\"\n", encoding="utf-8")
    assert paths.reference_table_path("storm_library.yaml", store) == override


def test_a_legacy_database_is_never_adopted_under_an_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-09-06: with AISWMM_MEMORY_DIR pointing at a temp store (the test
    suite), the workspace's own runs/sessions.sqlite must stay untouched."""
    from agentic_swmm.memory.session_sync import default_db_path

    workspace = tmp_path / "ws"
    (workspace / "runs").mkdir(parents=True)
    legacy = workspace / "runs" / "sessions.sqlite"
    legacy.write_bytes(b"legacy")
    monkeypatch.setattr(paths, "repo_root", lambda: workspace)
    monkeypatch.setattr(paths, "is_checkout", lambda root=None: True)
    monkeypatch.delenv("AISWMM_SESSION_DB", raising=False)
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(tmp_path / "elsewhere"))
    db = default_db_path()
    assert db == (tmp_path / "elsewhere").resolve() / "memory.sqlite"
    assert legacy.read_bytes() == b"legacy"
    assert not db.exists()


def test_the_suite_isolates_the_session_database() -> None:
    import os

    assert os.environ.get("AISWMM_SESSION_DB"), "conftest must point AISWMM_SESSION_DB at the temp store"
    assert Path(os.environ["AISWMM_SESSION_DB"]).parent == Path(os.environ["AISWMM_MEMORY_DIR"])


def test_the_atexit_resync_uses_the_database_captured_at_sync_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-09-06: the atexit re-sync resolved the database path at interpreter
    exit, after the test harness had restored the environment, and wrote
    test sessions into the developer's real store."""
    import json

    from agentic_swmm.agent import runtime_loop
    from agentic_swmm.agent.swmm_runtime.run_layout import agent_file_for_write

    session_dir = tmp_path / "sessions" / "2026-09-06" / "120000_probe_chat"
    trace = agent_file_for_write(session_dir, "agent_trace.jsonl")
    trace.write_text(
        json.dumps({"event": "user_prompt", "text": "probe", "timestamp_utc": "2026-09-06T12:00:00+00:00"}) + "\n",
        encoding="utf-8",
    )
    harness_db = tmp_path / "harness" / "memory.sqlite"
    monkeypatch.setenv("AISWMM_SESSION_DB", str(harness_db))
    monkeypatch.setattr(runtime_loop, "_SYNCED_SESSION_DIRS", {})
    runtime_loop._sync_session_end(session_dir)
    assert harness_db.exists()
    # The harness restores the environment; a later default would point elsewhere.
    real_default = tmp_path / "real" / "memory.sqlite"
    monkeypatch.setenv("AISWMM_SESSION_DB", str(real_default))
    runtime_loop._atexit_sync_recent_sessions()
    assert not real_default.exists(), "atexit re-sync must reuse the database captured at sync time"

