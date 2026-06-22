# Density-Gen Integration Map (read before implementing)

Exact signatures/shapes from the current `app`-branch code. Trust this over assumptions; verify against the file if editing it.

## create_lore_line — loop/lore.py:22
```python
def create_lore_line(store, skeleton: dict, *, day: int, scene: str, turn: int,
                     lifespan_days: int | None = None) -> dict
```
Required skeleton keys: `id, complexity, about, anchor, stages(list[{hint}]), threshold, description, trigger, l3_anchor`. Optional: `secret, state(default "暗")`. Emits `lore_created`, appends to store, returns the event. lifespan_days kwarg overrides per-complexity default (else LoreSystem assigns simple3/med7/cplx20 at projection).

## Determinism — engine/oracle.py
- `Oracle(seed)`: `.d100()`→1..100, `.chance(p)`, `.pick(items)`, `.draw(entries[{weight}])`, `.random()`, `.randint(a,b)`.
- `scene_seed(campaign_seed, scene_ordinal, salt=0)` → int (sha256). campaign_seed = `world["meta"]["campaign_seed"]`. ordinal can be a string key.
- Canonical: `Oracle(scene_seed(campaign_seed, f"lore:{lid}", next_turn)).d100()` (loop/lore.py:137).

## Providers
- `engine.cascade_provider` (app/engine.py:62) — cheap backstage provider, **may be None** (env RPG_CASCADE_MODEL/GLM_CASCADE_MODEL unset → None). Pass it into run_turn already.
- JSON: `provider.complete_json(system, user, schema, **kw) -> dict` (raises ValueError after 2 parse fails).
- `FakeLLMProvider(json_responses=[dict,...])` cycles dicts through complete_json (no parsing). `.calls` records `(system,user)`.

## Places = FactGraph in world["systems"]["ontology"]  (NOT world["places"]!)
- `g = world["systems"]["ontology"]`; `e = g.get_entity(pid)` → `Entity(id, etype, tier, attrs)` | None.
- Place attrs: `level(int 1|2|3), kind, seed, detail, last_update`. **PlaceSystem.apply (systems/place.py:123-129) only copies level/kind/seed/detail from place_created deltas** — extra keys (e.g. `density`) are NOT stored unless you extend that copy loop.
- **parent is an EDGE, not an attr**: `g.neighbors(pid, "contained_by", day)` → list[str] parents.
- L-ancestor walk (adapt `_l2_ancestor`, loop/lore_disclosure.py:26):
```python
def _ancestor_of_level(g, place_id, day, level):
    cur, seen = place_id, set()
    while cur and cur not in seen:
        seen.add(cur)
        e = g.get_entity(cur)
        if e and e.attrs.get("level") == level: return cur
        ps = g.neighbors(cur, "contained_by", day); cur = ps[0] if ps else None
    return None
```

## run_turn movement — loop/turn.py
- `_protagonist_location(world, protagonist)` (L74) → current L3 id. `_l2_ancestor(g, l3, day)` (imported L38) → current town.
- prev location snapshot at L265 (before `apply_turn` at L277). Backstage hooks run after, ~L354-375.
- Hook pattern (copy this exactly — demote hook, L366):
```python
try:
    if registry.owner_of_event("lore_seeded") is not None:
        ev = run_density(registry, store, new_world, protagonist, prev_l2, provider=cascade_provider, day=day, scene=scene_id, turn=turn_num_before)
        if ev:
            new_world = project(registry, store.iter_events())
except Exception:
    log.exception("run_turn: run_density failed (non-fatal, backstage)")
```
(prev_l2 must be resolved from prev_loc BEFORE apply_turn, threaded in.)

## Quest lines — world["systems"]["lore"]["lines"][id]
Per-line keys incl: `complexity, anchor(L2 town), state(暗/明/了结), status(active/resolved/expired), stage_idx, born_day, lifespan_days`. Count a tier:
```python
sum(1 for ln in lines.values() if ln.get("anchor")==town and ln.get("complexity")=="simple"
    and ln.get("state") in ("暗","明") and ln.get("status")=="active")
```

## Backstage hook append+project pattern — loop/turn.py:354
Hook calls `store.append(...)` (create_lore_line does) directly; caller reprojects only if events were appended; wrapped try/except non-fatal; guarded by `registry.owner_of_event(type) is not None`.

## place_created deltas — id, level, kind, seed, tier, detail, parent
To put density on a region: add `density` to deltas AND extend PlaceSystem attr-copy to include it; read back `g.get_entity(region).attrs.get("density")`.

## DECISIONS for gen-state storage
- `density` attr: store on place entity → extend `systems/place.py` attr-copy tuple to include `"density"`.
- seeded / last_refresh_day (mutable per-town gen state): store in **LoreSystem** state — add `world["systems"]["lore"]["gen"] = {town_id: {"seeded": bool, "last_refresh_day": int}}`, written by NEW events `lore_seeded{town}` / `density_refreshed{town, day}` owned by LoreSystem (add to event_types()/apply, replay-safe).
