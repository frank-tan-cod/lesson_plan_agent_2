"""External web-search tool definitions and registration."""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from backend.tools import Tool
from backend.tools.registry import ToolsRegistry
from .registry_utils import register_tools_once

DEFAULT_TIMEOUT_SECONDS = 12
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class SearchWebArgs(BaseModel):
    """Arguments for external web search."""

    query: str = Field(..., min_length=1, description="要搜索的关键词、问题或链接描述。")
    top_k: int = Field(default=5, ge=1, le=10, description="返回结果数量。")


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> str:
    """Perform a blocking HTTP request and return decoded text."""
    request_headers = {"User-Agent": DEFAULT_USER_AGENT, **(headers or {})}
    request = Request(url, data=body, headers=request_headers, method=method)
    with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:  # noqa: S310
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _strip_html(raw: str) -> str:
    """Collapse HTML markup into plain text."""
    cleaned = re.sub(r"<[^>]+>", " ", raw)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _normalize_duckduckgo_link(url: str) -> str:
    """Resolve DuckDuckGo redirect links to the target URL."""
    candidate = html.unescape(url).strip()
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    parsed = urlparse(candidate)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        redirect_target = parse_qs(parsed.query).get("uddg", [])
        if redirect_target:
            return unquote(redirect_target[0])
    return candidate


def _search_with_tavily(query: str, top_k: int) -> list[dict[str, Any]] | None:
    """Search with Tavily when an API key is configured."""
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return None

    payload = json.dumps(
        {
            "api_key": api_key,
            "query": query,
            "max_results": top_k,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
    ).encode("utf-8")
    raw = _http_request(
        "https://api.tavily.com/search",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=payload,
    )
    parsed = json.loads(raw)
    results = parsed.get("results")
    if not isinstance(results, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in results[:top_k]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        normalized.append(
            {
                "title": str(item.get("title") or url).strip(),
                "url": url,
                "snippet": str(item.get("content") or "").strip(),
                "source": "tavily",
            }
        )
    return normalized


def _search_with_duckduckgo_html(query: str, top_k: int) -> list[dict[str, Any]]:
    """Search via DuckDuckGo HTML results without requiring an API key."""
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    html_text = _http_request(url)

    results: list[dict[str, Any]] = []
    for match in re.finditer(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    ):
        href = _normalize_duckduckgo_link(match.group("href"))
        title = _strip_html(match.group("title"))
        if not href or not title:
            continue

        window = html_text[match.end() : match.end() + 1800]
        snippet_match = re.search(
            r'class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</',
            window,
            re.IGNORECASE | re.DOTALL,
        )
        snippet = _strip_html(snippet_match.group("snippet")) if snippet_match else ""

        if any(item["url"] == href for item in results):
            continue
        results.append(
            {
                "title": title,
                "url": href,
                "snippet": snippet,
                "source": "duckduckgo_html",
            }
        )
        if len(results) >= top_k:
            break
    return results


def _format_search_results(query: str, results: list[dict[str, Any]], provider: str) -> str:
    """Render web-search results into a compact text block."""
    lines = [f"已为“{query}”找到 {len(results)} 条网页结果（来源：{provider}）：", ""]
    for index, item in enumerate(results, start=1):
        title = str(item.get("title") or item.get("url") or "未命名结果").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "无摘要").strip()
        lines.append(f"{index}. {title}")
        if url:
            lines.append(f"   链接：{url}")
        if snippet:
            lines.append(f"   摘要：{snippet}")
        lines.append("")
    return "\n".join(lines).strip()


def _build_degraded_search_result(query: str, exc: Exception) -> dict[str, Any]:
    """Return a non-blocking fallback payload when live web search is unavailable."""
    reason = str(exc).strip() or exc.__class__.__name__
    return {
        "ok": True,
        "degraded": True,
        "query": query,
        "provider": "unavailable",
        "results": [],
        "message": (
            f"外部搜索当前不可用：{reason}。"
            "本轮将跳过联网检索，请改为根据现有教案上下文和模型已有知识继续设置；"
            "无法提供实时网页核验结果。"
        ),
    }


def _search_web_sync(query: str, top_k: int) -> dict[str, Any]:
    """Run a provider-ordered web search."""
    provider = "duckduckgo_html"
    results = _search_with_tavily(query, top_k)
    if results is not None:
        provider = "tavily"
    else:
        results = _search_with_duckduckgo_html(query, top_k)

    if not results:
        return {
            "ok": True,
            "query": query,
            "provider": provider,
            "results": [],
            "message": "未找到合适的网页结果。",
        }

    return {
        "ok": True,
        "query": query,
        "provider": provider,
        "results": results,
        "message": _format_search_results(query, results, provider),
    }


async def search_web_tool(**kwargs: Any) -> dict[str, Any]:
    """Search the web for live demos, official docs, or other external references."""
    query = kwargs["query"].strip()
    top_k = kwargs.get("top_k", 5)
    try:
        return await asyncio.to_thread(_search_web_sync, query, top_k)
    except Exception as exc:  # noqa: BLE001
        return _build_degraded_search_result(query, exc)


WEB_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="search_web",
        description=(
            "搜索外部网页结果，适合查找在线 Demo、官网链接、最新参考页面。"
            "当用户让你推荐一个可试用链接或需要联网补充素材时优先使用。"
        ),
        args_schema=SearchWebArgs,
        func=search_web_tool,
    ),
)


def register_web_tools(registry: ToolsRegistry) -> ToolsRegistry:
    """Register web-search tools into the target registry."""
    return register_tools_once(registry, WEB_TOOLS)
