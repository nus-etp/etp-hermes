# Harness interfaces (contract for the four implementation PRs)

Every PR builds against this file. Change it only in PR1, and only additively.

## Module: `scripts/harness/scenarios.py` (owned by PR1)

`validate(card) -> list[str]`, `load_all(root, layer=, status=) -> list[dict]`,
`save(card, root) -> Path`, `next_id(layer, date, root) -> str`,
`weight(card) -> int`, `cluster_key(layer, failure_kind, root_term, host=None) -> str`,
constants `LAYERS`, `STATUSES`, `ORIGIN_KINDS`, `GRADERS`, `SCENARIOS_DIR`.

Import pattern inside `scripts/harness/*.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenarios
```

Tests import it via pytest `pythonpath` (`scripts`): `from harness import scenarios`.

## Runtimes

`--runtime nvidia|zai` selects the provider. Env per runtime:

| runtime | base URL env (default) | key env | model env (default) |
|---|---|---|---|
| nvidia | `NVIDIA_BASE_URL` (`https://integrate.api.nvidia.com/v1/chat/completions`) | `NVIDIA_API_KEY` | `NVIDIA_MODEL` (`deepseek-ai/deepseek-v4.1-flash`) |
| zai | `ZAI_BASE_URL` (`https://api.z.ai/api/paas/v4/chat/completions`) | `ZAI_API_KEY` | `ZAI_MODEL` (`glm-4.7-flash`) |
| deepseek | `DEEPSEEK_BASE_URL` | `DEEPSEEK_API_KEY` | `AB_JUDGE_MODEL` (`deepseek-chat`) |

PR2 adds `chat(..., runtime=...)` + `have_key(runtime)` to `scripts/ab_llm.py`,
backward compatible (default runtime `deepseek`).

## CLIs

PR1:
- `scripts/harness/write_scenarios.py [--date YYYY-MM-DD] [--from-ab signals/ab/disagreements.jsonl] [--judged data/harness/judged/<date>.jsonl] [--issues data/harness/issues.json]` → writes cards, prints a count. Fail-open per source.
- `scripts/harness/daily_judge_sample.py --date D [--kept N] [--dropped N]` → `data/harness/judged/<date>.jsonl`, rows `{date, url, company, headline, description, source, verdict_pipeline: keep|drop, verdict_judge: keep|drop|null, origin: "daily"}`.
- `scripts/harness/snapshot_replay.py --date D` → copies `data/candidates.json` to `data/replay/<date>.json`, prunes to 30 newest.
- `scripts/harness/check_scenarios.py` → validates every card, exit 1 on problems (runs in `evals.yml` fast job).

PR2:
- `scripts/harness/simulate.py --layer ingest --prompt-path prompts/ingest.md --runtime nvidia --out data/harness/sim/<arm>-<runtime>.json [--scenarios evals/scenarios] [--window data/replay --window-days 14] [--limit N]` → `{"arm":..., "runtime":..., "layer":..., "scenario_results":{"<id>":{"verdict":"keep|drop|null","company":...,"raw":...}}, "window_results":[{"date","url","company","headline","verdict"}]}`. Fail-open per item, exit 0; exit 2 if no key for the runtime, every verdict is null, or `--max-consecutive-failures` calls fail in a row. An exit-2 run writes no `--out` file and removes a stale one, so a failed sim can never be graded.
- `scripts/harness/grade.py --champion data/harness/sim/champion-<rt>.json ... --proposal data/harness/sim/proposal-<rt>.json ... --out-table data/harness/replay-table.md --out-verdict data/harness/verdict.json [--max-null-share 0.5]` → verdict `{"accept": bool, "decision": "accept"|"reject"|"unavailable", "inconclusive": bool, "reasons": [...], "max_null_share": float, "runtimes": {rt: {"status": "graded"|"unavailable", "null_share": {"champion": float|null, "proposal": float|null}, "reason": str|null}}, "coverage": {rt: {"champion": float|null, "proposal": float|null}}, "per_runtime": {rt: {"status", "scenarios_passed", "scenarios_total", "failed_ids", "scenarios_unevaluated", "champion_right", "proposal_right", "p_value", "volume_ratio", "cost_delta", "null_share"}}}`. Blind judge via `ab_judge.py` logic over window disagreements; McNemar via `ab_stats.py`. An arm's null share is the larger of its scenario and window null-verdict shares (`coverage` keeps the old combined non-null fraction for compatibility). A runtime whose champion or proposal sim is missing, or whose null share on either arm exceeds `--max-null-share` (default 0.5, env `HARNESS_MAX_NULL_SHARE`; `--min-coverage X` is a deprecated alias for `1 - X`), is `unavailable`: listed in `runtimes`, absent from `per_runtime`, shown as `unavailable (null share 0.97)` in the replay table, and never counted as scenario failures. On a graded runtime a null proposal verdict leaves the card in `scenarios_unevaluated` instead of `failed_ids`. `per_runtime` only ever holds graded runtimes. If no runtime is available the verdict is `accept: false, decision: "unavailable", inconclusive: true`, `per_runtime` is `{}`, and grade.py exits 3 ("we could not test it": callers must not treat this as a rejection; harness-optimise keeps the proposal branch and records nothing in `signals/harness/rejections.md`; prune.py skips the row).

PR3:
- `scripts/harness/cluster.py [--min-size 3] --out data/harness/cluster.json` → `{"cluster": key, "size", "weighted_size", "scenario_ids": [...], "layer", "failure_kind", "root_term", "eligible": bool}`.
- `scripts/harness/check_scope.py --session write_scenarios|cluster|propose|review [--rung 1-4]` → exit 1 if `git diff --name-only` leaves the session's allowed paths.
- `prompts/harness/{cluster,propose,review}.md` run via `hermes -z`.
- `scripts/harness/pr_body.py --cluster data/harness/cluster.json --verdict data/harness/verdict.json --table data/harness/replay-table.md --review data/harness/review.json` → stdout markdown.
- `.github/workflows/harness-optimise.yml`.

PR4:
- `scripts/harness/ledger.py append --pr N --cluster K --rung R --files ... --scenarios ... --verdict data/harness/verdict.json` and `ledger.py list` over `signals/harness/ledger.jsonl`.
- `scripts/harness/prune.py --older-than-days 30` → for each ledger row, `git revert --no-commit` into a temp worktree, run simulate+grade, write `data/harness/prune-report.json`; `--open-prs` opens `harness:prune` PRs via `gh`.
- `scripts/harness/trust.py` → `{"auto_merge_rung1": bool, "streak": N}` from merged `harness:auto` PRs via `gh`.
- `.github/workflows/harness-prune.yml`.

## Shared paths

- `evals/scenarios/<layer>/<id>.json` (committed)
- `data/replay/<date>.json` (committed, ≤30)
- `data/harness/` (gitignored except `data/harness/judged/`, committed)
- `signals/harness/ledger.jsonl` (committed)

## Conventions

Pure stdlib, Python ≥3.11, no comments in code, tests under `tests/scripts/test_harness_*.py` with mocked HTTP/subprocess, workflows pin actions to SHAs already used in the repo, `continue-on-error` on anything that could block production.
