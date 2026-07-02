"""PRD-08 A.3 (audit #4): INP sidecar parser must distinguish section headers."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from agentic_swmm.agent.swmm_runtime.inp_parsing import (
    copy_inp_sidecar_files as _copy_inp_sidecar_files,
)


def test_section_header_token_raises_parser_error():
    """A section header captured as a filename triggers a parser error.

    Reproduces audit #4: an INP whose section ordering confuses the
    FILE regex must surface "INP parser error" rather than
    "FILE not found: /…/[OPTIONS]".
    """
    with TemporaryDirectory() as tmp:
        inp = Path(tmp) / "broken.inp"
        inp.write_text(
            "[FILES]\nSAVE FILE [OPTIONS]\n",
            encoding="utf-8",
        )
        inputs = Path(tmp) / "out"
        inputs.mkdir()
        with pytest.raises(FileNotFoundError) as excinfo:
            _copy_inp_sidecar_files(inp, inputs)
        message = str(excinfo.value)
        assert "INP parser error" in message
        assert "[OPTIONS]" in message
        # The old message format ("references an external FILE") must
        # NOT appear for this case.
        assert "references an external FILE" not in message


def test_real_missing_external_file_keeps_legacy_message():
    """A genuine missing-FILE reference keeps the existing error path."""
    with TemporaryDirectory() as tmp:
        inp = Path(tmp) / "good.inp"
        inp.write_text(
            "[FILES]\nUSE FILE missing.dat\n",
            encoding="utf-8",
        )
        inputs = Path(tmp) / "out"
        inputs.mkdir()
        with pytest.raises(FileNotFoundError) as excinfo:
            _copy_inp_sidecar_files(inp, inputs)
        message = str(excinfo.value)
        assert "references an external FILE" in message
        assert "missing.dat" in message
