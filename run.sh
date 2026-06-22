#!/usr/bin/env bash
# Run the standalone RPG engine against GLM (Zhipu "coding" endpoint, OpenAI-compatible).
# The API key is NEVER stored here — it comes from .env.local (gitignored) or the environment.
set -euo pipefail
cd "$(dirname "$0")"

# Load local secrets if present (.env.local is gitignored).
set -a
[ -f .env.local ] && . ./.env.local
set +a

: "${ZHIPU_API_KEY:?Set ZHIPU_API_KEY — your GLM/zai key — in .env.local or the environment}"

exec python3 -m app \
  --provider zhipu \
  --model "${GLM_MODEL:-glm-5.1}" \
  --base-url "${GLM_BASE_URL:-https://open.bigmodel.cn/api/coding/paas/v4}" \
  --max-tokens "${GLM_MAX_TOKENS:-32768}" \
  --max-repairs "${GLM_MAX_REPAIRS:-6}" \
  --campaign "${CAMPAIGN:-./campaign}" \
  "$@"
