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

from agentic_swmm.agent.tool_handlers._shared import _failure, _strip_html
from agentic_swmm.agent.types import ToolCall


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


def _web_search_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    max_results = min(int(call.args.get("max_results") or 5), 10)
    allowed = [str(domain).lower() for domain in call.args.get("allowed_domains") or []]
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "aiswmm-agent/0.1"})
        with _OPENER.open(request, timeout=20) as response:
            raw = response.read(1_000_000).decode("utf-8", errors="replace")
    except Exception as exc:
        return _failure(call, f"web search failed: {exc}")
    results = []
    for match in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        raw,
        flags=re.I | re.S,
    ):
        href = html.unescape(match.group(1))
        title = _strip_html(html.unescape(match.group(2))).strip()
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc == "duckduckgo.com":
            params = urllib.parse.parse_qs(parsed.query)
            href = params.get("uddg", [href])[0]
            parsed = urllib.parse.urlparse(href)
        if allowed and not any(
            parsed.netloc.lower().endswith(domain) for domain in allowed
        ):
            continue
        results.append({"title": title, "url": href})
        if len(results) >= max_results:
            break
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": results,
        "summary": f"{len(results)} web result(s); cite URLs and keep web evidence separate from run evidence",
    }


__all__ = ["_web_fetch_url_tool", "_web_search_tool"]
