# Feed health

_Last checked: 2026-08-27 (UTC)_  
2 dead · 1 stale · 23 sources tracked  (dead = ≥3 consecutive failures; stale = no new items in >45 days)

Dead **firehose/rss** feeds are auto-recovered each run via the r.jina.ai fallback in `collect-candidates.py`; a dead feed below is one even Jina couldn't reach, or a kind (`github_org`/`lever_jobs`) the Markdown fallback doesn't cover. Prune or replace those in `data/feeds.json` / `data/companies.json`.

## Dead feeds

| Feed | Kind | Fails | Last status | Jina-recoverable | Last error | URL |
| --- | --- | --- | --- | --- | --- | --- |
| https://microtube.tech/feed/ |  | 24 | 404 | False | HTTP 404 | https://microtube.tech/feed/ |
| The Low Down | firehose | 6 | 403 | True | HTTP 403 | https://thelowdown.momentum.asia/feed/ |

## Stale feeds

| Feed | Kind | Days since new item | Last change | URL |
| --- | --- | --- | --- | --- |
| PharLyfe+ · news | rss | 99 | 2026-05-20T06:53:27+00:00 | https://pharlyfeplus.com/news-update/f.rss |
