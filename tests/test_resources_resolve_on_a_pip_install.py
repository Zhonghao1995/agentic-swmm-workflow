"""Every packaged resource resolves on a pip install, not only in a checkout (F-130 to F-133).

Live test 2026-09-04 (phase 3, T1-A): `pip install aiswmm==0.9.4` into a clean
venv, probed from /tmp. Eighteen lookups in thirteen files addressed resources
under repo_root(), which on a wheel is site-packages, where nothing lives:
the memory layer read and wrote a directory that never existed while
`aiswmm bootstrap memory` wrote under the packaged root; doctor's mcp-deps row
scanned the wrong tree and reported nothing while all eleven servers lacked
node_modules; the HITL thresholds document was not in the wheel at all, so
pattern validation was silently off; and four hints sent the user to
`aiswmm setup --install-mcp`, a flag that does not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentic_swmm.utils import paths

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r'repo_root\(\) */ *"(memory|docs|mcp|examples)"|_repo_root\(\) */ *_THRESHOLDS')


def test_no_product_code_addresses_a_resource_under_repo_root() -> None:
    offenders = []
    for py in (REPO / "agentic_swmm").rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{py.relative_to(REPO)}:{i}: {line.strip()}")
    assert not offenders, "resources must resolve through resource_root()/resolve_memory_dir():\n" + "\n".join(offenders)


def test_no_hint_names_the_nonexistent_install_mcp_flag() -> None:
    hits = [str(py.relative_to(REPO)) for py in (REPO / "agentic_swmm").rglob("*.py") if "setup --install-mcp" in py.read_text(encoding="utf-8")]
    assert hits == [], hits


@pytest.fixture
def pip_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """repo_root() has no resources (a site-packages dir); the packaged root has them."""
    site = tmp_path / "site-packages"
    site.mkdir()
    packaged = tmp_path / "aiswmm"
    (packaged / "skills" / "swmm-runner").mkdir(parents=True)
    (packaged / "agent" / "memory").mkdir(parents=True)
    (packaged / "memory" / "modeling-memory").mkdir(parents=True)
    (packaged / "memory" / "modeling-memory" / "lessons_learned.md").write_text("# lessons\n", encoding="utf-8")
    (packaged / "docs").mkdir()
    (packaged / "docs" / "hitl-thresholds.md").write_text(
        "---\nschema_version: 1\nthresholds:\n  continuity_error_over_threshold:\n    severity: block\n"
        "    measured_key: \"continuity.flow_routing\"\n    operator: \">\"\n    value: 5.0\n"
        "    evidence_path: \"06_qa/qa_summary.json\"\n    message: \"Flow routing continuity error exceeds 5%.\"\n"
        "    rationale: \"Guards the solver.\"\n---\n", encoding="utf-8")
    (packaged / "mcp" / "swmm-uncertainty").mkdir(parents=True)
    (packaged / "mcp" / "swmm-uncertainty" / "package.json").write_text("{}\n", encoding="utf-8")
    (packaged / "examples" / "tecnopolo").mkdir(parents=True)
    (packaged / "scripts").mkdir()
    (packaged / "scripts" / "install_mcp_deps.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    monkeypatch.setattr(paths, "repo_root", lambda: site)
    monkeypatch.setattr(paths, "packaged_resource_root", lambda: packaged)
    monkeypatch.delenv("AISWMM_MEMORY_DIR", raising=False)
    return packaged


def test_memory_resolves_under_the_packaged_root(pip_shape: Path) -> None:
    assert paths.resolve_memory_dir() == pip_shape / "memory" / "modeling-memory"
    from agentic_swmm.commands import doctor

    assert doctor._memory_dir(paths.repo_root()) == pip_shape / "memory" / "modeling-memory"


def test_the_thresholds_document_is_read_from_the_packaged_root(pip_shape: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic_swmm.hitl import request_expert_review as handler

    monkeypatch.setattr(handler, "resource_root", paths.resource_root)
    assert "continuity_error_over_threshold" in handler._documented_patterns()


def test_doctor_sees_packaged_servers_without_node_modules(pip_shape: Path) -> None:
    from agentic_swmm.commands import doctor

    assert doctor._mcp_servers_without_deps(paths.resource_root()) == ["swmm-uncertainty"]


def test_the_preflight_names_a_script_that_exists(pip_shape: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic_swmm.agent import mcp_client

    monkeypatch.setattr(mcp_client, "resource_root", paths.resource_root)
    launcher = pip_shape / "scripts" / "run_mcp_server.mjs"
    launcher.write_text("// noop\n", encoding="utf-8")
    with pytest.raises(mcp_client.McpClientError) as excinfo:
        mcp_client._preflight("/opt/homebrew/bin/node", [str(launcher), "swmm-uncertainty"])
    message = str(excinfo.value)
    assert "swmm-uncertainty" in message
    assert str(pip_shape / "scripts" / "install_mcp_deps.sh") in message
    assert "--install-mcp" not in message


def test_the_public_wheel_ships_the_thresholds_document_and_nothing_else_from_docs() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("aiswmm_setup", REPO / "setup.py")
    source = (REPO / "setup.py").read_text(encoding="utf-8")
    ns: dict = {}
    exec(compile(source.split("setup(data_files=")[0], str(REPO / "setup.py"), "exec"), ns)
    assert "docs" in ns["PUBLIC_RESOURCE_DIRS"]
    include = ns["_include_public_resource"]
    assert include(Path("docs/hitl-thresholds.md")) is True
    assert include(Path("docs/adr/0001-something.md")) is False
    assert include(Path("docs/testing-prompts.md")) is False


def test_bootstrap_writes_where_doctor_reads(pip_shape: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """F-132: the skeleton used to land in ./memory/modeling-memory of the
    current directory, so doctor kept asking for a bootstrap that had run."""
    from agentic_swmm.commands import bootstrap_memory, doctor
    from agentic_swmm.diagnostics.doctor_report import collect_memory_store_status

    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    result = bootstrap_memory.bootstrap_memory_dir(None)
    assert result.target_dir == pip_shape / "memory" / "modeling-memory"
    assert not (elsewhere / "memory").exists()
    names = {s.name: s for s in collect_memory_store_status(doctor._memory_dir(paths.repo_root()))}
    for store in ("parametric_memory.jsonl", "calibration_memory.jsonl"):
        assert store in names, sorted(names)
        assert "bootstrap" not in (names[store].remediation or ""), names[store]


def test_bootstrap_creates_the_three_yaml_libraries_and_doctor_stops_asking(pip_shape: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agentic_swmm.commands import bootstrap_memory, doctor
    from agentic_swmm.diagnostics.doctor_report import collect_memory_store_status

    monkeypatch.chdir(tmp_path)
    bootstrap_memory.bootstrap_memory_dir(None)
    memory_dir = doctor._memory_dir(paths.repo_root())
    for name in ("reference_benchmarks.yaml", "citations.yaml", "storm_library.yaml"):
        assert (memory_dir / name).exists(), name
    remedies = {s.name: (s.remediation or "") for s in collect_memory_store_status(memory_dir)}
    assert not any("copy from repo" in r for r in remedies.values()), remedies


def test_the_yaml_skeletons_match_the_checkout_placeholders() -> None:
    from agentic_swmm.commands import bootstrap_memory

    for name, text in bootstrap_memory._YAML_SKELETONS.items():
        source = REPO / "memory" / "modeling-memory" / name
        if not source.exists():
            pytest.skip("not a checkout")
        assert text == source.read_text(encoding="utf-8"), f"{name} skeleton drifted from the checkout file"
