# RPG Engine (`app` branch) — Codebase Index

> Detailed module + call-graph index for fast pickup. **Status: S0–S5 complete, playable v1, 1151 tests green** (`python3 -m pytest -q`; interpreter `python3`, no venv; `fastembed`/`langfuse`/`networkx` NOT installed → offline uses Fakes). Design spec: `docs/2026-06-17-rpg-ultimate-harness-design.md`. Per-phase plans: `docs/superpowers/plans/2026-06-17-s*.md`. Project memory: `/root/.claude/projects/-root/memory/rpg-app-ultimate-harness.md`.

## Layered architecture (dependency DAG, bottom→top, no cycles)
```
engine/*   (reused primitives: log, schema, store, embed, vectorstore, archive, recall, …)
   ▲
kernel/*   (microkernel: ContextSystem contract, Registry, 5 drivers, TurnCommit, observability)
   ▲
facts/*    (bitemporal substrate: Entity, Fact/Relation, FactGraph)
   ▲
systems/*  (6 ContextSystems — write the SHARED FactGraph; require ontology)
   ▲
context/*  (viewpoint bundles + assemble_context)
   ▲
loop/*     (TurnStrategy 甲/乙, run_turn/produce/apply, compare, fleet)
   ▲
app/*      (build_engine, play_loop REPL, CLI __main__)
llm/* + memory/*  (sideways services: provider switchboard; importance/recall-rank/reflection)
```
**The system pattern:** every `systems/*` ContextSystem owns event-types + commit-sections, and in `apply(world, event)` writes the shared `FactGraph` at `world["systems"]["ontology"]`; `empty_state()={}`; `requires()={"ontology"}` (Registry enforces ontology-first). `kernel/` imports NO domain package (purity).

## Modules + key public symbols

### engine/ (16 files, ~1450 LOC — reused from `skill` branch)
- `log.py` — `configure_logging()`, `get_logger(name)` → `rpg.<name>` logger. (Everything depends on this.)
- `schema.py` — `EVENT_TYPES` (legacy closed set), `make_event()`, `validate_event(ev, allowed_types=None)` (the `allowed_types` override is what lets the kernel use decentralized event-types).
- `store.py` — `EventStore(db, jsonl, allowed_types=None)`: `append(ev)→seq`, `iter_events(include_retracted=False)`, `retract_from_seq/turn`, `sync_jsonl`.
- `embed.py` — `get_embedder()` → `FakeEmbedder` (offline, deterministic) / `FastEmbedEmbedder` (bge-small-zh, needs fastembed). `embed(texts)→vecs`.
- `vectorstore.py` — numpy-cosine `VectorStore`. `recall.py`/`archive.py` — FTS5 verbatim recall (skill-era). `projection.py` — LEGACY monolithic `project/apply` (superseded by `kernel.projection` for the app; still used by `engine.cli/compact`). `compact/rewind/oracle/director/seed/check/cli` — skill-era, mostly unused by the new app but available.

### kernel/ (11 files, ~390 LOC — the microkernel, game-logic-free)
- `contextsystem.py` — `ContextSystem` ABC: `event_types()/commit_sections()/empty_state()/apply(world,event)/validate(section,decl,world)/to_events(section,decl,*,turn,day,scene)/inject(scene,world)/recall(query,world)/digest_extract(prose,world)/requires()`. Dataclasses `ValidationError(section,field,code,hint)`, `Fragment(system,layer,text,affordance)`, `RecallHit(system,score,text,ref)`.
- `turncommit.py` — `TurnCommit{narration, sections}` + `from_dict/to_dict`. (`narration` is a RESERVED section name.)
- `registry.py` — `Registry`: `register(system)` (rejects event/section collisions, reserves `narration`, enforces `requires()`), `owner_of_event/owner_of_section`, `event_types()`, `systems`.
- `events.py` — `kernel_event(type, *, day, scene, summary, actors=…, deltas=…, turn=…, …)` (kw-only); `open_store(db, jsonl, allowed_types)`.
- `projection.py` — `empty_world(registry)` → `{"meta":{day,scene,timeline}, "systems":{name:slice}}`; `project(registry, events)` (fold: per-event `owner.apply(world, ev)`, skip retracted/unowned).
- `validation.py` — `validate_commit(registry, commit, world)→[ValidationError]` (dispatch each section to owner; unowned→error); `build_repair_request(errors)→str`.
- `assembler.py` — `assemble(registry, scene, world)→[Fragment]` (layer-sorted stable<scene<volatile); `render(frags)→str`.
- `recall.py` — `recall(registry, query, world, k=None)→[RecallHit]` (fan-out + score desc).
- `digest.py` — `digest_extract(registry, prose, world)→TurnCommit` (fan-out merge).
- `observability.py` — `get_tracer()`→`LangfuseTracer`(if LANGFUSE_PUBLIC_KEY)|`NoopTracer`; `dump(label, payload)` (RPG_DEBUG).

### facts/ (4 files, ~200 LOC — bitemporal substrate)
- `entity.py` — `Entity(id, etype, tier['tracked|mentioned|retired'], attrs)`.
- `fact.py` — `Fact(subject, predicate, value, event_time_start, ingest_turn, source_event, event_time_end=None, secrecy=None)` + `Relation(src, rel, dst, …, event_time_end=None, attrs={})`; both `is_current()`/`valid_at(day)` (half-open `[start,end)`).
- `graph.py` — `FactGraph`: `add_entity/get_entity/set_tier`; `assert_fact(subject, predicate, value, *, day, turn, source_event, secrecy=)` (predicate-scoped supersession; **raises ValueError on non-monotonic day** — precondition: apply events in non-decreasing day); `current_facts(subject)/value_at(subject,predicate,day)/fact_history`; `add_relation(src, rel, dst, *, day, turn, source_event, supersede=True, **attrs)` (supersede=True → per-`(src,rel)`; supersede=False → per-`(src,rel,dst)` dedup, multi-valued); `relations_at/neighbors/relation_attrs_at`.

### systems/ (7 files, ~1545 LOC — 6 ContextSystems on the shared graph)
- `ontology.py` `OntologySystem` — slice IS the shared `FactGraph`. events: `entity_created/fact_asserted/relation_added/tier_changed`; sections: `entities/facts/relations`.
- `place.py` `PlaceSystem` — Place entities (attrs level/kind/seed/detail); `contained_by`/`adjacent_to`(travel_cost,multi)/`located_in`(single). events: `place_created/place_linked/place_materialized/entity_moved`; sections: `places/moves/links/materialize`. Module fn `navigate(graph, src, dst, day)` (Dijkstra over adjacent_to). `inject` = current location + exits.
- `character.py` `CharacterSystem` — Persons; card = bitemporal facts `sketch`(req)/`goal`(req)/`past`(opt)/`hidden`(opt) + evolving `mood`/`trust:{x}`. events: `character_created/character_evolved/relationship_changed`; section: `cast` (items have `op` create|evolve|relationship). validate blocks reserved-prefix predicates (`knows/rank/group/trust`). `inject` = present-character current cards.
- `object.py` `ObjectSystem` — Object entities; `held_by` (single). events: `object_created/item_transferred`; section `items`. `inject` = protagonist inventory.
- `faction.py` `FactionSystem` — Faction entities (attrs ranks/groups); `member_of`(multi) + `rank:{F}`/`group:{F}` facts. events: `faction_created/member_changed`; section `factions`. Module fns `members_of(graph, faction, day, *, min_rank=, group=)`, `member_rank(graph, person, faction, day)`.
- `knowledge.py` `KnowledgeSystem` — knows-as-facts `knows:{fact_key}`. events: `knowledge_set` (told/endowment), `knowledge_broadcast` (apply resolves audience via `members_of`/place-occupants); section `knowledge`. Module fns `knows(graph, knower, fact_key, day)`, `knowers_of(graph, fact_key, day)`.

### llm/ (2 files — provider switchboard)
- `provider.py` — `LLMProvider` ABC (`complete(system,user,*,model,max_tokens)→str`, `complete_json(system,user,schema,**kw)→dict` retry-on-bad-json). `FakeLLMProvider(responses=[], json_responses=[])` (cycled, offline). `OpenAIProvider`/`ZhipuProvider`/`AnthropicProvider` (stdlib `urllib`, each has unit-tested `_build_request()`; ZhipuProvider DEFAULT_BASE_URL=`https://open.bigmodel.cn/api/paas/v4`, Bearer auth, POST `/chat/completions`). `make_provider(kind, *, model, base_url=None, api_key=None)` (`_ENV_KEY_MAP`: zhipu→ZHIPU_API_KEY etc.).

### memory/ (4 files — backstage cognition, inject provider/embedder)
- `importance.py` — `heuristic_floor(event)→int`, `score(event, *, provider=None)→int` (max heuristic & LLM rubric 1–10).
- `recall.py` — `rank(candidates, query_vec, *, now_day, embedder=None)→[(cand,score)]` (recency×importance×relevance, W_REC=0.35/W_IMP=0.35/W_REL=0.30); `embed_query(text, embedder)` (reuses `engine.embed`).
- `reflection.py` — `should_reflect(accumulated, *, threshold=30)→bool`; `reflect(subject, recent_events, *, provider)→{"predicate":"arc","value":...}`.

### context/ (3 files — assembler + POV/guardrail)
- `viewpoint.py` — `build_viewpoint(graph, *, protagonist, present, day, candidate_fact_keys)→{pov, guardrail, npc}` (pov=protagonist-known; guardrail=unknown-but-true; npc=per-present-NPC known; uses `systems.knowledge.knows`).
- `assembler.py` — `assemble_context(registry, world, scene, *, query=None, embedder=None, k=6)→str` (kernel `assemble`/`render` fragments + ranked recall via `kernel.recall`+`memory.recall.rank` + viewpoint, in stable→scene→volatile order; candidate fact_keys = `knows:` predicates in graph).

### loop/ (5 files — the agent loop)
- `strategy.py` — `TurnStrategy` ABC `produce(registry, world, scene, player_input, *, provider, embedder=None, repair=None)→TurnCommit`; `AuthorStrategy(甲)` (1 `complete_json` over assemble_context); `ExtractStrategy(乙)` (1 `complete` prose → 1 `complete_json` 史官 extract; narration forced to prose); `TURNCOMMIT_SCHEMA`.
- `turn.py` — `produce_turn(...)→(commit, attempts, dropped)` (validate+repair N=3, NO store write); `apply_turn(registry, store, commit, *, day, scene)→world` (to_events→append→project); `run_turn(...)→TurnResult{narration, world, commit, events, repair_attempts, dropped_sections}`.
- `compare.py` — `run_compare(registry, world, scene, input, *, provider, embedder=None)→{"甲":(commit,attempts,dropped), "乙":...}` (both on same snapshot, neither applied).
- `fleet.py` — `digest_fleet(registry, store, new_events, world, *, provider, threshold=30)` (importance.score per new event → accumulate per subject → reflection.reflect → append `character_evolved` arc event).

### app/ (4 files — CLI v1)
- `engine.py` — `build_engine(campaign_dir, *, provider=None, embedder=None)→Engine{registry, store, provider, embedder, world}` (registers all 6 systems ontology-first, `open_store(registry.event_types())`, provider via arg/`make_provider`, embedder via `engine.embed`); `new_game(engine)` genesis (place_created → character_created protagonist → entity_moved; events appended in that order to dodge the cross-section limitation).
- `play.py` — `play_loop(engine, inputs, *, out=print, strategy=None, compare=False, transcript_path=None)`; `dispatch_ooc` (`/quit`,`/recall`,`/compare on|off`,`/help`); `_build_scene(engine)`; writes a per-turn JSONL transcript (both 甲/乙 candidates in compare mode).
- `__main__.py` — `main(argv, *, inputs=None, out=print, provider=None)` (argparse `--campaign/--provider{fake,openai,zhipu,anthropic}/--model/--base-url/--compare/--transcript`; `configure_logging()`; build_engine; new_game if empty; play_loop). Run: `python3 -m app --campaign DIR --provider zhipu --model glm-5.1 --base-url … --compare` (also `run.sh` wraps this for GLM/zai; key from `.env.local`).

## Runtime flow — one turn (strategy 甲)
`play_loop` → `_build_scene(engine)` → `run_turn` = `produce_turn`[ `AuthorStrategy.produce` → `assemble_context` (kernel `assemble`/`render` + `memory.recall.rank` + `build_viewpoint`) → `provider.complete_json` → `TurnCommit`; `validate_commit`; if errors → `build_repair_request` → re-`produce(repair=)` up to N=3; then drop still-failing sections ] + `apply_turn`[ per-section `owner.to_events` → `store.append` → `kernel.projection.project` ] → `TurnResult.narration` printed + transcript row. **Backstage** (`loop.fleet.digest_fleet`, post-commit): `importance.score` new events → accumulate → `reflection.reflect` on threshold → append `character_evolved` arc event. **乙/compare:** `run_compare` runs both `produce_turn`s on the same snapshot (no apply); caller `apply_turn`s the chosen.

## Invariants & conventions
Event log = single truth (`project()` pure fold). Systems write the shared graph via `apply(world,…)`; `kernel/` imports no domain pkg. Bitemporal supersession (non-decreasing `day` precondition; FactGraph raises otherwise). validate = 机械处严 (refs/required/enum: `dangling_ref/missing/bad_enum/reserved`) 创作处松 (never require richness — anti-脸谱). Reserved predicate prefixes `knows/rank/group/trust`. Cache-layered context (stable/scene/volatile). Protagonist-POV is the red line; unknown-but-true facts are guardrail-only; god-truth to player is OOC-only (no dramatic-irony). Backstage uses cheap models; narrator uses strong. Every module logs via `engine.log.get_logger`. Tests offline with Fakes (`FakeLLMProvider`/`FakeEmbedder`); no network/SDKs/pip in tests.

## Polish backlog (not yet done)
Cross-section validation (one commit create+reference, currently needs create-then-act across turns); per-system `digest_extract` (richer 乙; currently a single central 史官 `complete_json`); faction `inject`; OOC `/oops` rewind via `engine.rewind`; real-model (fastembed+live provider) integration test; **final holistic review of S2–S5** (S0/S1 already opus-reviewed); requirements tidy (numpy/fastembed currently in `requirements-dev.txt`).

> Regenerate the pydeps/pyan3/ctags artifacts in this dir via `bash docs/codegraph/gen.sh` (now covers all packages). The authoritative dependency edges are the AST scan reproduced under `## Layered architecture` + each module's deps in `engine_deps.dot`.
