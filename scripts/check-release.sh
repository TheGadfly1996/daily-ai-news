#!/usr/bin/env bash
# 部署前核对：构建基于的提交必须仍是 origin/main，且构建输入没有被改动，否则拒绝部署过期内容。
# --check-only：只检查，不写 dist/revision.txt（Pages 产物上传之后的复查使用）。
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
# --check-only: re-verify without touching dist/ (used after the Pages artifact is already uploaded).
if [[ "${1:-}" != "--check-only" ]]; then
  git rev-parse HEAD > dist/revision.txt
fi
