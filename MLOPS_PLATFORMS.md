# MLOps / LLMOps Platforms — Eval Framework Comparison

Research note comparing eval frameworks and cloud LLMOps platforms for `etp-hermes`. Captured for future reference; no implementation work attached.

## Current state

Evals live in `tests/` and are pure pytest:

- `tests/static/` — schema immutability, brief template, slug rules (no API).
- `tests/scripts/` — unit tests for Python helpers with mocked HTTP.
- `tests/evals/` — 4 LLM-behavior tests + a batch integration test. Raw `requests.post` to DeepSeek; custom `graders.py` LLM-as-judge returning YES/NO. Gated on `DEEPSEEK_API_KEY`.

Gaps: no run-over-run comparison, no prompt versioning tied to expected outputs, no scoring trends, no production tracing of the daily cron, hardcoded fixtures inline in test files, no drift alerts.

## What upstream hermes-agent uses

Nous Research's `hermes-agent` (installed at `~/.hermes/hermes-agent/`) also uses pytest only — pytest 9, pytest-asyncio, pytest-xdist, pytest-split, pytest-timeout. No built-in eval framework. Advanced eval lives in separate repos:

- `hermes-compression-eval` — LLM-graded probes against saved baselines.
- `hermes-agent-self-evolution` — DSPy + GEPA prompt evolution (ICLR 2026).

The current pytest + LLM-as-judge pattern is consistent with upstream practice.

## Two needs, often conflated

1. **Pre-merge regression evals** — does a prompt edit break behavior? → promptfoo, DeepEval territory.
2. **Production observability / MLOps** — what did today's run cost, did output drift, where are the latency spikes? → Langfuse, Phoenix, Arize, Confident AI, W&B Weave territory.

The current pytest harness covers (1) crudely and (2) not at all.

## promptfoo vs pytest

**promptfoo is not better here.**

- Its sweet spot is prompt→completion eval with side-by-side A/B variants in YAML. This repo doesn't do prompt sweeps — `prompts/news.md` is a single living artifact.
- The agent fetches URLs, runs preflight, writes files. To use promptfoo we'd wrap the whole `hermes -z` invocation as one "prompt" — which is exactly what `tests/evals/test_batch_integration.py` already does in Python, without YAML/Nunjucks overhead.
- promptfoo does have agent tool-use support now, but it's still geared toward stateless eval-in-CI, not a scheduled CLI agent emitting Markdown.

**DeepEval would be a clean additive upgrade** if richer metrics are ever wanted: pytest-native, drops into existing assertions, works with any OpenAI-compatible endpoint including DeepSeek, replaces the hand-rolled YES/NO grader.

## Cloud LLMOps free-tier comparison

Project volume: ~50–200 LLM calls × 1 run/day ≈ 1.5–6k calls/month.

| Platform | Free quota | Retention | Evals on free | Prompt versioning | Self-host OSS | Fit |
|---|---|---|---|---|---|---|
| **Langfuse** | 50k units/mo | 30 days | Yes (LLM-as-judge) | Yes | Yes (MIT) | ★★★★★ |
| **Arize Phoenix (OSS)** | Unlimited | Local disk | Online evals | Via Alyx | Yes (Apache) | ★★★★ |
| **Arize AX (cloud)** | 25k spans/mo | 15 days | Yes | Yes | n/a | ★★★ |
| **Confident AI** | Unlimited spans, **5 test runs/wk** | 7 days | Yes | Yes | No (SaaS) | ★★ |
| **W&B Weave** | ~1 GB ingestion | unspecified | Yes | No | Yes (non-commercial) | ★★ |
| **Helicone** | 10k reqs/mo | 7 days | No | No | No | ★ |

### Per-platform notes

- **Langfuse** — best fit. 50k units covers ~8× the project's monthly volume. 30-day retention. Prompt versioning included. MIT-licensed OSS escape hatch if cloud free tier limits ever bite. Native DeepSeek support via OpenAI-compatible SDK. First paid tier is $20/mo (Pro).
- **Arize Phoenix (OSS)** — strong second. Truly unlimited if self-hosted on a small VM. OpenTelemetry-native, auto-instruments any LLM SDK. Higher operational overhead than Langfuse cloud.
- **Arize AX (cloud)** — 25k spans/mo is marginal; the OSS Phoenix variant is strictly better unless the hosted UI is required.
- **Confident AI** — disqualified by the **5 test runs/week** ceiling on free tier; one PR with retries exhausts it. First paid tier is $500/mo.
- **W&B Weave** — no prompt versioning in the free tier, which is the one MLOps feature that would actually matter here (prompts *are* the codebase).
- **Helicone** — 10k requests/month is too tight; proxy-only model, no eval features.

## Recommendation (recorded, not actioned)

If/when the project outgrows pytest:

1. **Keep pytest.** Don't migrate to promptfoo.
2. **Add Langfuse free tier** for daily-run tracing + drift detection.
3. **Add DeepEval as a dev extra** to replace the bespoke YES/NO grader in `tests/evals/graders.py` with battle-tested LLM-as-judge metrics.
4. **Phoenix self-hosted** is the alternative if zero vendor dependency is required.

For today: status quo wins. The current setup is consistent with upstream hermes-agent and adequate for the project's volume and cadence.
