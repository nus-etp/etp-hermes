#!/usr/bin/env bash
set -euo pipefail

LAYER="${1:?layer name required (ingest|agent|synthesis|infographics)}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
ENV_FILE="$HERMES_HOME/.env"
LAYER_SUFFIX_RE='-(ingest|agent|synthesis|infographics)$'

case "$LAYER" in
  ingest|agent|synthesis|infographics) ;;
  *) echo "set-langfuse-layer: unknown layer '$LAYER'" >&2; exit 2 ;;
esac

current=""
if [ -f "$ENV_FILE" ]; then
  current="$(grep -E '^HERMES_LANGFUSE_ENV=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
fi
if [ -z "$current" ]; then
  current="${HERMES_LANGFUSE_ENV:-}"
fi
if [ -z "$current" ]; then
  echo "set-langfuse-layer: no HERMES_LANGFUSE_ENV set; leaving traces untagged" >&2
  exit 0
fi

base="$(printf '%s' "$current" | sed -E "s/${LAYER_SUFFIX_RE}//")"
value="${base}-${LAYER}"

mkdir -p "$HERMES_HOME"
touch "$ENV_FILE"
tmp="$(mktemp)"
grep -vE '^HERMES_LANGFUSE_ENV=' "$ENV_FILE" > "$tmp" || true
printf 'HERMES_LANGFUSE_ENV=%s\n' "$value" >> "$tmp"
mv "$tmp" "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "set-langfuse-layer: HERMES_LANGFUSE_ENV=$value"
