# Feed health

_Last checked: 2026-08-16 (UTC)_  
2 dead · 1 stale · 18 sources tracked  (dead = ≥3 consecutive failures; stale = no new items in >45 days)

Dead **firehose/rss** feeds are auto-recovered each run via the r.jina.ai fallback in `collect-candidates.py`; a dead feed below is one even Jina couldn't reach, or a kind (`github_org`/`lever_jobs`) the Markdown fallback doesn't cover. Prune or replace those in `data/feeds.json` / `data/companies.json`.

## Dead feeds

| Feed | Kind | Fails | Last status | Jina-recoverable | Last error | URL |
| --- | --- | --- | --- | --- | --- | --- |
| Tech in Asia | firehose | 59 | 403 | True | HTTP 403 | https://www.techinasia.com/feed |
| Microtube Technologies · news | rss | 19 | 404 | True | HTTP 404 | https://microtube.tech/feed/ |

## Stale feeds

| Feed | Kind | Days since new item | Last change | URL |
| --- | --- | --- | --- | --- |
| PharLyfe+ · news | rss | 88 | 2026-05-20T06:53:27+00:00 | https://pharlyfeplus.com/news-update/f.rss |
