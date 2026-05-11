#!/usr/bin/env bash
# Restore the committed hermes/ tree into $HOME/.hermes/ and write secrets
# from environment into $HOME/.hermes/.env. Idempotent.

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)/hermes"

mkdir -p "$HERMES_HOME/cron"

cp "$SRC_DIR/config.yaml" "$HERMES_HOME/config.yaml"
cp "$SRC_DIR/cron/jobs.json" "$HERMES_HOME/cron/jobs.json"

for f in SOUL.md MEMORY.md USER.md AGENTS.md; do
  if [ -f "$SRC_DIR/$f" ]; then
    cp "$SRC_DIR/$f" "$HERMES_HOME/$f"
  fi
done

if [ -d "$SRC_DIR/skills" ]; then
  rm -rf "$HERMES_HOME/skills"
  cp -r "$SRC_DIR/skills" "$HERMES_HOME/skills"
fi

umask 077
# DeepSeek is reached via Hermes' OpenAI-compatible "custom" provider, which
# reads OPENAI_API_KEY. We accept DEEPSEEK_API_KEY from the env and write it
# under the name Hermes expects.
: "${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY is required}"
echo "OPENAI_API_KEY=${DEEPSEEK_API_KEY}" > "$HERMES_HOME/.env"
chmod 600 "$HERMES_HOME/.env"

echo "bootstrapped $HERMES_HOME from $SRC_DIR"
