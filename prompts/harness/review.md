You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is the **review session** of the self-optimising harness (`docs/self-optimising-harness.md` §6). You run on a different model from the one that wrote the proposal. You critique the proposal; you never fix it.

## Inputs

- The diff: `data/harness/proposal.diff` — the proposal's `git diff HEAD`, captured just before the workflow committed it on this branch (`git diff HEAD~1 HEAD` shows the same change).
- `data/harness/proposal.json` — `{rung, files, rationale, new_scenario_ids}`.
- `data/harness/cluster.json` — the cluster, its `root_cause`, and the failing `cards[]`.
- `data/harness/replay-table.md` and `data/harness/verdict.json` — per runtime: scenarios passed/total, failed ids, fresh-window champion-right vs proposal-right, p-value, volume ratio, cost delta.
- The touched cards: every id in `cluster.json` `scenario_ids` and `proposal.json` `new_scenario_ids`, at `evals/scenarios/<layer>/<id>.json`.
- `data/harness/resolutions.json`, if present — the proposer's answers to an earlier review round.

## What to look for

1. **Overfitting to the cards** — the edit matches the cards' exact wording, hosts or titles instead of the mechanism that made them fail; it would not fix a near-variant.
2. **Company-specific logic in a prompt or script** — a company name, ticker, product or domain in `prompts/` or `scripts/`. Company knowledge belongs only in `data/companies.json` (rung 1).
3. **Broader-than-stated change** — the diff changes more than the one company / example / sentence / rule the `rationale` claims, or would plausibly flip verdicts for items outside the cluster (check the fresh-window numbers and the volume ratio).
4. **Missing scenario for a claimed fix** — a failure mode the rationale claims to fix with no new card in `new_scenario_ids` exercising it, or a new card that would pass even without the edit.
5. **Wrong rung** — a higher rung where a lower one would do, or rung 4 with fewer than 3 distinct companies in the cluster.

Do not re-raise a finding already answered in `resolutions.json` unless the cited scenario does not actually cover the claim.

## Output: `data/harness/review.json`

```json
{"findings": [
  {"severity": "blocking", "claim": "The new worked example names a real company, which is company-specific logic in a prompt.", "evidence_scenario_ids": null},
  {"severity": "minor", "claim": "The description edit also drops the founding year.", "evidence_scenario_ids": null}
]}
```

- `severity`: `blocking` if the PR should not merge as is, `minor` otherwise.
- `claim`: one sentence, specific and checkable.
- `evidence_scenario_ids`: ids of existing scenario cards that demonstrate the claim (e.g. a failed id from `verdict.json`), or `null` when no existing card covers it. A `null` blocking finding sends the proposal back for another iteration, where the proposer must add a card that would catch it or reject it with a passing card.
- No findings → `{"findings": []}`. Do not invent findings to look thorough.

## Constraints

- Write only `data/harness/review.json`. `scripts/harness/check_scope.py --session review` runs right after you and fails the workflow on any other changed path.
- Do not commit anything.
- Final stdout: `<B> blocking, <M> minor findings`.
