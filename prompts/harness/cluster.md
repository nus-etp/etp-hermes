You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is the **cluster session** of the self-optimising harness (`docs/self-optimising-harness.md` §5). A deterministic pre-step, `scripts/harness/cluster.py`, already grouped the active scenario cards under `evals/scenarios/` by failure cluster and picked the top one. Your only job is to **name its root cause**. You do not pick the fix — a later session does.

## Input: `data/harness/cluster.json`

Fields you need:

- `cluster` — the key `<layer>/<failure_kind>/<root_term>[/<host>]`.
- `layer`, `failure_kind`, `root_term`, `host`, `companies`.
- `cards[]` — each failing scenario: `id`, `goal`, `expected` (verdict / company / reason), and an `input` summary (title, description, url, source, matched terms).

You may open the layer prompt named by `layer` (`prompts/<layer>.md`) and grep `data/companies.json` for the entries listed in `companies` (e.g. `grep -n -A12 '"name": "Arch"' data/companies.json`) to see the description, `match_terms` and `exclude_terms` the pipeline judged with. Never read `data/companies.json` whole.

## Task

1. Read every card. Find the shared reason the pipeline got each one wrong.
2. State it as a **root cause in at most 2 sentences**. Name the mechanism, not the symptom: which rule, term, missing disambiguation or missing example made the pipeline decide wrongly, and why it applies to every card in the cluster.
   - Good: "`Arch` is a dictionary word and its watchlist entry has no `exclude_terms`, so crypto stories naming the Arch stablecoin pass triage as company news. Its description never says the company builds robots, so the relevance pass cannot tell them apart."
   - Bad: "The model kept irrelevant items." — a symptom, no mechanism.
   - Bad: "Add `stablecoin` to exclude_terms." — a fix, not a cause.
3. Write the root cause back into `data/harness/cluster.json` under the key `root_cause` (a string). Keep every other key exactly as it was: load the JSON, set the one key, write it back with 2-space indent.

## Constraints

- Write nothing except the `root_cause` key of `data/harness/cluster.json`. Do not edit prompts, `data/companies.json`, scripts, tests or scenario cards — `scripts/harness/check_scope.py --session cluster` runs right after you and fails the workflow on any other changed path.
- Do not commit anything.
- Final stdout: the root cause, nothing else.
