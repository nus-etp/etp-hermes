---
name: ab-test
description: >
  Playbook for setting up an offline A/B experiment over an LLM/agent pipeline —
  champion vs one or more challenger arms, scored with a pre-registered
  significance test. Portable: the principles and checklist apply to any prompt
  or policy experiment; the last section maps them onto this repo's concrete
  wiring (scripts/ab_*.py, the v2 "freed judgment" arm) as the reference
  implementation. Use when adding/extending a challenger arm, deciding whether
  to promote or retire one, or reasoning about the A/B design.
  Triggers on: "set up A/B", "ab test", "ab testing", "add an experiment arm",
  "challenger arm", "champion challenger", "new prompt experiment",
  "promote the winning arm", "retire the arm", "how do we A/B".
---

# A/B testing an LLM/agent pipeline

This playbook is for **offline replay / champion–challenger** experiments: every
arm processes the *same* inputs, you diff their outputs, and score the
disagreements. That makes it a **paired, within-subjects** design — which is why
the significance test is McNemar and why you do **not** need staging
environments, traffic routing, or randomized assignment. Anything written for
online A/B (splitting live users between variants) does not apply.

Vocabulary used throughout:
- **Champion** — the production policy, the baseline you're trying to beat.
- **Challenger** — an experimental variant changing *one* thing.
- **Pairwise** — each challenger is A/B'd against the champion on its own. With
  three challengers you run three independent champion-vs-challenger tests,
  never one three-way test (a discordant pair only exists between two arms).

## The invariants (don't ship an arm that breaks these)

These are what make it safe to run experiments on `main` and trust the verdict:

1. **Paired inputs.** Every arm consumes the *same* upstream artifact. Never
   give a challenger its own input pool — that reintroduces between-sample
   variance and breaks McNemar.
2. **State isolation.** A challenger writes only its own namespace and **must
   never touch the champion's outputs**. Seed any append-only state (dedup logs,
   seen-sets) from the champion's at creation so it doesn't cold-start.
3. **Fail-open / never block production.** Every challenger + scoring step is
   non-fatal: a broken arm degrades to "no data this run," never a red
   production run. Shared *pre-steps* both arms depend on stay fail-loud.
4. **Blind evaluation.** The judge ruling keep/drop (or good/bad) must be blind
   to which arm produced the item, or it rationalizes instead of judging.
5. **Pre-registration / test once.** Fix the metric, effect size, sample target,
   and stopping rule *before* looking at data. **Don't peek** and stop early —
   it inflates the false-positive rate from 5% to 20–30%+.
6. **A toggle.** The arm is feature-flagged so you can kill it without a revert.
7. **One measured variable per arm.** Change one thing the test can isolate. If
   an arm bundles two changes, a win doesn't tell you which mattered — and may
   carry a worse change on a better one's coattails.
8. **A guardrail, not just a win metric.** The significance test only rules on
   the discordant subset; it's blind to a silent volume collapse/flood. Track a
   cheap operational ratio (e.g. challenger output volume / champion volume)
   with a sanity band, separate from the win metric.

## The pipeline (any implementation)

```
shared input  ─┬─►  champion policy   ─┐
               └─►  challenger policy ─┴─►  diff kept-sets  ─►  disagreements
                                                                   │
                                            blind judge labels each │ keep/drop
                                                                   ▼
                                   per-arm McNemar (champion_right vs challenger_right)
                                   + pre-registered target + volume guardrail
                                                                   ▼
                                          significant? → promote winner, retire arm
```

## Stand up a new challenger — checklist

1. **Prompt/policy.** Copy the production policy and change *only the one
   variable under test*. Keep the rest structurally identical so a `diff` shows
   exactly what's being tested.
2. **Isolated state.** Create the arm's output namespace and seed any append-only
   state from the champion's. Confirm it writes *only* there.
3. **Register the arm.** Add it to the arm registry so the scoring fans out to it
   automatically.
4. **Execution wiring.** Run the challenger right after its champion counterpart,
   non-fatal, behind the toggle, sharing the same input artifact and any fairness
   cohort the champion uses.
5. **Pre-register.** Write down the target sample size and the decision rule
   *before* the first run.
6. **Tests.** Extend the unit tests for the scoring scripts and any schema checks.

## Decision rule

While below target, treat the report as a **qualitative review queue**, not a
verdict — skim disagreements as they accrue. Promote a challenger only once its
significance subsection reads `significant` (its target reached, p<0.05) **and**
its volume guardrail is `ok`: graft the winning change into the champion and
retire the arm. If `not_significant` at target, the change didn't move the
needle — retire it and free the slot for the next hypothesis.

## Pitfalls

- **Peeking.** Checking the p-value daily and stopping when it dips is the #1
  way to ship a false positive. The `collecting` gate exists to stop this.
- **Bundling variables.** One arm, one measured change.
- **Pair non-independence.** Correlated items (same entity recurring, same
  source) make effective N below nominal N, so real power is under target.
- **Win-metric blindness.** McNemar only scores the discordant subset; the
  volume guardrail covers the rest.
- **Leaking arm identity to the judge.** Defeats blind evaluation; never do it.

---

## Reference implementation (this repo)

The pipeline above is wired here for Layers 1–2 of the hermes signal pipeline.
Read the `## v2 A/B arm` section of `AGENTS.md` for the full description; this is
the file map.

| Concept above | Here |
|---|---|
| Arm registry | `scripts/ab_arms.py` — `CHAMPION="v1"` (production at `signals/`), `DEFAULT_CHALLENGERS=("v2",)`; override per-run with `AB_CHALLENGERS=v2,v3` |
| Champion / challenger policy | `prompts/ingest.md` / `prompts/v2/ingest.md` (+ `agent_supplement.md`), kept structurally parallel for diffing |
| Shared input | `data/candidates.json` from `scripts/collect-candidates.py` (both arms judge the identical candidate set) |
| Isolated state | champion `signals/`; challenger `signals/v2/{updates,agent}/`, `signals/v2/seen-urls.txt` (seeded from v1) |
| Fairness cohort | shared `signals/agent-queue.txt` (both arms work the same gap-fill rotation) |
| Diff kept-sets → disagreements | `scripts/ab_compare.py` → `signals/ab/{metrics.jsonl,report.md,disagreements.jsonl}` |
| Blind judge | `scripts/ab_judge.py` (DeepSeek via `scripts/ab_llm.py`, fail-open, never sees `kept_by`) |
| Per-arm McNemar + guardrail | `scripts/ab_stats.py` → `signals/ab/significance.json` (keyed by arm) + report subsections |
| Sample backfill (offline) | `scripts/ab_backfill.py` — replays champion + each challenger over a candidate pool |
| Toggle / non-fatal | `v2` workflow dispatch input; every v2 + scoring step is `continue-on-error` |
| Trace split | each arm sed-swaps `HERMES_LANGFUSE_ENV` to `production-<arm>` for its run |
| Tests | `tests/scripts/test_ab_{compare,llm,judge,stats,backfill}.py` |

**Adding a `v3` here is the checklist above:** append `"v3"` to
`DEFAULT_CHALLENGERS`, add `prompts/v3/ingest.md` + `signals/v3/` (seed its
seen-urls from v1), and copy the v2 workflow steps (the `hermes -z` call wrapped
in the Langfuse env-swap, `continue-on-error`, gated on a `v3` input). The
scoring scripts (`ab_compare`/`ab_judge`/`ab_stats`/`ab_backfill`) read the
registry and adapt with **no further edits** — `ab_stats` will emit a separate
`v1 vs v3` significance subsection and guardrail automatically.

Run/read locally:

```bash
export DEEPSEEK_API_KEY=...
python3 scripts/collect-candidates.py          # shared candidate pool
hermes -z "$(cat prompts/ingest.md)"           # champion
hermes -z "$(cat prompts/v2/ingest.md)"        # challenger(s)
python3 scripts/filter_exclusions.py
python3 scripts/ab_compare.py                  # diff → signals/ab/report.md
python3 scripts/ab_judge.py                    # blind labels (needs key)
python3 scripts/ab_stats.py                    # per-arm McNemar + guardrail

# accumulating the target takes weeks at daily volume — collapse it offline:
python3 scripts/ab_backfill.py --limit 200 && python3 scripts/ab_judge.py && python3 scripts/ab_stats.py
```

Layers 3–4 (synthesis, infographics) stay champion-only — the A/B is judged on
kept-item sets, not duplicated briefs.
