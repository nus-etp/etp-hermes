#!/usr/bin/env bash
# Two-way sync between ~/.hermes/ and hermes/ subtree in this repo.
#
# Pull direction (repo → local): sync memories + repo-tracked skills
# Push direction (local → repo): sync memories + user-authored skills
#
# Config is NOT synced — the repo's hermes/config.yaml is a minimal
# distribution template, while ~/.hermes/config.yaml is the full expanded
# config. They are different by design. If you change the repo config
# meaningfully, run bootstrap-hermes.sh to re-apply.
#
# Bundled Hermes skills (tracked in .bundled_manifest) are excluded from
# push — only user-authored skills are committed back.
#
# Ephemeral/sensitive items excluded: sessions/, logs/, cron/, auth.json,
# .env, state.db, *.lock, *.pid, audio_cache/, image_cache/
#
# Usage: bash scripts/sync-hermes-local.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BRANCH="main"

cd "$REPO_DIR"

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

# ── Push direction: local → repo ───────────────────────────────────────────
echo "=== Push: local → repo ==="

# Memories: persist agent memory back to repo
if [ -d "$HERMES_HOME/memories" ]; then
  mkdir -p hermes/memories
  rsync -a --delete "$HERMES_HOME/memories/" hermes/memories/
  echo "  Synced memories: local → repo"
fi

# User-authored skills: skip Hermes-bundled skills using .bundled_manifest.
# The skills directory has category folders (apple/, creative/) containing
# individual skill dirs (apple-notes/, ascii-art/). The manifest lists
# individual skill names. We scan two levels deep to find authordable skills.
BUNDLED_MANIFEST="$HERMES_HOME/skills/.bundled_manifest"
bundled_skills=""
if [ -f "$BUNDLED_MANIFEST" ]; then
  bundled_skills=$(cut -d: -f1 "$BUNDLED_MANIFEST")
fi

is_bundled() {
  local name="$1"
  echo "$bundled_skills" | grep -qxF "$name" 2>/dev/null
}

# Scan top-level skill dirs (standalone skills like dogfood/)
for skill_dir in "$HERMES_HOME/skills/"*/; do
  [ -d "$skill_dir" ] || continue
  skill_name="$(basename "$skill_dir")"
  case "$skill_name" in .*) continue ;; esac

  # Check if this contains sub-skills (category folder) or is a skill itself
  has_subskills=false
  for sub in "$skill_dir"*/; do
    [ -d "$sub" ] && has_subskills=true && break
  done

  if [ "$has_subskills" = true ]; then
    # Category folder — scan individual skills within it
    skills_to_sync=""
    for sub_dir in "$skill_dir"*/; do
      [ -d "$sub_dir" ] || continue
      sub_name="$(basename "$sub_dir")"
      case "$sub_name" in .*) continue ;; esac
      if ! is_bundled "$sub_name"; then
        skills_to_sync="$skills_to_sync $sub_name"
      fi
    done
    if [ -n "$skills_to_sync" ]; then
      mkdir -p "hermes/skills/$skill_name"
      for sub_name in $skills_to_sync; do
        rsync -a --delete "${skill_dir}${sub_name}/" "hermes/skills/$skill_name/$sub_name/"
        echo "  Synced skill '$skill_name/$sub_name': local → repo"
      done
    fi
  else
    # Standalone skill — check against manifest
    if ! is_bundled "$skill_name"; then
      mkdir -p "hermes/skills/$skill_name"
      rsync -a --delete "$skill_dir" "hermes/skills/$skill_name/"
      echo "  Synced skill '$skill_name': local → repo"
    fi
  fi
done

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
