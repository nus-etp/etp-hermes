#!/usr/bin/env python3
"""Single source of truth for the fetch User-Agent ladder.

Both the direct feed fetcher (collect-candidates.py) and the change-detection
preflight (preflight-feeds.py) walk the same ladder: an honest bot UA first,
then escalate to a browser / Googlebot / verified-effective agent-fetcher UA
only when a host gates us (401/403/429). Many feed hosts front-ended by
Cloudflare 403 an unknown bot UA but serve the identical public RSS/Atom to a
browser, and no single UA wins everywhere — hence a ladder, defined once here.

Importable as a sibling module (`import user_agents`) because scripts run with
scripts/ on sys.path; the callers also insert their own directory on sys.path so
the import resolves under pytest's file-path module loader.
"""

from __future__ import annotations

# HTTP statuses that mean "gated, try a different UA" (vs. 404/5xx which re-raise).
GATED_STATUSES = {401, 403, 429}

# Shared fallback UAs, tried in order after the honest per-script UA. A browser
# UA, then Googlebot, then verified-effective agent-fetcher UAs (exact strings,
# some deliberately terse).
_FALLBACK_UAS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Google",  # Gemini's fetcher — yes, literally "Google"
    "OpenAI File Downloader",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; "
    "GPTBot/1.2; +https://openai.com/gptbot)",
    "Claude-User",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; "
    "PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
    "XaiImageApiFetch/1.0 (Linux; x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
)


def honest_ua(component: str) -> str:
    """The honest bot UA tried first, tagged with the caller's component name."""
    return f"feed-agent-{component}/1 (+https://github.com)"


def build_ladder(component: str) -> tuple[str, ...]:
    """The full UA ladder for a caller: its honest UA, then the shared fallbacks."""
    return (honest_ua(component), *_FALLBACK_UAS)
