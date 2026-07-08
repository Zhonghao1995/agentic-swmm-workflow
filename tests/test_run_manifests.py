"""Direct tests for the prepared-input manifest builders (run_manifests.py).

The ``aiswmm run`` CLI tests cover the end-to-end path; this file pins
the schema knowledge at function level — dicts in, dicts out, no argv.
"""
from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.swmm_runtime.run_manifests import (
    build_builder_manifest,
    build_qa_summary,
    build_top_manifest,
    parse_runner_manifest,
    source_type_of,
)


def _runner_manifest(tmp_path: Path, *, return_code: int = 0) -> dict:
    rpt = tmp_path / "model.rpt"
    out = tmp_path / "model.out"
    rpt.write_text("report", encoding="utf-8")
    out.write_text("binary", encoding="utf-8")
    return {
        "return_code": return_code,
        "files": {"rpt": str(rpt), "out": str(out)},
        "metrics": {
            "peak": {"node": "O1", "peak": 0.061, "source": "Node Inflow Summary"},
            "continuity": {
                "continuity_error_percent": {"runoff": -0.13, "flow": -0.004}
            },
        },
        "swmm5": {"version": "5.2.4"},
    }


def test_qa_summary_passes_on_healthy_run(tmp_path: Path) -> None:
    peak, continuity, qa = build_qa_summary(
        _runner_manifest(tmp_path), qa_dir=tmp_path / "07_qa"
    )
    assert qa["status"] == "pass"
    assert [c["id"] for c in qa["checks"]] == [
        "swmm_return_code_zero",
        "runner_rpt_exists",
        "runner_out_exists",
        "peak_parsed",
        "continuity_parsed",
    ]
    assert peak["peak"] == 0.061
    assert continuity["continuity_error_percent"]["runoff"] == -0.13


def test_qa_summary_fails_on_nonzero_return_code(tmp_path: Path) -> None:
    _, _, qa = build_qa_summary(
        _runner_manifest(tmp_path, return_code=1), qa_dir=tmp_path / "07_qa"
    )
    assert qa["status"] == "fail"
    failed = {c["id"] for c in qa["checks"] if not c["ok"]}
    assert "swmm_return_code_zero" in failed


def test_top_manifest_schema_keys(tmp_path: Path) -> None:
    inp = tmp_path / "model.inp"
    inp.write_text("[OPTIONS]\n", encoding="utf-8")
    runner_manifest = _runner_manifest(tmp_path)
    manifest = build_top_manifest(
        source_inp=inp,
        run_inp=inp,
        builder_inp=inp,
        sidecar_inputs=[],
        source_type=source_type_of(inp),
        runner_manifest=runner_manifest,
        runner_files=runner_manifest["files"],
        runner_dir=tmp_path / "06_runner",
        qa_dir=tmp_path / "07_qa",
        run_dir=tmp_path,
        command_trace={"command": "swmm_runner"},
    )
    assert manifest["schema_version"] == "1.0"
    assert manifest["pipeline"] == "external_inp_import"
    assert manifest["run_id"] == tmp_path.name
    assert manifest["inputs"]["source_inp"]["sha256"]
    assert manifest["outputs"]["runner_rpt"]["path"]
    assert manifest["tools"]["swmm5_version"] == "5.2.4"
    # case_id stamping is the CLI's job — the builder never sets it.
    assert "case_id" not in manifest


def test_builder_manifest_notes_differ_for_external_import(tmp_path: Path) -> None:
    inp = tmp_path / "model.inp"
    inp.write_text("[OPTIONS]\n", encoding="utf-8")
    external = build_builder_manifest(
        source_inp=inp,
        run_inp=inp,
        builder_inp=inp,
        sidecar_inputs=[],
        source_type="external_inp_import",
    )
    internal = build_builder_manifest(
        source_inp=inp,
        run_inp=inp,
        builder_inp=inp,
        sidecar_inputs=[],
        source_type="repository_inp",
    )
    assert len(external["validation"]["notes"]) == 2
    assert len(internal["validation"]["notes"]) == 1


def test_parse_runner_manifest_tolerates_garbage() -> None:
    assert parse_runner_manifest("not json") == {}
    assert parse_runner_manifest("[1, 2]") == {}
    assert parse_runner_manifest('{"ok": 1}') == {"ok": 1}
