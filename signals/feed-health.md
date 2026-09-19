# Feed health

_Last checked: 2026-09-19 (UTC)_  
3 dead · 0 stale · 23 sources tracked  (dead = ≥3 consecutive failures; stale = no new items in >45 days)

Dead **firehose/rss** feeds are auto-recovered each run via the r.jina.ai fallback in `collect-candidates.py`; a dead feed below is one even Jina couldn't reach, or a kind (`github_org`/`lever_jobs`) the Markdown fallback doesn't cover. Prune or replace those in `data/feeds.json` / `data/companies.json`.

## Dead feeds

| Feed | Kind | Fails | Last status | Jina-recoverable | Last error | URL |
| --- | --- | --- | --- | --- | --- | --- |
| The Low Down | firehose | 31 | 403 | True | HTTP 403 | https://thelowdown.momentum.asia/feed/ |
| https://microtube.tech/feed/ |  | 24 | 404 | False | HTTP 404 | https://microtube.tech/feed/ |
| PharLyfe+ · news | rss | 4 | 404 | True | HTTP 404 | https://pharlyfeplus.com/news-update/f.rss |

## Stale feeds

_None._
