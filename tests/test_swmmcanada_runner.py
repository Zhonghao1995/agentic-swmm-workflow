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


class _FlakyOpener:
    """Wrap a delegate opener, failing the first ``fail_first`` calls."""

    def __init__(self, delegate, *, fail_first: int, exc_factory) -> None:
        self._delegate = delegate
        self._fail_remaining = fail_first
        self._exc_factory = exc_factory
        self.attempts = 0

    def __call__(self, request, timeout=None):  # noqa: ANN001 - urlopen shape
        self.attempts += 1
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise self._exc_factory()
        return self._delegate(request, timeout=timeout)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://svc", code, f"HTTP {code}", {}, io.BytesIO(b""))


class TransientRetryTests(unittest.TestCase):
    """Issue #295: transient blips (429/5xx/URLError) must not abort the fetch."""

    def test_transient_503_on_submit_is_retried_then_succeeds(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import fetch_from_aoi

        inner = _FakeOpener(zip_bytes=_make_zip(), status_script=[])
        opener = _FlakyOpener(inner, fail_first=1, exc_factory=lambda: _http_error(503))
        sleeps: list[float] = []
        with TemporaryDirectory() as tmp:
            result = fetch_from_aoi(
                AOI, START, END,
                run_dir=Path(tmp) / "run",
                base_url="http://svc",
                opener=opener,
                sleep=sleeps.append,
            )
            self.assertTrue(result.inp_path.is_file())
        self.assertGreaterEqual(opener.attempts, 2)  # failed once, then retried
        self.assertEqual(len(sleeps), 1)  # one backoff sleep for the one failure

    def test_url_error_on_poll_is_retried_then_succeeds(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import fetch_from_aoi

        inner = _FakeOpener(zip_bytes=_make_zip(), status_script=[])

        class _PollFlaky:
            """Fail only the first status GET with a connection-level blip."""

            def __init__(self) -> None:
                self.failed = False

            def __call__(self, request, timeout=None):  # noqa: ANN001
                if (
                    request.get_method() == "GET"
                    and request.full_url.endswith("/tasks/t1")
                    and not self.failed
                ):
                    self.failed = True
                    raise urllib.error.URLError("connection reset")
                return inner(request, timeout=timeout)

        with TemporaryDirectory() as tmp:
            result = fetch_from_aoi(
                AOI, START, END,
                run_dir=Path(tmp) / "run",
                base_url="http://svc",
                opener=_PollFlaky(),
                sleep=lambda *_: None,
            )
            self.assertTrue(result.inp_path.is_file())

    def test_non_transient_404_raises_immediately_without_retry(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, fetch_from_aoi

        inner = _FakeOpener(zip_bytes=_make_zip(), status_script=[])
        opener = _FlakyOpener(inner, fail_first=99, exc_factory=lambda: _http_error(404))
        with TemporaryDirectory() as tmp:
            with self.assertRaises(CanadaFetchError) as ctx:
                fetch_from_aoi(AOI, START, END, run_dir=Path(tmp) / "r", base_url="http://svc", opener=opener, sleep=lambda *_: None)
        self.assertEqual(ctx.exception.stage, "submit")
        self.assertEqual(opener.attempts, 1)  # no retry on a non-transient 4xx

    def test_exhausted_retries_raise_stage_tagged_error(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, fetch_from_aoi
        from agentic_swmm.providers._http import DEFAULT_MAX_ATTEMPTS

        inner = _FakeOpener(zip_bytes=_make_zip(), status_script=[])
        opener = _FlakyOpener(inner, fail_first=99, exc_factory=lambda: _http_error(503))
        with TemporaryDirectory() as tmp:
            with self.assertRaises(CanadaFetchError) as ctx:
                fetch_from_aoi(AOI, START, END, run_dir=Path(tmp) / "r", base_url="http://svc", opener=opener, sleep=lambda *_: None)
        self.assertEqual(ctx.exception.stage, "submit")
        self.assertEqual(opener.attempts, DEFAULT_MAX_ATTEMPTS)


class ProgressCallbackTests(unittest.TestCase):
    """The optional progress callback surfaces the multi-minute build as
    live status; it is strictly best-effort and can never break a fetch."""

    def test_progress_reports_each_poll_tick_and_download(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import fetch_from_aoi

        opener = _FakeOpener(
            zip_bytes=_make_zip(),
            status_script=[
                {"state": "QUEUED", "progress_pct": 0, "stage": "QUEUED", "mode": "real", "error": None},
                {"state": "RUNNING", "progress_pct": 40, "stage": "BUILDING", "mode": "real", "error": None},
                {"state": "SUCCEEDED", "progress_pct": 100, "stage": "DONE", "mode": "real", "error": None},
            ],
        )
        seen: list[tuple[str, object]] = []
        with TemporaryDirectory() as tmp:
            fetch_from_aoi(
                AOI, START, END,
                run_dir=Path(tmp) / "run",
                base_url="http://svc",
                opener=opener,
                sleep=lambda *_: None,
                progress=lambda stage, pct: seen.append((stage, pct)),
            )
        self.assertEqual(
            seen,
            [("QUEUED", 0), ("BUILDING", 40), ("DONE", 100), ("DOWNLOADING", None)],
        )

    def test_broken_progress_callback_never_breaks_the_fetch(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import fetch_from_aoi

        def _boom(stage, pct):  # noqa: ANN001
            raise RuntimeError("progress UI crashed")

        opener = _FakeOpener(zip_bytes=_make_zip(), status_script=[])
        with TemporaryDirectory() as tmp:
            result = fetch_from_aoi(
                AOI, START, END,
                run_dir=Path(tmp) / "run",
                base_url="http://svc",
                opener=opener,
                sleep=lambda *_: None,
                progress=_boom,
            )
            self.assertTrue(result.inp_path.is_file())


class InfiltrationPassthroughTests(unittest.TestCase):
    """The optional infiltration method is passed through verbatim; the
    service owns the enum (this client never validates it)."""

    def _submitted_body(self, **kwargs) -> str:
        opener = _FakeOpener(zip_bytes=_make_zip(), status_script=[])
        bodies: list[bytes] = []

        def _capture(request, timeout=None):  # noqa: ANN001
            if request.get_method() == "POST":
                bodies.append(request.data)
            return opener(request, timeout=timeout)

        from agentic_swmm.integrations.swmmcanada_runner import fetch_from_aoi

        with TemporaryDirectory() as tmp:
            fetch_from_aoi(
                AOI, START, END,
                run_dir=Path(tmp) / "run",
                base_url="http://svc",
                opener=_capture,
                sleep=lambda *_: None,
                **kwargs,
            )
        self.assertEqual(len(bodies), 1)
        return bodies[0].decode()

    def test_infiltration_included_when_set(self) -> None:
        body = self._submitted_body(infiltration="HORTON")
        self.assertIn("infiltration=HORTON", body)

    def test_infiltration_omitted_by_default(self) -> None:
        body = self._submitted_body()
        self.assertNotIn("infiltration", body)


if __name__ == "__main__":
    unittest.main()
