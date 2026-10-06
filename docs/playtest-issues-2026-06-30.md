# Playtest forensics — play5 (2026-06-30)

Full call-trace + event-log autopsy of the `/root/games/play5` session
(glm-5.1, multiturn, new prompt). Method: reconstructed the whole call tree from
`trace.jsonl` (genesis + 3 player turns + backstage hooks) and cross-checked
`events.jsonl`. Issues below are ordered by fix priority, each with evidence,
root cause (file:line), and proposed fix.

Session shape: genesis **313s**, turn1 **578s**, turn2 **64s**, turn3 **77s**.

## Status (2026-06-30) — ALL fixed

- **I1 — FIXED** (`01baac3`): validator accepts bare `[]`; clock still required. No more 3-repair-per-turn.
- **I2 — FIXED** (`69bb278`): `characters_query` matches by id/真名/sketch + any-tier co-presence; fog + R7 preserved.
- **I3 — FIXED** (`e4bb66e`): augment dedups name-refs to co-located NPCs + prompt nudge to reuse co_present ids. (Root already removed by I1+I2.)
- **I4 — FIXED, prompt-strength** (`5f9b681`): knowledge-recording nudge (facts→knowledge, amnesia warning). Auto-derive **deferred** — would over-grant secrets-from-self, undermining fog.
- **I6 — BUILT** (the approved seeds+codex feature): P1 protagonist seeds (`2afd478`), P2 world seeds (`7ab9da3`), P3 Codex system+gen_codex+codex_query (`2a8393d`, `7a88937`).
- **I5 — FIXED** (`8d6b4b8`): protagonist `pthread_*` 暗线 exempt from autonomous finale/expiry (player drives their own arc).
- **I7 / I9 — FIXED** (`9c8b9b5`): I9 routes bulk genesis steps through the fast cascade model when configured; I7 fact-dedup prompt nudge.
- **I8 — addressed** (run.sh `CASCADE_MODEL` knob + `CASCADE_CONC=2` for 429). Genesis perf (I9) and backstage (I8) both benefit when `RPG_CASCADE_MODEL` is set.

---

## P0a — I1 · Every turn burns 3 failed repair rounds (REGRESSION, self-inflicted)

**Severity: Critical.** Every player turn ran the full 3 repair rounds, all
failing on the *same* error, wasting ~3 slow LLM calls/turn and dropping
`moves`/`places`.

**Evidence:** turn1/2/3 each = `produce` + 3× `repair` spans (turn1: 22+38+12s).
All three turn-1 repair inputs were identical: `moves (empty_no_reason)` +
`places (empty_no_reason)`. The model re-emitted `{"moves":[],"places":[],
"reasons":{"moves":"…未移动","places":"…无新地点"}}` three times and was rejected
every time.

**Root cause (two bugs compounding):**
1. `kernel/validation.py:95-107` — a REQUIRED_SECTIONS section that is empty AND
   has no `commit.reasons[section]` entry → `empty_no_reason`. So a bare `[]`
   for a quiet turn is invalid.
2. The prompt rewrite (commit `e581c0d`, `loop/strategy.py`) told the model to
   give bare `[]` for empty sections and dropped the `reasons` mechanism from the
   prompt — directly contradicting rule (1). Quiet turns (no move / no new place,
   i.e. most turns) now trip `empty_no_reason` every time.
3. `loop/turn.py:190,194` — `repair_sections` output is merged into
   `merged_sections`, but the rebuilt commit is constructed with
   `reasons=commit.reasons` (the *original*, empty). So even when the model
   supplies `reasons` in a repair, it is **discarded** → the repair can never
   satisfy the validator.

**Why offline tests (1641 green) missed it:** FakeLLMProvider canned commits carry
`reasons` or content; no test asserts that a bare-`[]` required section passes
`validate_commit` under the default REQUIRED_SECTIONS.

**Proposed fix:** Drop `empty_no_reason` — accept a bare `[]` for required
sections (this is what the rewritten prompt already teaches). Add a regression
test: `validate_commit` passes a commit with `moves:[], places:[]` and no
`reasons` under default REQUIRED_SECTIONS. (Repair-routing bug #3 becomes moot,
but worth fixing too for robustness.)

---

## P0b — I2 · Opening spotlight NPC is invisible to POV tools

**Severity: High.** Turn 1 ran the tool loop **12 times (~398s)** hunting for the
gray-robed old scholar the player wanted to talk to — who was standing right in
front of the protagonist.

**Evidence:** `npc_0` sketch = "身披星脉守望者灰袍的老学者"; `entity_moved
npc_0 → venue_0`. Yet the turn-1 scene's known-entities = places + protagonist
only. Tool trajectory: `characters_query("老学者")` → `[]`, then 8×
`ambient_query` keyword-roulette, never finding him.

**Root cause:** `app/play.py` `_build_scene` computes `present` = co-located
Persons with `tier=="tracked"` **only** (the loop filters `e.tier == "tracked"`).
`npc_0` is `tier=mentioned` → excluded from `present` → the POV tools (which read
`scene["present"]`) physically cannot surface him. Genesis created a plot-key
opening NPC at walk-on (`mentioned`) tier.

**Proposed fix:** Create the opening's spotlighted NPCs at `tracked` tier +
co-located + present; and/or have `_build_scene.present` include co-located
`mentioned` NPCs (surfaced to tools). Decide which during the fix.

---

## P0b — I3 · Entity duplication: one NPC minted 4×, each spawning a phantom quest

**Severity: High.** The single old scholar exists as **four** entities, and the
duplication leaked into the quest system.

**Evidence:** `npc_0` (mentioned) + `npc_moheb` + `npc_oldscholar` + `npc_scholar`
(all the same "灰袍老学者"). Three phantom quests: `quest_created` "backstop 暗
flag: npc_moheb 登场" (T3), npc_oldscholar (T7), npc_scholar (T11).

**Root cause:** `loop/entity_resolve.py` auto-create did not dedupe the model's
ad-hoc ids against the present `npc_0` — `npc_0` has no `真名`/name to match, and
the model invented a different id for the scholar across produce + each repair
round. Each new id → a new entity → the backstop-NPC lore/quest hook fires per
new NPC. (Tightly coupled to I2: if `npc_0` were visible, the model would reuse
its id.)

**Proposed fix:** Before minting, dedupe against co-located present NPCs by
sketch/真名 similarity; if a matching NPC is present, resolve the reference to it.

---

## P0c — I4 · Knowledge flow barely recorded → protagonist "amnesia"

**Severity: High.** The scholar revealed major secrets to the protagonist (the
arm's true nature; "40-year records: only two bare-handed resonance carriers, both
died"), but the engine recorded almost no knowledge transfer.

**Evidence:** **1** `knowledge_set` event for the whole session, vs 13
`fact_asserted` including secret reveals aimed at the protagonist. With knowledge
unrecorded, the fog/POV layer doesn't know the protagonist learned these → next
turn he effectively "forgets," and POV tools won't surface them.

**Root cause:** the model rarely emits the optional `knowledge` section; nothing
auto-derives knowledge when a secret is revealed to the protagonist in narration.

**Proposed fix (needs small design):** strengthen the knowledge-recording nudge,
and/or auto-set protagonist knowledge when a `secret`/`restricted` fact is
asserted about an entity in a scene the protagonist is present for and the
narration shows the reveal.

---

## P1 — I6 · Protagonist mode-collapse (confirmed in data)

**Severity: Medium (already designed: P1 seeds).** Every genesis yields the same
archetype.

**Evidence:** protagonist sketch = "残晖镇外围哨塔守哨人的**遗孤**…只留下一枚刻有
古老符文的星脉碎片"; opening = "你右臂内侧隐隐发痒…那道**旧疤**…发烫". Textbook
orphan + aching-arm-scar + sealed-rune cliché.

**Root cause:** `gen_protagonist` (`loop/bootstrap.py:689`) is free LLM generation
with **no oracle seed** (NPCs have `npc_roles`+`npc_traits`; the protagonist has
nothing).

**Proposed fix:** the approved P1 multi-dimensional protagonist seeds
(`origin_roots` / `entanglements` / `quirks` / `world_tie`).

---

## P5 — I5 · Premature 暗线 resolution

**Severity: Medium.** A protagonist-bound hidden line resolved its world-rescue
endgame inside a 3-turn game.

**Evidence:** `quest_world_resolved` "暗线救场了结:pthread_1" at T12.

**Root cause:** the lore endgame gating fires too early; possibly inflated by the
duplicate-NPC quest pollution (I3) bumping internal flag/turn counters.

**Proposed fix:** investigate lore resolution thresholds / 暗线 lifespans; re-check
after I3 is fixed (the pollution may be the trigger).

---

## Low / Perf

### I7 · Near-duplicate facts (Low)
3 overlapping facts about the arm scar (`右臂旧疤` 共鸣 / 异变 / 性质). No fact
dedup. Low priority — fact-dedup or accept.

### I8 · Backstage hooks run on the slow main model (Perf) — 机制就绪 / 待配置
`density` ran a **107s** synchronous LLM call inside turn 1 because
`cascade_provider` was unset (no `RPG_CASCADE_MODEL`). **Not a code gap:** the
routing is built — backstage hooks (暗骰/cascade/catchup/density) + bulk genesis
steps use `cascade_provider` whenever `RPG_CASCADE_MODEL` is set, and run.sh
exposes the `CASCADE_MODEL` knob (line 41) + conditional export (line 67).
**待配置:** put a fast model name (from your account) in `CASCADE_MODEL` to
activate; left empty, backstage correctly falls back to the main model — just
slower. No further code work outstanding.

### I9 · Genesis 313s (Perf)
9 sequential slow calls; `gen_threads` alone 125s (one call out=**3643** tokens).
Fix: faster model for some genesis steps and/or trim gen_threads output; and do
**not** pile more LLM calls into genesis (codex authoring must be lazy or
fast-model, or it makes the opening even slower).

---

## Suggested fix order

1. **I1** (P0a) — drop `empty_no_reason`, accept bare `[]`; + repair reasons
   routing; + regression test. *Highest ROI: removes 3 wasted slow calls every
   turn. Self-inflicted, fix first.*
2. **I2 + I3** (P0b) — opening NPCs tracked+present; dedupe auto-create. *Removes
   the turn-1 12-round flail and the 4× duplication + phantom quests.*
3. **I4** (P0c) — record knowledge so the protagonist remembers.
4. **I6 / I3-design** (P1) — protagonist (and world) multi-dimensional seeds.
5. **I5** (P5) — 暗线 pacing (re-check after I3).
6. **Codex** (P3), **I8/I9** perf, **I7** low.
