"""The RAG refresh hook must invoke refresh_after_run.py with ITS flags.

Found live (2026-08-08, SWMMCanada chain validation): the hook built the
command with the fallback script's interface (--out-dir, no --run-dir),
so with the real refresh entry point installed, every audit's RAG
refresh exited with an argparse usage error that the best-effort
contract swallowed into .last_refresh_error.json. These tests pin the
per-script invocation shapes.
"""
from __future__ import annotations

from pathlib import Path

from agentic_swmm.memory import audit_hook


class _Proc:
    returncode = 0
    stderr = ""
    stdout = "ok"


def _capture(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = [str(part) for part in cmd]
        return _Proc()

    monkeypatch.setattr(audit_hook.subprocess, "run", fake_run)
    return captured


def test_primary_refresh_script_gets_run_dir_and_rag_dir(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    rc, _ = audit_hook._refresh_rag_corpus(
        tmp_path / "memory", tmp_path / "rag", tmp_path / "runs", tmp_path / "runs" / "r1"
    )
    assert rc == 0
    cmd = captured["cmd"]
    assert any(part.endswith("refresh_after_run.py") for part in cmd), cmd
    assert "--run-dir" in cmd
    assert "--rag-dir" in cmd
    assert "--out-dir" not in cmd


def test_missing_run_dir_uses_corpus_fallback_with_its_own_flags(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    rc, _ = audit_hook._refresh_rag_corpus(
        tmp_path / "memory", tmp_path / "rag", tmp_path / "runs", None
    )
    assert rc == 0
    cmd = captured["cmd"]
    assert any(part.endswith("build_memory_corpus.py") for part in cmd), cmd
    assert "--out-dir" in cmd
    assert "--run-dir" not in cmd
