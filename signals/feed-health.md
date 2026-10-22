# Feed health

_Last checked: 2026-10-21 (UTC)_  
4 dead · 2 stale · 81 sources tracked  (dead = ≥3 consecutive failures; stale = no new items in >45 days)

Dead **firehose/rss** feeds are auto-recovered each run via the Parallel reader fallback in `collect-candidates.py`; a dead feed below is one even the reader couldn't reach, or a kind (`github_org`/`lever_jobs`) the Markdown fallback doesn't cover. Prune or replace those in `data/feeds.json` / `data/companies.json`.

## Dead feeds

| Feed | Kind | Fails | Last status | Jina-recoverable | Last error | URL |
| --- | --- | --- | --- | --- | --- | --- |
| The Low Down | firehose | 63 | 403 | True | HTTP 403 | https://thelowdown.momentum.asia/feed/ |
| PharLyfe+ · news | rss | 36 | 404 | True | HTTP 404 | https://pharlyfeplus.com/news-update/f.rss |
| https://microtube.tech/feed/ |  | 24 | 404 | False | HTTP 404 | https://microtube.tech/feed/ |
| The Edge Singapore | firehose | 4 | 403 | True | HTTP 403 | https://www.theedgesingapore.com/rss.xml |

## Stale feeds

| Feed | Kind | Days since new item | Last change | URL |
| --- | --- | --- | --- | --- |
| Patsnap · careers | lever_jobs | 60 | 2026-08-22T13:24:01+00:00 | https://api.lever.co/v0/postings/patsnap?mode=json |
| RO+ · blog | rss | 53 | 2026-08-29T16:55:49+00:00 | https://www.roplus.sg/feed/ |
