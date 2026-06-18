# Feed health

_Last checked: 2026-06-18 (UTC)_  
0 dead · 0 stale · 20 sources tracked  (dead = ≥3 consecutive failures; stale = no new items in >45 days)

Dead **firehose/rss** feeds are auto-recovered each run via the r.jina.ai fallback in `collect-candidates.py`; a dead feed below is one even Jina couldn't reach, or a kind (`github_org`/`lever_jobs`) the Markdown fallback doesn't cover. Prune or replace those in `data/feeds.json` / `data/companies.json`.

## Dead feeds

_None._

## Stale feeds

_None._
