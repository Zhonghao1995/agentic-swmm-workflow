"""Web fetch / web search handlers (PRD #128).

Two small, well-isolated handlers that share only HTTP / HTML
stdlib usage. ``_failure`` and ``_strip_html`` come from
``tool_handlers/_shared`` — the cross-cutting helpers that every
family imports.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure, _object, _strip_html
from agentic_swmm.agent.types import ToolCall, ToolSpec


def _assert_public_host(url: str) -> None:
    """Reject URLs that resolve to a non-public address (review P1-3).

    Blocks SSRF into loopback, RFC1918, link-local (incl. the
    169.254.169.254 cloud-metadata address), unique-local and other
    reserved ranges, and refuses embedded URL credentials. Raises
    ``ValueError`` when the URL is not safe to fetch.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve host {host!r}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError(f"host {host!r} resolves to non-public address {ip}")


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect hop so a public URL cannot bounce inward."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        _assert_public_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_PublicOnlyRedirectHandler)


def _web_fetch_url_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    url = str(call.args.get("url") or "")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _failure(call, "url must be http(s)")
    try:
        _assert_public_host(url)
    except ValueError as exc:
        return _failure(call, f"refused: {exc}")
    max_chars = int(call.args.get("max_chars") or 6000)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "aiswmm-agent/0.1"})
        with _OPENER.open(request, timeout=20) as response:
            raw = response.read(1_000_000).decode("utf-8", errors="replace")
    except Exception as exc:
        return _failure(call, f"web fetch failed: {exc}")
    text = _strip_html(raw)
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "path": url,
        "chars": len(text),
        "excerpt": text[:max_chars],
        "summary": f"fetched {url}",
    }


#: Live finding F-58 (2026-09-02): asked for Victoria IDF curves, the tool
#: reported "0 web result(s)" twice. DuckDuckGo's html endpoints now answer
#: an automated client with a challenge page; the lite endpoint still
#: answers. Zero results from a challenge page is "unknown", never
#: "absent", so the tool says so and fails instead of returning an empty
#: success.
_SEARCH_ENDPOINTS = (
    "https://duckduckgo.com/html/?",
    "https://lite.duckduckgo.com/lite/?",
)
_CHALLENGE_MARKERS = ("challenge", "captcha", "anomaly", "unusual traffic", "bot detection")
_RESULT_PATTERNS = (
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    r'<a[^>]+rel="nofollow"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
)


def _fetch_search_page(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (aiswmm-agent/0.1)"})
    with _OPENER.open(request, timeout=20) as response:
        return response.read(1_000_000).decode("utf-8", errors="replace")


def _parse_search_results(raw: str, allowed: list[str], max_results: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for pattern in _RESULT_PATTERNS:
        for match in re.finditer(pattern, raw, flags=re.I | re.S):
            href = html.unescape(match.group(1))
            title = _strip_html(html.unescape(match.group(2))).strip()
            parsed = urllib.parse.urlparse(href)
            if parsed.netloc.endswith("duckduckgo.com"):
                params = urllib.parse.parse_qs(parsed.query)
                href = params.get("uddg", [href])[0]
                parsed = urllib.parse.urlparse(href)
                if parsed.netloc.endswith("duckduckgo.com"):
                    continue
            if not parsed.netloc or href in seen:
                continue
            if allowed and not any(parsed.netloc.lower().endswith(domain) for domain in allowed):
                continue
            seen.add(href)
            results.append({"title": title, "url": href})
            if len(results) >= max_results:
                return results
        if results:
            break
    return results


def _looks_like_a_challenge(raw: str) -> bool:
    lowered = raw.lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


def _web_search_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    max_results = min(int(call.args.get("max_results") or 5), 10)
    allowed = [str(domain).lower() for domain in call.args.get("allowed_domains") or []]
    blocked = 0
    errors: list[str] = []
    for base in _SEARCH_ENDPOINTS:
        url = base + urllib.parse.urlencode({"q": query})
        try:
            raw = _fetch_search_page(url)
        except Exception as exc:
            errors.append(f"{urllib.parse.urlparse(base).netloc}: {exc}")
            continue
        results = _parse_search_results(raw, allowed, max_results)
        if results:
            return {
                "tool": call.name,
                "args": call.args,
                "ok": True,
                "results": results,
                "backend": urllib.parse.urlparse(base).netloc,
                "summary": f"{len(results)} web result(s); cite URLs and keep web evidence separate from run evidence",
            }
        if _looks_like_a_challenge(raw):
            blocked += 1
            continue
        # A real, parseable page with nothing in it: a genuine empty result.
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "results": [],
            "backend": urllib.parse.urlparse(base).netloc,
            "summary": "0 web result(s) from a parseable results page; cite URLs and keep web evidence separate from run evidence",
        }
    if blocked:
        return _failure(
            call,
            "web search blocked: the search backend answered with a challenge page, so results are unknown, not absent",
            hint="Use web_fetch_url on a source you can name, or ask the user for the document; do not conclude that nothing exists.",
        )
    return _failure(call, "web search failed: " + "; ".join(errors))


__all__ = ["_web_fetch_url_tool", "_web_search_tool", "tool_specs"]


def tool_specs() -> list[ToolSpec]:
    """This family's planner tools (issue #358 self-registration)."""
    return [
        # Not is_read_only: fetching a model-chosen URL is network egress (a
        # sensitive effect and an exfiltration channel), so it goes through
        # the approval gate rather than auto-approving (review P1-3).
        ToolSpec(
            "web_fetch_url",
            "Fetch and summarize a web page. Web evidence is not SWMM run evidence.",
            _object({"url": {"type": "string"}, "max_chars": {"type": "integer"}}),
            _web_fetch_url_tool,
        ),
        ToolSpec(
            "web_search",
            "Run a lightweight web search and return cited result URLs. Web evidence is not SWMM run evidence.",
            _object({"query": {"type": "string"}, "allowed_domains": {"type": "array", "items": {"type": "string"}}, "max_results": {"type": "integer"}}),
            _web_search_tool,
            is_read_only=True,
        ),
    ]
