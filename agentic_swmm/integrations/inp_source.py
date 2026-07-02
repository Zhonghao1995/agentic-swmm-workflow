"""The INP-source seam: one interface, N adapters.

An "INP source" is any origin that turns user inputs into a runnable
``model.inp`` (CONTEXT.md lists five). The two programmatic
fetch/synthesize sources — swmmanywhere (in-process synth) and
SWMMCanada (HTTP service, ADR-0001) — satisfy this shared surface, so
tool handlers compose uniformly and a future source #6 has something to
conform to instead of hand-rolling another bespoke result/error pair.

- :class:`InpSourceResult` — the base result every adapter returns:
  where the INP landed (``inp_path``), which run dir owns it
  (``run_dir``), and human-facing ``warnings``. Adapters subclass to
  carry their typed extras (zip path, stage durations, ...) rather than
  flattening them into stringly-typed dicts.
- :class:`InpSourceError` — the base stage-tagged failure. Handlers
  catch this one type and map ``.stage`` to an actionable hint;
  adapters keep their own constructor shapes.

Transport stays adapter-specific on purpose: ADR-0001 chose an HTTP
boundary for SWMMCanada precisely so its heavy geo stack never enters
this environment, while swmmanywhere imports its package in-process.
The seam unifies the *return shape*, not the transport.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InpSourceResult:
    """Common surface of every INP-source adapter's result."""

    inp_path: Path
    run_dir: Path
    warnings: tuple[str, ...]


class InpSourceError(RuntimeError):
    """Base stage-tagged INP-source failure.

    Adapters raise subclasses with their own constructor shapes; the
    contract callers rely on is the ``.stage`` attribute (the pipeline
    stage name, used for hint mapping) plus the human-readable
    ``str(exc)`` message.
    """

    stage: str


__all__ = ["InpSourceError", "InpSourceResult"]
