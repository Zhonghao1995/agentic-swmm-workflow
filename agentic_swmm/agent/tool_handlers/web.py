"""Web fetch / web search handlers (PRD #128).

Two small, well-isolated handlers that share only HTTP / HTML
stdlib usage. ``_failure`` and ``_strip_html`` are imported from
``tool_registry`` during the migration; they will move to
``tool_handlers/_shared.py`` once the package is fully populated.
"""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from agentic_swmm.agent.types import ToolCall


def _failure(call: ToolCall, summary: str) -> dict[str, Any]:
    """Standard failure payload shape; mirrored from ``tool_registry``."""
    return {"tool": call.name, "args": call.args, "ok": False, "summary": summary}


def _strip_html(text: str) -> str:
    """Strip HTML tags / scripts / styles; mirrored from ``tool_registry``."""
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _web_fetch_url_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    url = str(call.args.get("url") or "")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _failure(call, "url must be http(s)")
    max_chars = int(call.args.get("max_chars") or 6000)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "aiswmm-agent/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
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
        with urllib.request.urlopen(request, timeout=20) as response:
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
