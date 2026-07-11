"""Test fixtures shared across the suite.

Provides the audit-pipeline builders, the env-override context manager,
and the ``isolated_home`` / ``isolated_config_dir`` isolation fixtures
the provider / preflight tests rely on. The two LLM providers (openai
default + anthropic opt-in) are pure-stdlib ``urllib`` clients exercised
via their ``AISWMM_*_MOCK_*`` env hooks, so no synthetic SDK module is
needed here.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest


@contextlib.contextmanager
def env_overrides(**overrides: str | None):
    """Snapshot + restore ``os.environ`` for the duration of the block.

    Promoted to ``conftest.py`` per issue #201 — both
    ``tests/test_digest_locale_glyphs.py`` and
    ``tests/test_prd08_b_storm_and_chrome.py`` previously rolled the
    same context-manager (under the names ``_EnvOverride`` and
    ``_env_overrides``). A single definition keeps the env-restore
    contract aligned across the test suite.

    Pass ``key=None`` to *unset* a variable for the duration of the
    block (so a test that needs ``LC_ALL`` unset can ``LC_ALL=None``
    rather than ``monkeypatch.delenv``).
    """
    snapshot: dict[str, str | None] = {}
    for key, value in overrides.items():
        snapshot[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, original in snapshot.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


_REPO_ROOT = Path(__file__).resolve().parents[1]
_AUDIT_SCRIPT = (
    _REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"
)


def load_audit_module():
    """Load ``audit_run.py`` as an importable module.

    The audit script is run as a subprocess by ``aiswmm audit`` and
    therefore does not live inside the ``agentic_swmm`` package. Tests
    that want to exercise its helpers reach for ``importlib.util`` —
    previously each test file hand-rolled the spec/loader dance. Lifted
    here per issue #196 so both audit-test files share one definition.
    """
    spec = importlib.util.spec_from_file_location(
        "_audit_run_under_test", _AUDIT_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_audit_run_under_test"] = module
    spec.loader.exec_module(module)
    return module


def seed_minimal_run_dir(
    tmp_path: Path,
    *,
    case_name: str = "case-dedup",
    with_internal_node: bool = False,
) -> Path:
    """Build the minimal SWMM run-dir layout the audit pipeline accepts.

    The two audit-pipeline test files (``test_audit_run_results_section``
    and ``test_audit_runner_manifest_dedup``) previously hand-rolled the
    same ~50-line builder; consolidated here per issue #196 so the
    fixture has a single source of truth.

    ``with_internal_node=True`` adds the ``metrics.internal_node_peak``
    payload that the Tecnopolo fixture in
    ``test_audit_run_results_section`` requires.
    """
    run_dir = tmp_path / "runs" / case_name
    runner = run_dir / "05_runner"
    runner.mkdir(parents=True)
    (runner / "model.rpt").write_text(
        """
        ***** Node Inflow Summary *****
        ------------------------------------------------
          OU2             OUTFALL       0.001       0.061      2    03:15

        ***** Runoff Quantity Continuity *****
        Continuity Error (%) ............. -0.13

        ***** Flow Routing Continuity *****
        Continuity Error (%) ............. -0.004
        """,
        encoding="utf-8",
    )
    (runner / "model.out").write_text("binary-placeholder", encoding="utf-8")
    (runner / "stdout.txt").write_text("", encoding="utf-8")
    (runner / "stderr.txt").write_text("", encoding="utf-8")
    metrics: dict[str, Any] = {
        "peak": {
            "node": "OU2",
            "peak": 0.061,
            "time_hhmm": "03:15",
            "source": "Node Inflow Summary",
        },
        "continuity": {
            "runoff_quantity": {
                "Surface Runoff": {"col1": 0.097, "col2": 44.483},
                "Continuity Error (%)": -0.13,
            },
            "flow_routing": {
                "Continuity Error (%)": -0.004,
            },
        },
    }
    if with_internal_node:
        metrics["internal_node_peak"] = {
            "node": "J22",
            "peak": 0.007,
            "time_hhmm": "03:15",
        }
    (runner / "manifest.json").write_text(
        json.dumps(
            {
                "files": {
                    "rpt": str(runner / "model.rpt"),
                    "out": str(runner / "model.out"),
                    "stdout": str(runner / "stdout.txt"),
                    "stderr": str(runner / "stderr.txt"),
                },
                "metrics": metrics,
                "return_code": 0,
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def seed_runner_manifest(
    run_dir: Path,
    *,
    runner_dir_name: str = "06_runner",
    **overrides: Any,
) -> None:
    """Build the ``O1``/``OUTFALL`` runner-stage fixture the audit_run.py
    CLI accepts: model.rpt + model.out + stdout/stderr + manifest.json.

    Four audit-CLI subprocess test files (``test_commands_audit_moc_and_bak``,
    ``test_case_id_provenance``, ``test_audit_run_schema_v1_1``,
    ``test_audit_note_human_decisions_section``) previously hand-rolled a
    byte-for-byte-identical ``_seed_runner`` each -- only
    ``test_case_id_provenance`` differs, in the manifest's ``metrics``
    (adds a ``source: "rpt"`` peak and a ``continuity`` key) and a
    top-level ``swmm5`` key. Consolidated here per ADR-0006 D5.

    ADR-0004: the canonical runner stage is ``06_runner``; the four
    existing callers all pre-date the rename and pass
    ``runner_dir_name="05_runner"`` explicitly.

    ``overrides`` are shallow-merged into the manifest dict's top level
    after the base ``files``/``metrics``/``return_code`` are built --
    e.g. passing ``metrics={...}`` replaces the default metrics dict
    wholesale, and ``swmm5={...}`` adds a new top-level key.
    """
    runner = run_dir / runner_dir_name
    runner.mkdir(parents=True)
    (runner / "model.rpt").write_text(
        """
        ***** Node Inflow Summary *****
        ------------------------------------------------
          O1              OUTFALL       0.001       1.250      2    12:47

        ***** Flow Routing Continuity *****
        Continuity Error (%) ............. 0.00
        """,
        encoding="utf-8",
    )
    (runner / "model.out").write_text("binary-placeholder", encoding="utf-8")
    (runner / "stdout.txt").write_text("", encoding="utf-8")
    (runner / "stderr.txt").write_text("", encoding="utf-8")
    manifest: dict[str, Any] = {
        "files": {
            "rpt": str(runner / "model.rpt"),
            "out": str(runner / "model.out"),
            "stdout": str(runner / "stdout.txt"),
            "stderr": str(runner / "stderr.txt"),
        },
        "metrics": {
            "peak": {
                "node": "O1",
                "peak": 1.25,
                "time_hhmm": "12:47",
                "source": "Node Inflow Summary",
            }
        },
        "return_code": 0,
    }
    manifest.update(overrides)
    (runner / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def seed_provenance_run_dir(project_root: Path, provenance: dict[str, Any]) -> Path:
    """Build the minimal run dir ``trigger_memory_refresh`` accepts.

    Layout: ``<project_root>/runs/abc/09_audit/experiment_provenance.json``.
    Consolidates the per-file ``_make_run`` builders the audit-hook
    memory-bridge tests used to hand-roll (same treatment issue #196 gave
    the audit-pipeline run-dir builders).
    """
    run_dir = project_root / "runs" / "abc"
    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )
    return run_dir


@contextlib.contextmanager
def patched_audit_hook_subprocess(**extra_stubs: Any):
    """Stub ``trigger_memory_refresh``'s two heavy externals to success.

    Patches ``audit_hook._summarize_memory_cli`` and
    ``audit_hook._refresh_rag_corpus`` to ``(0, "")`` so audit-hook tests
    exercise only the in-process wiring. Additional ``audit_hook``
    attributes can be stubbed via keyword args, e.g.
    ``patched_audit_hook_subprocess(_run_decay_pass={"skipped": True})``.
    """
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            mock.patch(
                "agentic_swmm.memory.audit_hook._summarize_memory_cli",
                return_value=(0, ""),
            )
        )
        stack.enter_context(
            mock.patch(
                "agentic_swmm.memory.audit_hook._refresh_rag_corpus",
                return_value=(0, ""),
            )
        )
        for attr, retval in extra_stubs.items():
            stack.enter_context(
                mock.patch(
                    f"agentic_swmm.memory.audit_hook.{attr}",
                    return_value=retval,
                )
            )
        yield


class _FakeTTYStream(io.StringIO):
    """StringIO that claims to be a TTY.

    Spinner / TTY-rendering tests use this to force the carriage-return
    rendering path (instead of the newline-per-line non-TTY fallback)
    while still capturing output via ``.getvalue()``.

    Lives here (instead of being duplicated in each test module) per
    issue #190 — one definition keeps the test-side contract aligned
    with the production ``Spinner._stream_is_tty`` probe.
    """

    def isatty(self) -> bool:  # type: ignore[override]
        return True


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point ``Path.home()`` at a fresh tmp dir to isolate config files.

    The provider-preflight tests need an isolated ``HOME`` with no
    ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` leaking from the real
    environment, so the two-API-key resolution is exercised against a
    known-empty slate.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return home


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    """Point ``config_dir()`` at a fresh tmp dir so anything written
    under it (e.g. ``mcp.json``, ``silent_fallbacks.jsonl``) stays
    local to the test.

    Three test files previously rolled their own byte-identical copy
    of this fixture (issue #220 reuse-review finding). Centralised
    here next to ``isolated_home`` so the next consumer reuses it
    instead of copying it a fourth time.
    """
    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(tmp_path))
    yield tmp_path


def read_silent_fallback_events(jsonl_path):
    """Read every JSON object from ``silent_fallbacks.jsonl`` in line order.

    Helper shared by the error_boundary unit and regression tests so
    both consume the same parsing convention (one JSON object per
    non-empty line, UTF-8). Returns ``[]`` when the file does not
    exist — a healthy session that triggered no boundary catches
    leaves the jsonl absent.
    """
    import json
    from pathlib import Path

    path = Path(jsonl_path)
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
