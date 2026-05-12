#!/usr/bin/env bash
# Two-way sync between ~/.hermes/ and hermes/ subtree in this repo.
#
# Sync scope:
#   Pull (repo → local): memories + cron config + repo-tracked skills
#   Push (local → repo): memories + cron config (skills excluded by design)
#
# Config is NOT synced in either direction — the repo's hermes/config.yaml
# is a minimal distribution template, while ~/.hermes/config.yaml is the
# full expanded config. They are different by design.
#
# Skills are NOT auto-synced to the repo because ~/.hermes/skills/ contains
# ~145k lines of Hermes' bundled skill library. The GHA workflow has the
# same constraint. When you author a custom skill, commit it manually:
#   cp -a ~/.hermes/skills/<name> hermes/skills/<name>/
#
# Ephemeral/sensitive items excluded from both directions: sessions/, logs/,
# auth.json, .env, state.db, *.lock, *.pid, audio_cache/, image_cache/,
# cron/output/
#
# Usage: bash scripts/sync-hermes-local.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BRANCH="main"

cd "$REPO_DIR"

# Stash any pending changes so git pull --rebase doesn't choke
STASH_MSG="hermes-sync-auto-stash-$(date +%s)"
was_dirty=false
if ! git diff --quiet HEAD 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
  git stash push --include-untracked --message "$STASH_MSG" 2>/dev/null || true
  was_dirty=true
fi

# ── Pull direction: repo → local ───────────────────────────────────────────
echo "=== Pull: repo → local ==="

git pull --rebase origin "$BRANCH"

# Memories: sync repo-tracked memories into local
if [ -d "hermes/memories" ] && [ "$(ls -A hermes/memories 2>/dev/null)" ]; then
  mkdir -p "$HERMES_HOME/memories"
  rsync -a hermes/memories/ "$HERMES_HOME/memories/"
  echo "  Synced memories: repo → local"
fi

# Skills: sync repo-tracked skills into local (individual skill dirs only)
if [ -d "hermes/skills" ]; then
  for skill_dir in hermes/skills/*/; do
    [ -d "$skill_dir" ] || continue
    skill_name="$(basename "$skill_dir")"
    mkdir -p "$HERMES_HOME/skills/$skill_name"
    rsync -a "$skill_dir" "$HERMES_HOME/skills/$skill_name"
    echo "  Synced skill '$skill_name': repo → local"
  done
fi

# Cron config: sync repo-tracked jobs.json into local
if [ -f "hermes/cron/jobs.json" ]; then
  mkdir -p "$HERMES_HOME/cron"
  cp hermes/cron/jobs.json "$HERMES_HOME/cron/jobs.json"
  echo "  Synced cron config: repo → local"
fi

# ── Push direction: local → repo ───────────────────────────────────────────
echo "=== Push: local → repo ==="

# Memories: persist agent memory back to repo
if [ -d "$HERMES_HOME/memories" ]; then
  mkdir -p hermes/memories
  rsync -a --delete "$HERMES_HOME/memories/" hermes/memories/
  echo "  Synced memories: local → repo"
fi

# Cron config: persist cron jobs.json to repo
if [ -f "$HERMES_HOME/cron/jobs.json" ]; then
  mkdir -p hermes/cron
  cp "$HERMES_HOME/cron/jobs.json" hermes/cron/jobs.json
  echo "  Synced cron config: local → repo"
fi

# Skills: NOT automatically synced to the repo.
# The Hermes bundled skill library (~145k lines across 87+ skills) lives in
# ~/.hermes/skills/ but should NOT be committed to this repo. If you author
# a custom skill via skill_manage, commit it manually:
#   cp -a ~/.hermes/skills/<name> hermes/skills/<name>/
#   git add hermes/skills/<name>/
#   git commit -m "feat(hermes): add custom skill <name>"
#   git push
# This follows the same convention as the GHA workflow's hermes-sync.yml
# which also excludes skills from auto-sync. See scripts/bootstrap-hermes.sh
# for the pull-direction setup.

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
