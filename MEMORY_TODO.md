# Memory enhancement plan for etp-hermes

Evaluated OpenViking — don't adopt. It solves long-running conversational agent memory; this pipeline is a stateless daily batch. Adding a context database server for what amounts to dedup + precedent tracking is a sledgehammer for a thumbtack.

What follows is memory that _actually helps_ — each tier plugs into the existing pipeline without new infrastructure. All storage uses the GHA cache roundtrip pattern already proven by `feed-cache.json` and `jina-cache`.

---

## Pain points memory can fix

| Pain point | Layer | Why it hurts |
|---|---|---|
| Exact-URL dedup misses press-release syndication | 1, Step 4 | Same announcement on PR Newswire + Yahoo Finance + trade blog = 3 "new" signals |
| Relevance judging has no precedent | 1, Step 3 | Same borderline case ("is this 'Carousell' article about the SG marketplace?") decided differently day to day |
| Source noise isn't tracked programmatically | 1, Step 2 | Tribal knowledge lives in MEMORY.md comments; pipeline blindly re-judges junk from known-bad sources |
| Gap-fill can't distinguish "quiet company" from "missed coverage" | 2, Step 3 | Wastes search ops on genuinely dormant companies |
| Briefs accumulate same-story cards | 3, Step 4d | 5 articles about one funding round across 3 days = 5 separate cards |

---

## Tier 1 — Semantic dedup (highest value, lowest effort)

**One script, sqlite-vec, zero new infra.**

Catches press-release syndication by embedding `(headline + first 200 chars of description)` and checking cosine similarity > 0.85 against the last 14 days before writing a signal.

**Storage**: `data/semantic-dedup.db` (~50-100 MB for tens of thousands of embeddings). Lives in GHA cache, same pattern as `data/feed-cache.json`.

**Embedding model**: `all-MiniLM-L6-v2` via `sentence-transformers` (384-dim, free, fast, runs on CPU). No API cost.

**Prompt change**: One line in `ingest.md` Step 4 — candidates annotated `_semantic_dup_of` are dropped.

**Script sketch** (`scripts/semantic-dedup.py`):
- Load model, connect to `data/semantic-dedup.db` with sqlite_vec
- For each candidate in today's batch: embed, query for cos_sim > 0.85, annotate dup if found
- Insert kept candidates' embeddings + metadata into db
- Output filtered candidate list with annotations

---

## Tier 2 — Relevance precedent memory

**JSONL file, embedding retrieval, few-shot injection.**

Save every keep/drop decision as `{company, headline, source, decision, reason_short}` in `data/relevance-precedents.jsonl`. Before the next relevance pass, retrieve the 5 most similar past decisions per candidate and feed as few-shot examples.

**Prompt change**: `ingest.md` Step 3 — when a `_fewshot` block exists for a candidate, use past decisions as advisory precedent. Current headline + description remains authoritative; follow precedent when the headline is near-identical.

**Storage**: ~150 KB/day, ~55 MB/year. Negligible.

---

## Tier 3 — Source quality tracking

**One JSON file, no embeddings, no prompt change needed (pass as context).**

Track per-source stats across runs: `{items_fetched, items_kept, keep_rate, last_30_days_kept}`. Computed after each run by correlating candidates against output. The ingest prompt reads `data/source-stats.json` and applies bias: keep_rate < 0.05 over 100+ items → drop marginal; keep_rate > 0.25 → keep borderline.

Solves the "Jina pre-extracted items are often low-quality" problem programmatically instead of via MEMORY.md comments.

---

## Tier 4 — Cross-run narrative memory for briefs

**Most ambitious. Only attempt after Tiers 1-3 are stable and brief duplication is measured.**

Maintain a compact "current narrative" per company (3-5 sentences of active storylines). When new signals arrive, classify each as:
- **New development** → new top-level card
- **Corroboration** → merge as sub-bullet under existing card
- **Continuation** → reference existing card, add as follow-up

Storage: `## Narrative memory` HTML comment section in each LIVING_BRIEF.md, or parallel `signals/briefs/<slug>/narrative.json`. Synthesis prompt reads it, classifies, and updates it.

Measure first — spot-check 5 briefs and count how many cards are actually the same story before building this.

---

## What NOT to do

- **No vector database server.** sqlite-vec is a single file in GHA cache — no daemon, no port, no cold-start dance.
- **No new pipeline layer.** Each tier plugs into an existing step as annotation or context injection.
- **No full article text in memory.** Embeddings + metadata only. URLs are the source of truth.
- **No OpenViking / Honcho / pgvector service.** The pipeline is a daily batch that boots, runs, exits. A persistent memory server adds moving parts with no payoff at this scale.

---

## Sequence

1. **Tier 1** — semantic dedup. One script, one cache file, one prompt line. Catches the most obvious waste.
2. **Tier 3** — source stats. Trivially simple JSON counter. Surfaces noise without reading MEMORY.md.
3. **Tier 2** — relevance precedent. Adds consistency once Tier 1 is stable.
4. **Tier 4** — narrative memory. Only after measuring that brief duplication is a real problem.
