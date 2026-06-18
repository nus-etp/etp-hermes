#!/usr/bin/env python3
"""Shared Jina Reader helpers: fetch a URL as Markdown and extract listing items.

Used by two callers:
- scripts/jina-reader.py — prefetches html_scrape sources before Layer 1.
- scripts/collect-candidates.py — falls back to r.jina.ai when a feed's direct
  fetch fails (the host is unreachable from the runner but Jina can reach it).

Keyless by default: r.jina.ai serves anonymous requests at lower rate limits.
Pass an api_key (JINA_API_KEY) to lift them. Importable as a sibling module
(`import jina_fallback`) because scripts are run from the repo with scripts/ on
sys.path; both callers also insert their own directory on sys.path so the
import resolves under pytest's file-path module loader.
"""

from __future__ import annotations

import re
from typing import Any
from urllib import parse, request

READER_BASE = "https://r.jina.ai/"
USER_AGENT = "etp-hermes-jina/1 (+https://github.com/luarss/etp-hermes)"
TIMEOUT_SECS = 30

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
}
IGNORE_LINK_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf")


def fetch_reader(url: str, api_key: str | None) -> tuple[int, str]:
    """Return (status, markdown_body). Raises for non-HTTP errors caller handles."""
    target = READER_BASE + url
    headers = {"User-Agent": USER_AGENT, "Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(target, headers=headers, method="GET")
    with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


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
        if link is None and heading_text and not LINK_RE.search(heading_text):
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
                if cand_link and _is_useful_news_link(cand_link, base_url):
                    title = heading_text
                    link = cand_link
                    break

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
