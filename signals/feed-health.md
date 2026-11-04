# Feed health

_Last checked: 2026-11-04 (UTC)_  
9 dead · 0 empty · 3 stale · 187 sources tracked  (dead = ≥3 consecutive failures; empty = ATS board with zero postings on ≥3 consecutive reads; stale = no new items in >45 days)

Dead **firehose/rss** feeds are auto-recovered each run via the Parallel reader fallback in `collect-candidates.py`; a dead feed below is one even the reader couldn't reach, or a kind (`github_org`/`ats_jobs`) the Markdown fallback doesn't cover. Prune or replace those in `data/feeds.json` / `data/companies.json`.

## Dead feeds

| Feed | Kind | Fails | Last status | Jina-recoverable | Last error | URL |
| --- | --- | --- | --- | --- | --- | --- |
| PharLyfe+ · news | rss | 51 | 404 | True | HTTP 404 | https://pharlyfeplus.com/news-update/f.rss |
| https://microtube.tech/feed/ |  | 24 | 404 | False | HTTP 404 | https://microtube.tech/feed/ |
| The Edge Singapore | firehose | 19 | 403 | True | HTTP 403 | https://www.theedgesingapore.com/rss.xml |
| The Low Down | firehose | 11 | 403 | True | HTTP 403 | https://thelowdown.momentum.asia/feed/ |
| Flow Coffee · blog | rss | 7 | 404 | True | HTTP 404 | https://www.flowcoffee.sg/blog-feed.xml |
| BEEX · press | html_scrape | 6 | 404 | False | HTTP 404 | https://beex.sg/in-the-press/ |
| SilentConvo · blog | html_scrape | 6 | 404 | False | HTTP 404 | https://silentconvo.io/blog |
| Cellivate Technologies · press | html_scrape | 6 | 404 | False | HTTP 404 | https://www.cellivatetech.com/recognition |
| Synectify · news | html_scrape | 6 | -1 | False | <urlopen error [Errno -2] Name or service not known> | https://www.synectify.com/news.html |

## Empty ATS boards

_None._

## Stale feeds

| Feed | Kind | Days since new item | Last change | URL |
| --- | --- | --- | --- | --- |
| https://api.lever.co/v0/postings/patsnap?mode=json |  | 74 | 2026-08-22T13:24:01+00:00 | https://api.lever.co/v0/postings/patsnap?mode=json |
| Roplus · blog | rss | 67 | 2026-08-29T16:55:49+00:00 | https://www.roplus.sg/feed/ |
| Carousell · press | rss | 55 | 2026-09-10T00:13:35+00:00 | https://press.carousell.com/feed/ |
