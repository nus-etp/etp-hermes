# Feed health

_Last checked: 2026-07-22 (UTC)_  
1 dead · 3 stale · 18 sources tracked  (dead = ≥3 consecutive failures; stale = no new items in >45 days)

Dead **firehose/rss** feeds are auto-recovered each run via the r.jina.ai fallback in `collect-candidates.py`; a dead feed below is one even Jina couldn't reach, or a kind (`github_org`/`lever_jobs`) the Markdown fallback doesn't cover. Prune or replace those in `data/feeds.json` / `data/companies.json`.

## Dead feeds

| Feed | Kind | Fails | Last status | Jina-recoverable | Last error | URL |
| --- | --- | --- | --- | --- | --- | --- |
| Tech in Asia | firehose | 35 | 403 | True | HTTP 403 | https://www.techinasia.com/feed |

## Stale feeds

| Feed | Kind | Days since new item | Last change | URL |
| --- | --- | --- | --- | --- |
| Microtube Technologies · news | rss | 63 | 2026-05-20T06:53:22+00:00 | https://microtube.tech/feed/ |
| PharLyfe+ · news | rss | 63 | 2026-05-20T06:53:27+00:00 | https://pharlyfeplus.com/news-update/f.rss |
| RO+ · blog | rss | 63 | 2026-05-20T06:53:28+00:00 | https://www.roplus.sg/feed/ |
