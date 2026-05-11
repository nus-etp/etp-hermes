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
#   cron/      — bypassed (we use `hermes -z` rather than the internal scheduler)

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
# Hermes' "custom" provider reads OPENAI_API_KEY; rename happens here so the
# GitHub Secrets name matches what the user actually has.
echo "OPENAI_API_KEY=${DEEPSEEK_API_KEY}" > "$HERMES_HOME/.env"
chmod 600 "$HERMES_HOME/.env"

echo "bootstrapped $HERMES_HOME from $SRC_DIR"
