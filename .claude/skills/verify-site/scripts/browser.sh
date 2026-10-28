#!/usr/bin/env bash
# Ensure a headless chrome-devtools daemon exists, then open BASE_URL/<path>
# in a page isolated under this run's context. Marks the daemon as ours only
# if this script started it, so cleanup.sh never stops someone else's.
set -euo pipefail

run_dir="${1:?usage: browser.sh RUN_DIR [path]}"
path="${2:-/}"
base_url="$(cat "$run_dir/base_url")"
quiet() { "$@" 2>&1 | grep -v -E 'ExperimentalWarning|trace-warnings|Update available|npm install -g|^$' ; }

if chrome-devtools status 2>/dev/null | grep -qi 'not running'; then
  quiet chrome-devtools start --headless --isolated >/dev/null
  touch "$run_dir/started_daemon"
  echo "started headless chrome-devtools daemon"
fi
quiet chrome-devtools new_page "$base_url$path" --isolatedContext "$(basename "$run_dir")"
