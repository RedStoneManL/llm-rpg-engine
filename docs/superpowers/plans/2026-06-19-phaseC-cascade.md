# Phase C — 世界演化 / 波状传播 (cascade) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or superpowers:executing-plans for a separate session). Every task is TDD: write the REAL failing test first, run it, see it FAIL, write the minimal REAL implementation, run it, see it PASS, then commit exactly the files the task names. No placeholders, no stubbed-out bodies, no "fill this in later". If a step's behavior is unclear, re-read the cited source file before writing code.

**Goal:** When a turn produces a significant world-change, ripple its consequences down the containment tree (vertical descent) and — in C2 — across adjacent regions (horizontal chain, depth≤3), via a new harness-authored `CascadeSystem`, so nested/adjacent places evolve in step instead of going stale.

**Architecture:** A new `CascadeSystem(ContextSystem)` owns three harness-authored event types (`place_evolved` / `populace_shifted` / `world_change`) and projects them into the shared `FactGraph` (`world["systems"]["ontology"]`) plus a small `world["systems"]["cascade"]` slice (deferred async queue + per-turn audit). A new post-apply hook `loop/cascade.py::run_cascade` mirrors `loop/director.py`: it reads this turn's appended events, decides whether they trip the cascade trigger, walks containment from each affected place, runs **one cheap LLM call per descent node** to produce that node's verdict, lightweight-validates the verdict (referential checks only, drop-on-fail, NO repair loop per §12 line 177), appends the resulting events through the strict store, and is wired into `run_turn` post-apply (after director) inside a tracer span, non-fatal, re-projecting on append.

**Tech Stack:** Python 3.12 stdlib only (no new deps). Reuses S0 kernel (`Registry`/`project`/`kernel_event`/`EventStore`), S1 `facts/FactGraph` + `systems/place.py` containment, the `loop/director.py` hook pattern, `llm/provider.py` (synchronous urllib; C2 parallelism via `concurrent.futures.ThreadPoolExecutor`), and `memory/importance.py::heuristic_floor` for the cheap trigger gate. Tests are offline + deterministic with `FakeLLMProvider`.

---

## Design decisions (load-bearing — referenced by tasks)

These answer the six required design questions. Each task below cites the decision number it implements.

> ### ⭐ RESOLVED BY HUMAN (2026-06-19) — these OVERRIDE any conflicting detail below
> The human confirmed the cascade direction with these refinements (authoritative):
> 1. **Eager trigger + cheap model.** Player movement AND explicit world-changes both ripple (keep `entity_moved` in the trigger set). Cascade nodes run on the cheap model (glm-4.7) via a `cascade_provider` that the engine/play layer wires to a cheap provider (do NOT silently reuse the narrator model for cost — make the cheap wiring real, default to it).
> 2. **Cap is PER-ROUND, not global.** Breadth cap = **≤6 nodes per round/level** (configurable, `CASCADE_BREADTH=6`), NOT a single global `CASCADE_MAX_NODES=6`. Horizontal depth ≤3 rounds (vertical descent is not depth-capped per §10). So a turn may touch up to ~6×rounds nodes; "normally won't ripple that wide."
> 3. **Configurable model concurrency + lazy overflow.** Within a round, fan out node LLM calls **in parallel** bounded by a **configurable `max_concurrency` (default 3, because Zhipu allows max 3 concurrent)** — the architecture must support >3 when the provider allows. Whatever exceeds this turn's processing budget is **NOT dropped**: record it as a **lazy-deferred marker** in the cascade slice (place id + reason) and drain it at the START of next turn's cascade hook (this realizes §12's "远区 cascade 异步 / 懒更新"). Leave the info, update next time.
> 4. **Merge same-region.** Before processing, merge nodes in the same region/parent so the total map-update count drops (§10 "合并同区").
> 5. **Director pacing** stays functional-v1 (not deepened now).
>
> Build order unchanged: **C1 first (vertical, blocking, per-round-capped, cheap model, merge), verify on the real model, then C2 (horizontal depth≤3 + parallel `max_concurrency` fan-out + lazy-deferred overflow queue).** The parallel fan-out + lazy-defer + configurable concurrency are C2; C1 stays single-threaded but MUST structure per-node processing as a standalone function so C2 parallelizes it without rework. Replace the global `CASCADE_MAX_NODES` in the tasks below with the per-round `CASCADE_BREADTH` semantics above.

### D1 — Trigger predicate (what counts as a "significant world-change") — **gated, not every turn**
**Recommendation:** Cascade fires for a turn iff that turn's appended events contain at least one event in the **trigger set** whose primary place crosses an importance floor. Concretely (`loop/cascade.py::cascade_trigger(new_events) -> list[str]` returns the list of affected **root place ids**, empty ⇒ no cascade):

- An event is a **cascade root** if BOTH:
  1. `event["type"]` ∈ `{"world_change", "place_materialized", "place_created", "entity_moved"}` **OR** the event carries `deltas.get("world_change")` truthy (lets a future narrator `world` section opt in without a code change), AND
  2. `memory.importance.heuristic_floor(event) >= CASCADE_FLOOR` (default `CASCADE_FLOOR = 3`). `heuristic_floor` is the cheap, provider-free heuristic already used by the fleet; events with deltas score ≥ base+1, so a `world_change` with a payload clears 3 easily while a bare `action` (floor 1) never trips a cascade.
- The **root place id** for an event = `deltas.get("place") or deltas.get("id") or deltas.get("to")` filtered to ids that resolve to an `etype=="Place"` entity in the graph. (A protagonist `entity_moved` ⇒ the destination place is the root; a `place_materialized`/`place_created` ⇒ that place; a `world_change` ⇒ its `place`.)
- De-dup root ids preserving first-seen order.

Rationale: pigg-backing on `heuristic_floor` means zero extra LLM calls to decide *whether* to cascade (the expensive calls are per descent node, only after we've decided to ripple). `entity_moved` is included because "protagonist enters a place" is the canonical moment we want the place + its children to feel alive — but the floor + "must resolve to a Place" keeps noise out. **Cost note for the human:** including `entity_moved` means most player-movement turns trip a cascade; if that is too eager, drop `entity_moved` from the trigger set and rely on explicit `world_change` — this is a DECISION-FOR-THE-HUMAN below.

### D2 — C1 / C2 split — **C1 = vertical descent, BLOCKING, synchronous, breadth-capped, cheap model; C2 = horizontal chain + parallelism + async/remote queue**
See the dedicated **C1 / C2 split** section. C1 is self-contained and shippable: it makes "enter/disturb a place ⇒ its sub-places evolve in step" real, with hard cost bounds, no threads, no async. C2 layers the harder §10 axes on top without touching C1's contracts.

### D3 — Events + system — **new `CascadeSystem(ContextSystem)`, harness-authored (no commit sections)**
Three owned event types (none collide — grep over the repo confirms `world_change` / `place_evolved` / `populace_shifted` / `cascade` are unused outside `_legacy`):

| event type | deltas (required → optional) | apply effect (into shared `FactGraph`) |
|---|---|---|
| `place_evolved` | `id` (Place) → `state` (str), `note` (str) | assert fact `(id, "state", <state>)` bitemporal; set entity attr `last_cascade_turn`. Missing/dangling `id` ⇒ `log.warning` + skip (never crash projection). |
| `populace_shifted` | `id` (Place) → `mood` (str), `note` (str) | assert fact `(id, "populace", <mood>)` bitemporal. Same defensive skip. |
| `world_change` | `place` (Place) → `level` (int), `summary`, `valence` | record the change into `world["systems"]["cascade"]["changes"]` audit list **and** (C2) enqueue horizontal/remote follow-ups; assert fact `(place, "world_change", <summary>)`. This is the level-bearing event the horizontal axis (§10) re-emits with `level+1`. |

`CascadeSystem`: `name="cascade"`, `requires() == {"ontology"}` (writes the shared graph), `commit_sections() == set()` (harness-authored, exactly like `DirectorSystem` B1), `empty_state() == {"queue": [], "changes": [], "consumed_through_turn": 0}`.

**Registration is mandatory** (Task numbered below, in `app/engine.py::build_engine`): `kernel_event` does NOT closed-set-check, but `EventStore.append` calls `validate_event(ev, allowed_types)` where `allowed_types = registry.event_types()` — an unregistered type raises `ValueError("unknown event type")`. So the system must be registered before the hook can append, and the test that appends cascade events must build a registry that includes `CascadeSystem`.

**Lightweight validation (§11 / §12 line 177):** `loop/cascade.py::lightweight_validate(verdict, graph, allowed_ids) -> dict | None` does **referential checks only** — every place id the verdict names (`id`, and in C2 `chain[].place`) must resolve to an existing graph entity OR be in `allowed_ids` (ids created earlier this same cascade). On any failure: `log.warning(...)` and return `None` (the caller **drops** that node's verdict). There is **NO repair conversation** — that is the §11 strict gate for the main commit only; harness output (director/oracle/cascade) does not pass the full gate.

### D3.5 — Cost control — **hard per-turn cap, cheap model, depth≤3 on the horizontal axis, all `log()`-visible**
- **Breadth cap (both axes share one budget):** `CASCADE_MAX_NODES` (default **6**) caps the **total** number of descent LLM calls per turn across the whole cascade (vertical + horizontal). The walker consumes the budget breadth-first; when exhausted it stops descending and `log.info("cascade: node budget %d exhausted; %d places left unvisited (rolled up)", CASCADE_MAX_NODES, n_left)`. No silent truncation — a roll-up note is recorded in the cascade slice. (This is the §10 "breadth ≤N regions/turn" backstop, made a node budget so it also bounds the recursive descent.)
- **Region cap (C2):** `CASCADE_MAX_REGIONS` (default **3**) caps how many distinct horizontal regions one turn may touch (§10 "≤N 区/回合"), independent of the node budget.
- **Model:** cascade uses the **cheap model**, never the narrator model. The hook takes an optional `cascade_provider` param (default = the same `provider` passed in; in production wiring `build_engine`/`run_turn` will pass the cheap-model provider). Each descent call is a single `provider.complete_json(system, user, schema)` returning the node verdict — small fixed schema, short prompt. **DECISION-FOR-THE-HUMAN:** wiring a *separate* cheap-model provider (vs reusing the narrator provider) is a config choice; this plan plumbs the param and defaults to reuse, leaving the cheap-model wiring to the play/engine layer.
- **Depth≤3 (horizontal only, C2):** `world_change` carries `level`; a horizontal follow-up re-emits with `level+1`; the walker refuses to enqueue a chain hop when `level > CASCADE_MAX_DEPTH` (default **3**) and logs the prune. **Vertical descent is NOT depth-capped** (§10: "不吃 depth-3") — it is bounded only by the node budget and by the finite containment subtree.
- Every cap hit is logged at `info`/`warning`; the cascade slice keeps a `changes` audit list so a debug dump can show exactly what rippled and what was pruned.

### D4 — Blocking boundary in a synchronous engine — **C1 blocks inline (after director); C2 splits current-scene vs remote via a deferred queue**
- The engine is synchronous, so "async" ≠ a background thread that outlives the turn. It means **deferred to a queue, processed at the START of the next turn's hook** (same shape as how `run_director` consumes last turn's pending directives first).
- **C1:** the entire vertical descent runs **inline, blocking, post-apply, AFTER `run_director`** in `run_turn` (so a director-fired `world_change` this turn — once C2 lets the director emit one — could also seed a cascade; for C1 the director emits no cascade roots, so order is immaterial but fixed for forward-compat). It is wrapped in `get_tracer().span("cascade", ...)`, non-fatal `try/except`, and re-projects if it appended events — identical to the director block.
- **C2 (current-scene-relevant = BLOCKING):** a descent node whose place is the **current scene or within the current scene's containment subtree** is processed inline this turn (its consequences must be visible in next turn's bundle, per §12 line 176). A horizontal hop into a **remote region** (not under the current scene's root) is **enqueued** into `world["systems"]["cascade"]["queue"]` and the events are emitted; the *further* descent of that remote region is deferred. At the START of the next `run_cascade`, the hook drains a bounded slice of the queue (respecting the same node budget) before handling the new turn's roots. "Current scene" = `scene.get("id")`/`scene.get("location")` passed into the hook; "subtree" = walk `contained_by` upward to the scene root, downward via reverse `contained_by`.

### D5 — Determinism for tests — **`FakeLLMProvider` with order-independent (keyed) verdicts; assert on the SET of outcomes for parallel paths**
- All C tests are offline. Per-node verdicts come from a `FakeLLMProvider`. For **C1** (single-threaded, deterministic visit order) a small `json_responses` list cycled by call order is fine AND the test asserts the exact visited places (deterministic DFS/BFS order over a fixed tree).
- For **C2 parallelism** the hazard is real: `FakeLLMProvider._json_idx` is a plain integer incremented under no lock, and `complete_json` is called concurrently from `ThreadPoolExecutor` workers, so **response→node assignment is non-deterministic across threads**. Two mitigations, both used:
  1. **Keyed fake provider for cascade tests:** introduce a tiny test helper `KeyedFakeProvider` (in the test module, NOT in `llm/provider.py`) that returns a verdict chosen by parsing the place id out of the `user` prompt (the prompt embeds the node's place id), so the response does not depend on call ORDER. The cascade prompt MUST therefore include the place id verbatim (Task asserts this).
  2. **Assert on the SET, not the sequence:** C2 tests assert `set(visited_place_ids) == expected_set` and that caps held, never an ordering that threads could scramble.
- ThreadPoolExecutor with `FakeLLMProvider` is otherwise test-safe because the fake does no real I/O; we only avoid asserting on its mutable call-order state. (We DO still assert `len(provider.calls) <= CASCADE_MAX_NODES` to prove the budget — `list.append` under GIL is atomic enough for a count assertion, and the budget is enforced by the walker handing out at most N tasks, not by the provider.)

---

## C1 / C2 split

**C1 — Vertical descent (ship first; self-contained, useful).**
Trigger (D1) → for each affected root Place, walk **down** its `contained_by` children (and the children's children) breadth-first, bounded by `CASCADE_MAX_NODES`. For each visited child Place, run ONE cheap LLM call → a verdict `{evolve: bool, state, populace_mood, note}`. Lightweight-validate (referential). For an `evolve:true` verdict emit `place_evolved` (+ `populace_shifted` when a mood is given); for `evolve:false` (a **prune**) record a roll-up note (no event, but the node "has a verdict" per §10 full-coverage). Append through the strict store, return appended events. **BLOCKING, synchronous, single-threaded, no horizontal axis, no async queue.** Reuses the director hook shape exactly. Wired into `run_turn` post-apply after director.

C1 explicitly does NOT: emit `world_change`, do horizontal/adjacent chaining, use threads, or defer anything. `CASCADE_MAX_DEPTH` and the queue exist in the slice but are inert in C1.

**C2 — Horizontal chain + parallelism + async/remote queue (layer on top).**
Adds, without changing C1's event shapes or the C1 vertical walk:
1. **Horizontal chain:** a strong vertical verdict (verdict carries `spread: true` + a `magnitude`) promotes to a `world_change` on an **adjacent** region (`adjacent_to` neighbor) at `level+1`, depth-capped at `CASCADE_MAX_DEPTH=3`, **merging same-region** (dedupe by region id so two roots hitting the same neighbor collapse to one chain hop). Region cap `CASCADE_MAX_REGIONS=3`.
2. **Parallelism:** sibling descent nodes at the same frontier are fanned out over `ThreadPoolExecutor(max_workers=max_subagents)` — the per-node LLM call is the unit; I/O releases the GIL so real providers overlap. Budget accounting stays single-threaded (the walker hands out ≤ remaining-budget tasks per frontier, collects results, then descends).
3. **Async/remote (D4):** remote-region hops (outside the current scene subtree) are enqueued and their deeper descent deferred to next turn's hook start; current-scene-subtree descent stays inline/blocking.

Justification for the boundary: C1 delivers the headline "world feels alive" behavior (nested places react) with fully bounded, deterministic, single-threaded cost — the part most likely to be wrong (thread-safety, async ordering, depth/region accounting, same-region merge) is quarantined in C2 where it can be tested in isolation against C1's already-green contracts.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `systems/cascade.py` | **Create** | `CascadeSystem(ContextSystem)` — owns `place_evolved` / `populace_shifted` / `world_change`; `apply` writes shared graph + cascade slice; no commit sections; `inject` optional digest fragment (C2). |
| `loop/cascade.py` | **Create** | `cascade_trigger(new_events, world)`, `lightweight_validate(verdict, graph, allowed_ids)`, descent walker, `run_cascade(registry, store, world, *, scene, provider, cascade_provider=None, max_subagents=4)` post-apply hook. C1 vertical only; C2 adds horizontal + queue + threads. |
| `loop/turn.py` | **Modify** | Wire `run_cascade` into `run_turn` post-apply, AFTER the director block, in `get_tracer().span("cascade", ...)`, non-fatal, re-project on append. (No other change.) |
| `app/engine.py` | **Modify** | Register `CascadeSystem()` in `build_engine` (after `DirectorSystem`). One line + import. |
| `tests/systems/test_cascade_system.py` | **Create** | `CascadeSystem` unit: ownership, registration, apply for each event type, defensive skips, slice shape. |
| `tests/loop/test_cascade_loop.py` | **Create** | `run_cascade` behavior: trigger predicate, vertical walk visits correct children, breadth cap, prune roll-up, lightweight-validate drop, deterministic; (C2) horizontal chain depth/region caps, same-region merge, remote-queue defer, parallel fan-out outcome-set + budget. |
| `tests/loop/test_turn.py` | **Modify** | One added test: `run_turn` invokes cascade post-apply on a triggering turn; events land in the store + re-projected world. |

No other files are touched. `engine/oracle.py`, `engine/director.py`, `_legacy/`, and `docs/` (except this plan) are off-limits.

---

# PART C1 — Vertical descent (blocking, synchronous)

## Task 1: `CascadeSystem` — ownership, registration, empty slice

**Files:** Create `systems/cascade.py`; Create `tests/systems/test_cascade_system.py`.

- [ ] **Step 1 — failing test** in `tests/systems/test_cascade_system.py`:
  ```python
  """Tests for CascadeSystem (Phase C1)."""
  from __future__ import annotations

  from kernel.registry import Registry
  from kernel.projection import project
  from kernel.events import kernel_event
  from systems.ontology import OntologySystem
  from systems.place import PlaceSystem
  from systems.cascade import CascadeSystem


  def _reg():
      return (Registry().register(OntologySystem())
              .register(PlaceSystem()).register(CascadeSystem()))


  def test_cascade_owns_event_types():
      cs = CascadeSystem()
      assert cs.name == "cascade"
      assert cs.event_types() == {"place_evolved", "populace_shifted", "world_change"}
      # Harness-authored: owns no commit sections (like DirectorSystem B1).
      assert cs.commit_sections() == set()


  def test_cascade_requires_ontology_and_registers():
      reg = _reg()
      assert "cascade" in {s.name for s in reg.systems}
      assert reg.owner_of_event("place_evolved").name == "cascade"
      assert reg.owner_of_event("world_change").name == "cascade"


  def test_empty_state_shape():
      assert CascadeSystem().empty_state() == {
          "queue": [], "changes": [], "consumed_through_turn": 0,
      }
  ```
- [ ] **Step 2 — run, expect FAIL:** `cd /root/rpg-engine-app && python3 -m pytest -q tests/systems/test_cascade_system.py` → ImportError / fails (no module yet).
- [ ] **Step 3 — implement `systems/cascade.py`** (minimal): module docstring; `from engine.log import get_logger`; `log = get_logger("systems.cascade")`; `class CascadeSystem(ContextSystem)` with `name = "cascade"`, `requires(self)->{"ontology"}`, `event_types(self)->{"place_evolved","populace_shifted","world_change"}`, `commit_sections(self)->set()`, `empty_state(self)->{"queue": [], "changes": [], "consumed_through_turn": 0}`. (Leave `apply` inherited/no-op for now — Task 2 adds it.)
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add systems/cascade.py tests/systems/test_cascade_system.py && git commit -m "feat(systems): CascadeSystem ownership + empty slice (Phase C1)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 2: `CascadeSystem.apply` — project the three event types

**Files:** Modify `systems/cascade.py`; Modify `tests/systems/test_cascade_system.py`.

- [ ] **Step 1 — failing tests** appended to `tests/systems/test_cascade_system.py`. Cover via `project(reg, [events])` over `kernel_event`s (create the place first with a `place_created`):
  ```python
  def _place(pid, parent=None, day=1):
      d = {"id": pid, "level": 3, "kind": "venue", "seed": "x", "tier": "tracked"}
      if parent:
          d["parent"] = parent
      return kernel_event("place_created", day=day, scene="s1",
                          summary=f"{pid} 创建", deltas=d, turn=1)


  def test_place_evolved_asserts_state_fact():
      reg = _reg()
      world = project(reg, [
          _place("market"),
          kernel_event("place_evolved", day=2, scene="s1", summary="market 演化",
                       deltas={"id": "market", "state": "戒严", "note": "卫兵封锁"}, turn=2),
      ])
      g = world["systems"]["ontology"]
      assert g.value_at("market", "state", day=2) == "戒严"
      assert g.get_entity("market").attrs.get("last_cascade_turn") == 2


  def test_populace_shifted_asserts_mood_fact():
      reg = _reg()
      world = project(reg, [
          _place("market"),
          kernel_event("populace_shifted", day=2, scene="s1", summary="民心",
                       deltas={"id": "market", "mood": "惶恐"}, turn=2),
      ])
      assert world["systems"]["ontology"].value_at("market", "populace", day=2) == "惶恐"


  def test_world_change_records_audit_and_fact():
      reg = _reg()
      world = project(reg, [
          _place("capital"),
          kernel_event("world_change", day=2, scene="s1", summary="王都陷落",
                       deltas={"place": "capital", "level": 1, "valence": "disaster"}, turn=2),
      ])
      slice_ = world["systems"]["cascade"]
      assert len(slice_["changes"]) == 1
      assert slice_["changes"][0]["place"] == "capital" and slice_["changes"][0]["level"] == 1
      assert world["systems"]["ontology"].value_at("capital", "world_change", day=2) == "王都陷落"


  def test_apply_defensive_on_missing_id():
      reg = _reg()
      # missing id / dangling id must NOT crash projection (invariant 11)
      world = project(reg, [
          kernel_event("place_evolved", day=1, scene="s1", summary="bad",
                       deltas={"state": "x"}, turn=1),          # no id
          kernel_event("populace_shifted", day=1, scene="s1", summary="bad",
                       deltas={"id": "ghost", "mood": "y"}, turn=1),  # dangling id
      ])
      assert world is not None  # did not raise
  ```
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement `CascadeSystem.apply(self, world, event)`** following `systems/place.py`'s defensive style: `g = world["systems"]["ontology"]`; `d = event.get("deltas", {})`; `t = event["type"]`.
  - `place_evolved`: `pid = d.get("id")`; if falsy → `log.warning(...)` + return. If `g.get_entity(pid) is None` → `log.warning("place_evolved %s dangling id=%s; skipped", ...)` + return. Else, if `d.get("state")`: `g.assert_fact(pid, "state", d["state"], day=event["day"], turn=event.get("turn") or 0, source_event=event["id"])`; always set `g.get_entity(pid).attrs["last_cascade_turn"] = event.get("turn") or 0`.
  - `populace_shifted`: same guards; if `d.get("mood")`: `g.assert_fact(pid, "populace", d["mood"], ...)`.
  - `world_change`: `place = d.get("place")`; guard place exists (warn+skip if not, but STILL append to `changes` audit only if place is non-empty — keep audit referential). Append `{"place": place, "level": d.get("level", 1), "summary": event.get("summary"), "valence": d.get("valence"), "turn": event.get("turn") or 0}` to `world["systems"]["cascade"]["changes"]`; if place valid, `g.assert_fact(place, "world_change", event.get("summary",""), ...)`.
  - `log.debug(...)` on each branch.
- [ ] **Step 4 — run, expect PASS;** then full suite gate: `python3 -m pytest -q --ignore=tests/test_embed_real.py` (still 654 + new).
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add systems/cascade.py tests/systems/test_cascade_system.py && git commit -m "feat(systems): CascadeSystem.apply projects place_evolved/populace_shifted/world_change

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 3: Register `CascadeSystem` in `build_engine`

**Files:** Modify `app/engine.py`; Modify `tests/app/test_engine.py`.

- [ ] **Step 1 — failing test** appended to `tests/app/test_engine.py` (mirror the existing build_engine tests there): build an engine in a `tmp_path` campaign dir with `provider=FakeLLMProvider()`, assert `engine.registry.owner_of_event("place_evolved").name == "cascade"` and `"world_change" in engine.registry.event_types()` and `"cascade" in engine.world["systems"]`.
- [ ] **Step 2 — run, expect FAIL** (cascade not registered).
- [ ] **Step 3 — implement:** in `app/engine.py` add `from systems.cascade import CascadeSystem` and, in `build_engine`, `registry.register(CascadeSystem())` immediately after `registry.register(DirectorSystem())`.
- [ ] **Step 4 — run, expect PASS;** full suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add app/engine.py tests/app/test_engine.py && git commit -m "feat(engine): register CascadeSystem in build_engine

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 4: `cascade_trigger` — the gated predicate (D1)

**Files:** Create `loop/cascade.py`; Create `tests/loop/test_cascade_loop.py`.

- [ ] **Step 1 — failing tests** in `tests/loop/test_cascade_loop.py`. Use a registry of ontology+place+cascade and a real graph projected from `place_created` events so `_is_place` resolves:
  ```python
  """Tests for loop.cascade (Phase C1)."""
  from __future__ import annotations

  import tempfile, os
  from kernel.registry import Registry
  from kernel.projection import project
  from kernel.events import open_store, kernel_event
  from systems.ontology import OntologySystem
  from systems.place import PlaceSystem
  from systems.cascade import CascadeSystem
  from loop.cascade import cascade_trigger


  def _reg():
      return (Registry().register(OntologySystem())
              .register(PlaceSystem()).register(CascadeSystem()))


  def _place(pid, parent=None):
      d = {"id": pid, "level": 2, "kind": "settlement", "seed": "x", "tier": "tracked"}
      if parent: d["parent"] = parent
      return kernel_event("place_created", day=1, scene="s1",
                          summary=f"{pid}", deltas=d, turn=1)


  def test_trigger_empty_when_no_significant_event():
      world = project(_reg(), [_place("town")])
      # a bare action (heuristic floor 1) on a place is below CASCADE_FLOOR
      evs = [kernel_event("action", day=1, scene="s1", summary="walk",
                          deltas={}, actors=["hero"], turn=2)]
      assert cascade_trigger(evs, world) == []


  def test_trigger_on_world_change_returns_root_place():
      world = project(_reg(), [_place("capital")])
      evs = [kernel_event("world_change", day=1, scene="s1", summary="陷落",
                          deltas={"place": "capital", "level": 1}, turn=2)]
      assert cascade_trigger(evs, world) == ["capital"]


  def test_trigger_on_entity_moved_uses_destination_place():
      world = project(_reg(), [_place("town")])
      evs = [kernel_event("entity_moved", day=1, scene="s1", summary="到达",
                          deltas={"who": "hero", "to": "town"}, turn=2)]
      assert cascade_trigger(evs, world) == ["town"]


  def test_trigger_dedupes_and_ignores_non_place_ids():
      world = project(_reg(), [_place("town")])
      evs = [
          kernel_event("place_materialized", day=1, scene="s1", summary="m",
                       deltas={"id": "town"}, turn=2),
          kernel_event("world_change", day=1, scene="s1", summary="w",
                       deltas={"place": "town", "level": 1}, turn=2),
          kernel_event("world_change", day=1, scene="s1", summary="w2",
                       deltas={"place": "nowhere"}, turn=2),  # not a Place → ignored
      ]
      assert cascade_trigger(evs, world) == ["town"]
  ```
- [ ] **Step 2 — run, expect FAIL** (no `loop/cascade.py`).
- [ ] **Step 3 — implement `loop/cascade.py`** (start the module): docstring describing the hook (mirror `loop/director.py`'s header); `from engine.log import get_logger`; `log = get_logger("loop.cascade")`; constants `CASCADE_FLOOR = 3`, `CASCADE_MAX_NODES = 6`, `CASCADE_MAX_DEPTH = 3`, `CASCADE_MAX_REGIONS = 3`; import `from memory.importance import heuristic_floor`. Implement:
  - `_TRIGGER_TYPES = {"world_change", "place_materialized", "place_created", "entity_moved"}`.
  - `_is_place(graph, pid) -> bool`: `e = graph.get_entity(pid); return e is not None and e.etype == "Place"`.
  - `_root_place(event, graph) -> str | None`: try `deltas.get("place")`, then `deltas.get("id")`, then `deltas.get("to")`; return the first that `_is_place`.
  - `cascade_trigger(new_events, world) -> list[str]`: `g = world["systems"]["ontology"]`; iterate events; an event qualifies if (`type in _TRIGGER_TYPES` or `deltas.get("world_change")`) and `heuristic_floor(event) >= CASCADE_FLOOR`; collect `_root_place`; dedupe preserving order; `log.debug("cascade_trigger → roots=%s", roots)`; return list.
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add loop/cascade.py tests/loop/test_cascade_loop.py && git commit -m "feat(cascade): gated cascade_trigger predicate (Phase C1)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 5: `lightweight_validate` — referential checks, drop-on-fail, no repair (§12 line 177)

**Files:** Modify `loop/cascade.py`; Modify `tests/loop/test_cascade_loop.py`.

- [ ] **Step 1 — failing tests** appended to `tests/loop/test_cascade_loop.py`:
  ```python
  from loop.cascade import lightweight_validate

  def test_lightweight_validate_passes_existing_id():
      g = project(_reg(), [_place("town")])["systems"]["ontology"]
      v = {"id": "town", "evolve": True, "state": "繁荣", "populace_mood": "安宁"}
      assert lightweight_validate(v, g, allowed_ids=set()) == v

  def test_lightweight_validate_passes_allowed_id():
      g = project(_reg(), [_place("town")])["systems"]["ontology"]
      v = {"id": "new_child", "evolve": True, "state": "x"}
      assert lightweight_validate(v, g, allowed_ids={"new_child"}) == v

  def test_lightweight_validate_drops_dangling_id():
      g = project(_reg(), [_place("town")])["systems"]["ontology"]
      v = {"id": "ghost", "evolve": True, "state": "x"}
      assert lightweight_validate(v, g, allowed_ids=set()) is None

  def test_lightweight_validate_drops_missing_id():
      g = project(_reg(), [_place("town")])["systems"]["ontology"]
      assert lightweight_validate({"evolve": True}, g, allowed_ids=set()) is None
  ```
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement `lightweight_validate(verdict, graph, allowed_ids) -> dict | None`** in `loop/cascade.py`: if `verdict` is not a dict → warn+return None; `pid = verdict.get("id")`; if not `pid` → `log.warning("cascade verdict missing id; dropped")` + None; if `graph.get_entity(pid) is None and pid not in allowed_ids` → `log.warning("cascade verdict dangling place=%s; dropped", pid)` + None; (C2 will also check `chain[].place`); else return `verdict`. **No repair loop, ever.**
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add loop/cascade.py tests/loop/test_cascade_loop.py && git commit -m "feat(cascade): lightweight referential validation (drop-on-fail, no repair)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 6: `run_cascade` — vertical descent walker (C1 core, blocking)

**Files:** Modify `loop/cascade.py`; Modify `tests/loop/test_cascade_loop.py`.

Design notes the implementation must honor:
- Children of a place = entities `c` with a `contained_by` relation `c → place` valid at `day`. There is no `children(parent)` helper, so implement `_children(graph, parent, day)` by scanning `graph.relations` for `r.rel == "contained_by" and r.dst == parent and r.valid_at(day)` and returning `r.src` where `_is_place`.
- Walk is **breadth-first** from the de-duped trigger roots' children (the roots themselves already changed; we ripple INTO their sub-places). Maintain a `budget = CASCADE_MAX_NODES`; pop frontier nodes; for each, if budget == 0 → stop, record roll-up; else spend one budget, call the cheap LLM for that node's verdict, lightweight-validate, and on a valid `evolve:true` verdict emit `place_evolved` (and `populace_shifted` if `populace_mood` present) and enqueue that node's children onto the frontier; on `evolve:false` record a prune roll-up and do NOT descend further (pruning caps the subtree per §10 "剪枝盖 roll-up 戳").
- The per-node LLM call: `provider_for_cascade.complete_json(_NODE_SYSTEM, _node_prompt(place_id, context), _NODE_SCHEMA)`. `_node_prompt` MUST embed `place_id` verbatim (D5 keyed-fake requirement) plus the parent change summary. `_NODE_SCHEMA` documents `{evolve: bool, state: str, populace_mood: str, note: str}` (C2 adds `spread`, `magnitude`).
- Events are appended through `store.append` (strict store enforces registration). `day` = max day in `new_events` (fallback to `world["meta"]["day"]` or 1). `turn` = `_next_cascade_turn(store)` = max existing turn + 1 (cascade events occupy their own turn slot, like the director's fire). `scene` = passed-in scene id.
- Signature: `run_cascade(registry, store, world, *, scene, provider, cascade_provider=None, max_subagents=4) -> list[dict]`. `cp = cascade_provider or provider`. C1 ignores `max_subagents` (single-threaded). Returns appended events (possibly empty).
- The hook reads `new_events` from the store: the turn's just-applied events are those with `turn == _last_player_turn`. To keep the hook self-contained and match `run_director` (which re-derives from the store), accept `new_events` is computed by the caller OR recompute: **recompute** — `events = list(store.iter_events())`, `player_turn = max((e["turn"] or 0) for e in events if e["type"] not in self-owned cascade/oracle types ...)`. Simpler and tested: pass `new_events` explicitly is brittle; instead compute `roots = cascade_trigger([e for e in events if (e.get("turn") or 0) == _last_nonharness_turn(events)], world)`. Implement `_last_nonharness_turn(events)` = max turn among events whose type is NOT in `{"place_evolved","populace_shifted","world_change","oracle_roll","director_fired","character_evolved"}` (the harness-authored set), default 0. This mirrors how the director isolates player turns and keeps cascade from re-triggering on its OWN output.

- [ ] **Step 1 — failing tests** appended to `tests/loop/test_cascade_loop.py`. Build a fixed tree: `capital` (root) ⊃ `market`, `temple`; `market` ⊃ `stall`. Use a **keyed** fake provider so verdicts don't depend on call order:
  ```python
  import json
  from llm.provider import LLMProvider

  class KeyedFakeProvider(LLMProvider):
      """Returns a verdict chosen by the place id embedded in the user prompt.
      Order-independent → safe for the C2 ThreadPoolExecutor too (D5)."""
      def __init__(self, by_place: dict, default: dict | None = None):
          self.by_place = by_place
          self.default = default or {"evolve": False}
          self.calls = []
      def complete(self, system, user, *, model=None, max_tokens=None):
          return ""
      def complete_json(self, system, user, schema, **kw):
          self.calls.append((system, user))
          for pid, verdict in self.by_place.items():
              if pid in user:
                  return dict(verdict, id=pid)
          return dict(self.default)

  def _store(reg):
      d = tempfile.mkdtemp()
      return open_store(os.path.join(d, "events.db"),
                        os.path.join(d, "events.jsonl"), allowed_types=reg.event_types())

  def _tree_events():
      return [
          _place("capital"),
          _place("market", parent="capital"),
          _place("temple", parent="capital"),
          _place("stall", parent="market"),
      ]

  def test_run_cascade_visits_children_and_emits_place_evolved():
      from loop.cascade import run_cascade
      reg = _reg(); store = _store(reg)
      for e in _tree_events(): store.append(e)
      wc = kernel_event("world_change", day=1, scene="capital", summary="王都陷落",
                        deltas={"place": "capital", "level": 1}, turn=2)
      store.append(wc)
      world = project(reg, store.iter_events())
      prov = KeyedFakeProvider(by_place={
          "market": {"evolve": True, "state": "戒严", "populace_mood": "惶恐"},
          "temple": {"evolve": True, "state": "闭门", "populace_mood": "祈祷"},
          "stall":  {"evolve": True, "state": "歇业"},
      })
      appended = run_cascade(reg, store, world, scene="capital", provider=prov)
      evolved = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
      assert evolved == {"market", "temple", "stall"}   # full vertical coverage
      assert any(e["type"] == "populace_shifted" for e in appended)
      world2 = project(reg, store.iter_events())
      assert world2["systems"]["ontology"].value_at("market", "state", day=1) == "戒严"

  def test_run_cascade_prune_stops_descent():
      from loop.cascade import run_cascade
      reg = _reg(); store = _store(reg)
      for e in _tree_events(): store.append(e)
      store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                deltas={"place": "capital", "level": 1}, turn=2))
      world = project(reg, store.iter_events())
      # market prunes (evolve False) → its child 'stall' must NOT be visited
      prov = KeyedFakeProvider(by_place={
          "market": {"evolve": False},
          "temple": {"evolve": True, "state": "闭门"},
      })
      appended = run_cascade(reg, store, world, scene="capital", provider=prov)
      assert "stall" not in {c[1] for c in prov.calls if "stall" in c[1]} or \
          all("stall" not in u for _, u in prov.calls)   # stall never queried
      evolved = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
      assert evolved == {"temple"}

  def test_run_cascade_respects_node_budget():
      from loop.cascade import run_cascade
      import loop.cascade as cmod
      reg = _reg(); store = _store(reg)
      # wide tree: capital ⊃ p1..p10
      store.append(_place("capital"))
      for i in range(10):
          store.append(_place(f"p{i}", parent="capital"))
      store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                deltas={"place": "capital", "level": 1}, turn=2))
      world = project(reg, store.iter_events())
      prov = KeyedFakeProvider(by_place={f"p{i}": {"evolve": True, "state": "s"} for i in range(10)})
      appended = run_cascade(reg, store, world, scene="capital", provider=prov)
      # budget caps LLM calls at CASCADE_MAX_NODES (no silent over-spend)
      assert len(prov.calls) <= cmod.CASCADE_MAX_NODES

  def test_run_cascade_no_trigger_returns_empty():
      from loop.cascade import run_cascade
      reg = _reg(); store = _store(reg)
      store.append(_place("town"))
      store.append(kernel_event("action", day=1, scene="s1", summary="idle",
                                deltas={}, actors=["hero"], turn=2))
      world = project(reg, store.iter_events())
      prov = KeyedFakeProvider(by_place={})
      assert run_cascade(reg, store, world, scene="s1", provider=prov) == []

  def test_run_cascade_drops_invalid_verdict():
      from loop.cascade import run_cascade
      reg = _reg(); store = _store(reg)
      for e in _tree_events(): store.append(e)
      store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                deltas={"place": "capital", "level": 1}, turn=2))
      world = project(reg, store.iter_events())
      # market verdict returns a bogus id (simulate hallucination): patch by returning
      # a verdict whose id is overwritten to a dangling ref via default
      class BadProv(KeyedFakeProvider):
          def complete_json(self, system, user, schema, **kw):
              self.calls.append((system, user))
              if "market" in user:
                  return {"id": "HALLUCINATED", "evolve": True, "state": "x"}
              if "temple" in user:
                  return {"id": "temple", "evolve": True, "state": "ok"}
              return {"evolve": False}
      prov = BadProv(by_place={})
      appended = run_cascade(reg, store, world, scene="capital", provider=prov)
      ids = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
      assert "HALLUCINATED" not in ids and ids == {"temple"}   # dropped, not repaired
  ```
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement the walker** in `loop/cascade.py` per the design notes above: `_children`, `_node_prompt`, `_NODE_SYSTEM`, `_NODE_SCHEMA`, `_last_nonharness_turn`, `_next_cascade_turn`, and `run_cascade(...)` doing the budget-bounded BFS, lightweight-validate, emit `place_evolved`/`populace_shifted`, prune-on-`evolve:false`, append through store, return appended. Log budget exhaustion + prunes at info/warning. Keep C2 hooks (`spread`/`magnitude`/queue/threads) absent for now.
- [ ] **Step 4 — run, expect PASS;** full suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add loop/cascade.py tests/loop/test_cascade_loop.py && git commit -m "feat(cascade): run_cascade vertical descent walker (budget, prune, drop-on-fail) [C1]

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 7: Wire `run_cascade` into `run_turn` (post-apply, after director)

**Files:** Modify `loop/turn.py`; Modify `tests/loop/test_turn.py`.

- [ ] **Step 1 — failing test** appended to `tests/loop/test_turn.py` (reuse `_reg_with_director`-style helper but also register cascade — add a local `_reg_with_cascade()` that registers ontology+place+character+director+cascade). Seed a `capital ⊃ market` tree, run a turn whose commit moves the protagonist into `capital` (a trigger root via `entity_moved`), with a `FakeLLMProvider` whose narrator JSON is a minimal valid commit AND whose cascade calls return an `evolve:true` verdict for `market`. Simplest deterministic route: monkeypatch `loop.cascade.run_cascade` is NOT needed — instead pass a provider that the cascade can use. Because `run_turn` passes the SAME `provider` to cascade by default, use a `KeyedFakeProvider`-like provider that returns a valid commit for the narrator call (detect by the absence of a place id / presence of narration schema) — simpler: assert at the integration level only that cascade RAN and appended, by checking the store gained a `place_evolved` event after a triggering turn. Mirror `test_run_turn_invokes_director_and_next_turn_sees_directive`:
  ```python
  def test_run_turn_invokes_cascade_on_triggering_turn(monkeypatch):
      """A turn that moves the protagonist into a place with children triggers the
      vertical cascade post-apply; a place_evolved event lands in the store."""
      import loop.cascade as cmod
      from loop.turn import run_turn
      from loop.strategy import AuthorStrategy
      # ... build reg with cascade, seed capital ⊃ market, protagonist created ...
      # Stub the per-node verdict so the integration test is deterministic and does
      # not depend on the narrator provider's response cycling:
      monkeypatch.setattr(cmod, "_node_verdict",
                          lambda place_id, ctx, provider: {"id": place_id,
                          "evolve": True, "state": "动荡"} if place_id == "market" else {"evolve": False})
      # narrator commit moves hero into capital
      provider = FakeLLMProvider(json_responses=[{
          "narration": "你步入王都。", "moves": [{"who": "hero", "to": "capital"}],
          "places": [], "cast": [], "facts": [],
      }])
      result = run_turn(reg, store, world, scene, "进入王都",
                        strategy=AuthorStrategy(), provider=provider,
                        embedder=None, max_repairs=1)
      all_events = list(store.iter_events())
      assert any(e["type"] == "place_evolved" and e["deltas"]["id"] == "market"
                 for e in all_events)
  ```
  (To support the monkeypatch, Task 6's `run_cascade` must call a small extracted `_node_verdict(place_id, ctx, provider) -> dict` helper for the per-node LLM call — add that seam in Task 6's implementation. If it was not extracted, extract it now as part of this task's impl step and keep Task 6 tests green.)
- [ ] **Step 2 — run, expect FAIL** (cascade not wired into `run_turn`).
- [ ] **Step 3 — implement:** in `loop/turn.py`, `from loop.cascade import run_cascade`. After the existing director `try/except` block and **inside** the same `with get_tracer().span("turn", ...)`, add:
  ```python
  # §10 波状传播 (Phase C): a significant world-change ripples down nested
  # places. Same shape as digest_fleet/run_director: post-apply, tracer span,
  # never fatal, re-project on append.
  try:
      with get_tracer().span("cascade", turn=turn_num_before):
          cas_events = run_cascade(registry, store, new_world,
                                   scene=scene_id, provider=provider)
      if cas_events:
          new_world = project(registry, store.iter_events())
          log.debug("run_turn: cascade appended %d event(s)", len(cas_events))
  except Exception:
      log.exception("run_turn: run_cascade failed (non-fatal, backstage)")
  ```
  Place it AFTER the director block (D4 ordering). Do not change anything else.
- [ ] **Step 4 — run, expect PASS;** full suite gate green (654 baseline + all new still green — confirm `test_run_turn_invokes_director_and_next_turn_sees_directive` and the two-turn sequence test still pass, since cascade now also runs on those turns: verify those turns do NOT trip the trigger, or that their fake providers tolerate an extra `place_evolved`. If a pre-existing turn test now trips cascade unexpectedly, the fix is in the TEST's provider/fixtures only — never weaken the trigger. Most existing turn tests use a `town` location with no children, so the vertical walk finds no children and appends nothing.)
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add loop/turn.py tests/loop/test_turn.py && git commit -m "feat(loop): wire run_cascade into run_turn post-apply (after director) [C1]

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## C1 done criteria
- Full suite green (`python3 -m pytest -q --ignore=tests/test_embed_real.py`): the 654 baseline + all new tests.
- A triggering turn ripples one cheap LLM call per child place (BFS, capped at `CASCADE_MAX_NODES`), prunes stop descent, hallucinated/dangling verdicts are dropped (not repaired), and `place_evolved`/`populace_shifted` facts appear in the re-projected world.
- No game logic in `kernel/`; cascade writes only the shared graph + its own slice; harness-authored events bypass the §11 strict gate but pass the strict store via registration.

---

# PART C2 — Parallel fan-out + horizontal chain + lazy-deferred queue (layered on C1)

> Each C2 task keeps every C1 test green. C2 ADDS, without changing C1's event shapes or the vertical walk: (8) **configurable parallel fan-out** of the per-round `_node_verdict` calls; (9) the **horizontal chain** (adjacent-region `world_change` at `level+1`, depth≤3, merge-same-region); (10) the **lazy-deferred overflow queue** (event-sourced, drained at next-turn start).
>
> **Decomposition rationale (stated, per the spec's "adjust if cleaner").** The suggested split is followed exactly: Task 8 = parallel fan-out + configurable concurrency, Task 9 = horizontal chain + merge-same-region, Task 10 = lazy-deferred queue. Task 8 lands FIRST because it only refactors the shape of the per-round node loop (collect-then-mutate) — that same collect-then-mutate shape is the seam the horizontal hop (9) and the deferral queue (10) both bolt onto, so doing it first avoids re-touching the loop three times.
>
> ### ⚙️ Two load-bearing facts about the SHIPPED C1 code these tasks extend (read `loop/cascade.py` first)
> 1. **The breadth cap shipped as the per-ROUND `CASCADE_BREADTH = 6`** (applied at each BFS frontier level inside `_vertical_bfs`), NOT a global `CASCADE_MAX_NODES`. `CASCADE_FLOOR` shipped as **2** (a `world_change`/`entity_moved`/`place_created` scores 2 via `heuristic_floor`; a bare `action` scores 1). C2 tasks/tests therefore reference `cmod.CASCADE_BREADTH`, `cmod.CASCADE_MAX_DEPTH`, `cmod.CASCADE_MAX_REGIONS` — **never** `CASCADE_MAX_NODES` (it does not exist in the shipped module). The total per-turn budget the addendum asks for is realized in Task 10 as `CASCADE_NODE_BUDGET` (see there); the per-round cap stays `CASCADE_BREADTH`.
> 2. **The walker already extracts the parallelizable seam.** `_node_verdict(place_id, ctx, provider) -> dict` is a pure, side-effect-free standalone function (no shared state). `_vertical_bfs(roots, graph, day, ctx, provider, store, scene, turn)` owns the BFS, the per-round breadth cap, validation, emission, and child enqueue. `run_cascade(registry, store, world, *, scene, provider, cascade_provider=None, max_subagents=4)` is the public hook; `cascade_provider or provider` chooses the cheap model. The shipped `run_turn` call site already passes `cascade_provider=`; it does NOT pass `max_subagents`. **Tasks must extend these exact names, not reintroduce the old draft's API.**

## Task 8: Parallel fan-out of `_node_verdict` over a CONFIGURABLE `max_concurrency` (ThreadPoolExecutor)

**Goal:** Within each BFS round, submit that round's per-node `_node_verdict` calls to a `concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency)`, collect the raw verdicts, THEN (single-threaded, in the main thread, after the round's futures resolve) lightweight-validate, emit events, and compute the next frontier. NO shared mutable state is touched concurrently. `max_concurrency` is configurable: a new `run_cascade` param defaulting to env `RPG_CASCADE_CONCURRENCY` (fallback **3**, because Zhipu allows max 3 concurrent) — the architecture supports >3 when the provider permits.

**Files:** Modify `loop/cascade.py`; Modify `tests/loop/test_cascade_loop.py`.

Design notes the implementation must honor:
- **The `_node_verdict` seam is the unit of parallelism** and is already pure — DO NOT make it append to the store or mutate the graph. Submit `_node_verdict(place_id, ctx, cp)` per round-node to the pool; gather `(place_id, raw_verdict)` pairs (handle a worker exception per-node: catch it, log `log.warning`, treat that node as "no verdict" → skipped, identical to the shipped sequential `try/except`). Only AFTER all futures in the round resolve does the main thread loop the gathered pairs to `lightweight_validate` → `store.append(place_evolved/populace_shifted)` → enqueue children. `store.append` / `graph` mutations therefore stay 100% single-threaded.
- **Configurable concurrency.** Add to `run_cascade`'s signature: `max_concurrency: int | None = None`. Resolve once at the top of `run_cascade`: `conc = _resolve_concurrency(max_concurrency, max_subagents)`. Implement `_resolve_concurrency(explicit, max_subagents) -> int`: if `explicit` is not None → `max(1, int(explicit))`; else read `os.environ.get("RPG_CASCADE_CONCURRENCY")` (parse int, guard `ValueError`) → `max(1, that)`; else if `max_subagents` → `max(1, int(max_subagents))`; else `3`. (Order: explicit arg > env > legacy `max_subagents` > 3.) `import os` at module top. Pass `conc` down into `_vertical_bfs(...)` as a new `max_concurrency` arg. Keep `max_subagents` in the signature as an accepted back-compat alias — do NOT remove it (the Self-Review consistency note and any existing call sites depend on the signature not shrinking).
- **Determinism is preserved by the round structure, not by thread order.** The per-round breadth cap (`CASCADE_BREADTH`) still slices the frontier to ≤6 BEFORE submitting to the pool (cap is applied in the main thread). Children are enqueued in the main thread in a fixed iteration order over the (already order-stable) round frontier, so the SET of emitted events is invariant under thread scheduling. Tests assert on the SET, never the sequence (D5).
- **Thread-safe keyed fake provider (D5 — mandatory).** `FakeLLMProvider._json_idx` is a plain int mutated with no lock; under the pool it races, so its response→node assignment is nondeterministic. Tests in this task and Tasks 9/10 MUST use the `KeyedFakeProvider` already defined in Task 6's test file (verdict keyed by the `place_id` embedded in the prompt by `_node_prompt`), which is order-independent and therefore thread-safe. Reuse that class — do not redefine it. (Its `complete_json` only does `self.calls.append(...)` then a dict lookup; `list.append` under the GIL is atomic enough for a `len(provider.calls)` count assertion, but tests still assert on the outcome SET, not call order.)

- [ ] **Step 1 — failing tests** appended to `tests/loop/test_cascade_loop.py` (reuse `_reg`, `_place`, `_store`, `KeyedFakeProvider` from earlier in the file):
  ```python
  def test_run_cascade_parallel_fanout_outcome_set():
      """Wide single round fanned out over a pool; assert the SET of evolved ids
      (order-independent) and that the pool did not over-spend the round budget."""
      from loop.cascade import run_cascade
      import loop.cascade as cmod
      reg = _reg(); store = _store(reg)
      store.append(_place("capital"))
      for i in range(5):
          store.append(_place(f"p{i}", parent="capital"))
      store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                deltas={"place": "capital", "level": 1}, turn=2))
      world = project(reg, store.iter_events())
      prov = KeyedFakeProvider(by_place={f"p{i}": {"evolve": True, "state": "s"} for i in range(5)})
      appended = run_cascade(reg, store, world, scene="capital", provider=prov,
                             max_concurrency=4)
      evolved = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
      assert evolved == {"p0", "p1", "p2", "p3", "p4"}      # SET, not order
      assert len(prov.calls) <= cmod.CASCADE_BREADTH        # one round, breadth-capped

  def test_run_cascade_parallel_outcome_is_thread_schedule_invariant():
      """Same tree + keyed provider over two runs → identical SET of (type, id)."""
      from loop.cascade import run_cascade
      def run_once():
          reg = _reg(); store = _store(reg)
          for e in _tree_events(): store.append(e)
          store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                    deltas={"place": "capital", "level": 1}, turn=2))
          world = project(reg, store.iter_events())
          prov = KeyedFakeProvider(by_place={
              "market": {"evolve": True, "state": "戒严", "populace_mood": "惶恐"},
              "temple": {"evolve": True, "state": "闭门"},
              "stall":  {"evolve": True, "state": "歇业"},
          })
          ap = run_cascade(reg, store, world, scene="capital", provider=prov,
                           max_concurrency=4)
          return sorted((e["type"], e["deltas"].get("id")) for e in ap)
      assert run_once() == run_once()

  def test_resolve_concurrency_precedence(monkeypatch):
      import loop.cascade as cmod
      # explicit arg wins
      assert cmod._resolve_concurrency(7, 4) == 7
      # else env
      monkeypatch.setenv("RPG_CASCADE_CONCURRENCY", "5")
      assert cmod._resolve_concurrency(None, 4) == 5
      # bad env → falls through to max_subagents
      monkeypatch.setenv("RPG_CASCADE_CONCURRENCY", "oops")
      assert cmod._resolve_concurrency(None, 4) == 4
      # nothing set → default 3
      monkeypatch.delenv("RPG_CASCADE_CONCURRENCY", raising=False)
      assert cmod._resolve_concurrency(None, None) == 3
  ```
- [ ] **Step 2 — run, expect FAIL:** `cd /root/rpg-engine-app && python3 -m pytest -q tests/loop/test_cascade_loop.py` → `_resolve_concurrency` / `max_concurrency` kwarg do not exist yet (TypeError / AttributeError).
- [ ] **Step 3 — implement** in `loop/cascade.py`:
  - Add `import os` and `import concurrent.futures` at the module top (stdlib, no new dep).
  - Add `_resolve_concurrency(explicit, max_subagents) -> int` exactly as the design note specifies (explicit > env `RPG_CASCADE_CONCURRENCY` > `max_subagents` > 3; all clamped to `>= 1`; `ValueError` on a bad env value falls through).
  - In `run_cascade`, add `max_concurrency: int | None = None` to the keyword-only params (after `max_subagents`); compute `conc = _resolve_concurrency(max_concurrency, max_subagents)`; pass `max_concurrency=conc` into the `_vertical_bfs(...)` call.
  - In `_vertical_bfs`, add a `max_concurrency: int = 3` parameter. Replace the per-frontier sequential `for place_id in frontier:` body with a two-phase round: **(phase 1, parallel)** build the breadth-capped frontier slice (keep the existing `CASCADE_BREADTH` cap + `log.info` skip note exactly as shipped), then
    ```python
    results: list[tuple[str, dict | None]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_concurrency)) as ex:
        fut_to_pid = {ex.submit(_node_verdict, pid, ctx, provider): pid for pid in frontier}
        for fut in concurrent.futures.as_completed(fut_to_pid):
            pid = fut_to_pid[fut]
            try:
                results.append((pid, fut.result()))
            except Exception as exc:
                log.warning("cascade: _node_verdict raised for place=%s (%s); dropping node", pid, exc)
    # Re-sort into the deterministic frontier order so emission/child-enqueue
    # order does not depend on thread completion order.
    by_pid = dict(results)
    ordered = [(pid, by_pid[pid]) for pid in frontier if pid in by_pid]
    ```
    **(phase 2, single-threaded main thread)** loop `ordered`: `lightweight_validate` → on None skip; on `evolve:false` prune+roll-up; on `evolve:true` append `place_evolved` (+ `populace_shifted`) and enqueue children — i.e. the EXACT emission/prune/child-enqueue code the shipped sequential loop already runs, just moved below the pool. NO `store.append` or graph access inside the executor.
  - The comment block above the pool must state: *the per-node LLM call releases the GIL (urllib I/O) so real providers overlap; results are collected then mutated sequentially in the main thread; FakeLLMProvider's call counter is never relied on for response→node assignment (D5).*
- [ ] **Step 4 — run, expect PASS;** then determinism gate ×3 (`cd /root/rpg-engine-app && python3 -m pytest -q tests/loop/test_cascade_loop.py` run three times) and full-suite gate `cd /root/rpg-engine-app && python3 -m pytest -q --ignore=tests/test_embed_real.py` (654 baseline + C1 + new still green). Confirm every C1 test (`test_run_cascade_visits_children_and_emits_place_evolved`, `test_run_cascade_prune_stops_descent`, `test_run_cascade_respects_node_budget` now reading `CASCADE_BREADTH`, etc.) stays green — the refactor must not change C1 outcomes.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add loop/cascade.py tests/loop/test_cascade_loop.py && git commit -m "feat(cascade): parallel per-round fan-out over configurable max_concurrency (ThreadPoolExecutor) [C2]

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 9: Horizontal chain — strong verdict spawns an adjacent-region `world_change` (level+1, depth≤3, merge same-region)

**Goal:** After the vertical descent, a sufficiently-large change spawns a `world_change` on an ADJACENT region at `level+1`, bounded by `CASCADE_MAX_DEPTH=3` horizontal rounds and `CASCADE_MAX_REGIONS=3` regions/turn, with same-region hops merged into one node call.

**Adjacency mechanism (DECISION — grounded in the shipped graph, not invented).** Two real relations exist in `systems/place.py`: `contained_by` (child→parent) and **`adjacent_to`** (symmetric, multi-valued, written by the `place_linked` event, carries a `travel_cost` attr, queried via `graph.relation_attrs_at(src, "adjacent_to", day)` / `graph.neighbors(src, "adjacent_to", day)`). `adjacent_to` IS an explicit neighbor relation, so we use it as the primary horizontal axis — this is exactly the relation `navigate()` (Dijkstra) and `PlaceSystem.inject` ("出口") already treat as "the place next door". Define `_adjacent_regions(graph, place_id, day) -> list[str]`:
  1. **explicit neighbors:** `[n for n in graph.neighbors(place_id, "adjacent_to", day) if _is_place(graph, n)]`;
  2. **plus same-parent siblings:** the place's `contained_by` parent(s) at `day` (scan `graph.relations` for `r.rel=="contained_by" and r.src==place_id and r.valid_at(day)` → parent), then that parent's other `_children` (excluding `place_id` itself).
  Return the de-duped union preserving order (explicit neighbors first). This satisfies the spec: an explicit relation exists, so we use it, and we ALSO honor "same-parent siblings" — we do NOT invent any relation that is not in `place.py`.

**Files:** Modify `loop/cascade.py`; Modify `tests/loop/test_cascade_loop.py`.

Design notes the implementation must honor:
- **What promotes.** A vertical verdict promotes to a horizontal hop iff it is a valid `evolve:true` verdict carrying `spread: true`. Extend `_NODE_SCHEMA` with `"spread": {"type": "boolean"}` and `"magnitude": {"type": "string"}` (both optional, NOT in `required`), and extend `_node_prompt` to mention that a large/spreading change may set `spread:true`. `magnitude` is recorded for audit but does not gate (the human's "sufficiently-large" = the model's `spread:true`; keep the gate one boolean so the cheap model is reliable).
- **Level accounting.** The horizontal `level` originates from the triggering `world_change`'s `deltas["level"]` (default 1) — capture it in `run_cascade` as `root_level` and pass it into `_vertical_bfs`. A hop emits a `world_change` at `level = root_level + 1`. The walker refuses a hop when `root_level + 1 > CASCADE_MAX_DEPTH` (default 3) — `log.info("cascade: chain hop to %s pruned at depth %d (> CASCADE_MAX_DEPTH)", region, root_level + 1)` and records a roll-up note; no event.
- **Merge same-region (`_merge_same_region`).** Collect hop intents during phase-2 of the vertical rounds into a per-run dict `chain_targets: dict[str, dict]` keyed by region id, so two children spreading toward the SAME neighbor collapse to ONE entry (last-writer-wins on the note; level is identical). After the vertical BFS completes, build the final hop list via `_merge_same_region(nodes, graph, day) -> list` (dedupe by region, drop any region that already changed this cascade — i.e. is in `allowed_ids` / was a root — and any non-`_is_place`), then emit at most `CASCADE_MAX_REGIONS` `world_change` events (in stable order); when more than `CASCADE_MAX_REGIONS` distinct regions are pending, `log.info("cascade: region cap %d hit; %d adjacent region(s) deferred/dropped: %s", ...)` (no silent truncation). Add the helper `_merge_same_region(nodes, graph, day)` even if the dedupe could be inlined — the spec names it explicitly and Task 10 reuses it for queued hops.
- **Emission.** Each surviving hop appends a `world_change` via `kernel_event("world_change", day=day, scene=scene, summary=f"{region} 受波及", deltas={"place": region, "level": root_level+1, "valence": <from source>, "magnitude": <verdict magnitude>}, turn=turn)` through `store.append`. `CascadeSystem.apply` (already shipped) folds it into the slice `changes` and asserts the `world_change` fact — NO change to `systems/cascade.py` is needed for the horizontal emit itself.
- **This task does NOT descend the neighbor.** Emitting the adjacent `world_change` is the horizontal step; whether that neighbor's own sub-tree is descended now or deferred is Task 10's concern. For Task 9, the neighbor `world_change` lands in the store + slice and that is the asserted outcome.

- [ ] **Step 1 — failing tests** appended to `tests/loop/test_cascade_loop.py`. Helper to link places (the `place_linked` event creates symmetric `adjacent_to`):
  ```python
  def _link(a, b, cost=1):
      return kernel_event("place_linked", day=1, scene="s1", summary=f"{a}-{b}",
                          deltas={"a": a, "b": b, "travel_cost": cost}, turn=1)

  def test_horizontal_chain_emits_adjacent_world_change_level_plus_1():
      from loop.cascade import run_cascade
      reg = _reg(); store = _store(reg)
      store.append(_place("capital")); store.append(_place("market", parent="capital"))
      store.append(_place("outskirts"))            # a separate region
      store.append(_link("capital", "outskirts"))  # capital ↔ outskirts (adjacent_to)
      store.append(kernel_event("world_change", day=1, scene="capital", summary="陷落",
                                deltas={"place": "capital", "level": 1, "valence": "disaster"}, turn=2))
      world = project(reg, store.iter_events())
      prov = KeyedFakeProvider(by_place={
          "market": {"evolve": True, "state": "暴动", "spread": True, "magnitude": "big"},
      })
      appended = run_cascade(reg, store, world, scene="capital", provider=prov)
      hops = [e for e in appended if e["type"] == "world_change"]
      assert any(e["deltas"]["place"] == "outskirts" and e["deltas"]["level"] == 2 for e in hops)

  def test_horizontal_chain_depth_cap_blocks_at_max_depth():
      """A root world_change already at CASCADE_MAX_DEPTH cannot spawn a deeper hop."""
      from loop.cascade import run_cascade
      import loop.cascade as cmod
      reg = _reg(); store = _store(reg)
      store.append(_place("capital")); store.append(_place("market", parent="capital"))
      store.append(_place("outskirts")); store.append(_link("capital", "outskirts"))
      store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                deltas={"place": "capital", "level": cmod.CASCADE_MAX_DEPTH}, turn=2))
      world = project(reg, store.iter_events())
      prov = KeyedFakeProvider(by_place={
          "market": {"evolve": True, "state": "s", "spread": True},
      })
      appended = run_cascade(reg, store, world, scene="capital", provider=prov)
      assert [e for e in appended if e["type"] == "world_change"
              and e["deltas"]["place"] == "outskirts"] == []   # depth-capped, no hop

  def test_horizontal_chain_merges_same_region():
      """Two spreading children both adjacent to the SAME region → ONE world_change."""
      from loop.cascade import run_cascade
      reg = _reg(); store = _store(reg)
      store.append(_place("capital"))
      store.append(_place("market", parent="capital"))
      store.append(_place("docks", parent="capital"))
      store.append(_place("outskirts"))
      store.append(_link("market", "outskirts")); store.append(_link("docks", "outskirts"))
      store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                deltas={"place": "capital", "level": 1}, turn=2))
      world = project(reg, store.iter_events())
      prov = KeyedFakeProvider(by_place={
          "market": {"evolve": True, "state": "暴动", "spread": True},
          "docks":  {"evolve": True, "state": "罢工", "spread": True},
      })
      appended = run_cascade(reg, store, world, scene="capital", provider=prov)
      hops = [e for e in appended if e["type"] == "world_change" and e["deltas"]["place"] == "outskirts"]
      assert len(hops) == 1     # merged, not two

  def test_horizontal_chain_region_cap():
      """More than CASCADE_MAX_REGIONS distinct adjacent targets → at most that many hops."""
      from loop.cascade import run_cascade
      import loop.cascade as cmod
      reg = _reg(); store = _store(reg)
      store.append(_place("capital")); store.append(_place("market", parent="capital"))
      targets = [f"region{i}" for i in range(cmod.CASCADE_MAX_REGIONS + 2)]
      for r in targets:
          store.append(_place(r)); store.append(_link("market", r))
      store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                deltas={"place": "capital", "level": 1}, turn=2))
      world = project(reg, store.iter_events())
      prov = KeyedFakeProvider(by_place={"market": {"evolve": True, "state": "s", "spread": True}})
      appended = run_cascade(reg, store, world, scene="capital", provider=prov)
      hops = [e for e in appended if e["type"] == "world_change" and e["deltas"]["place"] in targets]
      assert len(hops) <= cmod.CASCADE_MAX_REGIONS

  def test_no_spread_no_horizontal_hop():
      """evolve:true WITHOUT spread:true must NOT emit any adjacent world_change (regression
      guard: C1 verdicts without spread stay purely vertical)."""
      from loop.cascade import run_cascade
      reg = _reg(); store = _store(reg)
      store.append(_place("capital")); store.append(_place("market", parent="capital"))
      store.append(_place("outskirts")); store.append(_link("capital", "outskirts"))
      store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                deltas={"place": "capital", "level": 1}, turn=2))
      world = project(reg, store.iter_events())
      prov = KeyedFakeProvider(by_place={"market": {"evolve": True, "state": "s"}})  # no spread
      appended = run_cascade(reg, store, world, scene="capital", provider=prov)
      assert [e for e in appended if e["type"] == "world_change"] == []
  ```
- [ ] **Step 2 — run, expect FAIL** (no `spread` handling, no `_adjacent_regions`/`_merge_same_region`, no hop emission yet).
- [ ] **Step 3 — implement** in `loop/cascade.py`:
  - Extend `_NODE_SCHEMA` with optional `spread`/`magnitude` and extend `_node_prompt` to invite `spread:true` for a large/spreading change (keep the `place_id` embedded verbatim — D5).
  - Add `_adjacent_regions(graph, place_id, day) -> list[str]` (explicit `adjacent_to` neighbors ∪ same-parent siblings, de-duped, `_is_place`-filtered) exactly as the Adjacency-mechanism decision specifies.
  - Add `_merge_same_region(nodes, graph, day) -> list` that takes a list of hop-intent dicts `{"region": str, "level": int, "valence": ..., "magnitude": ...}`, dedupes by `region` (last-writer-wins), drops non-`_is_place` regions, and returns the merged list in stable first-seen order.
  - In `_vertical_bfs`: thread a new `root_level: int` param and an accumulator `chain_targets: dict[str, dict]`. In phase-2, when a valid `evolve:true` verdict has `spread` truthy: for each `region in _adjacent_regions(graph, place_id, day)` that is not already changed this cascade (`region not in allowed_ids` and not a root) record `chain_targets[region] = {"region": region, "level": root_level + 1, "valence": <source valence>, "magnitude": verdict.get("magnitude")}` (the dict-keyed write IS the merge). AFTER the BFS loop, call `_merge_same_region(list(chain_targets.values()), graph, day)`; for each merged hop with `level <= CASCADE_MAX_DEPTH` (and within the first `CASCADE_MAX_REGIONS`), append a `world_change` event (design-note emission); log the depth prune and the region-cap deferral. Return the appended list including hop events. Pass `root_level` from `run_cascade` (derive it: the max `deltas["level"]` among this turn's trigger `world_change` events, default 1).
- [ ] **Step 4 — run, expect PASS;** determinism gate ×3 + full-suite gate `cd /root/rpg-engine-app && python3 -m pytest -q --ignore=tests/test_embed_real.py` green; ALL C1 + Task 8 tests green (the no-spread regression guard proves C1 verdicts stay vertical).
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add loop/cascade.py tests/loop/test_cascade_loop.py && git commit -m "feat(cascade): horizontal chain — adjacent world_change level+1, depth<=3, region-cap, merge same-region [C2]

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 10: Lazy-deferred overflow queue — event-sourced defer + drain-at-start (§12 "远区 cascade 异步 / 懒更新")

**Goal:** Define a per-turn work budget. Nodes beyond the budget this turn — specifically REMOTE-region horizontal hops (a hop target outside the current scene's containment subtree) and any breadth-overflow — are NOT dropped: they are deferred. At the START of the NEXT `run_cascade`, the deferred nodes are drained first (counting against that turn's budget) before computing new triggers — mirroring `run_director`'s consume-last-turn idiom.

**Queue-persistence mechanism (DECISION — event-sourced, rewind-safe, the spec's preferred (a)).** Slice mutations are NOT events, so a queue stored only by direct slice-mutation would not survive a rewind/replay. Therefore the queue is **event-sourced through the already-owned `world_change` type** — no new event type, no edit to the 3-type ownership:
  - **Enqueue (defer):** emit a `world_change` carrying a deferral marker: `deltas={"place": <region>, "level": <hop level>, "deferred": true, "reason": "remote"|"breadth_overflow", "depth": <level>}`. Extend `CascadeSystem.apply`'s `world_change` branch so that **when `deltas.get("deferred")` is truthy** it appends `{"region": place, "level": level, "reason": reason, "depth": depth, "enqueue_turn": turn, "consumed": False}` to `world["systems"]["cascade"]["queue"]` (in ADDITION to the existing `changes` audit + fact assert — keep those). Because projection replays this event, the queue is rebuilt deterministically on every projection ⇒ rewind-safe.
  - **Drain + consume (event-sourced too).** A queue entry is "drained" once its region's children have been descended. To keep consume-state rewind-safe WITHOUT a 4th event type, mark consumption with the slice's existing `consumed_through_turn`: at the START of `run_cascade`, read the projected `world["systems"]["cascade"]["queue"]`, take entries with `enqueue_turn > consumed_through_turn` (these are the not-yet-drained ones), descend each region's children via the SAME `_vertical_bfs` (sharing this turn's budget), then emit ONE bookkeeping `world_change` with `deltas={"place": <scene>, "deferred_consume_through": <max enqueue_turn drained>}`; extend `CascadeSystem.apply` so that a `world_change` carrying `deferred_consume_through` sets `world["systems"]["cascade"]["consumed_through_turn"] = max(current, that)` (and does NOT also enqueue). This mirrors `run_director` exactly: director marks last turn's directives `consumed=True` on the projected slice; here the consume watermark is itself event-sourced so replay reproduces it. (`empty_state` already ships `consumed_through_turn: 0`, so no slice-shape change.)

**Per-turn budget.** Introduce `CASCADE_NODE_BUDGET = 12` (a module constant — the addendum's "per-turn work budget"; ~`CASCADE_BREADTH × 2` rounds of headroom). It caps the TOTAL number of `_node_verdict` calls per `run_cascade` invocation across drain + new-trigger descent (the per-round `CASCADE_BREADTH=6` still caps each frontier). Thread a mutable budget counter through `_vertical_bfs` (e.g. a 1-element list or a small `_Budget` object so the drain pass and the new-trigger pass share one remaining count). When the budget hits 0 mid-descent, the still-unvisited frontier nodes are deferred as `reason="breadth_overflow"` (emit deferral `world_change`s for them) and `log.info("cascade: per-turn node budget %d exhausted; deferred %d node(s)", CASCADE_NODE_BUDGET, n)` — never a silent drop.

**Remote vs local (§12 line 176).** Add `_scene_subtree(graph, scene_id, day) -> set[str]`: walk `contained_by` UP from `scene_id` to the topmost ancestor (the scene root), then collect ALL descendants downward via reverse `contained_by` (`_children` transitively). A horizontal hop whose target is IN the subtree is processed inline this turn (Task 9 behavior unchanged for local neighbors); a hop whose target is NOT in the subtree is deferred (`reason="remote"`): emit its hop `world_change` AND a deferral marker, but do NOT descend its children this turn.

**Files:** Modify `loop/cascade.py`; Modify `systems/cascade.py`; Modify `tests/loop/test_cascade_loop.py`; Modify `tests/systems/test_cascade_system.py`.

- [ ] **Step 1 — failing tests.**
  - In `tests/systems/test_cascade_system.py` (unit — projection folds the deferral into the queue, and the consume watermark is honored):
    ```python
    def test_world_change_deferred_marker_enqueues():
        reg = _reg()
        world = project(reg, [
            _place("capital"),
            kernel_event("world_change", day=2, scene="s1", summary="远区波及",
                         deltas={"place": "capital", "deferred": True, "level": 2,
                                 "reason": "remote", "depth": 2}, turn=3),
        ])
        q = world["systems"]["cascade"]["queue"]
        assert any(e["region"] == "capital" and e["consumed"] is False
                   and e["enqueue_turn"] == 3 for e in q)

    def test_world_change_consume_watermark_sets_through_turn():
        reg = _reg()
        world = project(reg, [
            _place("capital"),
            kernel_event("world_change", day=2, scene="s1", summary="drain bookkeeping",
                         deltas={"place": "capital", "deferred_consume_through": 5}, turn=6),
        ])
        assert world["systems"]["cascade"]["consumed_through_turn"] == 5
    ```
  - In `tests/loop/test_cascade_loop.py` (behavior — defer remote, drain next turn, local stays inline). Build `capital ⊃ market`; a separate remote region `farland ⊃ hamlet`; `market` adjacent to `farland`; `market` spreads:
    ```python
    def test_remote_hop_deferred_not_descended_this_turn():
        from loop.cascade import run_cascade
        reg = _reg(); store = _store(reg)
        store.append(_place("capital")); store.append(_place("market", parent="capital"))
        store.append(_place("farland")); store.append(_place("hamlet", parent="farland"))
        store.append(_link("market", "farland"))   # market ↔ farland (remote: not under capital)
        store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                  deltas={"place": "capital", "level": 1}, turn=2))
        world = project(reg, store.iter_events())
        prov = KeyedFakeProvider(by_place={
            "market": {"evolve": True, "state": "暴动", "spread": True},
            "hamlet": {"evolve": True, "state": "should-not-run-yet"},
        })
        appended = run_cascade(reg, store, world, scene="capital", provider=prov)
        # the remote hop world_change is emitted...
        assert any(e["type"] == "world_change" and e["deltas"]["place"] == "farland" for e in appended)
        # ...and queued (event-sourced)...
        world2 = project(reg, store.iter_events())
        assert any(q["region"] == "farland" for q in world2["systems"]["cascade"]["queue"])
        # ...but farland's child hamlet was NOT descended this turn.
        assert all(not (e["type"] == "place_evolved" and e["deltas"]["id"] == "hamlet")
                   for e in appended)

    def test_queued_remote_region_drained_next_turn():
        from loop.cascade import run_cascade
        reg = _reg(); store = _store(reg)
        store.append(_place("capital")); store.append(_place("market", parent="capital"))
        store.append(_place("farland")); store.append(_place("hamlet", parent="farland"))
        store.append(_link("market", "farland"))
        store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                  deltas={"place": "capital", "level": 1}, turn=2))
        prov = KeyedFakeProvider(by_place={
            "market": {"evolve": True, "state": "暴动", "spread": True},
            "hamlet": {"evolve": True, "state": "波及"},
        })
        # turn 1: market spreads to remote farland → hop emitted + deferral queued,
        # farland's children NOT descended this turn.
        run_cascade(reg, store, project(reg, store.iter_events()),
                    scene="capital", provider=prov)
        # turn 2: NO new player trigger — the drain-at-start consumes the queued
        # farland and descends its child hamlet.
        appended2 = run_cascade(reg, store, project(reg, store.iter_events()),
                                scene="capital", provider=prov)
        assert any(e["type"] == "place_evolved" and e["deltas"]["id"] == "hamlet"
                   for e in appended2)
        # turn 3: the consume watermark advanced past the enqueue turn → the queue
        # entry is consumed, so a third drain re-emits nothing for hamlet.
        appended3 = run_cascade(reg, store, project(reg, store.iter_events()),
                                scene="capital", provider=prov)
        assert all(e["deltas"].get("id") != "hamlet" for e in appended3
                   if e["type"] == "place_evolved")

    def test_local_neighbor_stays_inline_not_queued():
        """A spread hop to a neighbor INSIDE the scene subtree is processed inline
        (no queue entry) — regression guard for §12 'current-scene blocking'."""
        from loop.cascade import run_cascade
        reg = _reg(); store = _store(reg)
        store.append(_place("capital"))
        store.append(_place("market", parent="capital"))
        store.append(_place("plaza", parent="capital"))   # sibling, under same scene root
        store.append(_link("market", "plaza"))
        store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                  deltas={"place": "capital", "level": 1}, turn=2))
        world = project(reg, store.iter_events())
        prov = KeyedFakeProvider(by_place={
            "market": {"evolve": True, "state": "暴动", "spread": True},
            "plaza":  {"evolve": True, "state": "s"},
        })
        run_cascade(reg, store, world, scene="capital", provider=prov)
        world2 = project(reg, store.iter_events())
        assert all(q["region"] != "plaza" for q in world2["systems"]["cascade"]["queue"])

    def test_cascade_own_world_change_does_not_retrigger():
        """A deferral/bookkeeping world_change (carrying deferred / deferred_consume_through)
        must NOT be seen as a fresh trigger root by cascade_trigger (self-trigger guard)."""
        from loop.cascade import cascade_trigger
        world = project(_reg(), [_place("capital")])
        evs = [
            kernel_event("world_change", day=1, scene="capital", summary="hop",
                         deltas={"place": "capital", "level": 2, "deferred": True}, turn=3),
            kernel_event("world_change", day=1, scene="capital", summary="bookkeeping",
                         deltas={"place": "capital", "deferred_consume_through": 2}, turn=4),
        ]
        assert cascade_trigger(evs, world) == []     # neither marker re-triggers
    ```
    (Note: each `run_cascade` call re-projects from the store first — `project(reg, store.iter_events())` — so the freshly-appended deferral markers are visible in the slice `queue` on the NEXT call. Three sequential calls model three turns where only the FIRST had a player `world_change`.)
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement.**
  - `systems/cascade.py`: in the `world_change` branch of `apply`, BEFORE the existing audit/fact logic, handle the two markers: if `d.get("deferred_consume_through") is not None` → `slice_["consumed_through_turn"] = max(slice_.get("consumed_through_turn", 0), int(d["deferred_consume_through"]))` and return (bookkeeping only, no fact). Else, run the existing audit + fact code; ADDITIONALLY if `d.get("deferred")` is truthy → append `{"region": place, "level": d.get("level", 1), "reason": d.get("reason"), "depth": d.get("depth"), "enqueue_turn": turn, "consumed": False}` to `slice_["queue"]`. Keep all existing defensive guards.
  - `loop/cascade.py`:
    - **Guard `cascade_trigger` against cascade's OWN `world_change` output.** Add to `cascade_trigger`'s per-event loop a skip: `if t == "world_change" and (d.get("deferred") or d.get("deferred_consume_through") is not None): continue`. The hop/marker/bookkeeping `world_change`s cascade itself appends carry one of these keys, so this stops them from re-triggering a fresh cascade next turn (a player/narrator `world_change` carries neither key, so it still triggers). This is the §11/D4 self-trigger guard — without it the drain's bookkeeping `world_change` (whose `place` is the scene) would re-cascade the scene's children every turn.
    - Add `CASCADE_NODE_BUDGET = 12`; add `_scene_subtree(graph, scene_id, day)`; thread a shared mutable budget through `_vertical_bfs` (so the drain pass + new-trigger pass share one remaining count) and have the per-round breadth slice ALSO clamp to the remaining budget; when budget exhausts, emit `reason="breadth_overflow"` deferral `world_change`s for the unvisited frontier and log.
    - In Task 9's chain step, split hop targets by `_scene_subtree`: in-subtree hops emit their `world_change` and (if budget remains) are descended inline; remote hops emit their `world_change` PLUS a `deferred:true` marker `world_change` and are NOT descended.
    - At the TOP of `run_cascade` (before `cascade_trigger`): read `slice_ = world["systems"]["cascade"]`, `through = slice_.get("consumed_through_turn", 0)`, `pending = [q for q in slice_.get("queue", []) if not q.get("consumed") and q.get("enqueue_turn", 0) > through]`; if pending, descend each `_children(region)` via `_vertical_bfs` (shared budget), collect appended, then emit ONE bookkeeping `world_change` with `deltas={"place": scene, "deferred_consume_through": <max enqueue_turn of drained entries>}` (the `deferred_consume_through` branch in `apply` sets the watermark and asserts NO fact). Drain runs even when `cascade_trigger` returns `[]` (so `run_cascade` returns the drained events, not early-`[]`). Keep the existing "no trigger AND empty queue ⇒ return []" quiet path.
- [ ] **Step 4 — run, expect PASS;** determinism gate ×3 + full-suite gate `cd /root/rpg-engine-app && python3 -m pytest -q --ignore=tests/test_embed_real.py` green; ALL C1 + Task 8 + Task 9 tests green. Re-confirm `tests/systems/test_cascade_system.py` C1 tests (`test_world_change_records_audit_and_fact`, `test_apply_defensive_on_missing_id`) still pass — the new marker branches must not regress the plain `world_change` path.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add loop/cascade.py systems/cascade.py tests/loop/test_cascade_loop.py tests/systems/test_cascade_system.py && git commit -m "feat(cascade): event-sourced lazy-deferred overflow queue + drain-at-start [C2]

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## C2 done criteria
- Per-round `_node_verdict` calls fan out over a `ThreadPoolExecutor` whose width is the configurable `max_concurrency` (env `RPG_CASCADE_CONCURRENCY`, default 3); results are collected then mutated single-threaded; outcomes are deterministic as a SET regardless of thread scheduling.
- Horizontal chain emits an adjacent-region `world_change` at `level+1`, depth-capped at `CASCADE_MAX_DEPTH=3`, region-capped at `CASCADE_MAX_REGIONS=3`, same-region merged via `_merge_same_region`; a no-`spread` verdict stays purely vertical.
- Remote-region hops (outside the current scene subtree) and breadth-overflow are deferred (NOT dropped) via event-sourced `world_change` deferral markers folded into `world["systems"]["cascade"]["queue"]`; the next `run_cascade` drains them first (shared per-turn `CASCADE_NODE_BUDGET=12`), advancing the event-sourced `consumed_through_turn` watermark; current-scene-subtree hops stay inline (§12 line 176). The queue is rewind-safe (rebuilt by projection).
- Every cap hit (`CASCADE_BREADTH`, `CASCADE_MAX_DEPTH`, `CASCADE_MAX_REGIONS`, `CASCADE_NODE_BUDGET`) emits a `log()` line — no silent truncation.
- All C1 tests + the 654 baseline remain green; the determinism gate passes repeatably.

---

## Conventions (apply to every task)
- Every new module starts with `from engine.log import get_logger` and `log = get_logger("<module.path>")`.
- Tests live under `tests/` mirroring source (`tests/systems/`, `tests/loop/`).
- Run a single test file: `cd /root/rpg-engine-app && python3 -m pytest -q tests/loop/test_cascade_loop.py`. Full-suite gate: `cd /root/rpg-engine-app && python3 -m pytest -q --ignore=tests/test_embed_real.py`. Binary is **python3**.
- Commit ONLY the files a task names, with the exact `git add` + message shown (each message ends with the `Co-Authored-By` trailer).
- Mirror the existing house style: defensive `.get()` in `apply` (invariant 11 — projection must never crash on a stored event), `log.warning` + skip on malformed deltas, harness events built via `kernel_event`.

## HARD git guardrails (state and obey)
- NEVER `git init`, NEVER `rm -rf .git`, NEVER `git checkout --orphan`, NEVER switch branches (stay on `app`).
- NEVER delete or edit `_legacy/`, NEVER edit `docs/` except writing THIS plan file.
- NEVER edit `engine/oracle.py` or `engine/director.py` (the inherited 暗骰 engine — cascade does not touch it).
- Minimal incremental edits only. "Rebuild from scratch" / multi-minute runs = red flag → stop and escalate (per `docs/INCIDENT-2026-06-16-git-reset.md`).
- The current full suite (654 passed) and all legacy tests must stay green after every task.

---

## Self-Review

### Roadmap §10 bullet → task coverage

| §10 design bullet | Covered by |
|---|---|
| 回合末 `world/places/moves` 段驱动 (trigger) | Task 4 (`cascade_trigger` over place/move/world_change events; D1) |
| 纵向下沉 (containment), 不吃 depth-3 | Task 6 (BFS over `contained_by` children; vertical NOT depth-capped, D3.5) |
| 横向连锁 = 新 world_change, level+1, depth≤3, 合并同区 | Task 9 (adjacent `world_change` via `_adjacent_regions` = `adjacent_to` ∪ same-parent siblings; level+1; `CASCADE_MAX_DEPTH=3`; `_merge_same_region`) |
| 下沉单元 = 地点节点 = 一次 LLM call (地点态+聚合民众+环境增量+促升) | Task 6 (`_node_verdict` one `complete_json`/node → `place_evolved` state + `populace_shifted` mood); 促升/promotion = Task 9 `spread` flag |
| 全覆盖 + 剪枝 (碰到的节点/tracked 都有 verdict, 剪枝盖 roll-up 戳) | Task 6 (every visited child gets a verdict; `evolve:false` = prune + roll-up note, stops descent) |
| 并行 subagent (configurable concurrency) | Task 8 (`ThreadPoolExecutor(max_workers=max_concurrency)`; `max_concurrency` from arg > env `RPG_CASCADE_CONCURRENCY` > `max_subagents` > 3; collect-then-mutate single-threaded) |
| backstop: depth≤3 (horizontal only; vertical uncapped) | Task 9 (`CASCADE_MAX_DEPTH=3` gates the hop; vertical BFS stays depth-uncapped per D3.5) |
| backstop: breadth (≤N 区/回合) + per-turn 预算 | Task 6 (`CASCADE_BREADTH=6` per-round) + Task 9 (`CASCADE_MAX_REGIONS=3` regions/turn) + Task 10 (`CASCADE_NODE_BUDGET=12` total/turn) |
| §12: 当前场景 cascade 阻塞 / 远区 cascade 异步 / 懒更新 | Task 10 (current-scene subtree inline via `_scene_subtree`; remote hops + breadth-overflow → event-sourced `world_change` deferral markers → `queue`; drained at next-turn start under shared budget, `consumed_through_turn` watermark); Task 7 (C1 inline post-apply) |
| §12 line 177: 后台产出轻量校验 (referential only, drop+log, no repair) | Task 5 (`lightweight_validate`) + Task 6 (drop-on-fail test) |
| §11: harness 自生成事件不过闸 (registered, strict store only) | Task 1+3 (`CascadeSystem` owns types, registered in `build_engine`; no `commit_sections`) |
| Hook shape mirrors director/fleet (post-apply, span, non-fatal, re-project) | Task 7 (wired after director, in `span("cascade")`, try/except, re-project) |

### Placeholder scan
No task body contains TODO / `pass  # fill in` / `...` placeholders. Every task supplies REAL runnable failing test code and a concrete implementation spec. Test fixtures (`_reg`, `_place`, `_store`, `_tree_events`, `KeyedFakeProvider`) are fully written in Task 1/4/6 and reused by every C2 task; C2 adds one more fixture, `_link(a, b, cost)` (a `place_linked` event), defined in Task 9 and reused by Task 10. The C2 tests use ONLY `KeyedFakeProvider` (order-independent, thread-safe) — never `FakeLLMProvider` under the pool (D5).

### Type / name consistency across tasks
- System name `"cascade"`; event types `place_evolved` / `populace_shifted` / `world_change` — used identically in `systems/cascade.py`, the registry, the walker emitter, and every test. No collision with existing repo names (verified by grep: none outside `_legacy`). `world_change` is the cascade-owned type and ALSO the carrier of the C2 horizontal hop AND the lazy-defer/consume markers (via `deltas.deferred` / `deltas.deferred_consume_through`) — so the queue is event-sourced WITHOUT adding a 4th event type (Task 10). The §3.3 envelope `world` *commit-section* is unowned today and is NOT introduced here — cascade events stay harness-authored, no commit section.
- Slice shape `{"queue": [], "changes": [], "consumed_through_turn": 0}` is fixed in Task 1 and consumed unchanged by Task 2 (`changes`), Task 10 (`queue` entries `{"region","level","reason","depth","enqueue_turn","consumed"}` folded by `CascadeSystem.apply`; `consumed_through_turn` advanced by the drain watermark), and the `inject`/debug path. No slice-shape change in C2 — only `apply` learns to populate `queue`/`consumed_through_turn`.
- Constants `CASCADE_FLOOR=2`, `CASCADE_BREADTH=6` (per-round), `CASCADE_MAX_DEPTH=3`, `CASCADE_MAX_REGIONS=3`, and `CASCADE_NODE_BUDGET=12` (per-turn total, added in Task 10) — all defined once in `loop/cascade.py` and referenced via `cmod.<NAME>` in tests so a tuning change does not break assertions. **There is no `CASCADE_MAX_NODES`** — the shipped C1 chose the per-ROUND `CASCADE_BREADTH=6` (addendum override), and the per-turn total is `CASCADE_NODE_BUDGET`. C2 tests assert against `cmod.CASCADE_BREADTH` / `cmod.CASCADE_MAX_REGIONS` / `cmod.CASCADE_MAX_DEPTH`, never `CASCADE_MAX_NODES`.
- `run_cascade` signature: shipped C1 is `(registry, store, world, *, scene, provider, cascade_provider=None, max_subagents=4)`. Task 8 ADDS one keyword-only param `max_concurrency: int | None = None` (env-defaulted via `_resolve_concurrency`) and KEEPS `max_subagents` as a back-compat alias — the signature only grows, never shrinks. Tasks 9/10 add behavior, not params. The shipped `run_turn` call site passes `cascade_provider=` and NOT `max_subagents`/`max_concurrency`, so concurrency defaults from the env there with no `run_turn`/`engine.py` edit (minimal-blast-radius — neither file is touched by C2).
- `_node_verdict(place_id, ctx, provider)` seam (shipped in C1) is the parallelized unit in Task 8 (submitted to the pool, pure/side-effect-free), monkeypatched in Task 7, and reused under threads throughout — one signature, no store/graph access inside it. `_vertical_bfs` gains `max_concurrency` (Task 8), `root_level` + `chain_targets` (Task 9), and a shared mutable budget + `_scene_subtree` split (Task 10) — additive params on the same internal helper.

### Risks the implementer must watch
1. **Existing turn tests now also run cascade.** Most use `location="town"` with no children, so the vertical walk appends nothing — but Task 7 step 4 explicitly verifies the director-wiring test and two-turn test stay green, fixing only TEST fixtures (never the trigger) if one trips.
2. **`heuristic_floor` import** must not create a cycle: `memory/importance.py` imports only `engine.log`, so `loop/cascade.py` importing it is safe (the fleet already does).
3. **`day` monotonicity:** `FactGraph.assert_fact` raises on out-of-order days. Cascade stamps its events with the same `day` as the triggering turn (max day in this turn's events), so it never asserts a day earlier than an existing current fact for the same subject. Tests use a single day=1 to stay clear of this; the walker must use `max(day, (world.get("meta") or {}).get("day") or 1)` to be safe (mirrors the shipped C1 line).
4. **(C2) The deferral/consume markers are themselves `world_change` events — they must NOT re-trigger a cascade.** `world_change` is in `_TRIGGER_TYPES` AND is deliberately EXCLUDED from `_HARNESS_TYPES` (so a player/narrator `world_change` still triggers) — so a naive drain would loop: the bookkeeping `world_change` (whose `place` is the scene) and the hop markers would be picked up by next turn's `cascade_trigger` and cascade again. **Two independent guards close this** (Task 10 step 3): (a) `cascade_trigger` skips any `world_change` carrying `deferred` or `deferred_consume_through` (a player `world_change` carries neither); (b) cascade stamps all its own output at the cascade turn slot (`_next_cascade_turn`, > player turn) so the `trigger_events` window (`turn == _last_nonharness_turn`) excludes them anyway. Guard (a) is the load-bearing one (covered by `test_cascade_own_world_change_does_not_retrigger`); guard (b) is belt-over-suspenders. The drain consumes from the `queue` gated by `consumed_through_turn`, NOT by re-running `cascade_trigger` over its own markers — so a deferral is consumed exactly once (covered by `test_queued_remote_region_drained_next_turn`'s third-drain assertion).
5. **(C2) ThreadPoolExecutor + `assert_fact` thread-safety.** `FactGraph.assert_fact` / `store.append` are NOT thread-safe (list mutation + supersession scan). The Task 8 design forbids calling them inside the pool — ALL mutation happens in the main thread after `as_completed` resolves. The only thing the pool does is `_node_verdict` (a pure `provider.complete_json`). The determinism gate (`pytest … ×3`) guards against an accidental in-pool mutation slipping in.
