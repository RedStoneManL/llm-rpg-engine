"""Probe: why does generate_lore_batch return [] against real glm-5.1?
Replicates the seeding gen call, prints the RAW model response + the parse outcome
+ the generate_lore_batch result, with debug logging on.

Run: cd /root/rpg-engine-app && set -a; . ./.env.local; set +a
     export PYTHONPATH=/root/rpg-engine-app
     python3 docs/superpowers/specs/density-build-2026-06-21/probe.py
"""
import os
import logging
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

from llm.provider import make_provider, _parse_json_object
from loop.density import generate_lore_batch

PROVIDER = make_provider("zhipu", model=os.environ["GLM_MODEL"],
                         base_url=os.environ["GLM_BASE_URL"],
                         max_tokens=int(os.environ.get("GLM_MAX_TOKENS", "32768")))

TOWN = "幽港镇"
KIND = "settlement"
FLAVOR = "雨季前的边陲盐港,河汊纵横,盐商把持,镇上人面和心不和"
VENUES = ["渔港码头", "盐商会馆", "渡口茶摊", "镇守庙"]
SPECS = [{"complexity": "simple", "stage_count": 2},
         {"complexity": "medium", "stage_count": 3},
         {"complexity": "complex", "stage_count": 5}]

# Rebuild the SAME system/user prompt generate_lore_batch builds (mirror of density.py)
n = len(SPECS)
spec_lines = "\n".join(f"  {i+1}. complexity={s['complexity']}, stage_count={s['stage_count']}"
                       for i, s in enumerate(SPECS))
venue_str = ", ".join(VENUES)
system = ("You are a TRPG world-building assistant. "
          "Generate hidden quest skeletons (暗线) for a town in a dark-fantasy RPG setting. "
          "Write vivid, concise story content in Chinese. "
          "Follow the output schema exactly — the game engine fills in all numeric values.")
user = (f"Town: {TOWN} (kind={KIND})\n"
        f"Flavor / atmosphere: {FLAVOR}\n"
        f"L3 venues in this town (l3_anchor MUST be one of these): {venue_str}\n"
        f"Existing quest themes to AVOID duplicating:\n  (none)\n\n"
        f"Generate exactly {n} hidden quest skeleton(s). Each must be thematically distinct "
        f"and fit the town's flavor.\n\n"
        f"Specs (one per line — write them in this order):\n{spec_lines}\n\n"
        f"Return a JSON object with a 'lines' array containing exactly {n} objects.")

print("=" * 70)
print("RAW complete() response from glm-5.1:")
print("=" * 70)
raw = PROVIDER.complete(system, user)
print(raw)
print("=" * 70)
print(f"RAW length: {len(raw)} chars")
parsed = _parse_json_object(raw)
print(f"_parse_json_object → {type(parsed).__name__}: "
      f"{(str(parsed)[:300]) if parsed is not None else 'None (PARSE FAILED)'}")
if isinstance(parsed, dict):
    print(f"top-level keys: {list(parsed.keys())}")
    if "lines" in parsed:
        print(f"lines is {type(parsed['lines']).__name__}, len="
              f"{len(parsed['lines']) if isinstance(parsed['lines'], list) else 'N/A'}")
        if isinstance(parsed["lines"], list) and parsed["lines"]:
            print(f"first line keys: {list(parsed['lines'][0].keys()) if isinstance(parsed['lines'][0], dict) else 'not a dict'}")
print("=" * 70)
print("generate_lore_batch() result:")
result = generate_lore_batch(PROVIDER, town_id=TOWN, kind=KIND, flavor=FLAVOR,
                             venues=VENUES, existing_abouts=[], specs=SPECS)
print(f"→ {len(result)} skeleton(s)")
for s in result:
    print(f"  - {s.get('id')} [{s.get('complexity')}] about={s.get('about')!r} "
          f"l3={s.get('l3_anchor')} stages={len(s.get('stages', []))}")
