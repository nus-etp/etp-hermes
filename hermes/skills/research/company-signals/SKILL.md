---
name: company-signals
description: Research recent news, signals, and developments about a specific company — funding rounds, partnerships, product launches, IPOs, acquisitions, leadership changes, and other corporate events.
---

# Company Signals Research

Use this skill when the user asks about "signals", "news about [company]", "what's happening with [company]", or any request for recent corporate/business developments about a specific organization.

## Workflow

### 1. Clarify scope first
"Signals" is ambiguous. Before searching, determine what kind of signals the user wants:
- Funding / IPO / M&A
- Product launches / feature releases
- Partnerships and integrations
- Leadership changes
- General news roundup

Don't dive into deep search until the scope is clear — you may waste time searching for the wrong thing.

### 2. Search Bing News (primary)
Bing News has the fewest captcha blocks for programmatic queries in 2025-2026.

```
URL: https://www.bing.com/news/search?q={company}+company+news
```

Use the browser to navigate, then scan the snapshot for recent headlines and sources. Articles appear with date labels like "4 days ago", "Jan 27" etc.

### 3. Pin the most recent articles
Click on article headings to open them for more detail when possible. Key info to extract per article:
- **Date** (prefer relative + absolute)
- **Source** (Bloomberg, TechCrunch, Business Wire, etc.)
- **Headline**
- **Key facts** (funding amount, partners, product name, etc.)

### 4. Prioritize by recency
Present most recent news first, then work backward. Group by timeframe if there are many items.

### 5. Include source attribution
Always say where the info came from. If the article is behind a paywall (Bloomberg, FT), note that and summarize what's available from the snippet.

## Pitfalls

- **Don't assume "signals" meaning** — the term could mean feature alerts, market signals, technical indicators, or corporate news. Clarify.
- **Google + DuckDuckGo are unreliable** for programmatic queries — both hit captchas aggressively in 2025-2026. Start with Bing News.
- **Paywalls** — Bloomberg, FT, and similar sources show snippets only. Note paywalled sources in your summary.
- **Subagent timeouts** — Delegating news research to a subagent can cause 600s+ timeouts. Do the search directly in the main session.
- **PatSnap's site has broken URLs** — `/resources/blog`, `/about/newsroom` return 404s. The actual product is at `eureka.patsnap.com`.

## Related Skills

- `research/blogwatcher` — for setting up ongoing RSS/blog monitoring of a company
