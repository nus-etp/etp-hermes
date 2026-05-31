#!/usr/bin/env bash
# Mirror the committed hermes/ tree into $HOME/.hermes/ and write the .env
# file from environment-injected secrets. The hermes/ subtree exactly mirrors
# the structure Hermes expects under HERMES_HOME:
#
#   hermes/config.yaml           -> ~/.hermes/config.yaml
#   hermes/SOUL.md               -> ~/.hermes/SOUL.md          (optional)
#   hermes/memories/MEMORY.md    -> ~/.hermes/memories/MEMORY.md (optional)
#   hermes/memories/USER.md      -> ~/.hermes/memories/USER.md   (optional)
#   hermes/skills/...            -> ~/.hermes/skills/...         (optional)
#
# Files NOT under hermes/ are intentionally excluded from ~/.hermes/:
#   prompts/   — repo-only, fed to `hermes -z` from the workflow
#   data/      — repo-only, read by prompts at runtime
#   signals/   — repo-only, written by the agent during the run
#
# Files we deliberately don't manage:
#   auth.json  — OAuth credentials (Nous Portal etc.); not needed with API-key auth
#   sessions/  — runtime, ephemeral
#   logs/      — runtime, secrets-redacted but still ephemeral

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)/hermes"

mkdir -p "$HERMES_HOME"

# Mirror the whole hermes/ tree verbatim. cp -a preserves directory structure;
# anything we ever add under hermes/ lands at the matching path under ~/.hermes/
# without further script changes.
cp -a "$SRC_DIR/." "$HERMES_HOME/"

: "${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY is required}"
umask 077
# Hermes' built-in `deepseek` provider reads DEEPSEEK_API_KEY directly.
echo "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}" > "$HERMES_HOME/.env"
# Provider failover: hermes/config.yaml lists xiaomi (Xiaomi MiMo) as a
# fallback_providers entry. The bundled xiaomi provider profile reads
# XIAOMI_API_KEY directly. Optional — if unset, the fallback chain entry
# resolves to no client and is skipped, so the primary deepseek path is
# unaffected (safe for forks without the secret).
if [ -n "${XIAOMI_API_KEY:-}" ]; then
  echo "XIAOMI_API_KEY=${XIAOMI_API_KEY}" >> "$HERMES_HOME/.env"
fi
# Layer 4 (infographics) calls Hermes' image_generate, which reads FAL_KEY.
# Optional — if unset, Layer 4 fails gracefully (continue-on-error in the
# workflow) and the brief renders without an image.
if [ -n "${FAL_KEY:-}" ]; then
  echo "FAL_KEY=${FAL_KEY}" >> "$HERMES_HOME/.env"
fi
# Web-search backends used by Layer 2 (agent supplement). Active backend is
# ddgs (search) + tavily (extract), set in hermes/config.yaml. EXA_API_KEY is
# seeded for future use / quick swap without touching the workflow.
if [ -n "${TAVILY_API_KEY:-}" ]; then
  echo "TAVILY_API_KEY=${TAVILY_API_KEY}" >> "$HERMES_HOME/.env"
fi
if [ -n "${EXA_API_KEY:-}" ]; then
  echo "EXA_API_KEY=${EXA_API_KEY}" >> "$HERMES_HOME/.env"
fi
# Jina Reader API key — consumed by scripts/jina-reader.py (Layer 0.5
# prefetch), not by hermes itself. Free tier ~100 req/day with the key,
# unauthenticated traffic works too at a lower rate. Optional: missing key
# just means the script falls back to anonymous Reader and on rate-limit
# failure the prompt's existing LLM html_scrape path takes over.
if [ -n "${JINA_API_KEY:-}" ]; then
  echo "JINA_API_KEY=${JINA_API_KEY}" >> "$HERMES_HOME/.env"
fi
# Langfuse observability plugin (hermes/config.yaml: plugins.enabled). All
# three keys are optional — if any is missing the bundled plugin's hooks
# no-op and hermes runs unchanged. BASE_URL defaults to cloud.langfuse.com
# inside the plugin, so we only write it when the operator overrode it.
if [ -n "${HERMES_LANGFUSE_PUBLIC_KEY:-}" ]; then
  echo "HERMES_LANGFUSE_PUBLIC_KEY=${HERMES_LANGFUSE_PUBLIC_KEY}" >> "$HERMES_HOME/.env"
fi
if [ -n "${HERMES_LANGFUSE_SECRET_KEY:-}" ]; then
  echo "HERMES_LANGFUSE_SECRET_KEY=${HERMES_LANGFUSE_SECRET_KEY}" >> "$HERMES_HOME/.env"
fi
if [ -n "${HERMES_LANGFUSE_BASE_URL:-}" ]; then
  echo "HERMES_LANGFUSE_BASE_URL=${HERMES_LANGFUSE_BASE_URL}" >> "$HERMES_HOME/.env"
fi
chmod 600 "$HERMES_HOME/.env"

echo "bootstrapped $HERMES_HOME from $SRC_DIR"
