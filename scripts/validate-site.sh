#!/usr/bin/env bash
# 本地与 CI 共用的前端验证：类型检查 → 测试 → 构建（不调用 AI，也不需要 API Key）
set -euo pipefail
npm run check
npm test
npm run build
