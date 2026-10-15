#!/usr/bin/env bash
# 只发布基于当前 main 构建并验证过的数据；绝不悄悄 rebase 到未经验证的新代码上。
set -euo pipefail
validated_sha="$(git rev-parse HEAD)"
git fetch origin main
if [[ "$(git rev-parse origin/main)" != "$validated_sha" ]]; then
  echo "::error::main changed during generation/build. Candidate data is retained in the workflow artifact. Rerun against latest main."
  exit 1
fi
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data/digests data/runs
if ! git diff --cached --quiet; then
  git commit -m "Update validated daily news"
fi
# 并发推送会被 Git 拒绝；不能因此对已构建的内容做 rebase。
git push origin HEAD:main
