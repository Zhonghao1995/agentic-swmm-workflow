"""Tests for ``agentic_swmm.integrations.swmmcanada_runner``.

The runner is a thin HTTP client over SWMMCanada's async tasks API
(ADR-0001): submit an AOI + date range, poll to a terminal state,
download ``swmm_model.zip``, extract ``model.inp`` and keep the whole
zip as the durable provenance artifact (CONTEXT.md §"INP sources").

Tests never touch the network: the client takes an injectable
``opener`` (mirroring ``providers/_http.py``) plus ``sleep``/``now``
seams, so the three endpoints are faked in-process.
"""
from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
import zipfile
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory


AOI = '{"type":"Polygon","coordinates":[[[-123.4,48.4],[-123.3,48.4],[-123.3,48.5],[-123.4,48.5],[-123.4,48.4]]]}'
START = date(2022, 6, 1)
END = date(2022, 6, 7)


def _make_zip(*, inp: bytes = b"[TITLE]\nVictoria real network\n", validation: dict | None = None) -> bytes:
    """Build an in-memory ``swmm_model.zip`` like the upstream returns."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("model.inp", inp)
        zf.writestr("datastore/network.gpkg", b"fake-geopackage")
        if validation is not None:
            zf.writestr("validation.json", json.dumps(validation))
    return buf.getvalue()


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> None:
        return None


class _FakeOpener:
    """Dispatch on (method, path); record calls; replay scripted responses.

    ``status_script`` is a list of states returned by successive GETs on
    ``/tasks/{id}`` so a test can stage RUNNING -> SUCCEEDED.
    """

    def __init__(
        self,
        *,
        zip_bytes: bytes,
        status_script: list[dict],
        task_mode: str = "real",
        default_state: str = "SUCCEEDED",
    ) -> None:
        self._zip = zip_bytes
        self._status_script = list(status_script)
        self._task_mode = task_mode
        self._default_state = default_state
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request, timeout=None):  # noqa: ANN001 - urlopen shape
        method = request.get_method()
        url = request.full_url
        self.calls.append((method, url))
        if method == "POST" and url.endswith("/api/v1/tasks"):
            body = json.dumps({"task_id": "t1", "status": "QUEUED", "mode": self._task_mode}).encode()
            return _FakeResp(body, status=202)
        if method == "GET" and url.endswith("/api/v1/tasks/t1"):
            state = self._status_script.pop(0) if self._status_script else {"state": self._default_state, "progress_pct": 100, "stage": "DONE", "mode": self._task_mode, "error": None}
            return _FakeResp(json.dumps(state).encode())
        if method == "GET" and url.endswith("/api/v1/tasks/t1/result"):
            return _FakeResp(self._zip)
        raise AssertionError(f"unexpected request: {method} {url}")


class HappyPathTests(unittest.TestCase):
    def test_fetch_submits_polls_downloads_and_keeps_whole_zip(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import fetch_from_aoi

        opener = _FakeOpener(
            zip_bytes=_make_zip(validation={"accepted": True}),
            status_script=[{"state": "SUCCEEDED", "progress_pct": 100, "stage": "DONE", "mode": "real", "error": None}],
        )
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            result = fetch_from_aoi(
                AOI, START, END,
                run_dir=run_dir,
                base_url="http://svc.example",
                opener=opener,
                sleep=lambda *_: None,
            )

            # The whole zip is kept as the durable provenance artifact (D6).
            self.assertEqual(result.zip_path, run_dir / "swmm_model.zip")
            self.assertTrue(result.zip_path.is_file())
            # model.inp is extracted and runnable by the normal run path.
            self.assertTrue(result.inp_path.is_file())
            self.assertEqual(result.inp_path.read_bytes(), b"[TITLE]\nVictoria real network\n")
            # Foreign keys back to the upstream provenance.
            self.assertEqual(result.task_id, "t1")
            self.assertEqual(result.service_url, "http://svc.example")
            self.assertEqual(result.mode, "real")
            # validation.json is surfaced read-only (not re-derived).
            self.assertEqual(result.validation, {"accepted": True})


class PollingTests(unittest.TestCase):
    def test_polls_until_terminal_state(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import fetch_from_aoi

        opener = _FakeOpener(
            zip_bytes=_make_zip(),
            status_script=[
                {"state": "QUEUED", "progress_pct": 0, "stage": "QUEUED", "mode": "real", "error": None},
                {"state": "RUNNING", "progress_pct": 40, "stage": "BUILDING", "mode": "real", "error": None},
                {"state": "SUCCEEDED", "progress_pct": 100, "stage": "DONE", "mode": "real", "error": None},
            ],
        )
        sleeps: list[float] = []
        with TemporaryDirectory() as tmp:
            fetch_from_aoi(
                AOI, START, END,
                run_dir=Path(tmp) / "run",
                base_url="http://svc",
                poll_interval=1.5,
                opener=opener,
                sleep=sleeps.append,
            )
        status_gets = [c for c in opener.calls if c[0] == "GET" and c[1].endswith("/tasks/t1")]
        self.assertEqual(len(status_gets), 3)  # QUEUED -> RUNNING -> SUCCEEDED
        self.assertEqual(sleeps, [1.5, 1.5])  # slept between the two non-terminal polls


class FailureTests(unittest.TestCase):
    def test_task_failed_raises_stage_tagged_error_with_message(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, fetch_from_aoi

        opener = _FakeOpener(
            zip_bytes=_make_zip(),
            status_script=[{"state": "FAILED", "progress_pct": 60, "stage": "VALIDATING", "mode": "real", "error": {"message": "AOI outside any supported city"}}],
        )
        with TemporaryDirectory() as tmp:
            with self.assertRaises(CanadaFetchError) as ctx:
                fetch_from_aoi(AOI, START, END, run_dir=Path(tmp) / "run", base_url="http://svc", opener=opener, sleep=lambda *_: None)
        self.assertEqual(ctx.exception.stage, "task_failed")
        self.assertIn("AOI outside any supported city", str(ctx.exception))

    def test_timeout_raises_stage_tagged_error(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, fetch_from_aoi

        opener = _FakeOpener(zip_bytes=_make_zip(), status_script=[], default_state="RUNNING")
        clock = iter([0.0, 100.0, 700.0])  # deadline=600; second check trips it
        with TemporaryDirectory() as tmp:
            with self.assertRaises(CanadaFetchError) as ctx:
                fetch_from_aoi(
                    AOI, START, END,
                    run_dir=Path(tmp) / "run",
                    base_url="http://svc",
                    timeout=600.0,
                    opener=opener,
                    sleep=lambda *_: None,
                    now=lambda: next(clock),
                )
        self.assertEqual(ctx.exception.stage, "timeout")


class MalformedResponseTests(unittest.TestCase):
    """The handler promises fail-soft: every failure must surface as a
    stage-tagged CanadaFetchError, never a raw exception into the planner."""

    def _opener_with_submit_body(self, body: bytes):
        class _O:
            calls: list = []

            def __call__(self, request, timeout=None):
                if request.get_method() == "POST":
                    return _FakeResp(body, status=202)
                raise AssertionError("should fail before polling")

        return _O()

    def test_non_json_submit_response_raises_submit_stage(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, fetch_from_aoi

        opener = self._opener_with_submit_body(b"<html>502 Bad Gateway</html>")
        with TemporaryDirectory() as tmp:
            with self.assertRaises(CanadaFetchError) as ctx:
                fetch_from_aoi(AOI, START, END, run_dir=Path(tmp) / "r", base_url="http://svc", opener=opener, sleep=lambda *_: None)
        self.assertEqual(ctx.exception.stage, "submit")

    def test_submit_response_missing_task_id_raises_submit_stage(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, fetch_from_aoi

        opener = self._opener_with_submit_body(json.dumps({"status": "QUEUED", "mode": "real"}).encode())
        with TemporaryDirectory() as tmp:
            with self.assertRaises(CanadaFetchError) as ctx:
                fetch_from_aoi(AOI, START, END, run_dir=Path(tmp) / "r", base_url="http://svc", opener=opener, sleep=lambda *_: None)
        self.assertEqual(ctx.exception.stage, "submit")

    def test_non_json_status_response_raises_poll_stage(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, fetch_from_aoi

        class _O:
            def __call__(self, request, timeout=None):
                if request.get_method() == "POST":
                    return _FakeResp(json.dumps({"task_id": "t1", "status": "QUEUED", "mode": "real"}).encode(), status=202)
                return _FakeResp(b"<html>504</html>")  # status poll returns non-JSON

        with TemporaryDirectory() as tmp:
            with self.assertRaises(CanadaFetchError) as ctx:
                fetch_from_aoi(AOI, START, END, run_dir=Path(tmp) / "r", base_url="http://svc", opener=_O(), sleep=lambda *_: None)
        self.assertEqual(ctx.exception.stage, "poll")


class ConfigTests(unittest.TestCase):
    def test_missing_base_url_raises_config_missing(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import BASE_URL_ENV, CanadaFetchError, fetch_from_aoi

        opener = _FakeOpener(zip_bytes=_make_zip(), status_script=[])
        saved = os.environ.pop(BASE_URL_ENV, None)
        try:
            with TemporaryDirectory() as tmp:
                with self.assertRaises(CanadaFetchError) as ctx:
                    fetch_from_aoi(AOI, START, END, run_dir=Path(tmp) / "run", base_url=None, opener=opener)
            self.assertEqual(ctx.exception.stage, "config_missing")
        finally:
            if saved is not None:
                os.environ[BASE_URL_ENV] = saved


if __name__ == "__main__":
    unittest.main()
