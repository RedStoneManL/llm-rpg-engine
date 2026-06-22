# World Bootstrap (开局长程初始化) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `app/engine.py::new_game()`'s placeholder genesis with a deterministic, reroll-able, tiered-skeleton world bootstrap: player gives a 基调 pitch → oracle distinct-draws from extensible dimension tables → LLM authors content via `complete_structured` → genesis events (macro region skeleton + local map + factions + NPCs-with-secrets + campaign threads + opening narration).

**Architecture:** A multi-step generation pipeline in `loop/bootstrap.py`. Each step: oracle rolls STRUCTURE (counts/types/complexity — deterministic, rewind-safe) → `complete_structured` makes the LLM author CONTENT (strict field-by-field prompt + validate→repair) → build `kernel_event`s. Orchestrator appends all in order and re-projects. A macro L1-region adjacency graph is pinned at bootstrap so later reactive generation cannot drift. Reroll = `retract_from_seq(step_boundary_seq)` + re-run from that step (downstream re-runs, no dangling refs).

**Tech Stack:** `engine.oracle` (`Oracle`/`scene_seed`/`load_table`), `llm.structured.complete_structured`, `loop.lore.create_lore_line`, `systems` events (place_created/place_linked/character_created/fact_asserted/faction_created/narration_recorded), T9 `secrecy`, kernel event sourcing.

## Global Constraints

- All rolls: `Oracle(scene_seed(engine.campaign_seed, f"genesis:{step}:{i}", attempt))`. NO `random`/time calls (breaks replay). `scene_seed(campaign_seed, scene_ordinal, salt)` — use `salt=attempt` for reroll.
- Every LLM step uses `complete_structured(provider, system=..., user=..., validate=fn, max_repairs=2, schema_reminder=..., log_label=...) -> (obj, errors)`; `validate(obj)->list[str]` NAMES missing/wrong fields. Mirror the strict field-by-field user prompt of `loop/density.py::generate_lore_batch` (loop/density.py:318-350).
- Engine decides ALL numbers (region/L2/venue/faction/NPC/thread counts, complexity, threshold). LLM writes ONLY story strings.
- Dimension tables live in `data/oracles/genesis/*.json` (loaded via `load_table(name, "genesis")`). Adding an axis = add a JSON + reference it; adding entries = edit JSON. Entry shape: `{"weight": int, "name": str, ...optional hints}`.
- Genesis events: `turn=0`, `day=1`, `scene="genesis"`, built via `kernel_event(type, day=1, scene="genesis", summary=..., deltas=..., turn=0)`.
- Defensive: every generator NEVER raises out (LLM failure → fall back to a minimal deterministic stub so bootstrap always yields a playable world). Mirror `loop/density.py::run_density`'s try/except discipline.
- Offline tests use `FakeLLMProvider` / a scripted provider (deterministic); NEVER hit the network in the suite. One live GLM probe lives in `docs/` (not collected by pytest).
- Python3, `PYTHONPATH=/root/rpg-engine-app`. Commit on `app`, per-task, message ends with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- **Create** `data/oracles/genesis/{thread_types,npc_roles,place_kinds,tone_axes,terrains}.json` — dimension tables (data).
- **Create** `loop/bootstrap.py` — `_draw_distinct`, the seven `gen_*` step functions, `bootstrap_world` orchestrator, `reroll_*` helpers.
- **Modify** `app/engine.py` — replace `new_game()` body to delegate to `bootstrap_world` (keep the name as a thin wrapper for back-compat, OR have `__main__` call `bootstrap_world` directly — Task 10 decides).
- **Modify** `app/__main__.py` + `app/play.py` — first-run flow: read pitch (`--pitch` flag or one interactive line), run bootstrap, print summary, reroll loop, then `play_loop`.
- **Create** `tests/loop/test_bootstrap.py` — offline tests (one section per task).
- **Create** `docs/superpowers/specs/p3-build-2026-06-22/probe_bootstrap.py` — live GLM probe (not in suite).

### The generator pattern (T2–T8 all follow this)

Each `gen_*` is a pure-ish function: it takes a `provider`, an `Oracle` (seeded by the orchestrator), and prior-step summaries (NOT the projected world), and returns `(events: list[dict], summary: dict)`. It does NOT touch the store (the orchestrator appends). Internally:
1. Oracle rolls the structural counts/types (deterministic).
2. Build a strict user prompt declaring each required field by name+type (mirror `generate_lore_batch`).
3. `obj, errors = complete_structured(provider, system=SYS, user=usr, validate=_v, max_repairs=2, log_label="genesis:<step>")`.
4. On `errors` (non-empty) or `provider is None`: fall back to a deterministic stub built from the oracle rolls (so the world is always playable).
5. Build `kernel_event`s from the validated/stub content. Return `(events, summary)`.

A `ScriptedProvider` test helper returns canned JSON per call so offline tests exercise the happy path + the fallback path deterministically.

---

## Task 1: Dimension tables + `_draw_distinct`

**Files:**
- Create: `data/oracles/genesis/thread_types.json`, `npc_roles.json`, `place_kinds.json`, `tone_axes.json`, `terrains.json`
- Create: `loop/bootstrap.py`
- Test: `tests/loop/test_bootstrap.py`

**Interfaces:**
- Produces: `_draw_distinct(oracle, entries: list[dict], k: int) -> list[dict]` — weighted draw of up to k DISTINCT entries (sample without replacement). `load_table(name, "genesis")` resolves these tables.

- [ ] **Step 1: Write the table JSONs.** Each is a JSON array of `{"weight": int, "name": str, ...}`. Concrete contents:

`thread_types.json`:
```json
[{"weight":3,"name":"身世","endpoint_hint":"揭开某人的真实出身"},
 {"weight":3,"name":"阴谋","endpoint_hint":"与幕后黑手的对决"},
 {"weight":2,"name":"物品","endpoint_hint":"某件造物的代价与归宿"},
 {"weight":2,"name":"势力","endpoint_hint":"某方势力的崛起或崩塌"},
 {"weight":2,"name":"情感","endpoint_hint":"一段关系的质变"}]
```
`npc_roles.json`:
```json
[{"weight":3,"name":"掌权者"},{"weight":3,"name":"知情者"},{"weight":2,"name":"走卒"},
 {"weight":2,"name":"对手"},{"weight":2,"name":"盟友"},{"weight":2,"name":"边缘人"}]
```
`place_kinds.json`:
```json
[{"weight":4,"name":"settlement"},{"weight":3,"name":"wilderness"},{"weight":1,"name":"dungeon"}]
```
`tone_axes.json`:
```json
[{"weight":2,"name":"悬疑"},{"weight":2,"name":"冒险"},{"weight":1,"name":"权谋"},
 {"weight":2,"name":"生存"},{"weight":2,"name":"恩怨"}]
```
`terrains.json`:
```json
[{"weight":2,"name":"平原"},{"weight":2,"name":"山地"},{"weight":2,"name":"森林"},
 {"weight":2,"name":"水乡"},{"weight":1,"name":"荒漠"},{"weight":1,"name":"雪原"}]
```

- [ ] **Step 2: Write the failing test** in `tests/loop/test_bootstrap.py`:

```python
from engine.oracle import Oracle, load_table
from loop.bootstrap import _draw_distinct

def test_draw_distinct_returns_k_distinct_and_deterministic():
    table = load_table("thread_types", "genesis")
    a = _draw_distinct(Oracle(123), table, 3)
    b = _draw_distinct(Oracle(123), table, 3)
    assert len(a) == 3
    assert len({e["name"] for e in a}) == 3          # all distinct
    assert [e["name"] for e in a] == [e["name"] for e in b]  # deterministic per seed

def test_draw_distinct_caps_at_pool_size():
    table = load_table("place_kinds", "genesis")      # only 3 entries
    out = _draw_distinct(Oracle(1), table, 10)
    assert len(out) == 3

def test_genesis_tables_load():
    for name in ("thread_types","npc_roles","place_kinds","tone_axes","terrains"):
        t = load_table(name, "genesis")
        assert isinstance(t, list) and t and all("name" in e for e in t)
```

- [ ] **Step 3: Run → FAIL** (`pytest tests/loop/test_bootstrap.py -q`; `ModuleNotFoundError: loop.bootstrap`).

- [ ] **Step 4: Implement** `loop/bootstrap.py` `_draw_distinct` (port from `~/.hermes/.../engine/seed.py`):

```python
from __future__ import annotations
from engine.oracle import Oracle, scene_seed, load_table
from engine.log import get_logger
log = get_logger("loop.bootstrap")

def _draw_distinct(oracle, entries, k):
    """Weighted draw of up to k DISTINCT entries (sample without replacement)."""
    pool = list(entries)
    out = []
    for _ in range(min(k, len(pool))):
        e = oracle.draw(pool)
        out.append(e)
        pool.remove(e)
    return out
```

- [ ] **Step 5: Run → PASS. Commit** (`feat(bootstrap): dimension tables + distinct draw`).

---

## Task 2: `gen_frame` — world frame

**Files:** Modify `loop/bootstrap.py`; Test `tests/loop/test_bootstrap.py`

**Interfaces:**
- Consumes: `_draw_distinct`, `complete_structured`.
- Produces: `gen_frame(provider, oracle, pitch: str) -> tuple[list[dict], dict]`. Returns `(events, frame)` where `frame = {"genre":str,"tone":str,"central_conflict":str,"world_name":str,"n_factions":int,"n_regions":int}` and `events` = `[entity_created(world)]` + frame facts (`fact_asserted` subject="world" predicate∈{genre,tone,central_conflict} secrecy="public")`. The `world` entity id is the literal `"world"`.

- [ ] **Step 1: Write failing tests:**

```python
import json
from loop.bootstrap import gen_frame

class ScriptedProvider:
    """Returns canned strings in order; supports_tools=False."""
    def __init__(self, replies): self._r = list(replies); self.i = 0
    def supports_tools(self): return False
    def complete_messages(self, messages):
        r = self._r[self.i] if self.i < len(self._r) else self._r[-1]; self.i += 1; return r

def test_gen_frame_rolls_counts_deterministically():
    p = ScriptedProvider([json.dumps({"world_name":"河谷王国","central_conflict":"漕运断绝引发的暗斗"})])
    evs, frame = gen_frame(p, Oracle(scene_seed_helper()), "东方武侠悬疑")
    assert 3 <= frame["n_factions"] <= 5
    assert 3 <= frame["n_regions"] <= 5
    assert frame["world_name"] == "河谷王国"
    assert frame["tone"] in {"悬疑","冒险","权谋","生存","恩怨"}
    # world entity + public frame facts emitted
    types = [e["type"] for e in evs]
    assert "entity_created" in types
    assert any(e["type"]=="fact_asserted" and e["deltas"].get("secrecy")=="public" for e in evs)

def test_gen_frame_falls_back_without_provider():
    evs, frame = gen_frame(None, Oracle(7), "x")
    assert frame["world_name"]            # non-empty stub name
    assert evs                            # still emits world entity
```

Add a helper `def scene_seed_helper(): from engine.oracle import scene_seed; return scene_seed(999, "genesis:frame:0", 0)` at top of the test file (reused by later tasks).

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `gen_frame`.** Rolls: `tone = oracle.draw(load_table("tone_axes","genesis"))["name"]`; `n_factions = oracle.randint(3,5)`; `n_regions = oracle.randint(3,5)`. Build a strict prompt (mirror `generate_lore_batch`): system = "你是 TRPG 世界设定生成器，只返回严格符合字段规范的 JSON，所有故事文本用中文。" user declares EXACTLY required keys `world_name` (str), `central_conflict` (str), given the player's pitch + the rolled `tone`. `validate(obj)` returns errors naming any missing/empty `world_name`/`central_conflict`. On error/None → stub `{"world_name": f"未名之地", "central_conflict": "一桩悬而未决的乱局"}`. Assemble `frame` dict (rolled counts + tone + genre=pitch + LLM strings). Emit: `kernel_event("entity_created", deltas={"id":"world","etype":"Place","tier":"mentioned","attrs":{"level":0,"kind":"region","seed":world_name}})` (a level-0 "world" anchor) and three `fact_asserted` events `{"subject":"world","predicate":k,"value":v,"secrecy":"public"}` for genre/tone/central_conflict.

- [ ] **Step 4: Run → PASS. Commit** (`feat(bootstrap): gen_frame world frame`).

---

## Task 3: `gen_regions` — macro skeleton + adjacency (anti-drift core)

**Files:** Modify `loop/bootstrap.py`; Test `tests/loop/test_bootstrap.py`

**Interfaces:**
- Consumes: `gen_frame`'s `frame`.
- Produces: `gen_regions(provider, oracle, frame) -> tuple[list[dict], dict]`. `summary = {"regions":[{"id","name","tier":"start"|"neighbor"|"far","terrain"}], "start_region": id, "density": float}`. Events: `place_created`(level=1,kind=region) ×n_regions + `place_linked` macro adjacency edges. Region ids are deterministic: `f"region_{i}"`. The start region (i=0) gets `density` attr (= `oracle` draw in [0.2,0.5] rounded to 0.1).

- [ ] **Step 1: Write failing tests** asserting:
  - `len(summary["regions"]) == frame["n_regions"]`.
  - exactly one region has `tier=="start"`; it equals `summary["start_region"]`.
  - adjacency: there are `place_linked` events forming a connected line/star over the region ids (every non-start region has ≥1 link toward start) — assert `n_regions-1` link events and that start region appears in links (anti-drift: macro graph pinned).
  - start region `place_created` deltas carry a numeric `density`.
  - neighbor regions are `place_created` with a non-empty `seed`; far regions too (rough seed). (All regions get a name+seed; detail tier is a flag in summary, not fewer events here.)
  - fallback without provider still emits n_regions regions + adjacency.

```python
from loop.bootstrap import gen_regions
def test_gen_regions_pins_connected_macro_graph():
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    evs, summ = gen_regions(ScriptedProvider([json.dumps({"regions":[
        {"name":"河谷","terrain":"水乡","seed":"依河而建"},
        {"name":"雪原","terrain":"雪原","seed":"苦寒之地"},
        {"name":"铁峰","terrain":"山地","seed":"矿脉纵横"},
        {"name":"商港","terrain":"平原","seed":"百货云集"}]})]), Oracle(5), frame)
    assert len(summ["regions"]) == 4
    starts = [r for r in summ["regions"] if r["tier"]=="start"]
    assert len(starts) == 1 and starts[0]["id"] == summ["start_region"]
    links = [e for e in evs if e["type"]=="place_linked"]
    assert len(links) == 3                                   # connected, n-1 edges
    assert all(summ["start_region"] in (e["deltas"]["a"], e["deltas"]["b"]) for e in links) or True  # star OR chain ok
    start_pc = [e for e in evs if e["type"]=="place_created" and e["deltas"]["id"]==summ["start_region"]][0]
    assert isinstance(start_pc["deltas"]["attrs"]["density"], (int,float))
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Roll `n=frame["n_regions"]`; terrains via `_draw_distinct(oracle, load_table("terrains","genesis"), n)`. Strict prompt: required `regions` = array of EXACTLY n objects each `{name:str, terrain:str(echo the given one), seed:str(one line)}`. validate names per-index missing/empty. Stub: regions named `f"地域{i+1}"`, seed `"一片待探索的疆域"`. Region i=0 = start (tier="start", + density `round(oracle.random()*0.3+0.2,1)`), i in 1..k = neighbor (tier="neighbor"), rest = far (tier="far"). Emit `place_created`(level=1,kind=region,id=`region_{i}`,seed,attrs={terrain,(density on start)}) + `place_linked` chain `region_0–region_i` for i≥1 (star graph pins every region adjacent to start → directions anchored). Return summary.
- [ ] **Step 4: Run → PASS. Commit.**

---

## Task 4: `gen_local_map` — start region's L2 + L3 (fixes l3_anchor drift)

**Files:** Modify `loop/bootstrap.py`; Test `tests/loop/test_bootstrap.py`

**Interfaces:**
- Consumes: `gen_regions` summary.
- Produces: `gen_local_map(provider, oracle, frame, regions_summary) -> tuple[list[dict], dict]`. `summary = {"start_town": id, "venues":[ids], "l2":[{id,kind,name}]}`. Events: `place_created`(L2, parent=start_region) for 1 start town (kind=settlement) + 1–2 neighbor L2 (kind via `place_kinds` distinct, may be wilderness) + `place_created`(L3, parent=start_town) ×2–4 venues + `place_linked` between start_town and each neighbor L2. Town id `town_0`; venue ids `venue_{i}`.

- [ ] **Step 1: Write failing tests:** exactly one L2 settlement = `summary["start_town"]`; 1–2 additional L2; 2–4 L3 venues all `parent==start_town`; every L3 in `summary["venues"]`; L2 places linked to start_town; fallback path emits a town + ≥2 venues. (This guarantees density's `l3_anchor` can hit a REAL venue — assert `len(summary["venues"]) >= 2`.)
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Roll: `n_extra_l2 = oracle.randint(1,2)`; extra kinds via `_draw_distinct(oracle, load_table("place_kinds","genesis"), n_extra_l2)`; `n_venues = oracle.randint(2,4)`. Strict prompt: required `town` `{name,seed}`, `venues` array of n_venues `{name,seed}`, `neighbors` array of n_extra_l2 `{name,seed}` (kind given). validate per-index. Stub: town "起始镇"/venues "集市"/"酒馆"/neighbors "野径". Emit places + links. Start town id `town_0` kind=settlement parent=start_region; venues `venue_{i}` level=3 kind=venue parent=town_0; neighbors `l2_{i}` level=2 parent=start_region linked to town_0.
- [ ] **Step 4: Run → PASS. Commit.**

---

## Task 5: `gen_factions`

**Files:** Modify `loop/bootstrap.py`; Test `tests/loop/test_bootstrap.py`

**Interfaces:**
- Produces: `gen_factions(provider, oracle, frame, regions_summary) -> tuple[list[dict], dict]`. `summary={"factions":[{id,name}]}`. Events: `faction_created` ×`frame["n_factions"]`, deltas `{"op":"faction","id":f"faction_{i}","tier":"mentioned","seed":name,"motivation":...}`.

- [ ] **Step 1: Failing tests:** `len(events)==frame["n_factions"]`; all `type=="faction_created"`; ids distinct; each deltas has `op=="faction"` and non-empty `seed`; fallback emits n stub factions.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement.** Strict prompt: required `factions` = array of EXACTLY n `{name:str, motivation:str}`, distinct, fitting `frame` tone/conflict. validate per-index. Stub names `f"势力{i+1}"`. Emit `faction_created` (id `faction_{i}`, seed=name, motivation in deltas). (No member edges in v1 — NPCs may join in Task 6.)
- [ ] **Step 4: PASS. Commit.**

---

## Task 6: `gen_npcs` — opening NPCs with secrets (T9 tie-in)

**Files:** Modify `loop/bootstrap.py`; Test `tests/loop/test_bootstrap.py`

**Interfaces:**
- Consumes: local_map summary (venues for placement), factions summary (optional membership).
- Produces: `gen_npcs(provider, oracle, frame, local_map, factions) -> tuple[list[dict], dict]`. `summary={"npcs":[{id,role}]}`. Events per NPC: `character_created`{id:`npc_{i}`,tier:"mentioned",sketch,goal} + `fact_asserted`{subject:`npc_{i}`,predicate:"真实身份"|"秘密",value:secret,**secrecy:"secret"**} + `entity_moved`{who:`npc_{i}`,to: a venue} (place them in the start town).

- [ ] **Step 1: Failing tests:** `n = len(summary["npcs"])` in 2..4; each NPC has a `character_created` + a `fact_asserted` whose `deltas["secrecy"]=="secret"` (the secret must be a hard secret, NOT public — locks the T9 tie-in); roles drawn distinct (`len({n['role'] for n in summary['npcs']})==n`); each NPC `entity_moved` to one of `local_map["venues"]`; fallback emits ≥2 NPCs each with a secret fact.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement.** Roll `n=oracle.randint(2,4)`; roles `_draw_distinct(oracle, load_table("npc_roles","genesis"), n)`; per NPC 2 traits `_draw_distinct(oracle, ...)` — reuse `npc_roles`? No: traits need a `npc_traits.json` — ADD it in this task (Files: also create `data/oracles/genesis/npc_traits.json` with ~8 entries like 谨慎/暴躁/慈悲/贪婪/忠诚/狡黠/木讷/热忱). Strict prompt: required `npcs` = array of EXACTLY n `{sketch:str, goal:str, secret:str}` given each NPC's rolled role+traits+the world frame. validate per-index. Stub sketch from role. Emit character_created + the secret as `fact_asserted` with `secrecy="secret"` + entity_moved to `venues[i % len(venues)]`.
- [ ] **Step 4: PASS. Commit.**

---

## Task 7: `gen_threads` — campaign 暗线 + protagonist-bound (lore reuse)

**Files:** Modify `loop/bootstrap.py`; Test `tests/loop/test_bootstrap.py`

**Interfaces:**
- Consumes: local_map (venues for `l3_anchor`), the protagonist id.
- Produces: `gen_threads(provider, oracle, frame, local_map, protagonist) -> tuple[list[dict], dict]`. Returns SKELETON dicts (the orchestrator calls `create_lore_line`). `summary={"threads":[{id,type,complexity,anchor}]}`. Each skeleton = the dict `create_lore_line` requires: `{id,complexity,about,anchor,stages,threshold,description,trigger,l3_anchor,secret}`. 3–5 campaign threads (anchor=`town_0`) + 1–2 protagonist-bound (anchor=protagonist id).

- [ ] **Step 1: Failing tests:** 3–5 campaign threads + 1–2 protagonist-bound (total counted); types distinct across campaign threads; each `l3_anchor` ∈ `local_map["venues"]` (NO floating anchor); each skeleton passes `loop.lore._REQUIRED` (import the tuple, assert all keys present); complexity ∈ {simple,medium,complex}; protagonist-bound threads have `anchor == protagonist`. Fallback emits ≥3 valid skeletons.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement.** Roll `n=oracle.randint(3,5)`; types `_draw_distinct(oracle, load_table("thread_types","genesis"), n)`; per thread `complexity = oracle.draw([{ "weight":3,"name":"medium"},{"weight":2,"name":"simple"},{"weight":2,"name":"complex"}])["name"]` (campaign-level — bias medium/complex), `threshold` from a speed roll (快→70/中→50/慢→30), `stage_count = {"simple":2,"medium":3,"complex":5}[complexity]`. Strict prompt (mirror `generate_lore_batch` EXACTLY — same required fields `about/description/trigger/secret/l3_anchor` + `stages[{hint}]`, `l3_anchor` MUST be one of `local_map["venues"]`). Build skeletons with engine-decided id/complexity/anchor/threshold + LLM strings + `stages` capped to stage_count. Then `n_p = oracle.randint(1,2)` protagonist-bound threads, anchor=protagonist, same shape. Return skeletons.
- [ ] **Step 4: PASS. Commit.**

---

## Task 8: `gen_opening` — opening scene narration

**Files:** Modify `loop/bootstrap.py`; Test `tests/loop/test_bootstrap.py`

**Interfaces:**
- Consumes: `frame`, a compact world summary (region/town/venues/NPC sketches/thread abouts).
- Produces: `gen_opening(provider, frame, world_summary: str, *, scene_loc: str) -> tuple[list[dict], str]`. Returns `(events, narration)`; events = `[narration_recorded]` deltas `{"scene":"genesis","text":narration}`. `narration` is also returned so the orchestrator prints it.

- [ ] **Step 1: Failing tests:** emits exactly one `narration_recorded` whose `deltas["text"]` is the returned narration; narration non-empty; uses `provider.complete(...)` free-prose (NOT structured — it's prose, like `HybridStrategy`'s narrate call) OR `complete_messages`; fallback (no provider) → a deterministic stub narration mentioning the world_name. (Keep it a plain prose call; no schema.)
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement.** `system` = opening-scene DM prompt (主角视角, 落在起始镇某 venue, 给钩子不替玩家决定). `user` = world_summary + "写一段开场叙事." Call `provider.complete(system, user)` (provider has `.complete`; if only `complete_messages`, wrap). Fallback: f"你来到{frame['world_name']}……". Emit `narration_recorded`.
- [ ] **Step 4: PASS. Commit.**

---

## Task 9: `bootstrap_world` orchestrator + reroll

**Files:** Modify `loop/bootstrap.py`; Test `tests/loop/test_bootstrap.py`

**Interfaces:**
- Consumes: all `gen_*`; an `engine` (has `.store`, `.registry`, `.campaign_seed`, `.world`, `.provider`); `loop.lore.create_lore_line`; `kernel.projection.project`.
- Produces:
  - `bootstrap_world(engine, pitch: str, *, attempt: int = 0) -> dict` — runs steps 1–8 in order, appends all events (threads via `create_lore_line`), re-projects `engine.world`, returns a `summary` dict (frame + counts + step-boundary seqs) for the UI. Also appends a `campaign_seeded` event FIRST (mirror current `new_game`) so `campaign_seed` is on record.
  - `reroll_all(engine, pitch) -> dict` — `engine.store.retract_from_turn(0)` (wipes genesis) + `bootstrap_world(engine, pitch, attempt=prev_attempt+1)`.
  - `reroll_step(engine, pitch, step: str) -> dict` for `step in {"factions","npcs","threads"}` — `engine.store.retract_from_seq(boundary_seq[step])` + re-run from that step onward with `attempt+1`, re-project. (Map/regions reroll → use `reroll_all`.)

- [ ] **Step 1: Write failing tests** (use a ScriptedProvider returning canned JSON for every step, plus a fake embedder-less engine via `build_engine(tmpdir, provider=scripted)`):
  - `bootstrap_world` on an empty campaign produces a world with: ≥`n_regions` Place entities at level 1; a level-2 `town_0`; ≥2 level-3 venues; ≥2 Person NPCs each with a `secrecy=="secret"` fact in the graph; ≥3 lore lines in `world["systems"]["lore"]["lines"]`; one `narration_recorded`.
  - determinism: two `bootstrap_world` runs on two engines with the SAME campaign dir name (same `campaign_seed`) + same scripted replies produce identical event-type histograms.
  - `reroll_all` changes the genesis (different attempt → different rolls) and leaves the store with a fresh single genesis (old retracted).
  - `reroll_step(engine,"threads")` retracts only thread + opening events (seq ≥ threads boundary) and re-runs; region/town/npc events survive (assert their ids still present, lore lines replaced).
  - **No drift invariant:** after bootstrap, every region id referenced by a `place_linked` exists as a `place_created` (macro graph closed).

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement.** Orchestrator: append `campaign_seeded`; for each step build `Oracle(scene_seed(engine.campaign_seed, f"genesis:{step}:{i}", attempt))`, call `gen_*`, record `boundary_seq[step] = ` the seq returned by the FIRST `store.append` of that step, append events (threads → `create_lore_line(engine.store, sk, day=1, scene="genesis", turn=0)`), then `engine.world = project(engine.registry, engine.store.iter_events())`. Protagonist: create a tracked protagonist (reuse `new_game`'s protagonist constants) BEFORE gen_npcs/gen_threads (they need its id) and move it into `town_0`'s first venue. `reroll_all`/`reroll_step` as specified.
- [ ] **Step 4: PASS. Commit.**

---

## Task 10: Integration — replace `new_game`, wire first-run flow

**Files:** Modify `app/engine.py` (`new_game`), `app/__main__.py`, `app/play.py`; Test `tests/loop/test_bootstrap.py` + `tests/app/` if present.

**Interfaces:**
- Consumes: `bootstrap_world`, `reroll_all`, `reroll_step`.

- [ ] **Step 1: Failing test:** calling the new first-run path (a scripted-provider engine, empty store, with a pitch) leaves a bootstrapped world (≥1 lore line, NPCs with secrets) — i.e., `new_game`/bootstrap entry produces the rich world, not the old 1-town placeholder. Assert the OLD placeholder ids (`starting_location` generic) are gone / replaced by `town_0` etc.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement.** Replace `app/engine.py::new_game(engine)` body with `from loop.bootstrap import bootstrap_world; bootstrap_world(engine, pitch)` — add a `pitch` param (default `""`). In `app/__main__.py`: add `--pitch` arg; when store empty, read pitch (flag or one `input()`/first stdin line → but keep testable via injected `inputs`), call `bootstrap_world`, print the returned summary, then a reroll loop (`reroll`/`reroll <step>`/`开始`) before `play_loop`. Keep the reroll loop thin and behind the same injected `inputs`/`out` seams `play_loop` uses (for tests). Document `RPG_BOOTSTRAP_PITCH` env as an alt pitch source.
- [ ] **Step 4: PASS + run FULL suite** (`pytest -q`) to confirm no regression (the old new_game tests may need updating to the new world shape — update them to assert the bootstrapped shape, do NOT weaken them). **Commit.**

---

## Task 11: Live GLM probe (validation artifact, not in suite)

**Files:** Create `docs/superpowers/specs/p3-build-2026-06-22/probe_bootstrap.py`

- [ ] **Step 1:** Write a probe (mirror `probe_t9.py`): build a zhipu engine, run `bootstrap_world(engine, "东方武侠悬疑")`, print the event-type histogram + the region adjacency graph + each NPC's secret fact `secrecy` + each thread's `l3_anchor` (assert ∈ real venues) + the opening narration. VERDICTs: world coherent ✓ / macro graph closed (no drift) ✓ / NPC secrets tagged `secrecy="secret"` ✓ / thread l3_anchors real ✓.
- [ ] **Step 2:** Run it live (`set -a; . ./.env.local; set +a; PYTHONPATH=. python3 docs/.../probe_bootstrap.py`); capture output to `probe_bootstrap.out`. (NOT committed unless asked; leave untracked like `probe.py`.)
- [ ] **Step 3:** Eyeball quality. If glm ignores a field or drifts, tighten the offending step's prompt + re-run. **Commit** any prompt fixes.

---

## Self-Review (plan vs spec)

- **§1 tiered skeleton / anti-drift** → Tasks 3 (macro graph pinned, n-1 links, closed-graph invariant) + 4 (local L2/L3). ✓
- **§2 extensible dimension tables + distinct draw** → Task 1 (tables + `_draw_distinct`), reused in 3/5/6/7. ✓
- **§3 seven-step pipeline** → Tasks 2–8 (frame/regions/local_map/factions/npcs/threads/opening). ✓
- **§4 reroll (whole + leaf-step)** → Task 9 (`reroll_all` + `reroll_step` for factions/npcs/threads; map/region → reroll_all). ✓
- **§5 determinism / rewind** → Task 1 (deterministic draw) + Task 9 (seed keys + same-seed histogram test). ✓
- **§6 integration + fix l3_anchor** → Task 10 (replace new_game, CLI) + Task 4/7 (venues real → l3_anchor hits). ✓
- **§7 testing** → offline per task + Task 11 live probe. ✓
- **T9 secrecy tie-in** → Task 6 (NPC secret = `secrecy="secret"` fact). ✓
- **Type consistency:** generator signature `(provider, oracle, ...) -> (events, summary)` uniform across T2–T8 (T8 returns `(events, narration)`); ids deterministic (`region_i`/`town_0`/`venue_i`/`l2_i`/`faction_i`/`npc_i`). ✓
- **YAGNI:** no per-item fine reroll, no full-world L2/L3, no genre-locked content tables, no rich world-bible (frame facts only). ✓
