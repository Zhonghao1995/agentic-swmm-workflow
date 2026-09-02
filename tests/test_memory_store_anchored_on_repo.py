"""The memory stores live in the repository, wherever aiswmm is run from (F-15).

`run_failures.resolve_store()` and `audit_hook._resolve_memory_dir()` fell
back to the cwd-relative ``memory/modeling-memory``; `aiswmm` run from any
other directory created a stray store there and recorded failures nobody
would read.
"""

from __future__ import annotations

import os
from pathlib import Path

from agentic_swmm.memory import audit_hook, run_failures
from agentic_swmm.utils.paths import repo_root


def test_run_failures_store_is_anchored_on_the_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("AISWMM_MEMORY_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    store = run_failures.resolve_store()
    assert store.is_absolute()
    assert store == repo_root() / "memory" / "modeling-memory" / "run_failures.jsonl"
    assert not (tmp_path / "memory").exists()


def test_audit_hook_memory_dir_is_anchored_on_the_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("AISWMM_MEMORY_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert audit_hook._resolve_memory_dir(None) == repo_root() / "memory" / "modeling-memory"


def test_explicit_arguments_and_the_override_still_win(tmp_path, monkeypatch):
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(tmp_path / "override"))
    assert run_failures.resolve_store() == (tmp_path / "override").resolve() / "run_failures.jsonl"
    assert audit_hook._resolve_memory_dir(None) == Path(str(tmp_path / "override"))
    monkeypatch.delenv("AISWMM_MEMORY_DIR", raising=False)
    assert run_failures.resolve_store(tmp_path / "explicit") == tmp_path / "explicit" / "run_failures.jsonl"
    assert audit_hook._resolve_memory_dir(tmp_path / "proj") == tmp_path / "proj" / "memory" / "modeling-memory"
