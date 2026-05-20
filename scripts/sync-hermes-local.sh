#!/usr/bin/env bash
# Two-way sync between ~/.hermes/ and this repo, using Hermes' native
# `hermes backup` / `hermes import` as the primary state round-trip and
# the `hermes/memories/` rsync as a belt-and-suspenders fallback.
#
# Pull direction (repo + local cache → ~/.hermes/):
#   1. git pull --rebase
#   2. If $REPO_DIR/.hermes-state.zip exists, hermes import --force it.
#   3. bash scripts/bootstrap-hermes.sh overlays the repo's hermes/ tree
#      (config.yaml, SOUL.md, skills/, committed memories/) on top so the
#      repo wins on static config drift.
#
# Push direction (~/.hermes/ → repo + local cache):
#   1. hermes backup → /tmp/hermes-state.zip
#   2. Strip secrets, large regenerable state, and the bundled skills
#      library from the zip (same list as the GHA workflow).
#   3. Move the stripped zip to $REPO_DIR/.hermes-state.zip (gitignored).
#   4. rsync ~/.hermes/memories/ → hermes/memories/ as the git-visible
#      fallback so a lost local zip doesn't cold-start the agent.
#
# Config is NOT round-tripped via the zip — the repo's hermes/config.yaml
# is a minimal distribution template, while ~/.hermes/config.yaml is the
# full expanded config. Bootstrap intentionally clobbers the latter with
# the former so the repo stays the source of truth for static config.
#
# Skills are NOT included in the zip — ~/.hermes/skills/ also contains
# Hermes' bundled skill library (~9 MB). If you author a custom skill via
# skill_manage, commit it manually:
#   cp -a ~/.hermes/skills/<name> hermes/skills/<name>/
#   git add hermes/skills/<name>/
#   git commit -m "feat(hermes): add custom skill <name>"
#   git push
# This matches the GHA workflow's hermes-sync.yml exclusion rationale.
#
# Usage: bash scripts/sync-hermes-local.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BRANCH="main"
LOCAL_ZIP="$REPO_DIR/.hermes-state.zip"
TMP_ZIP="/tmp/hermes-state.zip"

cd "$REPO_DIR"

# Stash any pending changes so git pull --rebase doesn't choke
STASH_MSG="hermes-sync-auto-stash-$(date +%s)"
was_dirty=false
if ! git diff --quiet HEAD 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
  git stash push --include-untracked --message "$STASH_MSG" 2>/dev/null || true
  was_dirty=true
fi

# ── Pull direction: repo + local zip → ~/.hermes/ ──────────────────────────
echo "=== Pull: repo + local zip → ~/.hermes/ ==="

git pull --rebase origin "$BRANCH"

if [ -f "$LOCAL_ZIP" ]; then
  hermes import --force "$LOCAL_ZIP"
  echo "  Imported $LOCAL_ZIP into $HERMES_HOME"
else
  echo "  No local zip at $LOCAL_ZIP; skipping import"
fi

bash "$REPO_DIR/scripts/bootstrap-hermes.sh"
echo "  Bootstrapped repo overlay onto $HERMES_HOME"

# ── Push direction: ~/.hermes/ → repo + local zip ──────────────────────────
echo "=== Push: ~/.hermes/ → repo + local zip ==="

hermes backup -o "$TMP_ZIP"

# Strip secrets, large regenerable state, and the bundled skills library
# so the cached zip stays small and never carries credentials. Same list
# as the GHA workflow.
zip -d "$TMP_ZIP" \
  '.env' \
  'auth.json' \
  'auth.lock' \
  'gateway.lock' \
  'gateway.pid' \
  'gateway_state.json' \
  '.hermes_history' \
  'interrupt_debug.log' \
  'models_dev_cache.json' \
  'config.yaml.bak.*' \
  'logs/*' \
  'sessions/*' \
  'state-snapshots/*' \
  'checkpoints/*' \
  'skills/*' \
  'bin/*' \
  'audio_cache/*' \
  'image_cache/*' \
  'cache/*' \
  'sandboxes/*' \
  || true

mv "$TMP_ZIP" "$LOCAL_ZIP"
echo "  Wrote stripped backup to $LOCAL_ZIP"
ls -lh "$LOCAL_ZIP"

# Belt-and-suspenders: still rsync committed memory back to the repo so
# a lost $LOCAL_ZIP doesn't cold-start the agent on a fresh clone.
if [ -d "$HERMES_HOME/memories" ]; then
  mkdir -p hermes/memories
  rsync -a --delete "$HERMES_HOME/memories/" hermes/memories/
  echo "  Synced memories: ~/.hermes/ → repo (fallback)"
fi

# Restore any pre-existing local changes we stashed earlier
if [ "$was_dirty" = true ]; then
  git stash pop 2>/dev/null || true
  echo "  Restored local changes from stash"
fi

# ── Commit and push ────────────────────────────────────────────────────────
echo "=== Commit ==="

git add hermes/
if git diff --cached --quiet; then
  echo "nothing to commit"
else
  git commit -m "chore(hermes): sync $(date -u +%F)"
  echo "=== Push ==="
  git push origin "$BRANCH"
  echo "done"
fi
