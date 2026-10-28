#!/usr/bin/env bash
# Tear down what launch.sh / browser.sh started for RUN_DIR. Keeps RUN_DIR/evidence.
set -uo pipefail

run_dir="${1:?usage: cleanup.sh RUN_DIR}"
[[ -d "$run_dir/www" || -f "$run_dir/server.pid" ]] || { echo "nothing to clean in $run_dir"; exit 0; }

pid="$(cat "$run_dir/server.pid" 2>/dev/null)"
if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  if ps -p "$pid" -o command= | grep -q "http.server"; then
    kill "$pid" && echo "stopped server pid $pid"
  else
    echo "pid $pid is not our http.server; leaving it alone"
  fi
fi

if [[ -f "$run_dir/started_daemon" ]]; then
  chrome-devtools stop >/dev/null 2>&1 && echo "stopped chrome-devtools daemon (started by this run)"
fi

rm -rf "$run_dir/www" "$run_dir/server.pid" "$run_dir/started_daemon"
echo "evidence kept at $run_dir/evidence"
ls -1 "$run_dir/evidence" 2>/dev/null | sed 's/^/  /'
