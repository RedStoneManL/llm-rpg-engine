# P1 — cascade world-段驱动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every task is TDD: write the REAL failing test first, run it, see it FAIL, write the minimal REAL implementation, run it, see it PASS, then commit exactly the files the task names. No placeholders, no stubbed-out bodies, no "fill this in later". If a step's behavior is unclear, re-read the cited source file before writing code.

**Goal:** Make the cascade fire from a narrator-declared `world` commit section (the narrator names which areas a region/world event hits, because it has the story context) instead of self-triggering off movement events and guessing horizontal spread with a cheap model that never said yes — so horizontal spread finally happens, driven by the model that knows the scope.

**Architecture:** `CascadeSystem` gains a `world` commit section (schema `{areas:[place_id,...], level:1|2|3, summary}`), validated through the strict gate (drop/repair, like `knowledge`) and exploded to one `world_change` event per area (the cascade roots). The `world` section is exposed in 甲 `_SYSTEM_PROMPT` + 丙 `_SYSTEM_PROMPT_HYBRID` as an OPTIONAL section. `loop/cascade.py::run_cascade` becomes a pure executor: it triggers ONLY on the narrator's `world_change` events this turn, descends each declared area vertically (cheap per-node verdict fills consequence detail), and allows a cheap secondary spread of AT MOST ONE hop (a node verdict may name `keep_spreading:[adjacent_ids]`, descended one further level via the existing deferred-queue machinery, no deeper). The old self-trigger on `entity_moved`/`place_created`/`place_materialized`, the root-level `_root_spread_verdict` block, and the per-child `spread`→`chain_targets`→depth≤3 horizontal chain are removed. Parallel fan-out, `_merge_same_region`, `lightweight_validate`, the deferred-queue/drain, and the cheap `cascade_provider` are kept and reused.

**Tech Stack:** Python 3.12 stdlib only (no new deps). Reuses S0 kernel (`Registry`/`project`/`kernel_event`/`EventStore`), S1 `facts/FactGraph` + `systems/place.py` containment, `llm/provider.py` (synchronous urllib; parallelism via `concurrent.futures.ThreadPoolExecutor`), `memory/importance.py::heuristic_floor` for the trigger floor, and the `loop/director.py` post-apply hook shape. Tests are offline + deterministic with `KeyedFakeProvider` / `FakeLLMProvider` (NO network). Test binary: `python3`. Logging convention: `from engine.log import get_logger`.

## Global Constraints

- Python 3.12; branch `app`; stdlib only — no new dependencies.
- Test binary is `python3` (e.g. `python3 -m pytest`).
- Logging: every module uses `from engine.log import get_logger` then `log = get_logger("<dotted.name>")`.
- Tests mirror source: `loop/cascade.py` → `tests/loop/test_cascade_loop.py`; `systems/cascade.py` → `tests/systems/test_cascade_system.py`; `loop/strategy.py` → `tests/loop/test_strategy.py`; `loop/turn.py` → `tests/loop/test_turn.py`.
- **HARD git guardrails:** NO `git init`, NO `git reset`, NO `git checkout`/branch-switch, NO new branches — you are already on `app`. Do NOT edit `engine/`, `_legacy/`, `docs/` (EXCEPT this one plan file), or `data/`. Only commit the exact files each task names.
- The existing 738-test suite (`python3 -m pytest -q --ignore=tests/test_embed_real.py`) plus all legacy tests MUST stay green at every commit. The only pre-existing tests this plan is ALLOWED to modify are the ones Task 6 names explicitly (they assert the OLD self-trigger / horizontal behavior that P1 deliberately removes).
- Full-suite gate after each implementation task: `python3 -m pytest -q --ignore=tests/test_embed_real.py`.
- Commit message trailer (every commit): `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Design decisions (load-bearing — referenced by tasks)

### DD1 — `world` section owner = `CascadeSystem`; ONE `world_change` event PER AREA

`CascadeSystem.commit_sections()` becomes `{"world"}` (it was `set()`). The section item schema:

```
world: [{ "areas": [place_id, ...],   // narrator names ALL affected areas (anywhere, not just neighbors)
          "level": 1 | 2 | 3,
          "summary": "一句话事件" }]
```

`to_events()` emits **one `world_change` event per area** (not one event carrying the whole area list). **Decision: one-`world_change`-per-area.** Rationale:
1. The existing `CascadeSystem.apply` `world_change` branch keys entirely off a single `deltas["place"]` (audit entry, fact assert, deferral/watermark handling). One-per-area reuses that apply path verbatim — zero change to projection.
2. `cascade_trigger` / `_root_place` already extract ONE root place id per event from `deltas["place"]`. One-per-area means each area is a clean, independent cascade root with no new fan-out logic in the trigger.
3. A list-carrying event would force BOTH `apply` and the trigger to learn a new multi-place shape, and would muddy the audit (`changes` list) and the dangling-place warning, for no benefit.

Each emitted event: `kernel_event("world_change", day, scene, summary=item["summary"], deltas={"place": area, "level": item["level"], "summary": item["summary"]}, turn=turn)`. The `summary` is duplicated into `deltas["summary"]` so cascade descent can read it as node context even though the top-level `summary` is also set (apply reads `event.get("summary")` for the fact; descent reads `deltas["summary"]`).

`validate()` for `section == "world"`, per item:
- `areas` must be a non-empty list; each `area` must be a `str` and resolve to an existing `Place` entity in the graph **OR** be in the same-commit `created_ids` (a place created THIS commit). Use the strict gate's pending-stub mechanism: the gate stubs `created_ids` into the graph before calling `validate`, so a plain `g.get_entity(area) is None` check correctly passes a same-commit place (see `kernel/validation.py` lines 56-72 — stubs are added then removed in `finally`). Therefore `validate` does NOT need to read `created_ids` itself; it just checks `g.get_entity(area) is None` → `dangling_ref`.
- `level` must be in `{1, 2, 3}` → `bad_enum` otherwise (mirror `PlaceSystem`'s level check). Missing `level` → `missing`.
- `summary` must be a non-empty `str` → `missing` otherwise.
- `areas` missing or empty → `missing`.

`created_ids(section, decl)` for `world` returns `set()` — the `world` section REFERENCES places, it never creates them (a place is created via the `places` section). Override the default (which would wrongly harvest `item["id"]`, but `world` items have no `id` anyway; still, an explicit `set()` is correct and clear, exactly like `PlaceSystem.created_ids` returning `set()` for non-`places` sections).

This section is LLM-authored, so it goes through the full strict gate (`validate` + repair loop + drop). This is unlike the OLD cascade outputs (`place_evolved`/`populace_shifted`/`world_change` bookkeeping) which are harness-authored and only `lightweight_validate`'d. The dual nature is intentional (see DD3).

### DD2 — `world` exposed in 甲 + 丙 prompts as an OPTIONAL section

Mirror exactly how `knowledge` is exposed (a bullet in the `【结构】` list + a dedicated explanatory block), in BOTH `loop/strategy.py::_SYSTEM_PROMPT` (甲) and `_SYSTEM_PROMPT_HYBRID` (丙). The narrator is instructed: when a region/world-level event happens (灾难、战争、瘟疫、政权更替、重大变故), declare a `world` section naming ALL affected areas by place id (it has the story context, so it may name areas anywhere, not just adjacent ones); routine personal turns OMIT it. `world` stays OPTIONAL — it is NOT added to `REQUIRED_SECTIONS` (which is `{"moves","places","cast","facts"}` in `loop/turn.py`, unchanged) and needs NO `reasons` entry when absent (same as `knowledge`).

### DD3 — `run_cascade` becomes a pure executor; what is REMOVED, what is KEPT

**Trigger (kept, narrowed):** `cascade_trigger` triggers ONLY on `world_change` events with a resolvable root Place that clear `CASCADE_FLOOR`. `_TRIGGER_TYPES` shrinks from `{"world_change","place_materialized","place_created","entity_moved"}` to `{"world_change"}`. The `bool(d.get("world_change"))` legacy opt-in clause is removed (no longer needed — the `world` section IS the opt-in, and it produces real `world_change` events). The self-trigger GUARD stays intact and load-bearing: a `world_change` carrying `deferred` or `deferred_consume_through` is still skipped, so cascade's own deferral markers + consume-watermark bookkeeping never re-trigger.

`_HARNESS_TYPES` is unchanged: it still EXCLUDES `world_change` from the harness set (the narrator-authored `world_change` must count as a player-turn event so the trigger window detection sees it). The comment in `_HARNESS_TYPES` already says exactly this — keep it, it is now even more true.

**Verdict + secondary spread (replaced):** `_NODE_SCHEMA` drops `spread`/`magnitude` and gains `keep_spreading: {"type": "array", "items": {"type": "string"}}` (a list of adjacent area ids this node says the event keeps spreading to). `_node_prompt` is rewritten: given the `summary` as context, fill consequence detail (`evolve`/`state`/`populace_mood`/`note`); and OPTIONALLY, if the event is violent enough to cross into an adjacent area, name those adjacent area ids in `keep_spreading` — but warn the model this is the LAST outward step (no further spreading beyond it).

**Secondary spread enforcement = MAX 1 hop (new, replaces depth≤3):** Each declared area (a root) is descended vertically. A node verdict's `keep_spreading` ids that are NOT already touched are emitted as deferred `world_change` hops via the EXISTING `_emit_hops`/deferred-queue path. The "max 1 hop" invariant is enforced structurally, NOT by a depth counter:
- Within `run_cascade`'s new-trigger descent, the `_vertical_bfs` call passes a flag `allow_secondary=True`. When a node names `keep_spreading`, those become deferred hops (queued for next-turn drain) — they are NOT descended this turn.
- The drain-at-start descent (`_vertical_bfs` for a queued region) passes `allow_secondary=False`. So a region that was reached via a secondary hop can have its OWN children descended (the hop's vertical consequence), but its nodes' `keep_spreading` is IGNORED — there is no third outward ring. This is the structural "at most one hop": ring 0 = declared areas (+ their vertical subtree), ring 1 = `keep_spreading` neighbors (+ their vertical subtree next turn), and ring 1's nodes cannot open a ring 2.

`CASCADE_MAX_DEPTH` and `CASCADE_MAX_REGIONS` are removed (the depth-3 horizontal chain and the multi-region root-spread cap they bounded are gone). `_emit_hops` keeps `CASCADE_MAX_REGIONS`'s job only as a per-call breadth guard on secondary hops — see DD4. `CASCADE_BREADTH=6` (per-level node cap) and `CASCADE_NODE_BUDGET=12` are KEPT unchanged.

**REMOVED in full:**
1. The root-level spread block in `run_cascade` (the `# C2 bugfix: root-level spread decision` section, ~lines 894-958: `root_chain_targets`, the per-root `_node_verdict(root, spread_ctx, cp)` call, the `_root_spread_verdict`-style prompt, the trailing `_emit_hops`).
2. The per-child `spread`→`chain_targets` accumulation inside `_vertical_bfs` (the `if verdict.get("spread"):` block, ~lines 672-683) and the post-BFS `_emit_hops(chain_targets=...)` at the end of `_vertical_bfs` (~lines 694-709) — REPLACED by the `keep_spreading` path.
3. The `【邻近区域】... spread:true` context augmentation in `run_cascade`'s new-trigger block (~lines 852-863) and the `root_level` derivation tied to it.
4. `_TRIGGER_TYPES` entries `place_materialized`/`place_created`/`entity_moved` and the `bool(d.get("world_change"))` clause.

**KEPT + reused:** `_vertical_bfs` parallel fan-out (ThreadPoolExecutor, `max_concurrency`), `_merge_same_region`, `lightweight_validate`, the deferred-queue drain in `run_cascade` (now feeding ring-1 secondary hops), `_scene_subtree` (local vs remote split for hops), `cascade_provider`, `_node_verdict`'s force-the-id behavior (live-caught glm-4.7 fix), the per-node-raises drop-not-abort behavior. `_adjacent_regions` is KEPT (it validates that a `keep_spreading` id is a real adjacent Place — see Task 4).

### DD4 — secondary-hop breadth guard

`keep_spreading` hops reuse `_emit_hops`, which today caps at `CASCADE_MAX_REGIONS`. Since `CASCADE_MAX_REGIONS` is removed, introduce a single secondary-spread breadth constant `CASCADE_SECONDARY_BREADTH = 3` and have `_emit_hops` cap the number of emitted hops at `CASCADE_SECONDARY_BREADTH` (after `_merge_same_region`). This keeps a runaway `keep_spreading: [50 ids]` bounded, preserving the old "≤N 区/回合" backstop without the depth machinery. The local-vs-remote (`scene_subtree`) split inside `_emit_hops` is kept verbatim; only the depth check (`hop_level > CASCADE_MAX_DEPTH`) is deleted and the region-cap constant is renamed.

### DD5 — determinism for tests

All tests offline. `KeyedFakeProvider` (already in `tests/loop/test_cascade_loop.py`) returns a verdict keyed by the place id embedded in the user prompt → order-independent, safe for the ThreadPoolExecutor. New tests for `keep_spreading` use the same keyed pattern. Assert on the SET of outcomes for parallel paths, never on thread-completion order. The narrator-side tests (`world` section validate/to_events) use plain dicts + `project` with a registry that includes `CascadeSystem`; the prompt-exposure tests assert substrings in the module-level prompt strings (no LLM call).

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `systems/cascade.py` | **Modify** | Add `commit_sections()` → `{"world"}`; add `validate(section, decl, world)` for `world`; add `to_events(section, decl, *, turn, day, scene)` emitting one `world_change` per area; add `created_ids(section, decl)` → `set()` for `world`. `apply` UNCHANGED (the per-area `world_change` reuses the existing branch). |
| `loop/cascade.py` | **Modify** | Shrink `_TRIGGER_TYPES` to `{"world_change"}` + drop the `world_change`-opt-in clause in `cascade_trigger`; rewrite `_NODE_SCHEMA`/`_node_prompt` for `keep_spreading`; rewrite `_vertical_bfs` to emit `keep_spreading` hops (gated by `allow_secondary`) and drop the `spread`/`chain_targets` path; delete the root-level spread block in `run_cascade`; pass `summary` as node ctx; rename `CASCADE_MAX_REGIONS`→`CASCADE_SECONDARY_BREADTH` (and drop `CASCADE_MAX_DEPTH`) in `_emit_hops`. |
| `loop/strategy.py` | **Modify** | Add a `world` bullet + a `【世界事件·world（可选段）】` block to BOTH `_SYSTEM_PROMPT` (甲) and `_SYSTEM_PROMPT_HYBRID` (丙). No code-path change. |
| `tests/systems/test_cascade_system.py` | **Modify** | Add tests: `commit_sections()=={"world"}`; `validate` accepts good `world`, flags dangling area / bad level / missing summary / empty areas; same-commit place resolves; `to_events` emits one `world_change` per area with correct deltas; `created_ids`→`set()`. |
| `tests/loop/test_cascade_loop.py` | **Modify** | Add tests: trigger fires only on narrator `world_change` (no longer on `entity_moved`/`place_created`/`place_materialized`); descent uses summary as ctx; `keep_spreading` emits a deferred ring-1 hop; ring-1 region drained next turn descends its children but its `keep_spreading` is ignored (no ring 2); secondary breadth cap. UPDATE the OLD horizontal/self-trigger tests per Task 6. |
| `tests/loop/test_strategy.py` | **Modify** | Add tests asserting the `world` block + bullet appear in both `_SYSTEM_PROMPT` and `_SYSTEM_PROMPT_HYBRID`, and that `world` is NOT in `REQUIRED_SECTIONS`. |
| `tests/loop/test_turn.py` | **Modify** | UPDATE the two cascade-integration tests that relied on the `entity_moved` self-trigger to instead drive cascade via a narrator-declared `world` section (Task 6). |

No other files are touched. `engine/`, `_legacy/`, `data/`, and `docs/` (except this plan) are off-limits.

---

## Task 1: `CascadeSystem` owns the `world` commit section (ownership + registration)

**Files:**
- Modify: `systems/cascade.py` (`commit_sections`)
- Test: `tests/systems/test_cascade_system.py`

**Interfaces:**
- Consumes: `Registry.owner_of_section(name)` (existing), `ContextSystem.commit_sections()`.
- Produces: `CascadeSystem.commit_sections() == {"world"}` so the registry routes the `world` section to `CascadeSystem`.

- [ ] **Step 1: Write the failing tests.** In `tests/systems/test_cascade_system.py`, REPLACE the existing `commit_sections` assertion in `test_cascade_owns_event_types` and add a routing test. Edit `test_cascade_owns_event_types` so its last line reads:

```python
def test_cascade_owns_event_types():
    cs = CascadeSystem()
    assert cs.name == "cascade"
    assert cs.event_types() == {"place_evolved", "populace_shifted", "world_change"}
    # P1: CascadeSystem now owns the LLM-authored `world` commit section.
    assert cs.commit_sections() == {"world"}
```

Then append:

```python
def test_world_section_routed_to_cascade():
    reg = _reg()
    owner = reg.owner_of_section("world")
    assert owner is not None and owner.name == "cascade"
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `python3 -m pytest -q tests/systems/test_cascade_system.py::test_cascade_owns_event_types tests/systems/test_cascade_system.py::test_world_section_routed_to_cascade`
Expected: FAIL — `commit_sections()` currently returns `set()`, so the equality and the routing both fail.

- [ ] **Step 3: Write minimal implementation.** In `systems/cascade.py`, change `commit_sections`:

```python
    def commit_sections(self) -> set[str]:
        return {"world"}
```

- [ ] **Step 4: Run test to verify it passes.**

Run: `python3 -m pytest -q tests/systems/test_cascade_system.py::test_cascade_owns_event_types tests/systems/test_cascade_system.py::test_world_section_routed_to_cascade`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
cd /root/rpg-engine-app && git add systems/cascade.py tests/systems/test_cascade_system.py && git commit -m "feat(cascade): CascadeSystem owns the world commit section

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `CascadeSystem.validate` for the `world` section

**Files:**
- Modify: `systems/cascade.py` (add `validate`)
- Test: `tests/systems/test_cascade_system.py`

**Interfaces:**
- Consumes: `kernel.contextsystem.ValidationError(section, field, code, hint)`; `world["systems"]["ontology"]` FactGraph (`g.get_entity(pid)`). The strict gate stubs same-commit `created_ids` into the graph before calling `validate` (see `kernel/validation.py` lines 56-72), so `g.get_entity(area) is None` correctly passes a place created in the same commit.
- Produces: `CascadeSystem.validate("world", decl, world) -> list[ValidationError]` with codes `missing` (empty/absent `areas`, missing `level`, empty `summary`), `bad_enum` (`level` ∉ {1,2,3}), `dangling_ref` (area not a graph entity and not stubbed).

- [ ] **Step 1: Write the failing tests.** Append to `tests/systems/test_cascade_system.py`:

```python
# ---------------------------------------------------------------------------
# P1 Task 2: CascadeSystem.validate for the `world` section
# ---------------------------------------------------------------------------

def _world_world(reg, *places):
    """Project a world with the given Place ids, return the world dict."""
    return project(reg, [_place(p) for p in places])


def test_validate_world_accepts_good_item():
    reg = _reg()
    world = _world_world(reg, "capital", "harbor")
    cs = CascadeSystem()
    decl = [{"areas": ["capital", "harbor"], "level": 1, "summary": "王都陷落"}]
    assert cs.validate("world", decl, world) == []


def test_validate_world_flags_dangling_area():
    reg = _reg()
    world = _world_world(reg, "capital")
    cs = CascadeSystem()
    decl = [{"areas": ["capital", "ghost_town"], "level": 1, "summary": "战火蔓延"}]
    errs = cs.validate("world", decl, world)
    assert any(e.code == "dangling_ref" and "ghost_town" in e.hint for e in errs)


def test_validate_world_flags_bad_level():
    reg = _reg()
    world = _world_world(reg, "capital")
    cs = CascadeSystem()
    decl = [{"areas": ["capital"], "level": 9, "summary": "x"}]
    errs = cs.validate("world", decl, world)
    assert any(e.code == "bad_enum" and e.field == "[0].level" for e in errs)


def test_validate_world_flags_missing_summary():
    reg = _reg()
    world = _world_world(reg, "capital")
    cs = CascadeSystem()
    decl = [{"areas": ["capital"], "level": 1, "summary": ""}]
    errs = cs.validate("world", decl, world)
    assert any(e.code == "missing" and e.field == "[0].summary" for e in errs)


def test_validate_world_flags_empty_areas():
    reg = _reg()
    world = _world_world(reg, "capital")
    cs = CascadeSystem()
    decl = [{"areas": [], "level": 1, "summary": "x"}]
    errs = cs.validate("world", decl, world)
    assert any(e.code == "missing" and e.field == "[0].areas" for e in errs)


def test_validate_world_resolves_same_commit_place():
    """A place created THIS commit is stubbed into the graph by the strict gate,
    so an area referencing it must validate (mirror of place/move cross-section)."""
    reg = _reg()
    world = _world_world(reg, "capital")
    g = world["systems"]["ontology"]
    g.add_entity("new_region", "_pending")     # simulate the gate's stub
    cs = CascadeSystem()
    decl = [{"areas": ["capital", "new_region"], "level": 2, "summary": "扩散"}]
    assert cs.validate("world", decl, world) == []


def test_validate_world_ignores_other_sections():
    reg = _reg()
    world = _world_world(reg, "capital")
    cs = CascadeSystem()
    assert cs.validate("knowledge", [{"op": "told"}], world) == []
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `python3 -m pytest -q tests/systems/test_cascade_system.py -k validate_world`
Expected: FAIL — `CascadeSystem` has no `validate`, so it inherits the base no-op returning `[]`; the dangling/bad-level/missing assertions all fail (they expect errors).

- [ ] **Step 3: Write minimal implementation.** In `systems/cascade.py`, add the import line at the top (the module currently imports only `ContextSystem`):

```python
from kernel.contextsystem import ContextSystem, ValidationError
```

Then add the method to `CascadeSystem` (place it after `apply`):

```python
    _VALID_LEVELS = frozenset({1, 2, 3})

    def created_ids(self, section: str, decl) -> set:
        # The `world` section REFERENCES existing places; it never creates ids.
        return set()

    def validate(self, section: str, decl, world: dict) -> list:
        """Validate the LLM-authored `world` section (strict-gate path).

        Per item: areas non-empty list of existing Place ids (or same-commit
        stubs); level in 1..3; summary non-empty. Codes mirror PlaceSystem.
        """
        if section != "world":
            return []
        g = world.get("systems", {}).get("ontology")
        errs: list = []
        for i, item in enumerate(decl or []):
            areas = item.get("areas")
            if not isinstance(areas, list) or not areas:
                errs.append(ValidationError(
                    section="world", field=f"[{i}].areas", code="missing",
                    hint="world 段每项必须给出非空 areas（受影响地点 id 数组）"))
            else:
                for j, area in enumerate(areas):
                    if not isinstance(area, str) or not area:
                        errs.append(ValidationError(
                            section="world", field=f"[{i}].areas[{j}]", code="missing",
                            hint="areas 每个元素必须是非空的地点 id 字符串"))
                    elif g is not None and g.get_entity(area) is None:
                        errs.append(ValidationError(
                            section="world", field=f"[{i}].areas[{j}]", code="dangling_ref",
                            hint=f"受影响地点 '{area}' 不存在于图中"))
            level = item.get("level")
            if level is None:
                errs.append(ValidationError(
                    section="world", field=f"[{i}].level", code="missing",
                    hint="world 段每项必须给出 level（1/2/3）"))
            elif level not in self._VALID_LEVELS:
                errs.append(ValidationError(
                    section="world", field=f"[{i}].level", code="bad_enum",
                    hint=f"level 必须为 1/2/3，当前值: {level!r}"))
            summary = item.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                errs.append(ValidationError(
                    section="world", field=f"[{i}].summary", code="missing",
                    hint="world 段每项必须给出 summary（一句话事件描述）"))
        return errs
```

- [ ] **Step 4: Run test to verify it passes.**

Run: `python3 -m pytest -q tests/systems/test_cascade_system.py -k "validate_world or world_section or owns_event"`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
cd /root/rpg-engine-app && git add systems/cascade.py tests/systems/test_cascade_system.py && git commit -m "feat(cascade): validate the world commit section

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `CascadeSystem.to_events` — one `world_change` per area

**Files:**
- Modify: `systems/cascade.py` (add `to_events`)
- Test: `tests/systems/test_cascade_system.py`

**Interfaces:**
- Consumes: `kernel.events.kernel_event(type, *, day, scene, summary, deltas, turn)`.
- Produces: `CascadeSystem.to_events("world", decl, turn=, day=, scene=) -> list[dict]` of `world_change` events, ONE per area, each with `deltas={"place": area, "level": item["level"], "summary": item["summary"]}` and top-level `summary=item["summary"]`. Non-`world` sections return `[]`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/systems/test_cascade_system.py`:

```python
# ---------------------------------------------------------------------------
# P1 Task 3: CascadeSystem.to_events — one world_change per area
# ---------------------------------------------------------------------------

def test_to_events_world_emits_one_per_area():
    cs = CascadeSystem()
    decl = [{"areas": ["capital", "harbor", "farms"], "level": 1, "summary": "王都陷落"}]
    evs = cs.to_events("world", decl, turn=5, day=3, scene="s1")
    assert len(evs) == 3
    assert all(e["type"] == "world_change" for e in evs)
    places = {e["deltas"]["place"] for e in evs}
    assert places == {"capital", "harbor", "farms"}
    for e in evs:
        assert e["deltas"]["level"] == 1
        assert e["deltas"]["summary"] == "王都陷落"
        assert e["summary"] == "王都陷落"
        assert e["turn"] == 5 and e["day"] == 3


def test_to_events_world_multiple_items_flattened():
    cs = CascadeSystem()
    decl = [
        {"areas": ["a"], "level": 1, "summary": "地震"},
        {"areas": ["b", "c"], "level": 2, "summary": "瘟疫"},
    ]
    evs = cs.to_events("world", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 3
    by_place = {e["deltas"]["place"]: e["deltas"] for e in evs}
    assert by_place["a"]["summary"] == "地震" and by_place["a"]["level"] == 1
    assert by_place["b"]["summary"] == "瘟疫" and by_place["c"]["level"] == 2


def test_to_events_non_world_section_empty():
    cs = CascadeSystem()
    assert cs.to_events("knowledge", [{"op": "told"}], turn=1, day=1, scene="s1") == []


def test_to_events_world_roundtrips_through_apply():
    """Emitted world_change events project cleanly via the existing apply branch:
    each area gets an audit entry + a world_change fact."""
    reg = _reg()
    base = [_place("capital"), _place("harbor")]
    cs = CascadeSystem()
    evs = cs.to_events("world", [{"areas": ["capital", "harbor"], "level": 1,
                                  "summary": "陷落"}], turn=2, day=1, scene="s1")
    world = project(reg, base + evs)
    g = world["systems"]["ontology"]
    assert g.value_at("capital", "world_change", day=1) == "陷落"
    assert g.value_at("harbor", "world_change", day=1) == "陷落"
    changes = world["systems"]["cascade"]["changes"]
    assert {c["place"] for c in changes} == {"capital", "harbor"}
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `python3 -m pytest -q tests/systems/test_cascade_system.py -k to_events_world`
Expected: FAIL — `CascadeSystem` inherits the base `to_events` returning `[]`, so the length/place assertions fail.

- [ ] **Step 3: Write minimal implementation.** In `systems/cascade.py`, add the `kernel_event` import at the top:

```python
from kernel.events import kernel_event
```

Then add to `CascadeSystem` (after `validate`):

```python
    def to_events(self, section: str, decl, *, turn: int, day: int, scene: str) -> list:
        """Explode the `world` section into one world_change event per area.

        Decision (DD1): one-world_change-per-area, so each area is a clean cascade
        root and reuses the existing world_change apply branch verbatim.
        """
        out: list = []
        if section != "world":
            return out
        for item in decl or []:
            summary = item.get("summary", "")
            level = item.get("level", 1)
            for area in item.get("areas") or []:
                out.append(kernel_event(
                    "world_change", day=day, scene=scene, summary=summary,
                    deltas={"place": area, "level": level, "summary": summary},
                    turn=turn,
                ))
        return out
```

- [ ] **Step 4: Run test to verify it passes.**

Run: `python3 -m pytest -q tests/systems/test_cascade_system.py`
Expected: PASS (the whole CascadeSystem test file).

- [ ] **Step 5: Commit.**

```bash
cd /root/rpg-engine-app && git add systems/cascade.py tests/systems/test_cascade_system.py && git commit -m "feat(cascade): to_events emits one world_change per area

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `run_cascade` trigger narrows to narrator `world_change`; verdict gains `keep_spreading`

**Files:**
- Modify: `loop/cascade.py` (`_TRIGGER_TYPES`, `cascade_trigger`, `_NODE_SCHEMA`, `_node_prompt`)
- Test: `tests/loop/test_cascade_loop.py`

**Interfaces:**
- Consumes: `world["systems"]["ontology"]`, `heuristic_floor`, `_root_place`, `_is_place` (all existing in `loop/cascade.py`).
- Produces:
  - `cascade_trigger(new_events, world) -> list[str]` that returns roots ONLY for `world_change` events (with resolvable place + floor), and STILL skips `deferred`/`deferred_consume_through` markers.
  - `_NODE_SCHEMA` with a `keep_spreading: array[str]` property (and no `spread`/`magnitude`).
  - `_node_prompt(place_id, context)` text that names the place id verbatim and asks for an OPTIONAL `keep_spreading` of adjacent area ids, described as the LAST outward step.

- [ ] **Step 1: Write the failing tests.** Append to `tests/loop/test_cascade_loop.py`:

```python
# ---------------------------------------------------------------------------
# P1 Task 4: trigger narrows to narrator world_change; keep_spreading schema
# ---------------------------------------------------------------------------

def test_trigger_no_longer_fires_on_entity_moved():
    world = project(_reg(), [_place("town")])
    evs = [kernel_event("entity_moved", day=1, scene="s1", summary="到达",
                        deltas={"who": "hero", "to": "town"}, turn=2)]
    assert cascade_trigger(evs, world) == []     # P1: movement no longer self-triggers


def test_trigger_no_longer_fires_on_place_created_or_materialized():
    world = project(_reg(), [_place("town")])
    evs = [
        kernel_event("place_materialized", day=1, scene="s1", summary="m",
                     deltas={"id": "town"}, turn=2),
        kernel_event("place_created", day=1, scene="s1", summary="c",
                     deltas={"id": "town", "level": 2, "kind": "venue", "seed": "x"}, turn=2),
    ]
    assert cascade_trigger(evs, world) == []


def test_trigger_still_fires_on_narrator_world_change():
    world = project(_reg(), [_place("capital")])
    evs = [kernel_event("world_change", day=1, scene="s1", summary="陷落",
                        deltas={"place": "capital", "level": 1, "summary": "陷落"}, turn=2)]
    assert cascade_trigger(evs, world) == ["capital"]


def test_trigger_self_guard_still_skips_deferred_markers():
    world = project(_reg(), [_place("capital")])
    evs = [
        kernel_event("world_change", day=1, scene="capital", summary="hop",
                     deltas={"place": "capital", "level": 2, "deferred": True}, turn=3),
        kernel_event("world_change", day=1, scene="capital", summary="bk",
                     deltas={"place": "capital", "deferred_consume_through": 2}, turn=4),
    ]
    assert cascade_trigger(evs, world) == []


def test_node_schema_has_keep_spreading_not_spread():
    import loop.cascade as cmod
    props = cmod._NODE_SCHEMA["properties"]
    assert "keep_spreading" in props
    assert props["keep_spreading"]["type"] == "array"
    assert "spread" not in props
    assert "magnitude" not in props


def test_node_prompt_embeds_place_id_and_mentions_keep_spreading():
    import loop.cascade as cmod
    p = cmod._node_prompt("market", "王都陷落")
    assert "market" in p              # KeyedFakeProvider relies on this
    assert "keep_spreading" in p
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `python3 -m pytest -q tests/loop/test_cascade_loop.py -k "trigger_no_longer or trigger_still or self_guard_still or node_schema or node_prompt_embeds"`
Expected: FAIL — `entity_moved`/`place_*` still trigger (old `_TRIGGER_TYPES`); `_NODE_SCHEMA` still has `spread`; `_node_prompt` mentions `spread`, not `keep_spreading`.

- [ ] **Step 3: Write minimal implementation.** In `loop/cascade.py`:

(a) Shrink `_TRIGGER_TYPES`:

```python
# Event types that can trigger a cascade. P1: ONLY the narrator-declared
# world_change (via the `world` commit section) triggers — movement and
# place creation no longer self-trigger (the narrator decides scope/spread).
_TRIGGER_TYPES: frozenset[str] = frozenset({"world_change"})
```

(b) In `cascade_trigger`, drop the legacy opt-in clause. Replace the line
`qualifies_type = (t in _TRIGGER_TYPES) or bool(d.get("world_change"))` with:

```python
        qualifies_type = t in _TRIGGER_TYPES
```

(Leave the `deferred`/`deferred_consume_through` self-guard block ABOVE it untouched.)

(c) Replace `_NODE_SCHEMA`:

```python
_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "id":            {"type": "string"},
        "evolve":        {"type": "boolean"},
        "state":         {"type": "string"},
        "populace_mood": {"type": "string"},
        "note":          {"type": "string"},
        # P1: optional at-most-one-hop secondary spread. The node may name
        # adjacent area ids the event keeps spreading to (the LAST outward ring).
        "keep_spreading": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["evolve"],
}
```

(d) Replace `_node_prompt`:

```python
def _node_prompt(place_id: str, context: str) -> str:
    """Build the user prompt for a single node verdict.

    The place_id is embedded verbatim so a KeyedFakeProvider (DD5) and the
    ThreadPoolExecutor workers key their response by place_id without relying
    on call order. `keep_spreading` is the optional at-most-one-hop secondary
    spread (see DD3): the LAST outward ring, never descended further than once.
    """
    return (
        f"子地点 id：{place_id}\n"
        f"上级发生的事件：{context}\n\n"
        f"地点「{place_id}」是否因此发生变化(evolve)？"
        f"若是(evolve:true)，给出 state（新状态，中文）与/或 populace_mood（民众情绪，中文）。"
        f"若否(evolve:false)，则不再向下传播。"
        f"如果这场变故剧烈到足以越境波及紧邻的地区，可在 keep_spreading 中列出那些相邻地点的 id"
        f"（这是最后一圈外扩，系统不会再从那里继续蔓延）。无需外扩时省略 keep_spreading。"
    )
```

- [ ] **Step 4: Run test to verify it passes.**

Run: `python3 -m pytest -q tests/loop/test_cascade_loop.py -k "trigger_no_longer or trigger_still or self_guard_still or node_schema or node_prompt_embeds"`
Expected: PASS. (Other tests in this file may now fail — that is expected and fixed in Task 5/6. Do NOT run the whole suite yet.)

- [ ] **Step 5: Commit.**

```bash
cd /root/rpg-engine-app && git add loop/cascade.py tests/loop/test_cascade_loop.py && git commit -m "feat(cascade): trigger only on narrator world_change; verdict keep_spreading

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `run_cascade` executor — `keep_spreading` ring-1 hop, drop the old horizontal/root-spread code

**Files:**
- Modify: `loop/cascade.py` (`_vertical_bfs`, `_emit_hops`, `run_cascade`, constants `CASCADE_MAX_DEPTH`/`CASCADE_MAX_REGIONS`)
- Test: `tests/loop/test_cascade_loop.py`

**Interfaces:**
- Consumes: `_children`, `_adjacent_regions`, `_merge_same_region`, `_scene_subtree`, `lightweight_validate`, `_node_verdict`, `kernel_event`, the deferred-queue drain in `run_cascade`, `CascadeSystem`'s queue/consume machinery (unchanged).
- Produces:
  - `_vertical_bfs(..., allow_secondary: bool = False)` — when `allow_secondary` is True, an `evolve:true` verdict's `keep_spreading` ids that are real adjacent Places (validated via `_adjacent_regions`) and not yet touched become deferred ring-1 hops via `_emit_hops`; when False, `keep_spreading` is ignored (no ring 2). The old `spread`/`chain_targets` path is gone.
  - `_emit_hops(...)` caps emitted hops at `CASCADE_SECONDARY_BREADTH` (renamed from `CASCADE_MAX_REGIONS`) and drops the `CASCADE_MAX_DEPTH` check; keeps the local-vs-remote `scene_subtree` split.
  - `run_cascade(...)` — new-trigger descent calls `_vertical_bfs(allow_secondary=True)`; drain-at-start descent keeps `allow_secondary=False` (default). The root-level spread block is deleted. Node ctx is the trigger events' `summary`.
  - `CASCADE_SECONDARY_BREADTH = 3` module constant; `CASCADE_MAX_DEPTH`/`CASCADE_MAX_REGIONS` removed.

- [ ] **Step 1: Write the failing tests.** Append to `tests/loop/test_cascade_loop.py`:

```python
# ---------------------------------------------------------------------------
# P1 Task 5: keep_spreading ring-1 hop + at-most-one-hop enforcement
# ---------------------------------------------------------------------------

def test_keep_spreading_emits_deferred_remote_hop():
    """A node naming keep_spreading:[remote_region] emits a deferred world_change
    for that region (ring-1), and the region is queued — not descended this turn."""
    from loop.cascade import run_cascade
    reg = _reg(); store = _store(reg)
    store.append(_place("capital")); store.append(_place("market", parent="capital"))
    store.append(_place("farland")); store.append(_place("hamlet", parent="farland"))
    store.append(_link("market", "farland"))   # market ↔ farland (remote: not under capital)
    store.append(kernel_event("world_change", day=1, scene="capital", summary="陷落",
                              deltas={"place": "capital", "level": 1, "summary": "陷落"}, turn=2))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={
        "market": {"evolve": True, "state": "暴动", "keep_spreading": ["farland"]},
        "hamlet": {"evolve": True, "state": "should-not-run-yet"},
    })
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    assert any(e["type"] == "world_change" and e["deltas"]["place"] == "farland" for e in appended)
    world2 = project(reg, store.iter_events())
    assert any(q["region"] == "farland" for q in world2["systems"]["cascade"]["queue"])
    # ring-1 region's child NOT descended this turn
    assert all(not (e["type"] == "place_evolved" and e["deltas"]["id"] == "hamlet")
               for e in appended)


def test_keep_spreading_local_neighbor_inline_not_queued():
    """keep_spreading to a neighbor INSIDE the scene subtree is inline (not queued)."""
    from loop.cascade import run_cascade
    reg = _reg(); store = _store(reg)
    store.append(_place("capital"))
    store.append(_place("market", parent="capital"))
    store.append(_place("plaza", parent="capital"))   # sibling, same scene subtree
    store.append(_link("market", "plaza"))
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1, "summary": "x"}, turn=2))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={
        "market": {"evolve": True, "state": "暴动", "keep_spreading": ["plaza"]},
        "plaza":  {"evolve": True, "state": "s"},
    })
    run_cascade(reg, store, world, scene="capital", provider=prov)
    world2 = project(reg, store.iter_events())
    assert all(q["region"] != "plaza" for q in world2["systems"]["cascade"]["queue"])


def test_ring1_drained_next_turn_but_no_ring2():
    """Ring-1 region is drained next turn and descends its OWN children, but its
    nodes' keep_spreading is IGNORED (at-most-one-hop: no ring 2)."""
    from loop.cascade import run_cascade
    reg = _reg(); store = _store(reg)
    store.append(_place("capital")); store.append(_place("market", parent="capital"))
    store.append(_place("farland")); store.append(_place("hamlet", parent="farland"))
    store.append(_place("beyond"))                      # ring-2 candidate
    store.append(_link("market", "farland"))
    store.append(_link("hamlet", "beyond"))             # hamlet ↔ beyond (would be ring 2)
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1, "summary": "x"}, turn=2))
    prov = KeyedFakeProvider(by_place={
        "market": {"evolve": True, "state": "暴动", "keep_spreading": ["farland"]},
        "hamlet": {"evolve": True, "state": "波及", "keep_spreading": ["beyond"]},
    })
    # turn 1: ring-1 hop to farland queued
    run_cascade(reg, store, project(reg, store.iter_events()), scene="capital", provider=prov)
    # turn 2: drain farland → hamlet descends, but hamlet's keep_spreading[beyond] IGNORED
    appended2 = run_cascade(reg, store, project(reg, store.iter_events()),
                            scene="capital", provider=prov)
    assert any(e["type"] == "place_evolved" and e["deltas"]["id"] == "hamlet" for e in appended2)
    assert all(e["deltas"].get("place") != "beyond" for e in appended2
               if e["type"] == "world_change")     # no ring 2


def test_no_keep_spreading_no_hop():
    """evolve:true WITHOUT keep_spreading emits no adjacent world_change (pure vertical)."""
    from loop.cascade import run_cascade
    reg = _reg(); store = _store(reg)
    store.append(_place("capital")); store.append(_place("market", parent="capital"))
    store.append(_place("outskirts")); store.append(_link("capital", "outskirts"))
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1, "summary": "x"}, turn=2))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={"market": {"evolve": True, "state": "s"}})
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    assert [e for e in appended if e["type"] == "world_change"] == []


def test_keep_spreading_ignores_non_adjacent_id():
    """A keep_spreading id that is NOT an adjacent Place is dropped (no hop)."""
    from loop.cascade import run_cascade
    reg = _reg(); store = _store(reg)
    store.append(_place("capital")); store.append(_place("market", parent="capital"))
    store.append(_place("unrelated"))   # exists but NOT linked to market
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1, "summary": "x"}, turn=2))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={
        "market": {"evolve": True, "state": "s", "keep_spreading": ["unrelated"]},
    })
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    assert all(e["deltas"].get("place") != "unrelated" for e in appended
               if e["type"] == "world_change")


def test_secondary_breadth_cap():
    """More than CASCADE_SECONDARY_BREADTH keep_spreading targets → capped."""
    from loop.cascade import run_cascade
    import loop.cascade as cmod
    reg = _reg(); store = _store(reg)
    store.append(_place("capital")); store.append(_place("market", parent="capital"))
    targets = [f"nbr{i}" for i in range(cmod.CASCADE_SECONDARY_BREADTH + 3)]
    for t in targets:
        store.append(_place(t)); store.append(_link("market", t))
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1, "summary": "x"}, turn=2))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={
        "market": {"evolve": True, "state": "s", "keep_spreading": targets},
    })
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    hops = [e for e in appended if e["type"] == "world_change" and e["deltas"].get("place") in targets]
    assert len(hops) <= cmod.CASCADE_SECONDARY_BREADTH


def test_max_depth_and_max_regions_constants_removed():
    import loop.cascade as cmod
    assert not hasattr(cmod, "CASCADE_MAX_DEPTH")
    assert not hasattr(cmod, "CASCADE_MAX_REGIONS")
    assert cmod.CASCADE_SECONDARY_BREADTH == 3
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `python3 -m pytest -q tests/loop/test_cascade_loop.py -k "keep_spreading or ring1 or no_keep or secondary_breadth or constants_removed"`
Expected: FAIL — `keep_spreading` does nothing yet; `CASCADE_MAX_DEPTH`/`CASCADE_MAX_REGIONS` still exist; `CASCADE_SECONDARY_BREADTH` undefined.

- [ ] **Step 3: Write minimal implementation.** In `loop/cascade.py`:

(a) Replace the depth/region constants block (the `CASCADE_MAX_DEPTH` and `CASCADE_MAX_REGIONS` definitions) with a single secondary-breadth constant:

```python
# P1: secondary ("keep_spreading") spread is at most ONE hop and bounded in
# breadth by this constant (replaces the removed depth-3 horizontal chain and
# CASCADE_MAX_REGIONS root-spread cap).
CASCADE_SECONDARY_BREADTH: int = 3
```

(b) Rewrite `_emit_hops`: delete the `hop_level > CASCADE_MAX_DEPTH` prune branch and replace the `n_emitted >= CASCADE_MAX_REGIONS` cap with `n_emitted >= CASCADE_SECONDARY_BREADTH`. Keep the `_merge_same_region` call, the local-vs-remote `scene_subtree` split, and both `kernel_event` emissions verbatim. The signature loses `root_level` (no longer used). New `_emit_hops`:

```python
def _emit_hops(
    chain_targets: dict,
    graph,
    day: int,
    scene: str,
    turn: int,
    scene_subtree: set | None,
    store,
) -> list[dict]:
    """Emit at-most-one-hop secondary world_change events for keep_spreading targets.

    chain_targets: mapping region_id → {"region", "level"}.
    Applies _merge_same_region + the CASCADE_SECONDARY_BREADTH breadth cap +
    local (inline) vs remote (deferred) split via scene_subtree (§12 line 176).
    Returns list of emitted events (already appended to store).
    """
    if not chain_targets:
        return []
    appended: list[dict] = []
    merged = _merge_same_region(list(chain_targets.values()), graph, day)
    n_emitted = 0
    for hop in merged:
        if n_emitted >= CASCADE_SECONDARY_BREADTH:
            log.info(
                "cascade: secondary breadth cap %d hit; %d region(s) dropped: %s",
                CASCADE_SECONDARY_BREADTH, len(merged) - n_emitted,
                [h["region"] for h in merged[n_emitted:]],
            )
            break
        region = hop["region"]
        hop_level = hop["level"]
        is_local = (scene_subtree is None) or (region in scene_subtree)
        if is_local:
            ev_hop = kernel_event(
                "world_change", day=day, scene=scene,
                summary=f"{region} 受波及",
                deltas={"place": region, "level": hop_level, "summary": f"{region} 受波及"},
                turn=turn,
            )
        else:
            ev_hop = kernel_event(
                "world_change", day=day, scene=scene,
                summary=f"{region} 受波及",
                deltas={"place": region, "level": hop_level, "summary": f"{region} 受波及",
                        "deferred": True, "reason": "remote", "depth": hop_level},
                turn=turn,
            )
        store.append(ev_hop)
        appended.append(ev_hop)
        log.debug("cascade: secondary hop region=%s level=%d local=%s",
                  region, hop_level, is_local)
        n_emitted += 1
    return appended
```

(c) Rewrite `_vertical_bfs`: add `allow_secondary: bool = False` to the signature (after `scene_subtree`); replace the `if verdict.get("spread"):` accumulation block with a `keep_spreading` accumulation gated by `allow_secondary`; and replace the post-BFS `_emit_hops(chain_targets=..., root_level=..., allowed_ids=...)` call with the new signature. The `keep_spreading` accumulation:

```python
            # P1: at-most-one-hop secondary spread. Only when allow_secondary
            # (the declared-area descent); a region reached via a hop does NOT
            # open a further ring (drain passes allow_secondary=False).
            if allow_secondary and verdict.get("keep_spreading"):
                adj = set(_adjacent_regions(graph, place_id, day))
                for region in verdict["keep_spreading"]:
                    if (region in adj and region not in allowed_ids
                            and region not in seen and region not in chain_targets):
                        chain_targets[region] = {"region": region, "level": root_level + 1}
```

The post-BFS emission becomes:

```python
    hop_events = _emit_hops(
        chain_targets=chain_targets,
        graph=graph,
        day=day,
        scene=scene,
        turn=turn,
        scene_subtree=scene_subtree,
        store=store,
    )
    appended.extend(hop_events)
    return appended
```

Keep `root_level` as an existing `_vertical_bfs` parameter (it is used for the hop level). Remove the now-unused `allowed_ids` argument from the `_emit_hops` call only (the BFS itself still uses `allowed_ids` for `lightweight_validate`).

(d) In `run_cascade`, the NEW-TRIGGER block:
- Delete the `【邻近区域】... spread:true` context augmentation (`adj` loop + the `ctx = ctx + (...)` lines).
- Keep building `ctx` from the trigger events' summaries: `ctx_parts = [e.get("summary", "") for e in trigger_events if e.get("summary")]; ctx = "; ".join(ctx_parts) if ctx_parts else "world change"`.
- Keep the `root_level` derivation from trigger `world_change` `deltas["level"]`.
- Pass `allow_secondary=True` to the new-trigger `_vertical_bfs` call.
- DELETE the entire root-level spread block (from `turn = _next_cascade_turn(store)  # refresh turn after vertical BFS` through the trailing `appended.extend(hop_events)` / `log.debug("run_cascade: root-level spread emitted ...")`).

The drain-at-start `_vertical_bfs` calls keep their current arguments and do NOT pass `allow_secondary` (defaults to `False`), so a drained ring-1 region descends its children but opens no ring 2.

- [ ] **Step 4: Run test to verify it passes.**

Run: `python3 -m pytest -q tests/loop/test_cascade_loop.py -k "keep_spreading or ring1 or no_keep or secondary_breadth or constants_removed or run_cascade_visits or prune_stops or node_budget or parallel or remote_hop_deferred or queued_remote or local_neighbor or own_world_change or injects_id or overrides_hallucinated or node_exception"`
Expected: PASS for the P1 tests and the still-valid C1/C2 tests (vertical descent, budget, parallel, prune, drain, defensive). The OLD horizontal/root-spread tests are addressed in Task 6 — they may still be present and FAILING at this point; do NOT delete or edit them here.

- [ ] **Step 5: Commit.**

```bash
cd /root/rpg-engine-app && git add loop/cascade.py tests/loop/test_cascade_loop.py && git commit -m "feat(cascade): run_cascade executor with at-most-one-hop keep_spreading

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Update the pre-existing tests that asserted the OLD self-trigger / horizontal behavior

> This task is called out explicitly per the spec: P1 deliberately removes behaviors that existing GREEN tests assert. Those tests must be UPDATED (not silently deleted) to reflect the new contract, and the rationale recorded here.

**Files:**
- Modify: `tests/loop/test_cascade_loop.py`
- Modify: `tests/loop/test_turn.py`
- Test: both files (full run)

**Which tests change and why:**

In `tests/loop/test_cascade_loop.py` — DELETE these tests (the behavior they assert is removed; replacements live in Tasks 4/5):
- `test_trigger_on_entity_moved_uses_destination_place` — asserted `entity_moved` self-triggers. **Removed:** movement no longer triggers (Task 4 `test_trigger_no_longer_fires_on_entity_moved` asserts the new behavior).
- `test_trigger_dedupes_and_ignores_non_place_ids` — built on `place_materialized` + `world_change` as co-triggers. **Replace** with a `world_change`-only dedupe test (below) since `place_materialized` no longer triggers.
- All of these (the OLD horizontal `spread`/`chain_targets` chain — Task 9 era): `test_horizontal_chain_emits_adjacent_world_change_level_plus_1`, `test_horizontal_chain_depth_cap_blocks_at_max_depth`, `test_horizontal_chain_merges_same_region`, `test_horizontal_chain_region_cap`, `test_no_spread_no_horizontal_hop`. **Removed:** the `spread`/`magnitude` per-child chain and `CASCADE_MAX_DEPTH`/`CASCADE_MAX_REGIONS` are gone; Task 5's `keep_spreading` tests (`test_keep_spreading_*`, `test_secondary_breadth_cap`, `test_no_keep_spreading_no_hop`, `test_keep_spreading_local_neighbor_inline_not_queued`) cover the replacement, and `test_horizontal_chain_merges_same_region`'s merge guarantee is preserved by `_merge_same_region` (still unit-exercised through `test_secondary_breadth_cap` + the local/remote tests).
- All of the OLD root-level spread tests (the `# Task C2-fix` block): `test_root_level_spread_emits_hop_to_adjacent_region`, `test_root_level_spread_children_also_evolve_independently`, `test_root_level_no_spread_no_hop`, `test_root_level_spread_respects_region_cap`, `test_root_level_spread_local_neighbor_not_deferred`. **Removed:** the root-level `_root_spread_verdict` block is deleted (DD3); root spread is now expressed by the narrator's multi-area `world` declaration (one `world_change` root per area, Task 3) plus per-node `keep_spreading` (Task 5).

KEEP unchanged (still valid under P1): `test_trigger_empty_when_no_significant_event`, `test_trigger_on_world_change_returns_root_place`, `test_lightweight_validate_*`, `test_run_cascade_visits_children_and_emits_place_evolved`, `test_run_cascade_injects_id_when_model_omits_it`, `test_run_cascade_prune_stops_descent`, `test_run_cascade_respects_node_budget`, `test_run_cascade_no_trigger_returns_empty`, `test_run_cascade_overrides_hallucinated_id`, `test_run_cascade_node_exception_drops_node_not_whole_cascade`, `test_run_cascade_parallel_fanout_outcome_set`, `test_run_cascade_parallel_outcome_is_thread_schedule_invariant`, `test_resolve_concurrency_precedence`, `test_remote_hop_deferred_not_descended_this_turn` (NOTE: this one uses the old `spread:True` verdict to trigger the remote hop — UPDATE its provider verdict to `keep_spreading:["farland"]` since `spread` no longer exists; see below), `test_queued_remote_region_drained_next_turn` (same update: `"market": {... "keep_spreading": ["farland"]}`), `test_local_neighbor_stays_inline_not_queued` (same update: `"market": {... "keep_spreading": ["plaza"]}`), `test_cascade_own_world_change_does_not_retrigger`.

> NOTE on the three "KEEP-but-update" tests (`test_remote_hop_deferred_not_descended_this_turn`, `test_queued_remote_region_drained_next_turn`, `test_local_neighbor_stays_inline_not_queued`): these assert the deferred-queue / drain / local-inline mechanics that P1 KEEPS, but their fixture provider used the removed `spread:True` key. They overlap with Task 5's `test_keep_spreading_*` tests. To avoid two near-duplicate tests, DELETE these three (their mechanics are re-asserted by `test_keep_spreading_emits_deferred_remote_hop`, `test_ring1_drained_next_turn_but_no_ring2`, and `test_keep_spreading_local_neighbor_inline_not_queued` respectively) — recording the overlap here.

In `tests/loop/test_turn.py` — UPDATE these two (they relied on the `entity_moved` self-trigger, which P1 removes):
- `test_run_turn_invokes_cascade_on_triggering_turn` — the narrator commit moved hero into `capital` and relied on `entity_moved` tripping the cascade. **Update:** make the narrator commit ALSO declare a `world` section so the cascade triggers the new way. Change its `provider` json to add `"world": [{"areas": ["capital"], "level": 1, "summary": "王都骤变"}]` (keep the `moves`/`places`/`cast`/`facts` keys). The assertion (place_evolved for `market`) stays — `market` is `capital`'s child, descended from the declared `capital` root.
- `test_run_turn_passes_cascade_provider_to_cascade` — same fix: add `"world": [{"areas": ["capital"], "level": 1, "summary": "王都骤变"}]` to `main_provider`'s json. The assertion (cascade node calls went to `cascade_sentinel`) stays.

- [ ] **Step 1: Apply the deletions/updates above.** In `tests/loop/test_cascade_loop.py`, delete the named tests; replace `test_trigger_dedupes_and_ignores_non_place_ids` with:

```python
def test_trigger_dedupes_world_change_roots():
    world = project(_reg(), [_place("town")])
    evs = [
        kernel_event("world_change", day=1, scene="s1", summary="w",
                     deltas={"place": "town", "level": 1, "summary": "w"}, turn=2),
        kernel_event("world_change", day=1, scene="s1", summary="w2",
                     deltas={"place": "town", "level": 1, "summary": "w2"}, turn=2),
        kernel_event("world_change", day=1, scene="s1", summary="w3",
                     deltas={"place": "nowhere", "summary": "w3"}, turn=2),  # not a Place
    ]
    assert cascade_trigger(evs, world) == ["town"]
```

In `tests/loop/test_turn.py`, edit the two providers as described (add the `world` key to each json response dict).

- [ ] **Step 2: Run the cascade + turn tests.**

Run: `python3 -m pytest -q tests/loop/test_cascade_loop.py tests/loop/test_turn.py`
Expected: PASS (no remaining references to `spread`/`CASCADE_MAX_DEPTH`/`CASCADE_MAX_REGIONS`/`_root_spread`; the two turn tests now trigger cascade via the `world` section).

- [ ] **Step 3: Full-suite gate.**

Run: `python3 -m pytest -q --ignore=tests/test_embed_real.py`
Expected: all pass (count = 738 minus the deleted cascade/turn tests, plus the new P1 tests added in Tasks 1-5; the net is green either way — what matters is 0 failures). If anything outside cascade fails, re-read the failing test and the cited source; do NOT change unrelated source to make a test pass.

- [ ] **Step 4: Commit.**

```bash
cd /root/rpg-engine-app && git add tests/loop/test_cascade_loop.py tests/loop/test_turn.py && git commit -m "test(cascade): retire old self-trigger/horizontal tests; drive cascade via world section

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Expose `world` in 甲 `_SYSTEM_PROMPT` + 丙 `_SYSTEM_PROMPT_HYBRID`

**Files:**
- Modify: `loop/strategy.py` (`_SYSTEM_PROMPT`, `_SYSTEM_PROMPT_HYBRID`)
- Test: `tests/loop/test_strategy.py`

**Interfaces:**
- Consumes: nothing new (string edits only).
- Produces: both prompt strings contain a `world:` bullet in the `【结构】` list and a `【世界事件·world（可选段）】` explanatory block. `world` stays OPTIONAL (NOT added to `loop.turn.REQUIRED_SECTIONS`).

- [ ] **Step 1: Write the failing tests.** Append to `tests/loop/test_strategy.py` (create the file with the standard header if it does not yet exist — check first; the repo has `tests/loop/` tests for turn/cascade, mirror their import style):

```python
# ---------------------------------------------------------------------------
# P1 Task 7: `world` section exposed in 甲 + 丙 prompts (optional)
# ---------------------------------------------------------------------------

def test_world_section_in_author_prompt():
    from loop.strategy import _SYSTEM_PROMPT
    assert "world:" in _SYSTEM_PROMPT
    assert "areas" in _SYSTEM_PROMPT
    assert "世界事件" in _SYSTEM_PROMPT


def test_world_section_in_hybrid_prompt():
    from loop.strategy import _SYSTEM_PROMPT_HYBRID
    assert "world:" in _SYSTEM_PROMPT_HYBRID
    assert "areas" in _SYSTEM_PROMPT_HYBRID
    assert "世界事件" in _SYSTEM_PROMPT_HYBRID


def test_world_section_is_optional_not_required():
    from loop.turn import REQUIRED_SECTIONS
    assert "world" not in REQUIRED_SECTIONS
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `python3 -m pytest -q tests/loop/test_strategy.py -k world_section`
Expected: FAIL — neither prompt mentions `world:`/`areas`/`世界事件` yet. (`test_world_section_is_optional_not_required` PASSES already — `world` was never required — that is fine, it is a regression guard.)

- [ ] **Step 3: Write minimal implementation.** In `loop/strategy.py`:

(a) In `_SYSTEM_PROMPT`, add a bullet to the `【结构】` list (after the `knowledge:` bullet) and a block after the `【信息视野·knowledge（可选段）】` block. The bullet:

```
- world: 区域/世界级事件波及的地点（可选）——详见下【世界事件】
```

The block (insert before the final `规则：` line):

```
【世界事件·world（可选段）】当本回合发生区域级或世界级的大事——灾难、战争、瘟疫、政权更替、重大变故——用 world 段点名所有受影响的地点，引擎据此向下波及这些地点的子地点。你有完整剧情视野，可点名任意位置的地点（不限当前场景的邻居）：
- world: [{"areas":[受影响地点id, ...], "level":1|2|3, "summary":"一句话事件"}]
areas 用已存在或本回合刚创建的地点 id；level 表示烈度（1 最轻、3 最重）；summary 一句话描述这件事。寻常的个人回合（赶路、对话、独自行动）不必给本段，省略即可（无需写 reason）。
```

(b) In `_SYSTEM_PROMPT_HYBRID`, add the same bullet to its `每个段落都是对象数组` list (after the `knowledge` bullet) and the same `【世界事件·world（可选段）】` block (insert before the final `6. 只输出合法 JSON` rule), adapting the lead-in to the hybrid framing ("散文中若描写了区域级/世界级大事..."):

```
   - world: 区域/世界级事件波及的地点（可选）——见第 7 条
```

and the block:

```
7. 【世界事件·world（可选段）】散文中若描写了区域级或世界级的大事（灾难、战争、瘟疫、政权更替、重大变故），用 world 段点名所有受影响地点：world: [{"areas":[受影响地点id,...],"level":1|2|3,"summary":"一句话事件"}]。areas 用已存在或本回合刚创建的地点 id；你有完整世界视野，可点名任意位置。寻常个人场景省略本段。
```

(Renumber the trailing JSON-only rule to `8.` so the list stays sequential.)

- [ ] **Step 4: Run test to verify it passes.**

Run: `python3 -m pytest -q tests/loop/test_strategy.py`
Expected: PASS.

- [ ] **Step 5: Full-suite gate + commit.**

Run: `python3 -m pytest -q --ignore=tests/test_embed_real.py`
Expected: all pass.

```bash
cd /root/rpg-engine-app && git add loop/strategy.py tests/loop/test_strategy.py && git commit -m "feat(strategy): expose optional world section in 甲 + 丙 prompts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: End-to-end integration test — narrator `world` section drives a vertical cascade through `run_turn`

**Files:**
- Test: `tests/loop/test_turn.py`

**Interfaces:**
- Consumes: `run_turn`, `AuthorStrategy`, `FakeLLMProvider`, the full P1 wiring (`CascadeSystem.to_events` → `world_change` → `run_cascade` trigger → vertical descent).
- Produces: a regression test proving the P1 end-to-end path (narrator declares `world` → cascade descends the declared area's children).

- [ ] **Step 1: Write the failing test.** Append to `tests/loop/test_turn.py`:

```python
# ---------------------------------------------------------------------------
# P1: narrator `world` section drives the cascade end-to-end
# ---------------------------------------------------------------------------

def test_run_turn_world_section_triggers_vertical_cascade(monkeypatch):
    """The narrator declares world:[{areas:[capital],...}] → one world_change per
    area → run_cascade descends capital's child (market) → place_evolved lands."""
    import loop.cascade as cmod
    from loop.turn import run_turn
    from loop.strategy import AuthorStrategy

    reg = _reg_with_cascade()
    store = _open_temp_store(reg)
    store.append(kernel_event(
        "place_created", day=1, scene="genesis", summary="capital 创建",
        deltas={"id": "capital", "level": 1, "kind": "settlement", "seed": "x", "tier": "tracked"},
        turn=0,
    ))
    store.append(kernel_event(
        "place_created", day=1, scene="genesis", summary="market 创建",
        deltas={"id": "market", "level": 2, "kind": "venue", "seed": "y",
                "tier": "tracked", "parent": "capital"},
        turn=0,
    ))
    store.append(kernel_event(
        "entity_created", day=1, scene="genesis", summary="hero 创建",
        deltas={"id": "hero", "etype": "Person", "tier": "tracked"},
        turn=0,
    ))
    world = project(reg, store.iter_events())
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "capital", "location": "capital"}

    monkeypatch.setattr(
        cmod, "_node_verdict",
        lambda place_id, ctx, provider: (
            {"id": place_id, "evolve": True, "state": "动荡"}
            if place_id == "market" else {"evolve": False}
        ),
    )

    # Narrator declares a world event over `capital` (NOT a move) → triggers cascade
    provider = FakeLLMProvider(json_responses=[{
        "narration": "王都骤变。",
        "moves": [], "places": [], "cast": [], "facts": [],
        "world": [{"areas": ["capital"], "level": 1, "summary": "王都骤变"}],
    }])
    run_turn(reg, store, world, scene, "环顾四周",
             strategy=AuthorStrategy(), provider=provider,
             embedder=None, max_repairs=1)

    all_events = list(store.iter_events())
    assert any(e["type"] == "place_evolved" and e["deltas"]["id"] == "market"
               for e in all_events), \
        f"Expected place_evolved for market; got {[e['type'] for e in all_events]}"
```

- [ ] **Step 2: Run test to verify it fails... or passes.**

Run: `python3 -m pytest -q tests/loop/test_turn.py::test_run_turn_world_section_triggers_vertical_cascade`
Expected: PASS (Tasks 1-7 already implement the whole path). This task is a pure regression-lock; if it FAILS, the failure points at a wiring bug in Tasks 1-5 — re-read `apply_turn`'s section routing (`loop/turn.py` lines 164-176) and `CascadeSystem.to_events`. Do NOT add new source to make it pass; fix the actual defect in the earlier task's file.

- [ ] **Step 3: Full-suite gate.**

Run: `python3 -m pytest -q --ignore=tests/test_embed_real.py`
Expected: all pass.

- [ ] **Step 4: Commit.**

```bash
cd /root/rpg-engine-app && git add tests/loop/test_turn.py && git commit -m "test(turn): end-to-end world-section drives vertical cascade

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec §2.1 coverage (each bullet → task):**

| spec §2.1 / §4-P1 requirement | Task |
|---|---|
| 叙事模型在 turn-commit 产 `world` 段（像 knowledge 段暴露给甲/丙） | Task 1 (ownership) + Task 7 (prompt exposure) |
| `world` schema `{areas:[id], level, summary}` | Task 2 (validate) + Task 3 (to_events) |
| 一个 system 认领 `world` 段 → world_change 事件 | Task 1 + Task 3 (one `world_change` per area — DD1) |
| cascade 退化成纯执行器：对每个 area 纵向下沉（廉价模型填后果细节） | Task 4 (trigger narrows) + Task 5 (`run_cascade` executor) |
| 次级 spread 最多一层（廉价模型在每个 area 自行点名再外扩一格、不再深入） | Task 5 (`keep_spreading`, `allow_secondary` ring-1, drain has no ring-2 — DD3) |
| 是否/往哪蔓延 = 叙事模型定 | Task 7 (narrator declares `world` areas) |
| 烧成啥样 + 末端再扩一格 = 廉价舰队定 | Task 4/5 (`_node_verdict` fills detail + `keep_spreading`) |
| 保留并行 fan-out / 懒延迟队列 / 合并同区，只换触发源 | Task 5 (reuses `_vertical_bfs` pool, deferred queue, `_merge_same_region`) |
| §4-P1 暴露进甲/丙 prompt + 次级一层 + 节点加"再扩一格"决策 | Task 5 + Task 7 |
| §4-P1 end-to-end "横向终于能由叙事模型驱动地触发" | Task 8 |
| Dual nature of `world_change` (narrator-validated vs harness bookkeeping); self-guard intact | DD1/DD3 + Task 4 (`test_trigger_self_guard_still_skips_deferred_markers`) |

No spec §2.1 bullet is unaddressed. (§2.2-§2.5 are P2/P3 and explicitly out of scope for this plan.)

**2. Placeholder scan:** No "TBD"/"TODO"/"handle edge cases"/"similar to Task N"/"write tests for the above". Every code step shows the real code; every test step shows the real test body; every command shows the exact invocation and expected result. The two "expected PASS" steps (Task 8 Step 2, and the optional-already-true assertion in Task 7 Step 2) are flagged inline as regression-locks, not gaps.

**3. Name consistency with shipped `loop/cascade.py`:** Verified against the current file —
- Kept names referenced: `cascade_trigger`, `_root_place`, `_is_place`, `_children`, `_adjacent_regions`, `_merge_same_region`, `_scene_subtree`, `lightweight_validate`, `_node_verdict`, `_vertical_bfs`, `_emit_hops`, `run_cascade`, `_next_cascade_turn`, `_HARNESS_TYPES`, `_resolve_concurrency`, `CASCADE_BREADTH`, `CASCADE_FLOOR`, `CASCADE_NODE_BUDGET`. All exist verbatim.
- Renamed/removed: `_TRIGGER_TYPES` (narrowed to `{"world_change"}`), `CASCADE_MAX_DEPTH` (removed), `CASCADE_MAX_REGIONS` (→ `CASCADE_SECONDARY_BREADTH`), `_NODE_SCHEMA.spread`/`.magnitude` (→ `keep_spreading`). Each rename has a dedicated assertion (Task 4 `test_node_schema_*`, Task 5 `test_max_depth_and_max_regions_constants_removed`).
- `CascadeSystem`: `commit_sections`/`validate`/`to_events`/`created_ids`/`apply`/`empty_state`/`event_types`/`requires`/`name` — all match the `ContextSystem` ABC and the `knowledge`/`place` precedents. `apply` is untouched and the one-`world_change`-per-area shape feeds its existing `world_change` branch (verified: `apply` reads `deltas["place"]`, `deltas.get("level")`, `event.get("summary")`, plus the `deferred`/`deferred_consume_through` branches — all satisfied).
- `_emit_hops` signature change (drops `root_level`, `allowed_ids`) is internal to `loop/cascade.py`; both call sites (the deleted root-spread one, and the `_vertical_bfs` one) are updated in Task 5. No external caller — grep confirms `_emit_hops` is only referenced inside `loop/cascade.py`.

**4. Guardrail consistency:** Tasks touch only `systems/cascade.py`, `loop/cascade.py`, `loop/strategy.py`, and the four mirrored test files. No `engine/`, `_legacy/`, `data/`, or `docs/` (besides this plan). No git init/reset/branch-switch. Each task's full-suite gate keeps the existing suite green; Task 6 is the single sanctioned place where pre-existing tests change, with the which/why recorded.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-19-P1-cascade-world-section.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
2. **Inline Execution** — execute tasks in this session via superpowers:executing-plans, batch execution with checkpoints. REQUIRED SUB-SKILL: superpowers:executing-plans.

Which approach?
