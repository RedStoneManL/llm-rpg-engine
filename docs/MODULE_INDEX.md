# Module Index — llm-rpg-engine (app branch)

A navigation map of the codebase: every top-level module, its responsibility, key
files, public symbols, and cross-module dependencies. Plus the two runtime flows
(genesis + per-turn) and a "where do I fix X" quick reference.

> Generated 2026-07-01 from a full scan. Keep it current when modules move.

---

## 1. Architecture in one screen

```
                       ┌─────────────────────────────────────────────┐
   player ──CLI──────► │ app/      CLI · genesis wiring · play loop   │
                       └───────────────┬─────────────────────────────┘
                                       │ drives
   ┌───────────────────────────────────▼──────────────────────────────────┐
   │ loop/     per-turn pipeline + genesis + backstage 暗骰 hooks           │
   │   produce → augment → validate/repair → commit_to_thread → to_events  │
   │            → project → (digest·director·cascade·catchup·lore·density) │
   └───┬───────────────┬───────────────┬───────────────┬──────────────────┘
       │ assembles     │ calls          │ validates     │ folds events
   ┌───▼─────┐   ┌─────▼──────┐   ┌─────▼──────┐   ┌─────▼───────────────────┐
   │ context/│   │ llm/       │   │ kernel/    │   │ systems/  (ContextSystem)│
   │ fog ctx │   │ provider + │   │ microkernel│◄──┤ ontology·place·character │
   │ + recall│   │ POV tools  │   │ projection │   │ ·object·faction·knowledge│
   └───┬─────┘   │ + struct.  │   │ validation │   │ ·director·cascade·time   │
       │         └─────┬──────┘   │ registry   │   │ ·narrative·scene·lore    │
       │               │          │ events·clock│  │ ·codex                   │
       │               │          └─────┬──────┘   └─────┬───────────────────┘
       │               │                │ defines        │ writes facts to
       │               │          ┌─────▼────────────────▼──────┐
       │               └─────────►│ facts/   bitemporal FactGraph│ (the ontology)
       │                          └──────────────────────────────┘
   ┌───▼─────────┐  ┌──────────────────────────┐  ┌───────────────────────────┐
   │ memory/     │  │ engine/  oracle·settings· │  │ data/oracles/  seed tables│
   │ importance· │  │ log·store·embed·recall··· │  │ (genesis + default)       │
   │ recall·refl.│  │ (+ a legacy standalone CLI│  └───────────────────────────┘
   └─────────────┘  │  layer — see §Audit flags)│
                    └───────────────────────────┘
```

**Dependency direction (lower = more foundational; arrows point "depends on"):**
`app/` → `loop/` → {`context/`, `llm/`, `systems/`} → `kernel/` → `facts/`
with `engine/` (infra: oracle/settings/log/store) and `memory/` and `data/` as
leaf utilities everything draws on. `kernel/` and `facts/` depend on nothing but
`engine/log`. No cycles across top-level dirs.

---

## 2. Module-by-module

### `facts/` — the bitemporal ontology (foundation, zero deps)
Stores all entities + time-scoped facts + relations; point-in-time queries.
- `facts/entity.py` — `Entity(id, etype, tier, attrs)`. etype∈Person/Place/Object/Faction; tier∈tracked/mentioned/retired.
- `facts/fact.py` — `Fact(subject,predicate,value,…,secrecy)` + `Relation(src,rel,dst,…,attrs)`; `valid_at(day)`, `is_current()`. **secrecy** is the fog-gating field.
- `facts/graph.py` — `FactGraph`: `add_entity`, `assert_fact` (predicate-scoped supersession), `add_relation` (single- or multi-valued), `current_facts`, `value_at`, `neighbors`, `relations_at`. Monotonic-time; raises on out-of-order.

### `kernel/` — the microkernel (deps: `engine/log` only)
Defines the pluggable-system contract + event-sourcing core; knows nothing about RPGs.
- `kernel/contextsystem.py` — **`ContextSystem` ABC** (event_types/commit_sections/requires/empty_state/apply/validate/to_events/created_ids/inject/recall/digest_extract) + `Fragment`, `RecallHit`, `ValidationError`.
- `kernel/registry.py` — `Registry`: 1:1 event-type & commit-section → owner routing.
- `kernel/events.py` — `kernel_event(...)` factory + `open_store(...)` (wraps `engine.store.EventStore`).
- `kernel/projection.py` — `empty_world(registry)`, **`project(registry, events)`** — pure fold of events through every system's `apply`.
- `kernel/validation.py` — **`validate_commit(registry, commit, world, *, required_sections)`** + `build_repair_request`. (I1 lives here: clock-required, bare `[]` ok.)
- `kernel/assembler.py` — `assemble(registry, scene, world)` → `[Fragment]` + `render` (layered: stable/scene/volatile).
- `kernel/recall.py` — `recall(registry, query, world)` — fan-out to each system's `recall`.
- `kernel/digest.py` — `digest_extract(registry, prose, world)` — fan-out for strategy 乙.
- `kernel/clock.py` — pure (day, band 0..3) ↔ scalar arithmetic; `band_name`, `advance`, `expired`.
- `kernel/turncommit.py` — `TurnCommit(narration, sections, reasons)` + from_dict/to_dict.
- `kernel/observability.py` — `get_tracer()` seam: Noop / Langfuse / DebugTracer (the `--debug` trace).

### `systems/` — the ContextSystems (deps: `kernel/`, `facts/`, `engine/log`)
One file per pluggable domain; registered in `app/engine.py:build_engine`. State lives in `world["systems"][name]`.
| File | Domain | Owns events | Narrator section |
|---|---|---|---|
| `ontology.py` | the FactGraph hub | entity_created, fact_asserted, relation_added, tier_changed | entities/facts/relations |
| `place.py` | 3-tier map + travel + containment | place_created/linked/materialized, entity_moved | places/moves/links/materialize |
| `character.py` | Person sketch/goal/hidden + trust | character_created/evolved, relationship_changed | cast |
| `object.py` | items + possession | object_created, item_transferred | items |
| `faction.py` | membership + ranks/groups | faction_created, member_changed | factions |
| `knowledge.py` | who-knows-what (`knows:{key}` facts) | knowledge_set, knowledge_broadcast | knowledge |
| `time.py` | day/band progression | time_advanced, clock_advanced | clock |
| `lore.py` | quest lifecycle 暗→明→了结 | lore_created/advanced, quest_* (open/surface/advance/resolve/expired/finale_due/world_resolved/catastrophe), lore_seeded, density_refreshed | quests |
| `director.py` | 暗骰 directives + threads | campaign_seeded, oracle_roll, director_fired, thread_* | *(harness)* |
| `cascade.py` | world-level ripple | place_evolved, populace_shifted, world_change | world |
| `narrative.py` | recency-tiered recap | narration_recorded, scene_summarized, recap_recompressed | *(harness)* |
| `scene.py` | scene-boundary tracking | scene_advanced | *(harness)* |
| `codex.py` | world-setting text blocks (世界设定典) | codex_entry_added | *(harness)* |
Public helpers used elsewhere: `place.navigate`, `faction.members_of/member_rank`, `knowledge.knows/knowers_of`, `narrative.aged_out_scene`, `codex.codex_entries`. **ontology** owns the shared graph; all entity systems write into it. `requires()` enforces ontology-first ordering.

### `llm/` — provider seam + POV fog tools + structured output
- `llm/provider.py` — `LLMProvider` ABC + `OpenAI/Zhipu/Anthropic/Fake/ScriptedTool` adapters; `complete_messages`/`complete_with_tools`/`supports_tools`; **`last_usage`** (the 70%-compaction signal); `_do_post` (600s timeout + 429/5xx backoff + Retry-After); `_parse_json_object` (tolerates fences/prose). Stdlib urllib only.
- `llm/tools.py` — **`build_tool_registry(registry, world, scene, *, dm=False)`** → the narrator's read-only POV tools: `map_query/recall_query/characters_query/factions_query/ambient_query/codex_query` (+ `dm_world_query` when dm). **Fog model**: POV (per-agent `knows()`) · ambient (secrecy=="public" only) · DM (ground truth). (I2 lives here: characters_query matches id/真名/sketch + any-tier co-presence.)
- `llm/structured.py` — `complete_structured(provider, …, validate, max_repairs)` — JSON-conformance repair loop for non-turn calls (genesis gen_*, cascade verdicts, catch-up).

### `loop/` — orchestration: per-turn + genesis + backstage
**Per-turn authoring:**
- `loop/turn.py` — **`run_turn`** (the whole turn) → `produce_turn` (produce+augment+validate+repair) + `apply_turn` (to_events+project) + all backstage hooks. `REQUIRED_SECTIONS`. `TurnResult`.
- `loop/strategy.py` — `TurnStrategy` ABC + **`AuthorStrategy` (甲)** + `HybridStrategy` (丙). Holds the **multi-turn thread + 70% compaction** (`_thread`/`_messages`/`_build_delta`/`commit_to_thread`/`_maybe_flag_compaction`, `CONTEXT_WINDOW=200_000`). The **DM system prompts** (`_SYSTEM_PROMPT_TEMPLATE` 甲, `_SYSTEM_PROMPT_HYBRID` 丙, `_NARRATE_PROMPT_TEMPLATE`).
- `loop/entity_resolve.py` — `augment_unresolved_refs` — resolve name-refs → ids, mint walk-on placeholders, dedup to co-located NPCs (I3).
- `loop/lore_disclosure.py` — `station_push_fragment` — PUSH ambient 暗线 into the prompt (no tool call) + `_l2_ancestor`.

**Genesis (`bootstrap_world`):**
- `loop/bootstrap.py` — orchestrator: `bootstrap_world` (sequential steps 1–9 + protagonist creation), `reroll_all/reroll_step`, `_build_world_summary`. Re-exports the `gen_*` for back-compat.
- `loop/genesis/` — per-step content generators (split out of bootstrap.py). `common.py` (`_draw_distinct/_empty_str`); `world.py` (`gen_frame/gen_regions/gen_local_map` + `_roll_world_seeds`); `cast.py` (`gen_protagonist/gen_factions/gen_npcs` + `_roll_protagonist_seeds/_protagonist_seed_block`); `lore.py` (`gen_codex/gen_threads/gen_opening` + thread tables). Oracle-seed + `provided=` override per step. (P1/P2/P3 seeds + codex authoring here.)
- `loop/genesis_spec.py` — pure `normalize/merge/missing_required` (the canonical GenesisSpec + required floor).
- `loop/genesis_blueprint.py` — `load_blueprint` (file → spec).
- `loop/import_sillytavern.py` — `convert_sillytavern` (LLM-translate 酒馆 cards/world-books → spec).

**Backstage hooks (per-turn, hidden, seeded, non-fatal — invoked by `run_turn`):**
- `loop/fleet.py` — `digest_fleet` (importance score → arc reflection, narration record, scene summary, quest backstop).
- `loop/director.py` — `run_director` (pacing → 暗骰 fire). Uses `engine/director.py` math.
- `loop/cascade.py` — `run_cascade` (region→sub-place BFS ripple). **ThreadPoolExecutor**, `RPG_CASCADE_CONCURRENCY` (429 fix → 2).
- `loop/time.py` — `run_catchup` (off-screen entities catch up on scene entry) + time-jump detect.
- `loop/lore.py` — `run_lore` (暗骰 advance lines; expiry/finale; **pthread_* exempt** = I5) + `create_lore_line`, `fetch_lore`, `jit_resequence`.
- `loop/density.py` — `run_density` (per-town density → generate new lines) + `roll_complexity`, `generate_lore_batch`.
- `loop/endgame.py` — pure math for complex-line finale: `roll_world_rescue`, `build_catastrophe_events`.
- `loop/graph_utils.py` — `ancestor_of_level` (walk containment).
- `loop/compare.py` — `run_compare` (甲/丙 dual-strategy A/B).

### `context/` — fog-filtered context assembly
- `context/assembler.py` — **`assemble_context(registry, world, scene, query, embedder, k)`** — the narrator's full context: calls `kernel.assemble` (system injects) + `context.viewpoint` (POV/guardrail/NPC) + ranked recall (`memory.recall`). Tiers: aged-summaries(stable) → injects(scene) → viewpoint(scene) → recall(volatile).
- `context/viewpoint.py` — `build_viewpoint(graph, protagonist, present, day, …)` → {pov, guardrail, npc} (what protagonist knows vs the secrets to protect vs each NPC's knowledge).

### `engine/` — infrastructure (oracle, settings, log, store) + a legacy CLI layer
**Active (used by app/loop/kernel):**
- `engine/oracle.py` — `Oracle` (d100/draw/randint/random), `load_table(name, genre)` (genesis vs default), `scene_seed` (deterministic, rewind-safe).
- `engine/settings.py` — process-global runtime config: `get/set_verbosity·max_tool_rounds·style·conversation_mode` + `reset_from_env` (env: RPG_NARRATION_VERBOSITY/MAX_TOOL_ROUNDS/NARRATION_STYLE/CONVERSATION_MODE).
- `engine/log.py` — `get_logger`, `configure_logging`.
- `engine/store.py` — `EventStore` (SQLite + JSONL append-only; retract/rewind).
- `engine/embed.py` — `FakeEmbedder` / `FastEmbedEmbedder` / `get_embedder` (RPG_EMBEDDER).
- `engine/archive.py`, `engine/vectorstore.py`, `engine/recall.py`, `engine/rewind.py` — verbatim narrative archive (FTS5) + vector store + `/recall` + `/rewind` infra.
- `engine/director.py` — pacing/thread-scheduling math (used by `loop/director.py`).
- `engine/schema.py` — legacy event-type set + `make_event/validate_event`.
> ℹ️ **Hermes-skill CLI/hooks backend (live via `bin/` + `hooks/`, NOT via app/loop/kernel):** `engine/projection.py`, `engine/seed.py`, `engine/check.py`, `engine/compact.py`, `engine/cli.py` — the pre-kernel "working-memory" projection + standalone CLI. **Not dead:** `bin/rpg` imports `engine.cli`; `hooks/pre_llm_call` imports `engine.compact.build_working_memory` (covered by `tests/test_cli.py` + `tests/test_hook_pre_llm.py`). Removable only as a unit *with* `bin/` + `hooks/` if the hermes integration is retired — see §4.

### `memory/` — scoring + ranking + reflection (deps: `engine/`)
- `memory/importance.py` — `score(event, provider)` (heuristic floor + LLM rubric 1–10).
- `memory/recall.py` — `rank(candidates, query_vec, now_day, embedder)` (recency × importance × relevance).
- `memory/reflection.py` — `reflect(subject, recent_events, provider)` (arc synthesis above a threshold).

### `app/` — CLI + wiring + play loop
- `app/__main__.py` — CLI flags (`--campaign/--provider/--model/--pitch/--genesis/--import-*/--style/--verbosity/--max-tool-rounds/--max-repairs/--debug`); pitch resolution; `resolve_genesis_spec` → `new_game` → intro/recap → reroll loop → `play_loop`.
- `app/engine.py` — **`build_engine`** (registers the 13 systems, opens store, resolves provider + optional `cascade_provider` via RPG_CASCADE_MODEL), `Engine`, `new_game`, `resolve_genesis_spec`, `_PROTAGONIST_ID`.
- `app/play.py` — **`play_loop`** + `_build_scene` (protagonist=first tracked Person; present=co-located tracked) + OOC dispatch (`/recall·/rewind·/undo·/compare·/verbosity·/style·/help·/quit`) + transcript.
- `app/session_zero.py` — `run_session_zero` (interactive required-floor gate: premise + protagonist name, or `/auto`).
- `app/trace.py` — `python -m app.trace` debug-trace viewer (`--stats/--turn/--phase/--show/--grep/--tree`).

### `data/oracles/` — deterministic seed tables
- `default/` — play-loop tables (event_types, npc_roles, npc_traits, thread_archetypes, twists, world_frames).
- `genesis/` — genesis tables: tone_axes, terrains, place_kinds, thread_types, npc_roles, npc_traits, **protagonist_origins/hooks/quirks** (P1), **world_magic/power/tension** (P2).

---

## 3. The two runtime flows (with file refs)

### Genesis — `loop/bootstrap.py:bootstrap_world` (one-off, ~8–9 sequential LLM calls)
`campaign_seeded` → `gen_frame`(P2 world dims) → `gen_regions` → `gen_local_map` →
`gen_protagonist`(P1 seeds) → create protagonist → `gen_factions` → `gen_npcs` →
`gen_codex`(P3) → `gen_threads`(暗线, pthread_*) → `gen_opening` → project.
Each step: `Oracle(scene_seed(seed, "genesis:<step>", attempt))` + `provided=` override.
Bulk steps (regions/factions/npcs/codex) use the fast `cascade_provider` when set (I9).

### Per turn — `loop/turn.py:run_turn`
1. **assemble** (`context/assembler.py`, or `strategy._build_delta` in multiturn) + `station_push_fragment`
2. **produce** (`loop/strategy.py` `AuthorStrategy.produce`) → tool loop (`llm/tools.py`) → narration + commit
3. **augment** (`loop/entity_resolve.py`)
4. **validate → repair** (`kernel/validation.py` + `strategy.repair_sections`)
5. **commit_to_thread** (multiturn)
6. **to_events → project** (`kernel/projection.py`)
7. **backstage**: `digest_fleet` · `run_director` · `run_cascade` · `run_catchup` · `run_lore` · demote-on-leave · `run_density` · scene/clock
8. display narration

---

## 4. "Where do I fix X?" quick map
| Symptom / change | Go to |
|---|---|
| Mode-collapsed / boring genesis content | `loop/genesis/` (gen_*) + `data/oracles/genesis/` |
| DM prose / narration style / commit format | `loop/strategy.py` (`_SYSTEM_PROMPT_*`) |
| "missing field" / repair loops / required sections | `kernel/validation.py` + `loop/turn.py` |
| What the narrator can see / fog leak / a POV tool | `llm/tools.py` + `systems/knowledge.py` |
| Duplicate NPCs / auto-created entities | `loop/entity_resolve.py` |
| Who's "present" / current location | `app/play.py` `_build_scene` |
| A world domain's rules (places, factions, items…) | `systems/<domain>.py` |
| 暗线 / quest pacing, surfacing, resolution | `systems/lore.py` + `loop/lore.py` + `loop/density.py` + `loop/endgame.py` |
| World events rippling to sub-places | `loop/cascade.py` + `systems/cascade.py` |
| Multi-turn conversation / compaction | `loop/strategy.py` |
| API / models / retries / 429 / usage | `llm/provider.py` + `app/engine.py` + `engine/settings.py` |
| What's in the prompt context | `context/assembler.py` + `context/viewpoint.py` |
| Randomness / determinism / oracle tables | `engine/oracle.py` + `data/oracles/` |
| Env knobs / runtime settings | `engine/settings.py` |
| CLI flags / startup | `app/__main__.py` + `app/engine.py` |
| Debug trace | `app/trace.py` + `kernel/observability.py` |
| Entities / facts / relations storage | `facts/graph.py` |
| Codex / world setting | `systems/codex.py` + `loop/genesis/lore.py` (gen_codex) + `llm/tools.py` (codex_query) |
| Recent live-play fixes (I1–I9) | `docs/playtest-issues-2026-06-30.md` |

> **Audit flags to chase next:** the `engine/{projection,seed,check,compact,cli}.py`
> layer is the live hermes-skill CLI/hooks backend (`bin/` + `hooks/`), **not** dead —
> the open question is whether the hermes integration is still wanted; if retired, drop
> the layer together with `bin/` + `hooks/` + their two tests as one unit. Genuine
> drift-checks: double `assemble` (kernel vs context); three `recall`s at different
> layers (kernel/engine/memory) — confirm they're intentional tiers, not drift.
