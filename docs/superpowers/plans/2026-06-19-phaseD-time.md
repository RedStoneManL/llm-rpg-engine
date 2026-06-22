# Phase D — 时间模型 (lazy drift / tracked catch-up) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or superpowers:executing-plans for a separate session). Every task is TDD: write the REAL failing test first, run it, see it FAIL, write the minimal REAL implementation, run it, see it PASS, run the full suite, then commit exactly the files the task names. No placeholders, no stubbed-out bodies, no "fill this in later". If a step's behavior is unclear, re-read the cited source file BEFORE writing code.

**Goal:** Give the engine a real sense of elapsed time **without a global tick** (§14). Two pieces: (1) game time can *advance* — an event may carry the new `day`, so `meta.day` climbs and a multi-day **jump** ("三天后…") is detectable; (2) a tracked entity's drift is derived **lazily**: it is NOT updated when time passes — only when it next **enters scope** after a jump does ONE cheap-model catch-up run derive how it changed over the elapsed span and emit a catch-up event. Entities that never come back into scope keep their old `last_update` forever (§14 断点3: "只对下一轮进入 scope 的 tracked 追,其余留 last_update 等再相关;不'跑所有 tracked'").

**Architecture:** Phase D adds NO new `ContextSystem` slice. It reuses the existing `entity.attrs` stamp pattern (the precedent is `last_cascade_turn`, set in `systems/cascade.py::CascadeSystem.apply`): every place/character `apply()` stamps `last_update = event["day"]` on the entity it touches, deterministically rebuilt on every `project()` (rewind-safe — Phase E replays events and the stamp re-derives). Time advance is opt-in per event via an existing field: `entity_moved` (and any event) may carry `deltas["arrive_day"]`; `to_events` in `systems/place.py` stamps the event's `day` from that when present, otherwise from `scene.day` (today's behavior, unchanged). A new post-apply hook `loop/time.py::run_catchup` mirrors `loop/cascade.py::run_cascade`: it detects a day-jump from the event stream, computes which tracked entities **enter scope** next turn, and for each entering-scope entity whose `last_update < now` runs ONE cheap LLM call to derive its drift, lightweight-validates the verdict (referential, drop-on-fail, NO repair loop), and appends a `character_evolved` / `place_evolved` catch-up event through the strict store. It is wired into `run_turn` **AFTER `run_cascade`** (§14: "先链式下沉,再补在场/将进场 tracked"), inside a tracer span, non-fatal, re-projecting on append.

**Tech Stack:** Python 3.12 stdlib only (no new deps). Reuses S0 kernel (`Registry`/`project`/`kernel_event`/`EventStore`), S1 `facts/FactGraph` (`value_at`/`assert_fact`, bitemporal), `systems/place.py` + `systems/character.py` (entity attrs + the `character_evolved`/`place_evolved` event paths already in place), the `loop/cascade.py` + `loop/director.py` hook patterns, `app/play.py::_build_scene` (scope = `present`), and `llm/provider.py` (synchronous urllib). Tests are offline + deterministic with `FakeLLMProvider` / a keyed fake (the `KeyedFakeProvider` idiom from `tests/loop/test_cascade_loop.py`). NO network in any test.

---

## Design decisions (load-bearing — referenced by tasks)

These answer the six required design questions. Each task below cites the decision number it implements.

> ### ⭐ CONTEXT THE PLANNER VERIFIED IN THE CODE (authoritative — read before coding)
> - **Time does NOT advance today.** `kernel/projection.py` sets `world["meta"]["day"] = ev["day"]` for every folded event; `app/play.py::_build_scene` reads `day = meta.get("day") or 1`. NOTHING ever emits an event whose `day` exceeds the last one (all genesis events + every `apply_turn` event take `day = scene["day"]`, and `scene["day"]` is just `meta.day`). So **the campaign is frozen on day 1**. The LLM does NOT control `day` — `TurnCommit` is `narration` + `sections` only (`kernel/turncommit.py`), and the day is stamped by the harness in `loop/turn.py::apply_turn(... day=scene.get("day",1) ...)`. Phase D's FIRST job is therefore to give time a way to advance at all; jump *detection* is trivial once it can.
> - **`last_cascade_turn` is the stamp precedent.** `systems/cascade.py::CascadeSystem.apply` does `entity.attrs["last_cascade_turn"] = turn` on every `place_evolved` (tested at `tests/systems/test_cascade_system.py:59`). `last_update` copies this pattern exactly — an `entity.attrs` value set in `apply()`, so it is recomputed from scratch on every `project()` and survives rewind for free. NO separate fact, NO new slice.
> - **Drift events already exist.** `character_evolved` (predicate/value, `systems/character.py`) and `place_evolved` (state/note, `systems/cascade.py`) are the catch-up event types. Phase D introduces NO new drift event type — catch-up reuses these. (One tiny new bookkeeping event type `time_advanced` is added only so a pure "time passes, nobody acts" turn can carry the day forward through the gate — see D1.)
> - **Cascade ignores Person drift.** `loop/cascade.py::cascade_trigger` only roots on Place events with `heuristic_floor >= CASCADE_FLOOR(=2)` and `_HARNESS_TYPES` excludes `character_evolved`. So a Person catch-up will NOT re-trigger a cascade. A `place_evolved` catch-up is already a harness type and also will not re-trigger. Confirmed safe — see D4.
> - **Scope = `scene["present"]`.** `_build_scene` builds `present` = all tracked `Person`s except the protagonist; the protagonist is `scene["protagonist"]`. "In scope this turn" = `{protagonist} ∪ set(present)`. See D3 for the precise "enters scope" definition.

### D1 — How time advances + how a JUMP is detected — **opt-in `arrive_day` on the move/scene; new `now`; jump = Δ≥2; new `time_advanced` carrier event**

**Recommendation.** Keep the existing `day` flow; do NOT add a ticker (§14 forbids). Make time advance *opt-in and explicit*, driven by the narrator's already-existing movement/scene decisions:

1. **`now` (current day)** = `world["meta"]["day"]` (already maintained by projection). A helper `loop/time.py::current_day(world) -> int` returns it (default 1).
2. **Advancing `day`.** Two real, minimal paths — no new LLM contract:
   - **Travel cost (primary).** `systems/place.py` already models `adjacent_to` edges carrying `travel_cost` (in days). When the narrator emits a `moves` section, it MAY include `arrive_day` on the move item (the day the traveller arrives). `PlaceSystem.to_events` for the `moves` section stamps the emitted `entity_moved` event's `day` = `max(arrive_day, scene_day)` when `arrive_day` is present, else `scene_day` (today's behavior — unchanged). Because projection sets `meta.day = ev["day"]`, the next `_build_scene` sees the advanced day. This reuses the existing `day`-stamping seam in `apply_turn`/`to_events` and needs NO change to `TurnCommit`.
   - **Pure elapse (secondary).** When story time passes with no move (e.g. "你休整了三天"), nothing in the `moves`/`cast`/`places`/`facts` sections needs to fire — so we add ONE harness-authored carrier event `time_advanced` (owned by a tiny `TimeSystem`, no commit section) that the narrator opts into via a new optional commit section `time` (a single `{"to_day": N, "reason": str}` object). It is validated minimally (must be `to_day >= now`) and `to_events`→`time_advanced` carries `day = to_day`. This is the ONLY new event type Phase D adds, and it exists solely so a "time passes" turn can move `meta.day` through the strict gate. **v1 may ship with travel-cost advance only and defer the `time` section (see D6).**
3. **JUMP detection.** `loop/time.py::detect_jump(events, world) -> (prev_day, now, jumped: bool)`. `now` = `meta.day`. `prev_day` = the max `day` among events strictly *before* this turn's events (i.e. the day as of the end of the previous turn). `jumped = (now - prev_day) >= JUMP_THRESHOLD` (default `JUMP_THRESHOLD = 2` — a single +1 day is "normal flow", not a skip; ≥2 days elapsing at once is a "三天后" jump). The elapsed span fed to the catch-up prompt is `span = now - last_update(entity)` per entity (NOT `now - prev_day` — an entity last touched long ago drifts over its OWN gap, which may exceed this turn's jump). Rationale: deriving `prev_day` and `now` from the event stream keeps it rewind-safe and needs no extra state.

Rationale: every mechanism here is an *opt-in field on an event the narrator already emits*, so the LLM stays in control of pacing (the user's "autonomy within harness" philosophy) while the harness deterministically derives drift. No global clock, no per-turn auto-increment.

### D2 — Where `last_update` lives — **`entity.attrs["last_update"]`, set in each system's `apply()` (the `last_cascade_turn` pattern)**

**Recommendation.** A per-entity attribute `entity.attrs["last_update"]`, holding the **day** of the most recent event that materially changed that entity. It is written inside `apply()` in the systems that own the entity, exactly like `last_cascade_turn`:
- `systems/character.py::CharacterSystem.apply`: on `character_created` / `character_evolved` / `relationship_changed`, set `entity.attrs["last_update"] = event["day"]` for the touched Person.
- `systems/place.py::PlaceSystem.apply`: on `place_created` / `place_materialized` / `entity_moved` (the place a tracked entity arrives at) set `entity.attrs["last_update"] = event["day"]` for the touched Place. (Movement of the protagonist INTO a place updates THAT place's stamp — arriving "refreshes" the place's currency, which is what we want: a place the party is standing in is not stale.)
- `systems/cascade.py::CascadeSystem.apply`: on `place_evolved`, ALSO set `last_update = event["day"]` next to the existing `last_cascade_turn = turn` (a cascade IS a material update; both stamps coexist — `last_cascade_turn` is a turn ordinal for cascade bookkeeping, `last_update` is a day for drift). The Phase D catch-up event itself (also a `place_evolved` / `character_evolved`) therefore advances `last_update` to `now` on apply, which closes the loop.

Why a day-stamped attr, not a fact: it is bitemporal-friendly because it is derived purely from events (no fact supersession bookkeeping needed) and rewind-safe because `project()` rebuilds the whole `Entity` graph from events every time — there is no persisted side state to get out of sync (this is the SAME guarantee `last_cascade_turn` and the cascade `queue` already rely on). Using **day** (not turn) is deliberate: drift is about elapsed *story time*, and `value_at`/`valid_at` are day-keyed.

### D3 — Lazy catch-up trigger + the precise definition of "enters scope"

**Recommendation.** `loop/time.py::stale_entering_scope(world, prev_scene, new_scene, now) -> list[str]` returns the tracked entity ids to catch up this turn. An entity `e` qualifies iff ALL hold:
1. `e` is a **tracked** entity (`entity.tier == "tracked"`) of etype `Person` or `Place`.
2. `e` **enters scope this turn**: `e ∈ scope(new_scene)` AND `e ∉ scope(prev_scene)`, where `scope(s) = {s["protagonist"]} ∪ set(s.get("present", []))` for a Person, and for a Place `scope` is the current-scene Place id `new_scene.get("id")/("location")` plus (optionally, v1-deferrable) the scene's containment subtree. "Enters" = transition from out-of-scope last turn to in-scope this turn — NOT merely "is in scope" (an entity that has been present every turn is being narrated live and does not need a catch-up). The protagonist is in scope every turn from genesis, so it never "enters" and is never catch-up'd (it is driven live).
3. **Stale:** `entity.attrs.get("last_update", <birth_day>) < now`. (`last_update == now` ⇒ skip — §14 "tracked 冲突: `last_update == now` ⇒ skip"; the entity was already updated this very day.)

**"Enters scope" precisely.** The hook needs the PREVIOUS turn's scope to compute the transition. `run_turn` knows the current `scene`; it does NOT today retain the prior scene. So `run_catchup` derives `prev_scope` from the event stream: the set of tracked Persons/Places that were `present`/located in the scene as of the previous player turn. **v1 simplification (recommended, see D6):** approximate "enters scope" as "**is in scope this turn AND was NOT in scope as of the entity's `last_update` (i.e. has been away)**" — operationally: `e ∈ scope(new_scene)` AND `e.last_update < now` AND `e` did not appear in `new_scene`'s scope on the previous turn's events. The dedicated `enters_scope` set is computed from comparing this turn's `scene["present"]` against the `present` reconstructed for the prior turn (the `entity_moved`/scene events already in the store). Entities NOT in `scope(new_scene)` are NEVER caught up — that is the whole point of §14 断点3.

The catch-up call: ONE `cascade_provider.complete_json(_CATCHUP_SYSTEM, _catchup_prompt(eid, kind, span, context), _CATCHUP_SCHEMA)` per qualifying entity, where `span = now - last_update`. The verdict shape: `{"id": eid, "changed": bool, "predicate"/"value" (Person) | "state"/"populace_mood" (Place), "note": str}`. `_catchup_prompt` embeds `eid` verbatim so a keyed fake (and any real model) can answer per-entity without relying on call order (the D5 hazard mitigation, same as cascade). On `changed:false` ⇒ emit NOTHING but STILL stamp currency (see D4) so we do not re-ask every turn. On `changed:true` ⇒ emit a `character_evolved` (Person) or `place_evolved` (Place) through `lightweight_validate` (referential only, drop-on-fail, NO repair).

### D4 — Ordering vs cascade — **catch-up runs AFTER cascade in `run_turn` (confirmed)**

**Recommendation (confirmed).** In `loop/turn.py::run_turn`, the post-apply hook order is: `digest_fleet` → `run_director` → `run_cascade` → **`run_catchup` (NEW, last)**. This realizes §14 "先链式下沉,再补在场/将进场 tracked": cascade descends the world first (so a place the party just entered may evolve via cascade and get its `last_update` bumped to `now`), THEN catch-up fills in any *still-stale* entering-scope tracked entity. Placement after cascade also means catch-up sees the re-projected post-cascade world, so the `last_update == now ⇒ skip` rule naturally suppresses double-work on a place cascade already refreshed this turn. The hook is wrapped in `get_tracer().span("catchup", ...)`, a non-fatal `try/except`, and re-projects iff it appended events — byte-identical to the cascade block above it.

**Re-trigger safety (verified).** Catch-up emits `character_evolved` (excluded from cascade `_HARNESS_TYPES` and never a cascade root) and `place_evolved` (a harness type, never a cascade root). Because catch-up runs AFTER cascade in the same turn and there is no second cascade pass, neither catch-up event can spark a new cascade this turn. (If a catch-up `place_evolved` *should* ripple, that is next turn's cascade via the normal trigger — out of scope for D1.)

**Currency stamp on `changed:false`.** Even when the model says "nothing changed", we must avoid re-asking every subsequent turn. The catch-up still emits a tiny `time_advanced` event scoped to that entity (`deltas={"id": eid, "to_day": now, "reason": "catchup-noop"}`) whose `apply` bumps `entity.attrs["last_update"] = now` WITHOUT asserting any drift fact. This keeps the lazy contract honest: an entity is asked AT MOST once per jump it is present for. (If D6's v1 defers the `time` section, this no-op carrier still ships, because it is harness-authored and needs no narrator contract.)

### D5 — Cost + determinism — **≤1 cheap call per entering-scope stale tracked entity, hard-capped, all offline-deterministic**

**Recommendation.**
- **Cost cap.** `CATCHUP_BUDGET` (default **4**) caps the number of catch-up LLM calls per turn. Entities are processed in a deterministic order (sorted by id); when the budget is hit, the rest are left for a future turn and `log.info("catchup: budget %d hit; %d entering-scope stale entity(ies) deferred: %s", CATCHUP_BUDGET, n_left, ids)`. No silent drop — the deferred entities simply keep their stale `last_update` and get caught up the next time they enter scope under budget (this is already §14-compatible laziness). One cheap call per entity, small fixed schema, short prompt.
- **Model.** Catch-up uses the **cheap** provider. The hook takes `catchup_provider` (default = `cascade_provider` if the engine has one, else `provider`). `app/engine.py` already builds an optional `cascade_provider`; reuse it (a single "cheap model" is correct for both backstage jobs). `run_turn` plumbs `catchup_provider` analogously to `cascade_provider`.
- **Determinism.** All tests offline. Per-entity verdicts come from a `FakeLLMProvider(json_responses=[...])` (single entity, deterministic order) OR a keyed fake (the `KeyedFakeProvider` idiom from `tests/loop/test_cascade_loop.py`, keyed by the entity id embedded in the prompt) when multiple entities are caught up — so the test asserts the exact per-entity outcome without depending on call order. NO network: tests never construct a real provider; `make_provider("fake")` / `FakeLLMProvider` only.

### D6 — v1 scope / split — **D1 = stamp + lazy catch-up on travel-cost day-jump; the `time` section is D2 (deferred)**

**Recommendation.** Ship a deliberately small **D1** (this is a 收尾 phase, not a flagship):

- **D1 (this plan, shippable):**
  1. `last_update` stamping in `character.py` / `place.py` / `cascade.py` `apply()` (Task 1).
  2. `arrive_day` travel-cost day-advance in `place.py::to_events` for `moves` (Task 2) — gives time a way to climb with ZERO new event type.
  3. `loop/time.py` helpers: `current_day`, `detect_jump`, `stale_entering_scope` (Tasks 3–4).
  4. `run_catchup` hook + `_node`-style catch-up call + `lightweight_validate` reuse, emitting `character_evolved`/`place_evolved` + the no-op `time_advanced` currency carrier (Tasks 5–6).
  5. A tiny `TimeSystem` owning `time_advanced` (Task 5, needed so the no-op currency carrier passes the store's allow-set), registered in `build_engine` (Task 7).
  6. Wire `run_catchup` into `run_turn` AFTER cascade + plumb `catchup_provider` (Task 7).
- **D2 (DEFERRED — note in plan, do NOT build now):** the optional narrator `time` commit section + full `TimeSystem` validation for explicit "时间流逝" turns with no movement. D1's travel-cost advance covers the common case (party travels ⇒ days pass ⇒ entities they meet on arrival are stale ⇒ caught up). The pure-elapse `time` section is a clean follow-up once D1 is proven on a real model.

---

## File structure

| File | New/Edit | Purpose |
|---|---|---|
| `systems/time.py` | **new** | `TimeSystem(ContextSystem)`: owns `time_advanced` (harness-authored, no commit section in D1); `apply` bumps `entity.attrs["last_update"]` (and, for a scoped carrier, the named entity's). Mirrors `systems/cascade.py` shape. |
| `loop/time.py` | **new** | `current_day` / `detect_jump` / `stale_entering_scope` / `run_catchup` + the catch-up LLM call + prompt/schema. Mirrors `loop/cascade.py`. |
| `systems/character.py` | edit | Stamp `last_update = event["day"]` in `apply()` for the three character events. |
| `systems/place.py` | edit | Stamp `last_update = event["day"]` in `apply()` for place/move events; honor `arrive_day` in `to_events` for the `moves` section. |
| `systems/cascade.py` | edit | Add `last_update = event["day"]` beside the existing `last_cascade_turn` on `place_evolved`. |
| `loop/turn.py` | edit | Call `run_catchup` AFTER `run_cascade` (tracer span, non-fatal, re-project on append); add `catchup_provider` param. |
| `app/engine.py` | edit | Register `TimeSystem`; pass `catchup_provider` (reuse `cascade_provider`) through `run_turn` wiring is via play layer. |
| `app/play.py` | edit | Pass `engine.cascade_provider` (or a dedicated `catchup_provider`) into `run_turn` as `catchup_provider`. |
| `tests/systems/test_time_system.py` | **new** | `TimeSystem` apply + `last_update` stamping unit tests. |
| `tests/loop/test_time_loop.py` | **new** | `current_day` / `detect_jump` / `stale_entering_scope` / `run_catchup` tests (keyed fake, offline). |
| `tests/systems/test_character.py` | edit | Assert `last_update` stamped on character events. |
| `tests/systems/test_place.py` | edit | Assert `last_update` stamped + `arrive_day` day-advance. |

Conventions for ALL tasks: `from engine.log import get_logger` for logging; tests mirror source paths; binary is `python3`; full-suite gate after every task is `python3 -m pytest -q --ignore=tests/test_embed_real.py` (baseline **698 passing** — must stay green, including the `_legacy` tests in the suite); commit ONLY the files the task names with trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## HARD git guardrails (every task obeys; see `docs/INCIDENT-2026-06-16-git-reset.md`)

- NO `git init`, `git reset`, `git checkout --orphan`, `rm -rf .git`, NO branch switch. Stay on `app`.
- Do NOT edit anything under `engine/`, `_legacy/`, `docs/` (except writing/updating THIS plan file), or `data/`.
- Each commit names ONLY the files that task changed (`git add <explicit paths>` — never `git add -A`/`.`).
- The current **698-test** suite (incl. legacy) MUST stay green after every task. A task that reds the suite is not done.
- "Rebuild from scratch" + multi-minute runs = red flag → stop and ask the controller.

---

## TDD tasks (bite-sized)

### Task 1 — `last_update` stamping in character + place + cascade `apply()`

**Files:** `systems/character.py`, `systems/place.py`, `systems/cascade.py`, `tests/systems/test_character.py`, `tests/systems/test_place.py`, `tests/systems/test_cascade_system.py`

- [ ] **Write failing tests first.** In `tests/systems/test_character.py` add:
  ```python
  def test_character_created_stamps_last_update():
      reg = _reg()  # existing helper in this file
      world = project(reg, [
          kernel_event("character_created", day=3, scene="s1", summary="登场",
                       deltas={"id": "npc", "tier": "tracked",
                               "sketch": "守桥人", "goal": "守住桥"}, turn=1),
      ])
      g = world["systems"]["ontology"]
      assert g.get_entity("npc").attrs.get("last_update") == 3

  def test_character_evolved_advances_last_update():
      reg = _reg()
      world = project(reg, [
          kernel_event("character_created", day=1, scene="s1", summary="登场",
                       deltas={"id": "npc", "tier": "tracked",
                               "sketch": "守桥人", "goal": "守桥"}, turn=1),
          kernel_event("character_evolved", day=5, scene="s1", summary="变",
                       deltas={"id": "npc", "predicate": "mood", "value": "疲惫",
                               "op": "evolve"}, turn=2),
      ])
      assert world["systems"]["ontology"].get_entity("npc").attrs.get("last_update") == 5
  ```
  In `tests/systems/test_place.py` add the analogous `test_place_created_stamps_last_update` (assert `last_update == event day`) and `test_entity_moved_stamps_destination_last_update` (move protagonist to a place on day 4 ⇒ that place's `last_update == 4`). In `tests/systems/test_cascade_system.py` extend the existing `test_place_evolved_asserts_state_fact` (or add a sibling) to also assert `g.get_entity("market").attrs.get("last_update") == 2`.
- [ ] **Run → FAIL:** `python3 -m pytest -q tests/systems/test_character.py tests/systems/test_place.py tests/systems/test_cascade_system.py` — new asserts fail (`last_update` is `None`).
- [ ] **Minimal impl.** In `systems/character.py::apply`, after each successful entity touch (character_created after `add_entity`; character_evolved/relationship_changed after `assert_fact`), set `g.get_entity(pid).attrs["last_update"] = event["day"]` (guard `is not None`). In `systems/place.py::apply`: on `place_created` set the new place's `last_update`; on `place_materialized` set it; on `entity_moved` set the **destination** place's `last_update = event["day"]` (look up `to` via `g.get_entity(to)`, guard None). In `systems/cascade.py::apply` `place_evolved` branch, add `entity.attrs["last_update"] = day` next to the existing `entity.attrs["last_cascade_turn"] = turn`.
- [ ] **Run → PASS** the three files.
- [ ] **Full suite:** `python3 -m pytest -q --ignore=tests/test_embed_real.py` (still 698+).
- [ ] **Commit** the 6 named files. `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

### Task 2 — `arrive_day` travel-cost day-advance in `place.py::to_events` (`moves`)

**Files:** `systems/place.py`, `tests/systems/test_place.py`

- [ ] **Write failing test first.** In `tests/systems/test_place.py`:
  ```python
  def test_moves_with_arrive_day_stamps_event_day():
      ps = PlaceSystem()
      evs = ps.to_events("moves",
                         [{"who": "protagonist", "to": "town", "arrive_day": 4}],
                         turn=2, day=1, scene="s1")
      assert evs[0]["type"] == "entity_moved"
      assert evs[0]["day"] == 4            # arrive_day wins over scene day

  def test_moves_without_arrive_day_keeps_scene_day():
      ps = PlaceSystem()
      evs = ps.to_events("moves", [{"who": "p", "to": "town"}],
                         turn=2, day=1, scene="s1")
      assert evs[0]["day"] == 1            # unchanged behavior

  def test_moves_arrive_day_never_goes_backward():
      ps = PlaceSystem()
      evs = ps.to_events("moves", [{"who": "p", "to": "town", "arrive_day": 1}],
                         turn=2, day=6, scene="s1")
      assert evs[0]["day"] == 6            # max(arrive_day, scene_day)
  ```
- [ ] **Run → FAIL** (`arrive_day` ignored; event day == scene day always).
- [ ] **Minimal impl.** In `systems/place.py::to_events`, in the `moves` branch only, compute `ev_day = max(int(m.get("arrive_day", day)), day)` and pass `day=ev_day` to the `entity_moved` `kernel_event`. Leave `places`/`links`/`materialize` untouched. (Validation of `arrive_day` is intentionally absent — it is an optional creative field; a bad value clamps via `max`. Note in a comment that `FactGraph.assert_fact` requires non-decreasing day, which `max(...)` guarantees.)
- [ ] **Run → PASS** the file.
- [ ] **Full suite** green.
- [ ] **Commit** `systems/place.py` + `tests/systems/test_place.py`.

### Task 3 — `loop/time.py`: `current_day` + `detect_jump`

**Files:** `loop/time.py` (new), `tests/loop/test_time_loop.py` (new)

- [ ] **Write failing tests first.** New `tests/loop/test_time_loop.py`:
  ```python
  from kernel.registry import Registry
  from kernel.projection import project
  from kernel.events import kernel_event
  from systems.ontology import OntologySystem
  from systems.place import PlaceSystem
  from systems.character import CharacterSystem
  from loop.time import current_day, detect_jump, JUMP_THRESHOLD

  def _reg():
      return (Registry().register(OntologySystem())
              .register(PlaceSystem()).register(CharacterSystem()))

  def test_current_day_reads_meta():
      world = project(_reg(), [kernel_event("place_created", day=7, scene="s",
                      summary="x", deltas={"id": "t", "tier": "tracked"}, turn=1)])
      assert current_day(world) == 7

  def test_current_day_defaults_to_1_when_empty():
      assert current_day(project(_reg(), [])) == 1

  def test_detect_jump_true_on_big_gap():
      events = [
          kernel_event("place_created", day=1, scene="s", summary="x",
                       deltas={"id": "t", "tier": "tracked"}, turn=1),     # prev turn
          kernel_event("entity_moved", day=5, scene="s", summary="到",
                       deltas={"who": "h", "to": "t"}, turn=2),            # this turn
      ]
      world = project(_reg(), events)
      this_turn = [e for e in events if e["turn"] == 2]
      prev, now, jumped = detect_jump(this_turn, world, all_events=events)
      assert (prev, now, jumped) == (1, 5, True)

  def test_detect_jump_false_on_single_day_step():
      events = [
          kernel_event("place_created", day=1, scene="s", summary="x",
                       deltas={"id": "t", "tier": "tracked"}, turn=1),
          kernel_event("entity_moved", day=2, scene="s", summary="到",
                       deltas={"who": "h", "to": "t"}, turn=2),
      ]
      world = project(_reg(), events)
      this_turn = [e for e in events if e["turn"] == 2]
      _, _, jumped = detect_jump(this_turn, world, all_events=events)
      assert jumped is False        # +1 day is normal flow, not a jump
  ```
- [ ] **Run → FAIL** (`ModuleNotFoundError: loop.time`).
- [ ] **Minimal impl.** New `loop/time.py` with module docstring (mirror `loop/cascade.py` header style), `from engine.log import get_logger`, `log = get_logger("loop.time")`, `JUMP_THRESHOLD = 2`, and:
  - `current_day(world) -> int`: `return (world.get("meta") or {}).get("day") or 1`.
  - `detect_jump(this_turn_events, world, *, all_events) -> tuple[int,int,bool]`: `now = current_day(world)`; `this_turns = {e.get("turn") for e in this_turn_events}`; `prev_day = max((e.get("day") or 1 for e in all_events if e.get("turn") not in this_turns), default=now)`; `jumped = (now - prev_day) >= JUMP_THRESHOLD`; `return prev_day, now, jumped`.
- [ ] **Run → PASS** the file.
- [ ] **Full suite** green.
- [ ] **Commit** `loop/time.py` + `tests/loop/test_time_loop.py`.

### Task 4 — `loop/time.py`: `stale_entering_scope`

**Files:** `loop/time.py`, `tests/loop/test_time_loop.py`

- [ ] **Write failing tests first** (append to `tests/loop/test_time_loop.py`):
  ```python
  from loop.time import stale_entering_scope

  def _person(pid, day, sketch="人", goal="活着"):
      return kernel_event("character_created", day=day, scene="s",
                          summary="登场",
                          deltas={"id": pid, "tier": "tracked",
                                  "sketch": sketch, "goal": goal}, turn=1)

  def test_entering_scope_stale_tracked_is_selected():
      # npc created day1, not seen since; now day5; enters scope this turn
      world = project(_reg(), [_person("npc", 1)])
      prev_scene = {"protagonist": "hero", "present": []}
      new_scene = {"protagonist": "hero", "present": ["npc"], "day": 5}
      assert stale_entering_scope(world, prev_scene, new_scene, now=5) == ["npc"]

  def test_present_last_turn_is_not_entering():
      world = project(_reg(), [_person("npc", 1)])
      prev_scene = {"protagonist": "hero", "present": ["npc"]}   # already present
      new_scene = {"protagonist": "hero", "present": ["npc"], "day": 5}
      assert stale_entering_scope(world, prev_scene, new_scene, now=5) == []

  def test_fresh_entity_not_selected():
      # last_update == now ⇒ skip (§14 conflict rule)
      world = project(_reg(), [_person("npc", 5)])
      prev_scene = {"protagonist": "hero", "present": []}
      new_scene = {"protagonist": "hero", "present": ["npc"], "day": 5}
      assert stale_entering_scope(world, prev_scene, new_scene, now=5) == []

  def test_offscreen_entity_never_selected():
      # npc stale but NOT in this turn's scope ⇒ never caught up (§14 断点3)
      world = project(_reg(), [_person("npc", 1)])
      prev_scene = {"protagonist": "hero", "present": []}
      new_scene = {"protagonist": "hero", "present": [], "day": 9}
      assert stale_entering_scope(world, prev_scene, new_scene, now=9) == []

  def test_protagonist_never_selected():
      world = project(_reg(), [_person("hero", 1)])
      prev_scene = {"protagonist": "hero", "present": []}
      new_scene = {"protagonist": "hero", "present": [], "day": 5}
      assert stale_entering_scope(world, prev_scene, new_scene, now=5) == []
  ```
- [ ] **Run → FAIL** (`ImportError`).
- [ ] **Minimal impl.** Add `stale_entering_scope(world, prev_scene, new_scene, *, now) -> list[str]`:
  - `g = world["systems"]["ontology"]`.
  - `prev_scope = {prev_scene.get("protagonist")} | set(prev_scene.get("present") or [])`.
  - `new_scope = set(new_scene.get("present") or [])` (NOTE: exclude the protagonist — it is driven live and never catch-up'd; do NOT add `new_scene["protagonist"]` here).
  - For each `eid in sorted(new_scope - prev_scope)`: `e = g.get_entity(eid)`; skip if `e is None` or `e.tier != "tracked"` or `e.etype not in {"Person","Place"}`; `lu = e.attrs.get("last_update")`; skip if `lu is None or lu >= now`; else select.
  - Return the list in sorted order. Add a docstring citing §14 断点3 + the conflict rule. (v1: Place-scope-from-subtree is deferred per D3; `new_scope` is the Person `present` set. A Place entering scope is handled when D2 ships the subtree scope.)
- [ ] **Run → PASS** the file.
- [ ] **Full suite** green.
- [ ] **Commit** `loop/time.py` + `tests/loop/test_time_loop.py`.

### Task 5 — `TimeSystem` (owns `time_advanced`) + currency stamp on apply

**Files:** `systems/time.py` (new), `tests/systems/test_time_system.py` (new)

- [ ] **Write failing tests first.** New `tests/systems/test_time_system.py`:
  ```python
  from kernel.registry import Registry
  from kernel.projection import project
  from kernel.events import kernel_event
  from systems.ontology import OntologySystem
  from systems.character import CharacterSystem
  from systems.time import TimeSystem

  def _reg():
      return (Registry().register(OntologySystem())
              .register(CharacterSystem()).register(TimeSystem()))

  def test_timesystem_registers_and_owns_event():
      reg = _reg()
      assert reg.owner_of_event("time_advanced") is not None
      assert TimeSystem().commit_sections() == set()    # harness-authored in D1

  def test_time_advanced_scoped_bumps_last_update_only():
      reg = _reg()
      world = project(reg, [
          kernel_event("character_created", day=1, scene="s", summary="登场",
                       deltas={"id": "npc", "tier": "tracked",
                               "sketch": "守桥人", "goal": "守桥"}, turn=1),
          kernel_event("time_advanced", day=5, scene="s", summary="时间流逝",
                       deltas={"id": "npc", "to_day": 5, "reason": "catchup-noop"},
                       turn=2),
      ])
      g = world["systems"]["ontology"]
      # currency advanced, but NO drift fact asserted
      assert g.get_entity("npc").attrs.get("last_update") == 5
      assert g.value_at("npc", "mood", 5) is None

  def test_time_advanced_unscoped_does_not_crash():
      reg = _reg()
      world = project(reg, [
          kernel_event("time_advanced", day=3, scene="s", summary="三天后",
                       deltas={"to_day": 3, "reason": "elapse"}, turn=1),
      ])
      assert world["meta"]["day"] == 3      # carries the day forward via projection
  ```
- [ ] **Run → FAIL** (`ModuleNotFoundError: systems.time`).
- [ ] **Minimal impl.** New `systems/time.py` mirroring `systems/cascade.py`: `class TimeSystem(ContextSystem)` with `name="time"`, `requires() == {"ontology"}`, `event_types() == {"time_advanced"}`, `commit_sections() == set()`, `empty_state() == {}`. `apply(world, event)`: read `g`, `d = event.get("deltas",{})`, `pid = d.get("id")`; if `pid` and `g.get_entity(pid) is not None`, set that entity's `attrs["last_update"] = event["day"]` (no fact). If no `pid`, do nothing beyond letting projection set `meta.day` (it already does). Defensive `log.warning` on a dangling `id`. (No `to_events` in D1 — `time_advanced` is harness-authored, emitted directly by the catch-up hook via `kernel_event`.)
- [ ] **Run → PASS** the file.
- [ ] **Full suite** green.
- [ ] **Commit** `systems/time.py` + `tests/systems/test_time_system.py`.

### Task 6 — `run_catchup` hook (catch-up call + lightweight validate + emit)

**Files:** `loop/time.py`, `tests/loop/test_time_loop.py`

- [ ] **Write failing tests first** (append to `tests/loop/test_time_loop.py`). Use a keyed fake mirroring `tests/loop/test_cascade_loop.py::KeyedFakeProvider` and a real store:
  ```python
  import tempfile, os
  from kernel.events import open_store
  from llm.provider import LLMProvider
  from systems.cascade import CascadeSystem
  from systems.time import TimeSystem
  from loop.time import run_catchup

  def _full_reg():
      return (Registry().register(OntologySystem()).register(PlaceSystem())
              .register(CharacterSystem()).register(CascadeSystem())
              .register(TimeSystem()))

  def _store(reg):
      d = tempfile.mkdtemp()
      return open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                        allowed_types=reg.event_types())

  class KeyedCatchup(LLMProvider):
      def __init__(self, by_id):
          self.by_id = by_id; self.calls = []
      def complete(self, system, user, *, model=None, max_tokens=None):
          return ""
      def complete_json(self, system, user, schema, **kw):
          self.calls.append(user)
          for eid, v in self.by_id.items():
              if eid in user:
                  return dict(v, id=eid)
          return {"changed": False}

  def test_run_catchup_emits_character_evolved_for_entering_stale():
      reg = _full_reg(); store = _store(reg)
      store.append(_person("npc", 1))                       # created day1
      store.append(kernel_event("entity_moved", day=5, scene="s", summary="到",
                   deltas={"who": "hero", "to_loc": "x"}, turn=2))  # advances meta.day→5
      world = project(reg, store.iter_events())
      prev_scene = {"protagonist": "hero", "present": []}
      new_scene = {"protagonist": "hero", "present": ["npc"], "id": "s", "day": 5}
      prov = KeyedCatchup({"npc": {"changed": True, "predicate": "mood",
                                   "value": "形容枯槁", "note": "独守五日"}})
      appended = run_catchup(reg, store, world, prev_scene, new_scene, provider=prov)
      types = [e["type"] for e in appended]
      assert "character_evolved" in types
      ev = next(e for e in appended if e["type"] == "character_evolved")
      assert ev["deltas"]["id"] == "npc" and ev["deltas"]["value"] == "形容枯槁"
      # re-project: drift fact + last_update now current
      w2 = project(reg, store.iter_events())
      assert w2["systems"]["ontology"].value_at("npc", "mood", 5) == "形容枯槁"
      assert w2["systems"]["ontology"].get_entity("npc").attrs["last_update"] == 5

  def test_run_catchup_noop_still_stamps_currency():
      reg = _full_reg(); store = _store(reg)
      store.append(_person("npc", 1))
      store.append(kernel_event("time_advanced", day=5, scene="s", summary="x",
                   deltas={"to_day": 5, "reason": "elapse"}, turn=2))
      world = project(reg, store.iter_events())
      prev_scene = {"protagonist": "hero", "present": []}
      new_scene = {"protagonist": "hero", "present": ["npc"], "id": "s", "day": 5}
      prov = KeyedCatchup({"npc": {"changed": False}})
      appended = run_catchup(reg, store, world, prev_scene, new_scene, provider=prov)
      assert [e["type"] for e in appended] == ["time_advanced"]   # currency carrier
      w2 = project(reg, store.iter_events())
      assert w2["systems"]["ontology"].get_entity("npc").attrs["last_update"] == 5

  def test_run_catchup_quiet_when_no_entering_stale():
      reg = _full_reg(); store = _store(reg)
      store.append(_person("npc", 5))
      world = project(reg, store.iter_events())
      scene = {"protagonist": "hero", "present": ["npc"], "id": "s", "day": 5}
      prov = KeyedCatchup({})
      assert run_catchup(reg, store, world, scene, scene, provider=prov) == []
      assert prov.calls == []        # no LLM call when nothing qualifies

  def test_run_catchup_budget_caps_calls():
      reg = _full_reg(); store = _store(reg)
      for i in range(6):
          store.append(_person(f"npc{i}", 1))
      store.append(kernel_event("time_advanced", day=5, scene="s", summary="x",
                   deltas={"to_day": 5, "reason": "e"}, turn=2))
      world = project(reg, store.iter_events())
      prev_scene = {"protagonist": "hero", "present": []}
      new_scene = {"protagonist": "hero", "present": [f"npc{i}" for i in range(6)],
                   "id": "s", "day": 5}
      prov = KeyedCatchup({f"npc{i}": {"changed": False} for i in range(6)})
      import loop.time as tmod
      run_catchup(reg, store, world, prev_scene, new_scene, provider=prov)
      assert len(prov.calls) == tmod.CATCHUP_BUDGET    # capped at 4
  ```
- [ ] **Run → FAIL** (`ImportError: run_catchup`).
- [ ] **Minimal impl.** Add to `loop/time.py`:
  - `CATCHUP_BUDGET = 4`, the `_CATCHUP_SYSTEM` prompt (Chinese, "你是 TRPG 世界引擎… 这个角色/地点离开镜头 N 天后会有什么变化…只输出 JSON"), `_CATCHUP_SCHEMA` (`{"id","changed","predicate","value","state","populace_mood","note"}`, required `["changed"]`), and `_catchup_prompt(eid, kind, span, context)` embedding `eid` verbatim.
  - `lightweight_validate`: reuse `loop.cascade.lightweight_validate` (import it) — referential check on `id`. (Do NOT duplicate; import the existing function.)
  - `_next_turn(store)`: max turn + 1 (copy the `loop/cascade.py::_next_cascade_turn` idiom).
  - `run_catchup(registry, store, world, prev_scene, new_scene, *, provider, catchup_provider=None) -> list[dict]`:
    1. `cp = catchup_provider or provider`; `now = current_day(world)`.
    2. `ids = stale_entering_scope(world, prev_scene, new_scene, now=now)`; if empty `return []`.
    3. If `len(ids) > CATCHUP_BUDGET`: `log.info("catchup: budget %d hit; %d deferred: %s", CATCHUP_BUDGET, len(ids)-CATCHUP_BUDGET, ids[CATCHUP_BUDGET:])`; `ids = ids[:CATCHUP_BUDGET]`.
    4. `g = world["systems"]["ontology"]`; `turn = _next_turn(store)`; `appended = []`.
    5. For each `eid`: `e = g.get_entity(eid)`; `kind = "Person" if e.etype=="Person" else "Place"`; `span = now - e.attrs.get("last_update", now)`; `raw = cp.complete_json(_CATCHUP_SYSTEM, _catchup_prompt(eid, kind, span, ctx), _CATCHUP_SCHEMA)`; force `raw = {**raw, "id": eid}` (the harness owns the id — same fix as cascade `_node_verdict`).
    6. If `raw.get("changed")` and `lightweight_validate(raw, g, set()) is not None`:
       - Person ⇒ `kernel_event("character_evolved", day=now, scene=new_scene.get("id") or "scene", summary=f"{eid} 时移境迁", deltas={"id": eid, "predicate": raw.get("predicate","arc"), "value": raw.get("value",""), "op":"evolve"}, turn=turn)`.
       - Place ⇒ `kernel_event("place_evolved", day=now, scene=..., summary=f"{eid} 时移境迁", deltas={"id": eid, "state": raw.get("state",""), "note": raw.get("note","")}, turn=turn)`; if `raw.get("populace_mood")` also append a `populace_shifted`.
       - `store.append(ev)`, `appended.append(ev)`.
    7. ELSE (changed:false OR dropped): emit the currency carrier `kernel_event("time_advanced", day=now, scene=..., summary=f"{eid} currency", deltas={"id": eid, "to_day": now, "reason": "catchup-noop"}, turn=turn)`; append.
    8. `log.debug("run_catchup: done appended=%d", len(appended))`; `return appended`.
- [ ] **Run → PASS** the file.
- [ ] **Full suite** green.
- [ ] **Commit** `loop/time.py` + `tests/loop/test_time_loop.py`.

### Task 7 — Wire into `run_turn` (after cascade) + register `TimeSystem` + plumb provider

**Files:** `loop/turn.py`, `app/engine.py`, `app/play.py`, `tests/loop/test_turn.py`, `tests/app/test_engine.py`

- [ ] **Write failing tests first.**
  - In `tests/app/test_engine.py`: `test_build_engine_registers_time_system` — `assert eng.registry.owner_of_event("time_advanced") is not None`.
  - In `tests/loop/test_turn.py`: a `run_turn` smoke test that passes `catchup_provider=FakeLLMProvider(...)`, a `scene` whose `present` includes a stale tracked NPC after a day-jump, and asserts the returned `TurnResult.world` reflects the catch-up (or at minimum that `run_turn` accepts `catchup_provider` and does not raise — keep it minimal but real; reuse the existing `run_turn` test harness/fakes in this file). Assert ordering indirectly: a `place_evolved` from cascade on the entered place leaves `last_update == now`, so the catch-up does NOT re-fire for that place (assert no duplicate). If the existing `run_turn` test fixtures make a full jump scenario heavy, assert the narrower contract: `run_turn(..., catchup_provider=fake)` runs and `TimeSystem`-owned events are permitted by the store.
- [ ] **Run → FAIL** (`run_turn` has no `catchup_provider`; `build_engine` does not register `TimeSystem`).
- [ ] **Minimal impl.**
  - `loop/turn.py`: import `from loop.time import run_catchup`; add `catchup_provider=None` to `run_turn`'s signature; after the cascade block, add an identical block:
    ```python
    try:
        with get_tracer().span("catchup", turn=turn_num_before):
            cat_events = run_catchup(registry, store, new_world,
                                     prev_scene=scene, new_scene=scene,
                                     provider=provider,
                                     catchup_provider=catchup_provider)
        if cat_events:
            new_world = project(registry, store.iter_events())
            log.debug("run_turn: catchup appended %d event(s)", len(cat_events))
    except Exception:
        log.exception("run_turn: run_catchup failed (non-fatal, backstage)")
    ```
    NOTE on `prev_scene`/`new_scene`: `run_turn` does not retain the prior scene, so pass `scene` for both in this wiring — `stale_entering_scope` then degrades to "in scope this turn AND stale" (the v1 approximation in D3; `prev_scope == new_scope` means `new_scope - prev_scope` is empty, so to make catch-up fire in v1 the comparison must use the **event-derived** prior presence). **Implementation choice for this task:** have `run_catchup` compute `prev_scope` from the store (tracked entities present as of the previous player turn) when `prev_scene is new_scene` (identity check) — add that derivation in Task 6's impl if not already present, OR (simpler, recommended) pass `prev_scene={"protagonist": scene["protagonist"], "present": []}` is WRONG (would catch up everyone every turn). The correct minimal wiring: `run_catchup` derives `prev_scope` from `store` via the most recent prior-turn `present`. Keep the public signature `run_catchup(registry, store, world, prev_scene, new_scene, *, provider, catchup_provider=None)` and, when `prev_scene is new_scene`, reconstruct `prev_scope` from events inside `run_catchup`. Add a focused unit test for that reconstruction in `tests/loop/test_time_loop.py` as part of THIS task.
  - `app/engine.py`: `from systems.time import TimeSystem`; `registry.register(TimeSystem())` (after `CascadeSystem`).
  - `app/play.py`: pass `catchup_provider=engine.cascade_provider` into the `run_turn(...)` call (reuse the cheap cascade provider; if `None`, `run_catchup` falls back to `provider`).
- [ ] **Run → PASS** the changed test files.
- [ ] **Full suite** green (698 + new).
- [ ] **Commit** the 5 named files.

---

## Self-Review

### §14 bullet → task coverage
- "无全局 tick;漂移态按 last_update+跨度懒派生" → Tasks 1 (`last_update` stamp), 3 (`detect_jump`/`current_day` derive `now`), 6 (`span = now - last_update` fed to the cheap call; NO ticker anywhere). ✓
- "tracked catch-up 一律懒性…只对下一轮进入 scope 的 tracked 追,其余留 last_update 等再相关;不'跑所有 tracked'" → Task 4 `stale_entering_scope` selects ONLY `new_scope - prev_scope` stale tracked (tests `test_offscreen_entity_never_selected`, `test_present_last_turn_is_not_entering`); off-screen entities keep `last_update`. ✓
- "tracked 冲突:`last_update==now` 跳过" → Task 4 skip-on-`lu >= now` (`test_fresh_entity_not_selected`). ✓
- "先链式下沉,再补在场/将进场 tracked" → Task 7 wires `run_catchup` AFTER `run_cascade` in `run_turn`; D4 re-trigger-safety note + "place already cascaded this turn has `last_update==now` ⇒ skipped" assertion. ✓
- Time can actually advance (the verified gap: nothing climbed `meta.day`) → Tasks 2 (`arrive_day`) + 5 (`time_advanced` carrier). ✓

### Placeholder scan
No task body contains `pass`-only impls, `TODO`, `...`, or "fill in later". Every task ships a REAL failing test, a REAL minimal impl, and a full-suite gate. The only DEFERRED item (D6 D2: the narrator `time` commit section + full validation) is explicitly scoped OUT of this plan and flagged — it is not a placeholder, it is a named follow-up.

### Name consistency
- New module `loop/time.py`: `current_day`, `detect_jump`, `stale_entering_scope`, `run_catchup`, `JUMP_THRESHOLD`, `CATCHUP_BUDGET`, `_CATCHUP_SYSTEM`, `_CATCHUP_SCHEMA`, `_catchup_prompt`, `_next_turn`. Used identically in tasks + tests.
- New module `systems/time.py`: `TimeSystem`, event type `time_advanced`, `name="time"`. Registry registration in `app/engine.py`.
- Reused names verified against source: `entity.attrs["last_update"]` (new, parallels `last_cascade_turn`); `lightweight_validate` imported from `loop.cascade` (NOT redefined); `character_evolved`/`place_evolved`/`populace_shifted` (existing event types, owners unchanged); `arrive_day` (new optional `moves` field). `meta.day` / `scene["present"]` / `scene["protagonist"]` / `entity.tier`/`etype` match `kernel/projection.py`, `app/play.py::_build_scene`, `facts/entity.py`. ✓
- Hook order string in `run_turn`: `digest_fleet → run_director → run_cascade → run_catchup`. ✓
