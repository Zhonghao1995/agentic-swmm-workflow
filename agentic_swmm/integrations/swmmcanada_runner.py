"""Thin HTTP client for the SWMMCanada upstream INP source (ADR-0001).

SWMMCanada (``Zhonghao1995/SWMMCanada``) turns an AOI + date range into a
runnable SWMM model from Canadian open data. aiswmm consumes it over a
**service boundary** — an async tasks API — not as an in-process import:

    POST /api/v1/tasks            -> 202 {task_id, status, mode}
    GET  /api/v1/tasks/{id}       -> 200 {state, progress_pct, stage, mode, error}
    GET  /api/v1/tasks/{id}/result-> 200 swmm_model.zip | 409 not ready | 404

This module submits, polls to a terminal state, downloads the zip, and
extracts ``model.inp``. Per the canonical run-directory layout (ADR-0004,
``agentic_swmm.agent.swmm_runtime.run_layout``): the whole zip is kept as
the durable provenance artifact inside the ``10_upstream/swmmcanada/``
box (the upstream task store is in-memory, and the upstream's own
provenance — datastore + validation.json — rides inside the zip), while
the extracted ``model.inp`` is staged under ``05_builder/`` — the same
canonical stage every other INP source lands its runnable model in.
aiswmm records only two foreign keys: the service URL and task_id.

The HTTP seam is pure stdlib ``urllib`` with an injectable ``opener``
(mirroring ``agentic_swmm/providers/_http.py``), so callers and tests can
swap the transport without monkeypatching globals.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.integrations.inp_source import InpSourceError, InpSourceResult
from agentic_swmm.providers._http import (
    DEFAULT_BACKOFF_BASE_S,
    DEFAULT_MAX_ATTEMPTS,
    RETRY_STATUSES,
    _retry_delay,
)
from typing import Any, Callable

# Environment variable holding the SWMMCanada service base URL. The client is
# agnostic to whether this points at a local container (localhost:8000) or a
# hosted backend.
BASE_URL_ENV = "AISWMM_SWMMCANADA_URL"

# Terminal task states from the upstream tasks API.
_TERMINAL_OK = "SUCCEEDED"
_TERMINAL_FAIL = "FAILED"


def _report(progress: Callable[[str, Any], None] | None, stage: str, pct: Any) -> None:
    """Fire the progress callback, swallowing its errors (best-effort UI)."""
    if progress is None:
        return
    try:
        progress(stage, pct)
    except Exception:
        pass


def _open_with_retry(
    req: urllib.request.Request,
    *,
    timeout: float,
    opener: Callable[..., Any],
    sleep: Callable[[float], None],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_base: float = DEFAULT_BACKOFF_BASE_S,
) -> bytes:
    """Send ``req`` and return the raw body, retrying transient failures.

    Shares the transient set (429 + 5xx) and ``Retry-After``-aware backoff
    with ``providers/_http.py`` (issue #295). Non-transient HTTP errors and
    exhausted retries re-raise the original ``urllib`` error so each caller's
    stage-tagged ``CanadaFetchError`` wrapping stays unchanged.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            with opener(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUSES and attempt < max_attempts:
                sleep(_retry_delay(exc, attempt, backoff_base))
                continue
            raise
        except urllib.error.URLError:
            # Connection-level blip (DNS, refused, socket timeout) — transient.
            if attempt < max_attempts:
                sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            raise
    raise AssertionError("unreachable: loop always returns or raises")


@dataclass(frozen=True)
class CanadaFetchResult(InpSourceResult):
    """SWMMCanada adapter result at the INP-source seam.

    Inherits the shared surface (``inp_path``, ``run_dir``,
    ``warnings``) and adds the service path's typed extras. The zip is
    the durable provenance artifact (ADR-0001).
    """

    zip_path: Path
    service_url: str
    task_id: str
    mode: str
    validation: dict | None


class CanadaFetchError(InpSourceError):
    """Stage-tagged failure at the INP-source seam.

    ``stage`` is one of: ``config_missing``, ``submit``, ``poll``,
    ``task_failed``, ``timeout``, ``download``, ``extract``.
    """

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"SWMMCanada stage '{stage}' failed: {message}")
        self.stage = stage


def fetch_from_aoi(
    aoi_geojson: str,
    start: date,
    end: date,
    *,
    run_dir: Path,
    base_url: str | None = None,
    infiltration: str | None = None,
    progress: Callable[[str, Any], None] | None = None,
    poll_interval: float = 3.0,
    timeout: float = 600.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> CanadaFetchResult:
    """Fetch a SWMM model from SWMMCanada for ``aoi_geojson`` over ``start``..``end``.

    ``infiltration`` optionally selects the upstream build's infiltration
    method (CURVE_NUMBER / HORTON / GREEN_AMPT, case-insensitive). It is
    passed through verbatim — the service owns the enum and 422s unknown
    values, so this client never drifts from upstream's list.

    ``progress`` is an optional ``(stage, progress_pct)`` callback fired
    on every poll tick and at download start, so callers can surface the
    multi-minute upstream build as live status. Callback errors are
    swallowed: reporting progress must never break the fetch.

    Returns a :class:`CanadaFetchResult` with ``model.inp`` extracted under
    the canonical ``05_builder/`` stage of ``run_dir`` and the whole
    ``swmm_model.zip`` kept alongside it in the ``10_upstream/swmmcanada/``
    box (ADR-0004).
    """
    service_url = (base_url or os.environ.get(BASE_URL_ENV) or "").strip().rstrip("/")
    if not service_url:
        raise CanadaFetchError(
            "config_missing",
            f"no SWMMCanada base URL — pass base_url= or set ${BASE_URL_ENV}",
        )

    task_id, mode = _submit(
        service_url, aoi_geojson, start, end,
        infiltration=infiltration, opener=opener, sleep=sleep,
    )
    _poll_until_done(
        service_url, task_id,
        poll_interval=poll_interval, timeout=timeout,
        opener=opener, sleep=sleep, now=now, progress=progress,
    )

    _report(progress, "DOWNLOADING", None)
    run_dir.mkdir(parents=True, exist_ok=True)
    zip_path = _download_zip(service_url, task_id, run_dir, opener=opener, sleep=sleep)
    inp_path, validation = _extract(zip_path, run_dir)

    return CanadaFetchResult(
        inp_path=inp_path,
        run_dir=run_dir,
        zip_path=zip_path,
        service_url=service_url,
        task_id=task_id,
        mode=mode,
        validation=validation,
        warnings=(),
    )


def _submit(
    service_url: str,
    aoi_geojson: str,
    start: date,
    end: date,
    *,
    infiltration: str | None = None,
    opener: Callable[..., Any],
    sleep: Callable[[float], None],
) -> tuple[str, str]:
    fields = {"start_date": start.isoformat(), "end_date": end.isoformat(), "polygon": aoi_geojson}
    if infiltration:
        fields["infiltration"] = infiltration
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        f"{service_url}/api/v1/tasks",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        payload = json.loads(_open_with_retry(req, timeout=60, opener=opener, sleep=sleep).decode())
        task_id = str(payload["task_id"])
        mode = str(payload.get("mode") or "")
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise CanadaFetchError("submit", repr(exc)) from exc
    except (ValueError, KeyError, AttributeError) as exc:
        # Non-JSON body (e.g. a proxy's HTML 5xx page) or a response missing
        # task_id — keep the fail-soft contract instead of leaking a raw error.
        raise CanadaFetchError("submit", f"bad submit response: {exc!r}") from exc
    return task_id, mode


def _poll_until_done(
    service_url: str,
    task_id: str,
    *,
    poll_interval: float,
    timeout: float,
    opener: Callable[..., Any],
    sleep: Callable[[float], None],
    now: Callable[[], float],
    progress: Callable[[str, Any], None] | None = None,
) -> None:
    deadline = now() + timeout
    while True:
        req = urllib.request.Request(f"{service_url}/api/v1/tasks/{task_id}", method="GET")
        try:
            status = json.loads(_open_with_retry(req, timeout=60, opener=opener, sleep=sleep).decode())
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise CanadaFetchError("poll", repr(exc)) from exc
        except ValueError as exc:
            # Non-JSON status body (e.g. a transient proxy error page) — fail
            # soft with a stage tag rather than crashing the poll loop.
            raise CanadaFetchError("poll", f"bad status response: {exc!r}") from exc

        state = str(status.get("state") or "")
        _report(progress, str(status.get("stage") or state), status.get("progress_pct"))
        if state == _TERMINAL_OK:
            return
        if state == _TERMINAL_FAIL:
            err = status.get("error") or {}
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise CanadaFetchError("task_failed", message or "task FAILED")
        if now() >= deadline:
            raise CanadaFetchError(
                "timeout", f"task {task_id} not done after {timeout:.0f}s (last state {state!r})"
            )
        sleep(poll_interval)


def _download_zip(
    service_url: str,
    task_id: str,
    run_dir: Path,
    *,
    opener: Callable[..., Any],
    sleep: Callable[[float], None],
) -> Path:
    req = urllib.request.Request(f"{service_url}/api/v1/tasks/{task_id}/result", method="GET")
    try:
        data = _open_with_retry(req, timeout=300, opener=opener, sleep=sleep)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise CanadaFetchError("download", repr(exc)) from exc
    # The zip is provenance evidence, so it lives in the opaque upstream box
    # (ADR-0001/ADR-0004) rather than at the run-dir root.
    upstream_box = run_layout.upstream_dir(run_dir, run_layout.UPSTREAM_SWMMCANADA, create=True)
    zip_path = upstream_box / "swmm_model.zip"
    zip_path.write_bytes(data)
    return zip_path


def _extract(zip_path: Path, run_dir: Path) -> tuple[Path, dict | None]:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            inp_name = next((n for n in names if n.lower().endswith(".inp")), None)
            if inp_name is None:
                raise CanadaFetchError("extract", f"no .inp in {zip_path.name} ({names})")
            # The runnable model is a builder artifact (ADR-0004) — it sits in
            # 05_builder/ like every other INP source, separate from the
            # opaque upstream zip.
            builder_dir = run_layout.stage_dir(run_dir, run_layout.BUILDER, create=True)
            inp_path = builder_dir / "model.inp"
            # Stream instead of zf.read() + write_bytes — avoids holding a
            # second full copy of a large model in memory (issue #295, LOW).
            with zf.open(inp_name) as src, inp_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            validation: dict | None = None
            val_name = next((n for n in names if n.lower().endswith("validation.json")), None)
            if val_name is not None:
                try:
                    validation = json.loads(zf.read(val_name).decode())
                except (ValueError, UnicodeDecodeError):
                    validation = None
    except zipfile.BadZipFile as exc:
        raise CanadaFetchError("extract", repr(exc)) from exc
    return inp_path, validation


__all__ = ["BASE_URL_ENV", "CanadaFetchResult", "CanadaFetchError", "fetch_from_aoi"]
