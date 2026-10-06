#!/usr/bin/env bash
# Dedicated DeepSeek profile; credentials live outside the repository.
set -euo pipefail
cd "$(dirname "$0")"
rpg_deepseek_env="${RPG_DEEPSEEK_ENV:-${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine/deepseek.env}"
rpg_show_help=false
for rpg_arg in "$@"; do
  if [[ "$rpg_arg" == "--help" || "$rpg_arg" == "-h" ]]; then rpg_show_help=true; fi
done
if [[ -f "$rpg_deepseek_env" ]]; then
  set -a
  source "$rpg_deepseek_env"
  set +a
elif [[ -n "${RPG_DEEPSEEK_ENV:-}" && "$rpg_show_help" == false ]]; then
  printf 'DeepSeek config file does not exist: %s\n' "$rpg_deepseek_env" >&2
  exit 2
fi
export RPG_CONVERSATION_MODE="${RPG_CONVERSATION_MODE:-multiturn}"
export RPG_RESOURCE_RULES="${RPG_RESOURCE_RULES:-1}"
export RPG_CASCADE_MODEL="${DEEPSEEK_MODEL:-deepseek-flash}"
# First fail before creating a campaign if the endpoint/key/model is unavailable.
if [[ "$rpg_show_help" == false ]]; then
  : "${DEEPSEEK_API_KEY:?Set DEEPSEEK_API_KEY in the environment or a DeepSeek config file}"
  python3 - <<'PY'
import json, os, sys, urllib.request
base = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').rstrip('/')
req = urllib.request.Request(base + '/models', headers={'Authorization': 'Bearer ' + os.environ['DEEPSEEK_API_KEY']})
try:
    with urllib.request.urlopen(req, timeout=20) as response:
        available = {m['id'] for m in json.load(response).get('data', [])}
    if os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash') not in available:
        raise ValueError('configured model not present in /models')
except Exception as exc:
    print('DeepSeek connection check failed before opening a campaign: ' + type(exc).__name__, file=sys.stderr)
    sys.exit(2)
PY
fi
# Native DeepSeek profile defaults to thinking=disabled for interactive latency.
# Tool messages retain complete assistant/tool-call groups and reasoning fields.
exec python3 -m app --provider deepseek \
  --model "${DEEPSEEK_MODEL:-deepseek-flash}" \
  --base-url "${DEEPSEEK_BASE_URL:-https://api.deepseek.com}" \
  --campaign "${RPG_DEEPSEEK_CAMPAIGN:-./campaign}" \
  --max-tokens 16384 --max-tool-rounds 3 --max-repairs 3 \
  --flavor isekai --verbosity concise "$@"
