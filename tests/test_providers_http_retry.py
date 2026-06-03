"""Phase H — shared provider HTTP helper retries transient 429/5xx.

A single rate-limit or 5xx on a long agent run must not discard the whole
in-flight run. ``post_json_with_retry`` retries transients with backoff,
honours ``Retry-After``, and re-raises the provider's original message once
attempts run out. Non-transient 4xx raise immediately.

Pure stdlib, no network: the ``opener`` and ``sleep`` seams are injected.
"""

from __future__ import annotations

import email.message
import io
import unittest
import urllib.error

from agentic_swmm.providers._http import post_json_with_retry


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _http_error(code: int, *, detail: bytes = b"boom", retry_after: str | None = None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        url="https://api.example/v1",
        code=code,
        msg="err",
        hdrs=headers,
        fp=io.BytesIO(detail),
    )


class _ScriptedOpener:
    """Yields the next scripted outcome on each call (exception or response)."""

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class PostJsonWithRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.slept: list[float] = []

    def _sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def test_returns_json_on_first_success(self) -> None:
        opener = _ScriptedOpener([_FakeResp(b'{"ok": true}')])
        result = post_json_with_retry(
            object(), timeout=1, provider_label="OpenAI", opener=opener, sleep=self._sleep
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(opener.calls, 1)
        self.assertEqual(self.slept, [])

    def test_retries_on_429_then_succeeds(self) -> None:
        opener = _ScriptedOpener([_http_error(429), _FakeResp(b'{"ok": 1}')])
        result = post_json_with_retry(
            object(), timeout=1, provider_label="OpenAI",
            opener=opener, sleep=self._sleep, backoff_base=0.5,
        )
        self.assertEqual(result, {"ok": 1})
        self.assertEqual(opener.calls, 2)
        self.assertEqual(len(self.slept), 1)

    def test_honours_retry_after_header(self) -> None:
        opener = _ScriptedOpener([_http_error(429, retry_after="2"), _FakeResp(b"{}")])
        post_json_with_retry(
            object(), timeout=1, provider_label="Anthropic",
            opener=opener, sleep=self._sleep,
        )
        self.assertEqual(self.slept, [2.0])

    def test_retries_5xx(self) -> None:
        opener = _ScriptedOpener([_http_error(503), _FakeResp(b"{}")])
        post_json_with_retry(
            object(), timeout=1, provider_label="OpenAI", opener=opener, sleep=self._sleep
        )
        self.assertEqual(opener.calls, 2)

    def test_non_retryable_4xx_raises_immediately(self) -> None:
        opener = _ScriptedOpener([_http_error(400, detail=b"bad key")])
        with self.assertRaises(RuntimeError) as cm:
            post_json_with_retry(
                object(), timeout=1, provider_label="OpenAI",
                opener=opener, sleep=self._sleep,
            )
        self.assertIn("HTTP 400", str(cm.exception))
        self.assertIn("bad key", str(cm.exception))
        self.assertEqual(opener.calls, 1)  # no retry
        self.assertEqual(self.slept, [])

    def test_exhausts_retries_then_raises_original_message(self) -> None:
        opener = _ScriptedOpener([_http_error(500, detail=b"server down")] * 3)
        with self.assertRaises(RuntimeError) as cm:
            post_json_with_retry(
                object(), timeout=1, provider_label="OpenAI",
                opener=opener, sleep=self._sleep, max_attempts=3,
            )
        self.assertIn("HTTP 500", str(cm.exception))
        self.assertIn("server down", str(cm.exception))
        self.assertEqual(opener.calls, 3)
        self.assertEqual(len(self.slept), 2)  # slept between the 3 attempts

    def test_url_error_retried_then_raises_reason(self) -> None:
        err = urllib.error.URLError("connection refused")
        opener = _ScriptedOpener([err, err])
        with self.assertRaises(RuntimeError) as cm:
            post_json_with_retry(
                object(), timeout=1, provider_label="Anthropic",
                opener=opener, sleep=self._sleep, max_attempts=2,
            )
        self.assertIn("connection refused", str(cm.exception))
        self.assertEqual(opener.calls, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
