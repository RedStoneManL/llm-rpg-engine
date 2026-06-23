#!/usr/bin/env bash
# ============================================================================
#  llm-rpg-engine launcher — edit the CONFIG block below, then run  ./run.sh
#  The API key is NEVER stored here — it is loaded from .env.local (gitignored).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"     # cd to the repo root; relative paths below resolve from here

# ╔════════════════════════════ CONFIG: edit here ════════════════════════════╗

CAMPAIGN="./campaign"      # Save folder. A NEW EMPTY dir = a fresh world (genesis runs
                          # only on an empty store; reusing a dir loads the save instead).

PITCH="东方武侠悬疑"        # World keywords (free text). Leave "" for an interactive
                          # session-zero (the engine asks "what world / who are you";
                          # answer /auto to let the model decide).

# ---- Optional: player-defined genesis (all blank = model fills everything) ----
GENESIS=""                # Blueprint file; defines any genesis part, model fills the rest.
                          #   e.g.  GENESIS="./genesis.example.yaml"
IMPORT_CARD=""            # SillyTavern character card .json (LLM-translated into the spec).
IMPORT_WORLD_BOOK=""      # SillyTavern world-book .json.
CARD_AS="protagonist"     # Import the card as  protagonist  or  npc.

# ---- Experience ----
VERBOSITY="medium"        # Narration verbosity:  concise | medium | rich
DEBUG="no"                # "yes" = record a full trajectory to <campaign>/trace.jsonl
                          #   inspect:  PYTHONPATH=. python3 -m app.trace <campaign>/trace.jsonl

# ---- Model / endpoint (usually leave as-is) ----
PROVIDER="zhipu"
MODEL="${GLM_MODEL:-glm-5.1}"
BASE_URL="${GLM_BASE_URL:-https://open.bigmodel.cn/api/coding/paas/v4}"

# ╚═══════════════════════════════════════════════════════════════════════════╝
# ------------------------------- leave below as-is --------------------------

set -a
[ -f .env.local ] && . ./.env.local
set +a
: "${ZHIPU_API_KEY:?Set ZHIPU_API_KEY — your GLM/zai key — in .env.local or the environment}"

args=(
  --campaign "$CAMPAIGN"
  --provider "$PROVIDER"
  --model    "$MODEL"
  --base-url "$BASE_URL"
  --max-tokens  "${GLM_MAX_TOKENS:-32768}"
  --max-repairs "${GLM_MAX_REPAIRS:-6}"
  --verbosity   "$VERBOSITY"
)
if [ -n "$PITCH" ];             then args+=(--pitch "$PITCH"); fi
if [ -n "$GENESIS" ];          then args+=(--genesis "$GENESIS"); fi
if [ -n "$IMPORT_CARD" ];      then args+=(--import-card "$IMPORT_CARD" --card-as "$CARD_AS"); fi
if [ -n "$IMPORT_WORLD_BOOK" ]; then args+=(--import-world-book "$IMPORT_WORLD_BOOK"); fi
if [ "$DEBUG" = "yes" ];        then args+=(--debug); fi

exec python3 -m app "${args[@]}" "$@"
