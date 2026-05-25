"""Schema for data/feeds.json."""

from __future__ import annotations

from urllib.parse import urlparse


def _is_http_url(s: object) -> bool:
    if not isinstance(s, str):
        return False
    p = urlparse(s)
    return p.scheme in {"http", "https"} and bool(p.netloc)


def test_feeds_shape(feeds: list[dict]) -> None:
    assert isinstance(feeds, list) and feeds, "feeds.json must be a non-empty list"
    errors: list[str] = []
    seen_urls: set[str] = set()
    for i, f in enumerate(feeds):
        if not isinstance(f, dict):
            errors.append(f"feeds[{i}] not a dict")
            continue
        name = f.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"feeds[{i}] missing/empty name")
        if f.get("type") != "firehose":
            errors.append(f"feeds[{i}] ({name}) type must be 'firehose', got {f.get('type')!r}")
        url = f.get("url")
        if not _is_http_url(url):
            errors.append(f"feeds[{i}] ({name}) url not a valid http(s) URL: {url!r}")
            continue
        assert isinstance(url, str)
        if url in seen_urls:
            errors.append(f"feeds[{i}] ({name}) duplicate URL: {url}")
        seen_urls.add(url)
    assert not errors, "feeds.json violations:\n  - " + "\n  - ".join(errors)
