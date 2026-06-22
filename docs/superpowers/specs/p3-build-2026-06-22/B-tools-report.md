# P3a Tools Report — B-tools (Tasks 3, 4, 5)

Build date: 2026-06-22  
Agent: Claude Sonnet 4.6 (tools side)  
Commit range: 0c00eb1..e8c429c  
Files delivered: `llm/tools.py` (new), `tests/llm/test_tools.py` (new)  

---

## What was built

### Task 3 — Tool / ToolRegistry scaffold

`Tool` is a dataclass with `name`, `description`, `parameters` (JSON-schema), and `fn`.
`schema()` returns the OpenAI function-calling shape `{"type":"function","function":{...}}`.

`ToolRegistry` holds a `dict[name, Tool]`:
- `schemas()` → `[t.schema() for t in tools]` — the `tools` array sent to the provider.
- `execute(name, args) -> str` — looks up by name, calls `fn(**args)` inside a
  `try/except Exception`, logs and returns `{"error": "<msg>"}` JSON on any failure
  (including unknown tool name). A bad model arg can NEVER crash the turn (DD3).

`build_tool_registry(registry, world, scene, *, dm=False) -> ToolRegistry` assembles
the P3a POV set: `map_query` + `recall_query`. `dm=True` is accepted for forward-compat
(P3b) but currently builds the same POV set (the DM branch is not yet implemented).

### Task 4 — map_query POV fog

Fog rule applied (DD4):

- **Structural topology = PUBLIC**: `level`, `kind`, `seed` attrs; `contained_by` parent;
  `adjacent_to` exits with `travel_cost`; navigate paths (via `systems.place.navigate`).
  None of these touch `knows()` — they are always returned regardless of protagonist knowledge.

- **Place state/detail facts = fog-gated**: Any predicate on a Place entity that is not
  in `_INTERNAL_PREDICATES` (`level`, `kind`, `seed`, `detail`, `density`, `last_update`,
  `tier`) and does not start with an internal prefix (`knows:`, `trust:`, `rank:`, `group:`,
  `hidden:`) is treated as a story fact. For each such fact, the tool calls:
  ```python
  believed = knows(graph, pov, f"{place_id}.{predicate}", day)
  ```
  If `believed is not None` → emit the believed value (not `graph.value_at` truth).  
  If `believed is None` → omit entirely. Ground truth never appears in any POV path.

POV validation (DD5): `pov` arg defaults to `scene["protagonist"]`. If supplied, it must
be `== protagonist` OR in `scene["present"]`. Out-of-scene pov → `{"error":"pov not in scene"}`.

No call to `graph.value_at` anywhere in the POV tool path (verified by grep). The module
docstring comment referencing `value_at` is in the description of what NOT to do.

### Task 5 — recall_query POV fog + navigate path

**recall_query** calls `kernel.recall.recall(registry, q, world)` (fan-out substring
matching over all registered systems), then applies fog:

- Hits with `ref["fact_key"]` set → call `knows(graph, pov, fact_key, day)`.
  If `None` → drop the hit (the POV agent doesn't know this fact). Logged at DEBUG.
- Hits without `ref["fact_key"]` (structural hits: Place entities by id/seed,
  Character entities, etc.) → always included (public topology).
- Returns `{"query": q, "hits": [{"system", "text", "score"}, ...]}`.

Note: `memory.recall.rank` (which requires embedding vectors) is NOT called in P3a.
`kernel.recall.recall` (offline substring match) is used directly, consistent with
the offline-determinism constraint (命脉). The ranking output is deterministic.

**map_query navigate path**: If `path_to` arg is supplied, calls
`navigate(graph, src, path_to, day)` where `src` is the pov agent's current
`located_in` location. Merges `{"path": [...], "total_cost": N}` into the result.
Returns `{"path": [], "total_cost": None}` when unreachable (Dijkstra returns empty).

---

## Fog invariant verification

| Check | Result |
|---|---|
| `grep -c "value_at" llm/tools.py` | 1 (docstring comment only — not a call) |
| `grep -c "assert_fact\|\.add_entity\|\.add_relation" llm/tools.py` | 1 (docstring only) |
| `grep -c "urlopen\|http://" tests/llm/test_tools.py` | 0 (no network) |
| Full suite 1163 → 1180 | +17 tests, 0 regressions |

---

## Reuse of existing helpers

- `systems.knowledge.knows(graph, pov, fact_key, day)` — the sole fog oracle.
  Called per-predicate in `_place_known_facts()` (map_query) and per-hit in
  `_recall_query_fn()` (recall_query). No new knowledge logic introduced.
- `systems.place.navigate(graph, src, dst, day)` — unmodified Dijkstra, called
  directly when `path_to` is supplied to map_query.
- `kernel.recall.recall(registry, q, world)` — fan-out recall, unchanged.
- `context.viewpoint.build_viewpoint` — NOT called in P3a tools. The tools re-use
  the underlying `knows()` function directly (same logic, lower-level API), which
  avoids coupling to the viewpoint bundle's candidate_fact_keys interface. The
  viewpoint builder remains for the PUSH context path (assemble_context).

---

## Tests (17 new, all offline)

| Test | Covers |
|---|---|
| `test_tool_dataclass_schema_shape` | Tool.schema() OpenAI shape |
| `test_registry_schemas_and_execute` | ToolRegistry round-trip |
| `test_registry_execute_unknown_tool_returns_error_json` | Unknown tool → error JSON |
| `test_registry_execute_catches_tool_exception` | Exception → error JSON, never raises |
| `test_build_tool_registry_returns_named_tools` | map_query + recall_query in schema |
| `test_build_tool_registry_dm_false_excludes_dm_tools` | No dm_world_query in POV set |
| `test_map_query_returns_topology_public` | Exits visible without knows fact |
| `test_map_query_hides_unknown_place_fact` | Unknown state fact omitted from output |
| `test_map_query_shows_known_place_fact` | Believed value surfaced; truth hidden |
| `test_map_query_pov_not_in_scene_errors` | Out-of-scene pov → error JSON |
| `test_map_query_pov_present_npc_allowed` | Present NPC pov is valid |
| `test_map_query_navigate_path` | path_to → {"path": [...], "total_cost": 1} |
| `test_map_query_navigate_path_not_found` | Unreachable → empty path |
| `test_recall_query_returns_hits` | Hits returned for matching query |
| `test_recall_query_fog_drops_unknown_fact_hits` | Structural hits always included |
| `test_recall_query_empty_query_returns_hits_list` | Empty-match → hits:[] |
| `test_recall_query_gated_fact_hit_dropped` | Fog drop via ref["fact_key"] |

---

## P3b forward-compat

- `dm=False` param accepted by `build_tool_registry` (no behavior change).
- No `dm_world_query` tool built; its P3b insertion point is clear (add to the
  `tools` list inside `build_tool_registry` when `dm=True`).
- `characters_query` and `factions_query` (P3b Task 8) follow the same pattern:
  close over `(world, scene)`, call `knows()` per NPC facet, use `build_viewpoint`
  as a reference for which fact_keys to gate.

---

## C1/I1 fog-leak fix

Status: DONE  
Commit: cf3645b (base: fe96134)  
Covering tests: `test_recall_query_person_hit_dropped_when_never_met` (was FAIL, now PASS), `test_recall_query_person_hit_allowed_when_protagonist_knows` (PASS), `test_recall_query_person_hit_allowed_when_co_present` (PASS)  
Full suite: 1185 passed, 1 deselected (was 1182 + 3 new; 0 regressions)

Gating rule: in `_recall_query_fn`, Person hits (ref={"id": eid}, no fact_key) are dropped unless `knows(graph, pov, f"{eid}.sketch", day)` or `knows(graph, pov, f"{eid}.goal", day)` returns non-None, OR the NPC id is in `scene["present"]` (co-presence exception); Place/ontology hits (non-Person) remain public.
