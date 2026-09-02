"""swmmanywhere's warnings land in the run, not on the spinner line (F-51).

Live finding 2026-09-02 (scenario S13c, Seattle): two RuntimeWarnings from
design_graphfcns.py printed raw over the interactive shell's progress line
during synth_swmm_from_bbox.
"""

from __future__ import annotations

import warnings

from agentic_swmm.integrations.swmmanywhere_runner import (
    PIPELINE_WARNINGS_LOG,
    run_pipeline_capturing_warnings,
)


class _NoisyPipeline:
    def swmmanywhere(self, config):
        warnings.warn("invalid value encountered in scalar power", RuntimeWarning)
        warnings.warn("invalid value encountered in scalar power", RuntimeWarning)
        return "/tmp/synth/model.inp", None


class _QuietPipeline:
    def swmmanywhere(self, config):
        return "/tmp/synth/model.inp", None


def test_warnings_are_written_to_the_upstream_box_and_counted(tmp_path, recwarn):
    log = tmp_path / "10_upstream" / "swmmanywhere" / PIPELINE_WARNINGS_LOG
    inp, captured = run_pipeline_capturing_warnings(_NoisyPipeline(), {}, log)
    assert inp == "/tmp/synth/model.inp"
    assert captured == 2
    text = log.read_text()
    assert text.count("RuntimeWarning: invalid value encountered in scalar power") == 2
    assert len(recwarn) == 0, "nothing may escape to the caller's warning stream"


def test_a_quiet_pipeline_leaves_no_log(tmp_path):
    log = tmp_path / PIPELINE_WARNINGS_LOG
    inp, captured = run_pipeline_capturing_warnings(_QuietPipeline(), {}, log)
    assert (inp, captured) == ("/tmp/synth/model.inp", 0)
    assert not log.exists()
