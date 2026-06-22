# Unified Event-Line / Quest System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps. TDD. This is a REFACTOR that merges two live systems — read the real code each task touches before editing; keep the full suite green at every commit.

**Goal:** Merge LoreSystem (暗线) + StorySystem (明线/storylines) into ONE event-line/quest model: a quest with `state ∈ {暗,明,了结}`, advanced by the engine 暗骰 while 暗 and by the player+narrator while 明, with transitions 浮现(暗→明)/搁置(明→暗, option-a JIT-resequence)/了结, and disclosure by state (明账 push + ambient B).

**Architecture:** Keep `LoreSystem`/`systems/lore.py` as the unified home (lower churn; a cosmetic lore→quest file rename is a deferred final sweep). Lines gain `state`. LoreSystem ABSORBS StorySystem's role: a narrator commit section `quests` (ops surface/advance/resolve) + the 明账 inject. StorySystem is retired. The 暗骰 (`run_lore`) only touches 暗 lines + does world-push surfacing; `jit_resequence` handles option-a on demote. Disclosure: 明账 (明) + `station_push_fragment` ambient (暗). The A tool-loop infra stays for future P3 (unused here).

**Tech Stack:** Python 3.12, pytest offline. Run: `cd /root/rpg-engine-app && PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q`.

**Specs:** `docs/superpowers/specs/2026-06-21-unified-questline-design.md` (authoritative). Supersedes the lore-event-line + storyline split.

## Global Constraints
- Python 3.12; branch `app`; `python3` not `python`. Offline-deterministic tests; 暗骰 via seeded `Oracle`; JIT-resequence via the provider but offline-tested with `FakeLLMProvider`.
- Baseline before this plan: **912 passed, 1 deselected**. This is a MIGRATION — existing storyline/lore tests will MOVE to the unified model; the passing count must not drop except via deliberate, named test migrations a task describes. Behavior preserved (明账 open/advance/resolve equivalent; 暗骰 brewing equivalent).
- **The bug being fixed:** advance channels partitioned by state — 暗骰 ONLY advances 暗 lines; the narrator `quests` section ONLY advances 明 lines (the ones in the 明账); 暗→明 is a `surface` op, not an advance. The narrator never sees 暗 lines as advanceable (only their ambient clues).
- HARD git guardrails: stay on `app`; NEVER git init/reset/rebase/checkout/branch-switch; never delete `_legacy/` or `docs/`. Commit only files each task names. Trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Disclosure (settled by the A/B comparison): 明 = full 明账 strongly pushed; 暗 = `station_push_fragment` (current-venue L1 + town L0). Drop the A/PULL tool path from the lore disclosure (keep `llm/tools.py`+`llm/lore_tools.py`+the tool-loop for future P3, just don't wire it into the quest disclosure).

## Phasing (functional merge in LoreSystem first; cosmetic rename last)
- **T1** state field + state-aware 暗骰 + `jit_resequence` helper
- **T2** LoreSystem absorbs the 明-side: `quests` commit section (surface/advance/resolve) + 明账 inject
- **T3** retire StorySystem; migrate its events/section/tests; narrator prompt storylines→quests
- **T4** transitions wired into run_turn: world-push surfacing + demote-on-leave (+ JIT-resequence)
- **T5** disclosure unification in the strategy (明账 + ambient B; drop A-tool path)
- **T6** complex full-lifecycle glm-5.1 demo

---

### T1: `state` field + state-aware 暗骰 + `jit_resequence`
**Files:** Modify `systems/lore.py`, `loop/lore.py`; Test `tests/systems/test_quest_state.py`, `tests/loop/test_quest_dark.py`.
- LoreSystem `apply` on `lore_created`: store `state` (from deltas, default `"暗"`). Existing lines/tests (no state) default 暗 → behavior unchanged.
- `loop/lore.run_lore`: only roll/advance lines with `state == "暗"` (skip 明/了结). (Existing tests: all lines 暗 → unchanged.)
- Add `loop/lore.jit_resequence(line, world, provider) -> list[dict]`: option-a — given a line whose remaining pre-set stages no longer fit the current (player-diverged) reality, call `provider.complete_json` with the line's about/secret/current clues_dropped/current world summary → returns a NEW `stages` list (the续写 default trajectory from here). Pure-ish (provider injected); offline-test with a FakeLLMProvider returning canned stages. Returns the new stages (caller emits an event to apply them).
- Tests: 暗骰 skips 明/了结 lines; `jit_resequence` returns parsed stages from a fake provider; a malformed provider response → safe fallback (keep old remaining stages).

### T2: LoreSystem absorbs the 明-side (`quests` commit section + 明账 inject)
**Files:** Modify `systems/lore.py`; Test `tests/systems/test_quest_active.py`. (Read `systems/story.py` for the明账 behavior to mirror.)
- LoreSystem `commit_sections()` → `{"quests"}` (it had none). `event_types()` gains `quest_surfaced`, `quest_advanced`, `quest_resolved` (alongside lore_created/lore_advanced).
- `validate("quests", decl, world)`: each item `{op: surface|advance|resolve, id, summary?}`. surface: id must be an existing 暗 line. advance/resolve: id must be an existing 明 line. (机械处严; 创作处松.)
- `to_events("quests", ...)`: surface→`quest_surfaced`{id}; advance→`quest_advanced`{id, summary}; resolve→`quest_resolved`{id, summary?, by:"player"}.
- `apply`: 
  - `quest_surfaced`: line.state 暗→明, set surfaced_turn; (暗骰 will now skip it).
  - `quest_advanced`: require state==明; update line.summary (the明账 line); (this is the player-driven advance — narrator-authored).
  - `quest_resolved`: state→了结, record by/summary.
- 明账 inject: render `state=="明"` lines as the active-quest ledger (mirror `systems/story.py` inject — "故事线·明账·强推", scene layer). This is the明态 disclosure (always pushed).
- Tests: surface flips 暗→明; advance updates a明 line's summary; advance on a 暗 line → validation error (can't narrator-advance a 暗 line — the bug guard); resolve→了结; 明账 inject renders only明 lines.

### T3: Retire StorySystem; migrate
**Files:** Modify `app/engine.py` (registry), `loop/strategy.py` (prompts: storylines→quests section), delete/empty `systems/story.py` usage; migrate `tests/systems/test_story*.py` + any storyline references → quests. (Do NOT delete docs.)
- Remove `StorySystem` from `build_engine` registry; LoreSystem now owns the quest/明账 role. The old `storylines` events (storyline_opened/advanced/resolved) + section are replaced by `quests`. 
- Narrator prompts (`_SYSTEM_PROMPT` + `_SYSTEM_PROMPT_HYBRID`): replace the `storylines` section description with `quests` (ops surface/advance/resolve; explain: advance/resolve only your ACTIVE/明 quests shown in the明账; surface a 暗 line when the player engages a clue). 
- Migrate existing storyline tests to the quests section/QuestSystem. Migrate any code referencing `world["systems"]["story"]` → `["lore"]`.
- Full suite green (migrations counted, no net loss of coverage). This is the churny task — go carefully; read every storyline reference first (grep `storyline`, `"story"`, `StorySystem`).

### T4: Transitions wired into run_turn
**Files:** Modify `loop/lore.py` (run_lore: world-push surface), `loop/turn.py` (demote-on-leave + JIT-resequence). Test `tests/loop/test_quest_transitions.py`.
- **World-push surface** (in run_lore): a 暗 line whose `stage_idx` reaches a `checkpoint`/爆点 flag (skeleton marks late stages) → emit `quest_surfaced{id, by:"world"}` (it bursts into 明 regardless of player). (For now: a line reaching its LAST stage while 暗 → world-push surface, so it doesn't silently finish off-screen; refine爆点 flags later.)
- **Demote-on-leave** (in run_turn, near the scene/location logic): a 明 line whose anchor town != the protagonist's current town (player left) AND idle ≥ N turns → emit `quest_demoted{id, new_stages}`: if the line materially diverged (clues_dropped beyond its stages), call `jit_resequence` for option-a new stages; apply sets state 明→暗 + replaces remaining stages. (Simple/medium: demote→dormant; complex: demote→keep brewing — both via this op, the difference is whether 暗骰 keeps advancing, which it does for all 暗.)
- `quest_demoted` apply: state→暗, set stages to new_stages (if provided).
- Tests: a 暗 line hitting last stage → world-surfaced; a 明 line, player leaves town → demoted to 暗 (with jit_resequence stages when diverged); demoted line resumes 暗骰.

### T5: Disclosure unification in the strategy
**Files:** Modify `loop/strategy.py`, `loop/lore_disclosure.py`, `systems/lore.py` (inject). Test `tests/loop/test_quest_disclosure.py`.
- The narrator context now always gets: (1) the 明账 (明 quests, from the LoreSystem明账 inject — pushed) + (2) ambient `station_push_fragment` (暗 quests' clues near the player). Both every turn (no A/B mode switch — B won; the tool/A path is dropped from quest disclosure).
- Simplify `AuthorStrategy.produce`: remove the disclosure_mode A/B branching for lore; always append `station_push_fragment` (暗 ambient) to context; the明账 rides the normal inject. (Keep the off path / existing non-quest behavior intact.)
- LoreSystem.inject: now the 明账 (明 lines) — the暗 ambient comes via station_push_fragment appended by the strategy (or also via inject — pick one path, document it). Remove the old "dump 暗 clues" inject (superseded).
- Tests: context carries the明账 for明 quests + ambient clues for暗 quests at the current venue; a 暗 line is NOT presented as advanceable (only its clue); a明 line IS in the明账.

### T6: Complex full-lifecycle glm-5.1 demo
**Files:** Create `docs/superpowers/specs/unified-quest-demo-2026-06-21/demo.py` + README (like lore-AB). 
- A scenario exercising the WHOLE state machine on glm-5.1: seed暗 lines → 暗骰 brews + drops clues (暗态推进 + ambient disclosure) → player follows a clue → `surface` 暗→明 (narrator surfaces it, enters明账) → player advances it a few turns (明态 narrator advance, 明账 updates) → player leaves → demote 明→暗 + JIT-resequence (verify续写) → a complex 暗 line world-push surfaces (爆点炸场) → resolve one by player + one to了结. Dump each step: per-quest state, which channel advanced it, 明账, ambient clues, JIT-resequence before/after. 
- Run; archive script + transcript + findings. This is the "看效果" deliverable.

## Testing emphasis (user: 多测一测)
Every task: full-suite green + the task's own offline tests. After T5: a full offline integration test driving a multi-turn play through the whole state machine with a FakeLLMProvider (deterministic) BEFORE the real-LLM T6 demo. The real-LLM demo is the final confidence check, not the only test.

## Out of scope (later)
- director fold-in (its seeded-thread firing = a world-push surface) — PHASE 2.
- density-generation (seed-on-first-entry, per-tier caps) — later.
- cosmetic lore→quest file/event rename — final mechanical sweep once the model is proven.

## Self-Review
State-partitioned advance channels (T1+T2) structurally fix the pollution bug. Transitions (T4) realize the state machine; option-a JIT-resequence (T1+T4) handles diverged demotion. Disclosure (T5) = the A/B-won B for 暗 + 明账 for 明. Migration (T3) preserves StorySystem behavior. Demo (T6) validates the full lifecycle live. Names: LoreSystem kept internally (rename deferred) — the model/section/behavior is the unification.
