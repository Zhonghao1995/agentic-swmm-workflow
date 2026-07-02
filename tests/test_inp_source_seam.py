"""Contract lock for the INP-source seam (integrations/inp_source.py).

Two adapters exist today (swmm-anywhere in-process synth, SWMMCanada
HTTP service). This file pins the seam contract a future source #6
conforms to: results share the ``InpSourceResult`` surface, errors
share the ``InpSourceError`` catch surface with a ``.stage`` tag, and
the shared handler glue maps a stage-tagged failure onto the fail-soft
payload with a hint.
"""
from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_handlers._shared import _inp_source_tool
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.integrations.inp_source import InpSourceError, InpSourceResult
from agentic_swmm.integrations.swmmanywhere_runner import (
    SynthRunError,
    SynthRunResult,
)
from agentic_swmm.integrations.swmmcanada_runner import (
    CanadaFetchError,
    CanadaFetchResult,
)


def test_both_adapters_results_share_the_seam_surface() -> None:
    synth = SynthRunResult(
        inp_path=Path("/r/model.inp"),
        run_dir=Path("/r"),
        warnings=("w",),
        raw_manifest_path=Path("/r/raw.json"),
        provenance={},
        stage_durations={},
    )
    canada = CanadaFetchResult(
        inp_path=Path("/r/model.inp"),
        run_dir=Path("/r"),
        warnings=(),
        zip_path=Path("/r/swmm_model.zip"),
        service_url="http://localhost:8000",
        task_id="t-1",
        mode="real_pipe",
        validation=None,
    )
    for result in (synth, canada):
        assert isinstance(result, InpSourceResult)
        # The shared surface every consumer may rely on.
        assert result.inp_path.name == "model.inp"
        assert result.run_dir == Path("/r")
        assert isinstance(result.warnings, tuple)


def test_both_adapters_errors_share_the_catch_surface() -> None:
    errors = [
        SynthRunError("extra_missing", ModuleNotFoundError("no swmmanywhere")),
        CanadaFetchError("timeout", "task never reached a terminal state"),
    ]
    for exc in errors:
        assert isinstance(exc, InpSourceError)
        assert isinstance(exc.stage, str) and exc.stage
        assert str(exc)  # human-readable message contract


def test_inp_source_tool_maps_stage_failure_to_hint() -> None:
    call = ToolCall(name="fetch_swmm_from_canada", args={})

    def _boom() -> None:
        raise CanadaFetchError("submit", "connection refused")

    payload = _inp_source_tool(
        call,
        fetch=_boom,
        describe=lambda r: ({}, ""),
        stage_hint=lambda stage: f"hint-for-{stage}",
    )
    assert payload["ok"] is False
    assert payload["stage"] == "submit"
    assert payload["hint"] == "hint-for-submit"
    assert "submit" in payload["summary"]


def test_inp_source_tool_wraps_describe_in_the_envelope() -> None:
    call = ToolCall(name="synth_swmm_from_bbox", args={"bbox": [0, 0, 1, 1]})
    sentinel = object()
    payload = _inp_source_tool(
        call,
        fetch=lambda: sentinel,
        describe=lambda r: ({"inp_path": "/r/model.inp"}, "synth_inp=/r/model.inp"),
        stage_hint=lambda stage: "",
    )
    assert payload["ok"] is True
    assert payload["results"] == {"inp_path": "/r/model.inp"}
    assert payload["summary"] == "synth_inp=/r/model.inp"
