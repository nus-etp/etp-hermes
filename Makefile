# Repo automation helpers. The pipeline itself is documented in CLAUDE.md.

HERMES_REPO := NousResearch/hermes-agent
WORKFLOW    := .github/workflows/hermes-sync.yml

.PHONY: pin-hermes

# Re-pin the hermes-agent install in the daily workflow to a new release.
# Resolves REF (tag, branch, or 40-char commit SHA) to a commit, fetches the
# installer at that commit, and rewrites HERMES_COMMIT + HERMES_INSTALL_SHA256
# in the workflow. Review the printed diff, then commit.
#
# Usage: make pin-hermes REF=v2026.6.5
pin-hermes:
	@test -n "$(REF)" || { echo "usage: make pin-hermes REF=<tag|branch|sha>"; exit 1; }
	@set -e; \
	if echo "$(REF)" | grep -qE '^[0-9a-f]{40}$$'; then \
	  sha="$(REF)"; \
	else \
	  sha=$$(git ls-remote "https://github.com/$(HERMES_REPO).git" "refs/tags/$(REF)^{}" | cut -f1); \
	  [ -n "$$sha" ] || sha=$$(git ls-remote "https://github.com/$(HERMES_REPO).git" "refs/tags/$(REF)" "refs/heads/$(REF)" | head -1 | cut -f1); \
	  [ -n "$$sha" ] || { echo "ref '$(REF)' not found in $(HERMES_REPO)"; exit 1; }; \
	fi; \
	echo "pinning hermes-agent to $(REF) ($$sha)"; \
	curl -fsSL "https://raw.githubusercontent.com/$(HERMES_REPO)/$$sha/scripts/install.sh" -o /tmp/hermes-install-pin.sh; \
	hash=$$( (sha256sum /tmp/hermes-install-pin.sh 2>/dev/null || shasum -a 256 /tmp/hermes-install-pin.sh) | cut -d' ' -f1 ); \
	perl -pi -e "s/^(\s*HERMES_COMMIT:).*/\$$1 $$sha # $(REF)/" $(WORKFLOW); \
	perl -pi -e "s/^(\s*HERMES_INSTALL_SHA256:).*/\$$1 $$hash/" $(WORKFLOW); \
	git --no-pager diff -- $(WORKFLOW)
