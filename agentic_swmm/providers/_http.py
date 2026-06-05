"""Shared stdlib HTTP helper for the API-key providers.

Both providers (``openai``, ``anthropic``) POST to their respective APIs over
pure-stdlib ``urllib`` — no SDK, no new dependency. On a 15-40 step agent run
a single transient blip (a 429 rate-limit or a 5xx) would otherwise discard the
whole in-flight run by raising straight to the user. This helper retries those
transient failures with exponential backoff, honouring a ``Retry-After`` header
when the server sends one, and re-raises the provider's original error message
once attempts are exhausted (so existing error-handling stays unchanged).

Non-transient errors (4xx other than 429 — bad key, bad model, bad request)
raise immediately: retrying them is pointless and only delays the real message.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

# Transient HTTP statuses worth retrying: rate-limit + the standard 5xx set.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# Authentication / authorization failures: the key is missing, invalid,
# expired, or lacks access. Not transient (no retry) — but unlike a generic
# 4xx we know exactly how the user fixes it, so we append a remediation hint
# to the error the CLI surfaces verbatim as ``error: ...``.
AUTH_STATUSES = frozenset({401, 403})
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_S = 0.5

# provider_label (lower-cased) -> (env var name, the command that stores a key).
_AUTH_REMEDIATION = {
    "openai": ("OPENAI_API_KEY", "aiswmm login --openai"),
    "anthropic": ("ANTHROPIC_API_KEY", "aiswmm login --anthropic"),
}


def _auth_hint(provider_label: str) -> str:
    """Return a one-line ' — how to fix your key' suffix for a 401/403."""
    env_var, login_cmd = _AUTH_REMEDIATION.get(
        provider_label.lower(), ("the provider API key", "aiswmm login")
    )
    return (
        f" — authentication failed; your {env_var} is missing, invalid, or "
        f"expired. Run `{login_cmd}` to store a working key, then retry."
    )


def _retry_delay(exc: urllib.error.HTTPError, attempt: int, backoff_base: float) -> float:
    """Seconds to wait before the next attempt.

    Honour a numeric ``Retry-After`` (seconds) header when the server sends a
    parseable one; otherwise fall back to exponential backoff.
    """
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    return backoff_base * (2 ** (attempt - 1))


def post_json_with_retry(
    request: urllib.request.Request,
    *,
    timeout: float,
    provider_label: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_base: float = DEFAULT_BACKOFF_BASE_S,
    sleep: Callable[[float], None] = time.sleep,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Send ``request`` and return the parsed JSON body, retrying transients.

    Args:
        request: a prepared ``urllib.request.Request`` (POST).
        timeout: per-attempt socket timeout, in seconds.
        provider_label: human label for error messages, e.g. ``"OpenAI"``.
        max_attempts: total attempts including the first (>= 1).
        backoff_base: base seconds for exponential backoff fallback.
        sleep / opener: injection seams for tests.

    Raises:
        RuntimeError: on a non-transient HTTP error, or after exhausting
            retries — carrying the provider's original status + detail/reason.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with opener(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUSES and attempt < max_attempts:
                sleep(_retry_delay(exc, attempt, backoff_base))
                last_exc = exc
                continue
            detail = exc.read().decode("utf-8", errors="replace")
            message = (
                f"{provider_label} API request failed with HTTP {exc.code}: {detail}"
            )
            if exc.code in AUTH_STATUSES:
                message += _auth_hint(provider_label)
            raise RuntimeError(message) from exc
        except urllib.error.URLError as exc:
            # Connection-level blip (DNS, refused, socket timeout) — transient.
            if attempt < max_attempts:
                sleep(backoff_base * (2 ** (attempt - 1)))
                last_exc = exc
                continue
            raise RuntimeError(
                f"{provider_label} API request failed: {exc.reason}"
            ) from exc
    # Defensive: the loop always returns or raises, but keep mypy/readers happy.
    raise RuntimeError(
        f"{provider_label} API request failed after {max_attempts} attempts"
    ) from last_exc
