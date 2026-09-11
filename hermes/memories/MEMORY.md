Hermes venv at ~/.hermes/hermes-agent/venv/ ships without pip. To install packages into it, use `uv pip install <pkg> --python ~/.hermes/hermes-agent/venv/bin/python` — NOT `uvx --python ... pip install` (uvx installs into a temp ephemeral env, not the target venv). Root cause for "No adapter available for telegram/discord/slack" errors in gateway logs.
§
Per-company html_scrape pages (Webflow/Next.js) are low-value: JS rendering yields nav-only text and content is 2+ years stale, so a 14-day filter catches nothing. Jina `pre_extracted: true` does not guarantee a useful item — filter short headlines, CDN image URLs, nav text.
§
Best real-signal feeds: Carousell press RSS, Horizon newsroom (table-based), GitHub org feeds, Lever job postings.
§
Layer 3 synthesis merges agent/updates signals into per-company living briefs. Uses `data/touched-companies.json` slice, parses H2=company/H3=company from agent file, generates signal cards with URL fetch enrichment (20 budget, 15s timeout, skip-list hosts). Detects duplicate announcements via word-overlap matching. Silent-corrects Sector: bullet to match c.sector. Funding history re-rendered from c.funding_rounds each run. No-write check compares byte content before overwriting.
§
The etp-hermes GHA runner can boot with a corrupted PATH (env `declare -x` output embedded into it, so /usr/bin is missing and `cat`/`rm`/`ls` fail for tools that shell out, incl. write_file). Fix: `export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin` per command, or symlink coreutils into /home/runner/.local/bin (it's on PATH). Verify with `command -v cat` before assuming tools work.
§
In etp-hermes Layer 1 runs, MCP jina read endpoints return 401 (no key wired), and since 2026-09-11 the r.jina.ai bearer fallback (JINA_API_KEY) returns 402 InsufficientBalanceError (out of credit). Working path: direct curl with a browser UA.
§
etp-hermes curated pages 404ing: BeeX /in-the-press/, Cellivate /recognition, Polybee /blog, Green COP /news-1. Undated listings (Invigilo, HiCura) fail open on date, but every link is already in seen-urls.txt — run the grep membership check first.