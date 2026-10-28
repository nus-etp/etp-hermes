#!/usr/bin/env bash
# Read-only health check for a run started by launch.sh. Exit 0 = worth driving.
set -uo pipefail

run_dir="${1:?usage: doctor.sh RUN_DIR}"
fail=0
ok()  { echo "ok    $*"; }
bad() { echo "FAIL  $*"; fail=1; }

[[ -d "$run_dir/www/etp-hermes" ]] || { echo "FAIL  $run_dir is not a launch.sh run dir"; exit 1; }
pid="$(cat "$run_dir/server.pid" 2>/dev/null)"
port="$(cat "$run_dir/port" 2>/dev/null)"
base_url="$(cat "$run_dir/base_url" 2>/dev/null)"

if kill -0 "$pid" 2>/dev/null; then ok "server pid $pid alive"; else bad "server pid $pid not running"; fi

owner="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)"
if [[ "$owner" == "$pid" ]]; then ok "port $port owned by our pid"; else bad "port $port owned by '${owner:-nobody}', expected $pid"; fi

index="$(curl -fsS "$base_url/" 2>/dev/null)"
expected="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["totalCompanies"])' "$run_dir/meta.json")"
if grep -q ">$expected<!-- --> companies tracked<\|$expected companies tracked" <<<"$index"; then
  ok "index serves $expected companies (meta.json generatedAt $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generatedAt"])' "$run_dir/meta.json"))"
else
  bad "index does not show '$expected companies tracked'"
fi

build_id="$(cat "$run_dir/build_id")"
if grep -q "$build_id" <<<"$index"; then ok "served build id $build_id"; else bad "build id $build_id not referenced by served index"; fi

chunk="$(grep -o '/etp-hermes/_next/static/chunks/[^"]*\.js' <<<"$index" | head -1)"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port$chunk")"
if [[ "$code" == 200 ]]; then ok "JS chunk under basePath serves 200"; else bad "JS chunk $chunk returned $code (hydration will fail)"; fi

echo "built from git $(cut -c1-12 "$run_dir/git_head"); repo HEAD now $(git rev-parse --short=12 HEAD 2>/dev/null)"
if chrome-devtools status 2>/dev/null | grep -qi 'not running'; then
  echo "info  chrome-devtools daemon not running (start it per SKILL.md Drive)"
else
  echo "info  chrome-devtools daemon running$( [[ -f "$run_dir/started_daemon" ]] && echo ' (started by this run)' || echo ' (NOT started by this run — use new_page, do not restart it)')"
fi
exit "$fail"
