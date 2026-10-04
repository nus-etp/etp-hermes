#!/usr/bin/env python3
"""Shared reader helpers: fetch a URL as Markdown and extract listing items.

Used by two callers:
- scripts/jina-reader.py — prefetches html_scrape sources before Layer 1.
- scripts/collect-candidates.py — falls back to the reader when a feed's direct
  fetch fails (the host is unreachable from the runner but the reader can reach it).

The reader is the keyless Parallel Search MCP (`web_fetch` with
`full_content`), the same free endpoint hermes uses as its web surface
(hermes/config.yaml → mcp_servers.parallel). It replaced r.jina.ai, whose keyed
tier went 402 once the token balance ran out. Importable as a sibling module
(`import jina_fallback`) because scripts are run from the repo with scripts/ on
sys.path; both callers also insert their own directory on sys.path so the
import resolves under pytest's file-path module loader.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib import error, parse, request

from user_agents import honest_ua

PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
MCP_PROTOCOL_VERSION = "2025-03-26"
USER_AGENT = honest_ua("reader")
TIMEOUT_SECS = 60

# Heuristic constants for extract_items().
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
LINK_RE = re.compile(r"\[([^\]]+?)\]\(\s*<?([^)\s>]+)>?\s*\)")
INLINE_HEADING_LINK_RE = re.compile(r"^\[([^\]]+?)\]\(\s*<?([^)\s>]+)>?\s*\)\s*$")
DATE_PATTERNS = [
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
        re.IGNORECASE,
    ),
]
LINK_SCAN_LINES = 5
DATE_SCAN_LINES = 4
IGNORE_LINK_HOSTS = {
    "twitter.com",
    "x.com",
    "facebook.com",
    "linkedin.com",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "t.me",
    "wa.me",
    "pinterest.com",
    # Asset CDNs — Webflow card layouts emit the thumbnail as the first link.
    "cdn.prod.website-files.com",
    "assets.website-files.com",
    "assets-global.website-files.com",
}
IGNORE_LINK_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".avif",
    ".pdf",
    ".webm",
    ".mp4",
    ".ico",
)


def _mcp_post(payload: dict[str, Any], session_id: str | None) -> tuple[str | None, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = request.Request(
        PARALLEL_MCP_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.headers.get("Mcp-Session-Id") if resp.headers else None, body


def _jsonrpc_message(body: str) -> dict[str, Any]:
    text = body.strip()
    if text.startswith("{"):
        return json.loads(text)
    data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    for line in reversed(data_lines):
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError("no JSON-RPC message in response")


def _fetch_result(message: dict[str, Any]) -> dict[str, Any]:
    if "error" in message:
        raise ValueError(f"MCP error: {message['error']}")
    result = message.get("result") or {}
    if result.get("isError"):
        raise ValueError("web_fetch tool error")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for block in result.get("content") or []:
        if block.get("type") == "text":
            return json.loads(block.get("text") or "{}")
    raise ValueError("web_fetch returned no content")


def _page_text(fetch_result: dict[str, Any], url: str) -> str:
    for page in fetch_result.get("results") or []:
        text = page.get("full_content") or "\n\n".join(page.get("excerpts") or [])
        if text.strip():
            return text
    reasons = [
        f"{e.get('error_type')} (HTTP {e.get('http_status_code')})"
        for e in fetch_result.get("errors") or []
    ]
    raise ValueError(f"no content for {url}: {', '.join(reasons) or 'empty result'}")


def fetch_reader(url: str) -> tuple[int, str]:
    """Return (status, page_text) via Parallel's keyless MCP web_fetch.

    Any protocol or content failure surfaces as URLError so both callers'
    existing fail-open handling applies unchanged.
    """
    try:
        session_id, _ = _mcp_post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "etp-hermes-reader", "version": "1"},
                },
            },
            None,
        )
        _mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)
        _, body = _mcp_post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "web_fetch",
                    "arguments": {"urls": [url], "full_content": True},
                },
            },
            session_id,
        )
        return 200, _page_text(_fetch_result(_jsonrpc_message(body)), url)
    except error.URLError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise error.URLError(f"reader: {e}") from e


FENCED_BLOCK_RE = re.compile(r"^\s*```[\w-]*\n(.*?)\n```\s*$", re.DOTALL)
BARE_AMPERSAND_RE = re.compile(r"&(?!#?\w+;)")


def as_feed_xml(text: str) -> bytes | None:
    """The page text as parseable RSS/Atom bytes when the reader returned a raw feed."""
    fenced = FENCED_BLOCK_RE.match(text)
    body = (fenced.group(1) if fenced else text).strip()
    if not body.startswith("<") or ("<rss" not in body[:1000] and "<feed" not in body[:1000]):
        return None
    return BARE_AMPERSAND_RE.sub("&amp;", body.replace("&;", "&amp;")).encode("utf-8")


def _resolve(href: str, base_url: str) -> str | None:
    href = href.strip()
    if not href:
        return None
    if href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    return parse.urljoin(base_url, href)


def _is_useful_news_link(link: str, base_url: str) -> bool:
    if not link:
        return False
    lp = parse.urlparse(link)
    if lp.scheme not in ("http", "https"):
        return False
    if lp.netloc.lower().lstrip("www.") in IGNORE_LINK_HOSTS:
        return False
    if lp.path.lower().endswith(IGNORE_LINK_EXTENSIONS):
        return False
    bp = parse.urlparse(base_url)
    if (lp.netloc, lp.path.rstrip("/")) == (bp.netloc, bp.path.rstrip("/")):
        return False
    return True


def _find_date_near(lines: list[str], i: int) -> str | None:
    lo = max(0, i - 1)
    hi = min(len(lines), i + 1 + DATE_SCAN_LINES)
    for j in range(lo, hi):
        for pat in DATE_PATTERNS:
            m = pat.search(lines[j])
            if m:
                return m.group(0)
    return None


def extract_items(markdown: str, base_url: str) -> list[dict[str, Any]]:
    """Heading-plus-link heuristic over Jina Reader Markdown → listing items.

    Returns [{headline, link, source_kind: "html_scrape", pre_extracted: True,
    pubDate?}]. Works on the `### [Title](url)` + date layout Jina emits for both
    listing pages and RSS/Atom feeds.
    """
    lines = markdown.splitlines()
    items: list[dict[str, Any]] = []
    seen_links: set[str] = set()

    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if not m:
            continue
        heading_text = m.group(2).strip()

        title: str | None = None
        link: str | None = None

        # Case 1: heading is itself a single link — "## [Title](url)"
        inline = INLINE_HEADING_LINK_RE.match(heading_text)
        if inline:
            cand_title = inline.group(1).strip()
            cand_link = _resolve(inline.group(2), base_url)
            if cand_title and cand_link and _is_useful_news_link(cand_link, base_url):
                title = cand_title
                link = cand_link

        # Case 2: pick the first plain-text-inside-link the heading contains.
        if link is None:
            inline_any = LINK_RE.search(heading_text)
            if inline_any:
                cand_title = inline_any.group(1).strip()
                cand_link = _resolve(inline_any.group(2), base_url)
                if cand_title and cand_link and _is_useful_news_link(cand_link, base_url):
                    title = cand_title
                    link = cand_link

        # Case 3: heading has no link; look forward a few lines for one.
        # Collect the useful links in the scan window and prefer one on the same
        # host as base_url — on Webflow card layouts the first link is often an
        # off-host thumbnail and the real article link comes right after it.
        if link is None and heading_text and not LINK_RE.search(heading_text):
            base_host = parse.urlparse(base_url).netloc
            first_link: str | None = None
            same_host_link: str | None = None
            for j in range(i + 1, min(i + 1 + LINK_SCAN_LINES, len(lines))):
                next_line = lines[j].strip()
                if not next_line:
                    continue
                # Stop scanning once another heading is hit.
                if HEADING_RE.match(next_line):
                    break
                lm = LINK_RE.search(next_line)
                if not lm:
                    continue
                cand_link = _resolve(lm.group(2), base_url)
                if not (cand_link and _is_useful_news_link(cand_link, base_url)):
                    continue
                if first_link is None:
                    first_link = cand_link
                if parse.urlparse(cand_link).netloc == base_host:
                    same_host_link = cand_link
                    break
            chosen = same_host_link or first_link
            if chosen:
                title = heading_text
                link = chosen

        if not title or not link:
            continue
        if link in seen_links:
            continue
        seen_links.add(link)

        item: dict[str, Any] = {
            "headline": title,
            "link": link,
            "source_kind": "html_scrape",
            "pre_extracted": True,
        }
        date = _find_date_near(lines, i)
        if date:
            item["pubDate"] = date
        items.append(item)

    return items
