#!/usr/bin/env bash
set -euo pipefail
git fetch origin main
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "::error::This build has been superseded on main. Refusing to deploy stale content."
  exit 1
fi
if ! git diff --quiet HEAD -- src public data config astro.config.mjs package.json package-lock.json; then
  echo "::error::Build inputs changed after validation."
  exit 1
fi
git rev-parse HEAD > dist/revision.txt
