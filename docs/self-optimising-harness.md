# Self-optimising harness — design

Status: proposal, 2026-10-03. Supersedes the manual champion/challenger A/B
loop (`.claude/skills/ab-test/SKILL.md`, `scripts/ab_*.py`) as the way the
pipeline's judgment improves. The A/B scripts are kept and repurposed as
graders; nothing is deleted.

## 1. Why the A/B loop was not self-sustaining

The v1/v2 experiment produced a valid verdict (p=0.0004) and then stopped.
Each link in the chain needed a human, so the chain ran exactly once:

| Step | What a human had to do | Consequence |
|---|---|---|
| Hypothesis | Write `prompts/v2/*` by hand | No v3 was ever written |
| Sample | Wait weeks at 1–5 disagreements/day, or run `ab_backfill.py` | `ab-experiment.yml` self-disables at target and never re-arms |
| Promotion | Graft the winning sections verbatim, retire the arm, delete the workflow steps | Harness torn down after one use |
| Memory | None — 75 judged disagreements live in `signals/ab/disagreements.jsonl` and are never re-run | Gains can silently regress; the eval suite is 3 hand-written fixtures |
| Cost | A full duplicate `hermes -z` session per arm per day | Expensive to keep an arm alive "just in case" |

The fix is not a better A/B test. It is a loop where **production failures
become permanent scenarios, scenarios drive proposals, proposals are graded
on replay, and only graded wins reach a human**, who merges a PR.

## 2. The loop mapped onto this repo

```
                       ┌──────────── 1 Simulate ─────────────┐
                       │  replay scenario corpus + a fresh    │
                       │  14-day candidate window through     │
                       │  the layer prompt (ab_backfill mode) │
                       └──────────────┬───────────────────────┘
   6 Human gate                       ▼
   merge the PR          2 Judge & grade: deterministic graders +
        ▲                  blind LLM judge (ab_judge) per scenario
        │                             ▼
   5 Agent review        3 Cluster: (layer, failure_kind, entity/term, host)
   reviewer agent                     ▼
   critiques the PR;     4 Propose: one cluster → one root cause → one
   each finding gets       smallest edit + its new scenario card → one PR
   a scenario or a                    │
   rejection with        ◄────────────┘
   evidence
```

| Diagram | Here |
|---|---|
| A. Agents write the evals | `scripts/harness/write_scenarios.py` turns each graded failure into a versioned **scenario card** (§3) |
| B. Agents spawn agents | `harness-optimise.yml` runs an orchestrator that invokes writer → clusterer → proposer → reviewer as separate `hermes -z` sessions with disjoint write scopes |
| C. Agents call agents | The simulator is **replay**, not live traffic: a scenario's frozen candidate is fed to the real production prompt in single-candidate override mode (what `ab_backfill.py` already does) |
| D. Sparse, attributable edits | One cluster per PR; the PR may touch exactly one of: a company's `match_terms`/`exclude_terms`/`description`, one prompt worked example, one prompt rule, one script rule |
| E. End-state grading | Deterministic graders (`tests/static`, dedup/volume/budget invariants) + blind judge; graded on what was *written* (`signals/updates`, `agent-reached.txt`, brief diff), not on the transcript |
| F. Dual-runtime regression | A change must hold on the primary model **and** the first fallback (`hermes/config.yaml`: nvidia/deepseek-v4-flash and zai/glm-4.7-flash). Failover is turn-scoped and real, so a prompt tuned to one model regresses silently on the other |
| G. Agents review agents | Reviewer session reads the PR diff + replay table; every finding is answered with a new scenario (fix) or a rejection with the scenario id that contradicts it |
| H. Keep what generalizes | Company-specific fixes are only ever allowed in `data/companies.json`; a monthly prune replays with each merged change reverted and opens a prune PR for any change that no scenario needs |

## 3. Scenario cards — the unit of memory

Directory: `evals/scenarios/<layer>/<id>.json`, committed, append-only (a
superseded card gets `status: retired`, never deleted). One card = one
failure trace frozen into a replayable test.

```json
{
  "id": "ingest-2026-10-03-0007",
  "layer": "ingest",
  "version": 1,
  "status": "active",
  "origin": {
    "kind": "judge_disagreement",
    "date": "2026-10-03",
    "trace": "langfuse:trace/…",
    "run_id": 18234567890
  },
  "persona": "Layer 1 relevance pass over data/candidates.json",
  "goal": "Drop a stablecoin story that matched the watchlist entry Arch on a weak term",
  "input": {
    "candidate": { "title": "…", "url": "…", "source": "…", "matched": ["Arch"] },
    "companies": { "Arch": { "description": "…" } }
  },
  "expected": {
    "verdict": "drop",
    "company": null,
    "reason": "Arch is a dictionary word; the item is about a stablecoin issuer"
  },
  "cluster": "ingest/false_keep/weak_term/arch",
  "graders": ["verdict_match", "blind_judge"],
  "created_by": "write_scenarios@run 18234567890"
}
```

Layer-specific `input` / `expected`:

- **ingest** — one candidate + its matched companies; expected keep/drop +
  company. Graded by `verdict_match` (deterministic) and `blind_judge`.
- **agent_supplement** — one company slice + its open questions; expected
  `must_answer` question ids, `must_verify_date`, `op_budget ≤ N`. Graded on
  the written `signals/agent/<date>.md` block and `agent-reached.txt`.
- **synthesis** — a brief before + today's signals; expected template
  invariants, `retired_questions`, `no_new_bullet_for_corroboration`. Graded
  by `tests/static/test_briefs_template.py` logic + `no_fabrication` judge.

Where failures come from (the "failing call traces"):

| Source | Signal | Card kind |
|---|---|---|
| Daily blind judge over a sample of kept items | judge says drop | `ingest/false_keep` |
| Daily blind judge over a sample of dropped candidates | judge says keep | `ingest/false_drop` |
| `filter_exclusions.py` removing an item the LLM kept | exclude_terms gap or LLM miss | `ingest/exclude_leak` |
| `filter_seen_updates.py` removing an item | dedup miss | `ingest/dedup_miss` |
| Open question queued ≥3 times, never retired | agent cannot answer | `agent/unanswered_question` |
| `agent-reached.txt` shorter than the queue | budget arithmetic | `agent/budget_shortfall` |
| `tests/static` red on `main` after a sync commit | template drift | `synthesis/template_drift` |
| Human correction (GitHub issue with label `harness:wrong-keep`, `harness:missed`, `harness:brief-error`) | authoritative | any; `origin.kind: human` and weight 3× in grading |

The daily judge sample is the only new daily cost: ~40 kept + ~40 dropped
candidates through `ab_judge.py` ≈ 80 cheap calls/day.

## 4. Grading (E)

Two tiers, both run per scenario, per runtime:

1. **Deterministic graders** (pure Python, fail-loud): verdict match,
   company match, template invariants, dedup invariants, op budget, volume
   guardrail (`ab_stats.py` band, kept/dropped ratio vs champion), cost
   (tokens from Langfuse, ≤ +15% vs champion).
2. **Blind LLM judge** (`ab_judge.py`, never sees which arm produced the
   output; temperature 0; two votes on disagreement, majority of three).

A scenario **passes** when every grader listed in its `graders` passes.

Aggregate decision for a proposal (the ACCEPT box in the diagram):

- every **active** scenario passes on **both** runtimes, including the
  scenario(s) the proposal adds;
- on the **fresh 14-day window** (unlabeled candidates, judged blind) the
  proposal's judged-correct rate is not lower than the champion's: paired
  McNemar from `ab_stats.py`, accept if `challenger_right ≥ champion_right`
  and `p ≥ 0.05` for a loss (no significant regression) — generalization
  guard against overfitting the scenario corpus;
- volume guardrail `ok`, cost guard `ok`.

Otherwise the PR is closed by the workflow with the failing scenario ids in
the body. The harness stays as it was.

## 5. Clustering and proposing (3, 4, D)

**Cluster key** is deterministic: `layer/failure_kind/root_term/host`.
`root_term` is the matched company or the weak term for ingest, the question
id for agent, the section for synthesis. The clusterer agent only *names*
the root cause for the top cluster and writes `data/harness/cluster.json`;
it does not pick the fix.

**Proposer** reads the top cluster (min 3 active failing scenarios, or 1
human-origin card) and must choose the *lowest* rung that can plausibly fix
it:

1. `data/companies.json` — `match_terms`, `exclude_terms`, `description`
   for the one company involved (config, never harness — rule H);
2. one worked example added/replaced in the layer prompt;
3. one rule sentence in the layer prompt;
4. one rule in `scripts/entity_terms.py` / `collect-candidates.py` (+ unit
   test).

Rung 4 is only allowed when the cluster spans ≥3 distinct companies. The PR
body is generated from a template: cluster, root cause, rung, diff, the new
scenario id(s), the replay table (per runtime: scenarios passed/total, fresh
window champion-right/challenger-right/p, volume ratio, cost delta), and the
reviewer's findings with their resolutions.

Write scopes per agent session (enforced by the workflow's `git diff --name-only`
check before commit):

| Session | May write |
|---|---|
| write_scenarios | `evals/scenarios/**` |
| cluster | `data/harness/cluster.json` |
| propose | exactly one of the rung-1..4 paths + `evals/scenarios/**` |
| review | `data/harness/review.json` only |

## 6. Review (5, G)

The reviewer session gets the diff, the replay table and the scenario
cards it touches, and must emit findings as
`{severity, claim, evidence_scenario_ids | null}`. The orchestrator then:

- for each `blocking` finding with no contradicting scenario: asks the
  proposer for one more iteration (max 2) — the fix must add a scenario that
  would have caught the finding;
- for each finding the proposer rejects: requires the id of an existing
  passing scenario that demonstrates the claim is wrong; otherwise the
  finding stands and the PR is closed.

The reviewer runs on the *other* runtime from the proposer (nvidia proposes,
zai reviews, or vice versa) so model-specific blind spots are less
correlated.

## 7. Human gate and prune (6, H)

- **Gate**: a human merges. The PR is labelled `harness:auto`, assigned to
  the maintainer, and carries everything needed to decide in two minutes.
  No auto-merge in phase 1; after 10 consecutive merged-without-edit PRs the
  workflow may enable auto-merge for rung-1 (config-only) proposals.
- **Ledger**: `signals/harness/ledger.jsonl` — one row per merged proposal:
  PR, cluster, rung, files, scenario ids added, gain (fresh-window delta,
  scenarios fixed).
- **Prune** (monthly job): for each ledger row older than 30 days, replay
  the active corpus with that change reverted. If zero scenarios fail
  without it, open a `harness:prune` PR reverting it with the evidence.
  Changes without measured gain do not accumulate.

## 8. Workflows

- `hermes-sync.yml` (unchanged cadence) gains one step after Layer 1 and
  one after Layer 3, both `continue-on-error`: snapshot the day's
  `data/candidates.json` to `data/replay/<date>.json` (committed, ≤30 kept,
  older pruned) and run the daily blind-judge sample → `data/harness/judged/<date>.jsonl`.
- `harness-optimise.yml` — weekly (Sunday 15:00 UTC) + `workflow_dispatch`.
  Steps: write_scenarios → cluster → gate (any eligible cluster?) → propose →
  simulate on runtime A and B (matrix) → grade → review loop → open/refresh
  PR on branch `harness/<cluster-slug>` or close with evidence. One PR open
  at a time per layer; a second eligible cluster waits a week.
- `harness-prune.yml` — monthly (1st, 07:00 UTC).
- `evals.yml` `fast` job additionally runs the **deterministic** graders over
  the scenario corpus against the committed prompts (no LLM) so a hand
  edit to a prompt that breaks a config-only or template scenario fails the PR.

## 9. What is reused

| Existing | New role |
|---|---|
| `scripts/ab_backfill.py` single-candidate override | the simulator (`scripts/harness/simulate.py` wraps it, adds `--runtime` and `--prompt-path`) |
| `scripts/ab_judge.py` | blind judge grader |
| `scripts/ab_stats.py` | McNemar + volume guardrail on the fresh window |
| `scripts/ab_llm.py` | LLM client for graders; gains a provider switch mirroring `hermes/config.yaml` |
| `signals/ab/disagreements.jsonl` (75 labeled rows) | seed corpus: converted to the first ~75 ingest scenario cards on day one |
| `tests/static/test_briefs_template.py` | synthesis deterministic grader |
| `tests/evals/_obs.py` | every grader run traced to Langfuse under `HERMES_LANGFUSE_ENV=harness` |

`ab_arms.py` and the arm/namespace concept go away: there is no long-lived
challenger, only a branch under test for one workflow run.

## 10. Budget

Per weekly run, worst case: 200 scenarios × 2 arms × 2 runtimes = 800
single-candidate calls, + fresh window ~300 × 2 × 2 = 1,200, + judge ~500,
+ 4 short agent sessions. All on flash-class models; well under one day of
the current pipeline's spend. Daily added cost is the ~80-call judge sample.

## 11. Phases

1. **Memory first** (1 PR): scenario schema + `write_scenarios.py` seeded
   from `signals/ab/disagreements.jsonl`; daily judge sample + replay
   snapshot steps; deterministic graders in `evals.yml`. Value even if
   nothing else ships: regressions become visible.
2. **Simulator + grading** (1 PR): `simulate.py` over two runtimes,
   `grade.py` producing the replay table, run by hand on a branch.
3. **Closed loop** (1 PR): `harness-optimise.yml` with cluster → propose →
   review → PR. First month: proposer limited to rung 1 and 2.
4. **Prune + trust** (1 PR): ledger, monthly prune, auto-merge for rung 1
   after the trust threshold.

## 12. Invariants carried over from the A/B playbook

Paired inputs (same frozen candidates for champion and proposal); state
isolation (a proposal never writes `signals/`); fail-open everywhere outside
the shared pre-steps; blind judging; pre-registered acceptance rule (§4,
fixed in the workflow, not chosen per run); one variable per proposal.
