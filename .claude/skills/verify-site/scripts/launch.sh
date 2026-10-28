#!/usr/bin/env bash
# Build the static export (unless --skip-build), snapshot it into a fresh run
# dir, and serve it at http://127.0.0.1:<port>/etp-hermes/ exactly as GitHub
# Pages does. Prints RUN_DIR=... and BASE_URL=... on success.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(git -C "$SKILL_DIR" rev-parse --show-toplevel)"
WEB_DIR="$REPO_ROOT/site/apps/web"
tmp_root="${TMPDIR:-/tmp}"
RUNS_ROOT="${VERIFY_SITE_RUNS:-${tmp_root%/}/verify-etp-hermes-site}"

skip_build=0
for arg in "$@"; do
  case "$arg" in
    --skip-build) skip_build=1 ;;
    *) echo "usage: launch.sh [--skip-build]" >&2; exit 2 ;;
  esac
done

if [[ "$skip_build" == 0 ]]; then
  (cd "$REPO_ROOT/site" && pnpm install --frozen-lockfile >/dev/null && pnpm --filter @acme/web build) \
    || { echo "FAIL: site build failed" >&2; exit 1; }
fi
[[ -f "$WEB_DIR/out/index.html" ]] || { echo "FAIL: $WEB_DIR/out/index.html missing — run without --skip-build" >&2; exit 1; }

run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
run_dir="$RUNS_ROOT/$run_id"
mkdir -p "$run_dir/www" "$run_dir/evidence"
cp -cR "$WEB_DIR/out" "$run_dir/www/etp-hermes" 2>/dev/null || cp -R "$WEB_DIR/out" "$run_dir/www/etp-hermes"
cp "$WEB_DIR/src/data/meta.generated.json" "$run_dir/meta.json"
ls "$run_dir/www/etp-hermes/_next/static" | grep -v -E '^(chunks|media)$' | head -1 > "$run_dir/build_id"
git -C "$REPO_ROOT" rev-parse HEAD > "$run_dir/git_head"

port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
nohup python3 -m http.server "$port" --bind 127.0.0.1 --directory "$run_dir/www" \
  > "$run_dir/server.log" 2>&1 &
echo $! > "$run_dir/server.pid"
echo "$port" > "$run_dir/port"
base_url="http://127.0.0.1:$port/etp-hermes"
echo "$base_url" > "$run_dir/base_url"

for _ in $(seq 1 50); do
  if curl -fsS "$base_url/" 2>/dev/null | grep -q 'companies tracked'; then
    echo "RUN_DIR=$run_dir"
    echo "BASE_URL=$base_url"
    exit 0
  fi
  sleep 0.2
done
echo "FAIL: server did not serve $base_url/ within 10s; see $run_dir/server.log" >&2
kill "$(cat "$run_dir/server.pid")" 2>/dev/null || true
exit 1
