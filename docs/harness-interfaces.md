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
| nvidia | `NVIDIA_BASE_URL` (`https://integrate.api.nvidia.com/v1/chat/completions`) | `NVIDIA_API_KEY` | `NVIDIA_MODEL` (`deepseek-ai/deepseek-v4-flash-0731`) |
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
- `scripts/harness/simulate.py --layer ingest --prompt-path prompts/ingest.md --runtime nvidia --out data/harness/sim/<arm>-<runtime>.json [--scenarios evals/scenarios] [--window data/replay --window-days 14] [--limit N]` → `{"arm":..., "runtime":..., "layer":..., "scenario_results":{"<id>":{"verdict":"keep|drop|null","company":...,"raw":...}}, "window_results":[{"date","url","company","headline","verdict"}]}`. Fail-open per item, exit 0; exit 2 if no key for the runtime.
- `scripts/harness/grade.py --champion data/harness/sim/champion-<rt>.json ... --proposal data/harness/sim/proposal-<rt>.json ... --out-table data/harness/replay-table.md --out-verdict data/harness/verdict.json` → verdict `{"accept": bool, "reasons": [...], "per_runtime": {rt: {"scenarios_passed", "scenarios_total", "failed_ids", "champion_right", "proposal_right", "p_value", "volume_ratio", "cost_delta"}}}`. Blind judge via `ab_judge.py` logic over window disagreements; McNemar via `ab_stats.py`.

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
