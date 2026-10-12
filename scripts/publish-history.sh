#!/usr/bin/env bash
# Only publish data built against the current main. Never silently rebase onto untested code.
set -euo pipefail
validated_sha="$(git rev-parse HEAD)"
git fetch origin main
if [[ "$(git rev-parse origin/main)" != "$validated_sha" ]]; then
  echo "::error::main changed during generation/build. Candidate data is retained in the workflow artifact. Rerun against latest main."
  exit 1
fi
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data/digests.json data/runs
if ! git diff --cached --quiet; then
  git commit -m "Update validated daily news"
fi
# A concurrent push is rejected by Git; it must not trigger a rebase of already built content.
git push origin HEAD:main
