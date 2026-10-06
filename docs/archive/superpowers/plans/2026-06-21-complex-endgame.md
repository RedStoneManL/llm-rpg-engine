# 复杂线终局 (L4) Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Spec: `docs/superpowers/specs/2026-06-21-complex-endgame-design.md`. READ IT.

**Goal:** 没人管的 complex 暗线自动收口——世界救场 checkpoint(渐进式)悄然了结,或寿命到(pending_finale)终局炸成 region-封顶的 world_change(交 cascade 演化)→ 了结,释放 region complex 上限。

**Architecture:** 新 `loop/endgame.py`(纯逻辑:救场阈值曲线 + 确定性掷骰 + catastrophe 事件构造);LoreSystem 加 `quest_world_resolved`/`quest_catastrophe` 两事件;`run_lore`(loop/lore.py)在复杂线推进/pending_finale 处插钩。

**Tech Stack:** Python3, pytest, 事件溯源, `Oracle`/`scene_seed` 确定性, 复用 `loop.density.region_scope` + cascade 的 `world_change`。

## Global Constraints
- `python3`;`PYTHONPATH=/root/rpg-engine-app`;suite 当前 1083 passed 全绿,保持。
- 停 `app` 分支;禁 git init/reset/rebase/branch-switch;禁删 `_legacy/`、`docs/`。
- commit message 结尾**必须** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 事件溯源:每个状态变更是事件 + 可重放(防双重 apply)+ 状态守卫。掷骰走 `Oracle(scene_seed(campaign_seed, key, salt))`,可重放。
- autonomy within harness:**所有数值引擎定**(RESCUE_GRACE_STAGES=1、RESCUE_BASE=10、RESCUE_RANGE=40、FINALE_RESCUE_CHANCE=60,都可调);summary 用模板,**不新增 structured LLM 返回**(rich 演化复用 cascade)。
- 只 complex 线有终局/灾难;simple/medium 寿命到直接了结(lifespan 已实现)。

---

### Task 1: endgame.py 核心 + LoreSystem 两事件

**Files:** Create `loop/endgame.py`; Modify `systems/lore.py`; Test `tests/loop/test_endgame.py`, `tests/systems/test_lore_endgame.py`

**Interfaces — Produces:**
```python
# loop/endgame.py
RESCUE_GRACE_STAGES = 1; RESCUE_BASE = 10; RESCUE_RANGE = 40; FINALE_RESCUE_CHANCE = 60
def world_rescue_chance(stage_idx: int, n_stages: int) -> int
    # progressive low→high: RESCUE_BASE + round(stage_idx/max(1,n_stages-1) * RESCUE_RANGE), clamp [0,100]
def roll_world_rescue(oracle, stage_idx: int, n_stages: int) -> bool   # oracle.d100() <= chance
def build_catastrophe_events(line: dict, world: dict, *, day, scene, turn) -> list[dict]
    # returns [quest_catastrophe{id,summary,anchor}, world_change{place:region_scope(anchor), level, summary}]
    # (only emit world_change if a "world_change" owner is registered — guard via caller; see T2)
def rescue_summary(line) -> str   # template: f"【世界自行了结】{about}:外力介入,事态平息"
def catastrophe_summary(line, region) -> str  # template: f"【终局】{about}失控,{secret},波及{region}"
```
- `region_scope` import from `loop.density`. Person/Place agnostic (lines anchor a town).

**Steps:**
- [ ] Write failing `tests/loop/test_endgame.py`: `world_rescue_chance` is monotonic non-decreasing in stage_idx, == RESCUE_BASE at stage 0-ish, higher near n-1, clamped [0,100]; `roll_world_rescue` deterministic with a fixed-seed Oracle (assert specific bool for a pinned seed); `rescue_summary`/`catastrophe_summary` contain about/secret/region; `build_catastrophe_events` returns a quest_catastrophe + a world_change anchored at region_scope(anchor).
- [ ] Run → fail. Implement `loop/endgame.py`.
- [ ] `systems/lore.py`: add `quest_world_resolved`/`quest_catastrophe` to `event_types()`; apply branches — both set `state="了结"`, `status="resolved"`, `resolved={"by":<by>,"summary":<summary>}`, and clear `pending_finale`; replay-safe (if already 了结 → no-op). Test `tests/systems/test_lore_endgame.py`: append each event → project → line 了结 + resolved.by correct + pending_finale gone; re-apply idempotent.
- [ ] Run tests green + full suite green. Commit.

---

### Task 2: run_lore 钩入(checkpoint 救场 + finale + catastrophe world_change)

**Files:** Modify `loop/lore.py` (run_lore); maybe `loop/endgame.py`. Test `tests/loop/test_lore_endgame_wiring.py`

**Interfaces — Consumes:** T1 functions; `run_lore`'s existing complex-line advance + world-push + lifespan-expiry logic; `registry.owner_of_event("world_change")` guard.

**Logic to add in `run_lore` (read its current structure first):**
1. **Checkpoint rescue** — when a `state=="暗"` complex line is advanced to a new `stage_idx` that is `>= RESCUE_GRACE_STAGES` AND is NOT the last stage (last stage keeps the existing world-push surface): `oracle = Oracle(scene_seed(campaign_seed, f"rescue:{id}", stage_idx))`; if `roll_world_rescue(oracle, stage_idx, n_stages)` → emit `quest_world_resolved{id, by:"world_rescue", summary:rescue_summary(line)}` and do NOT also world-push/advance further this trip.
2. **Finale** — for a `state=="暗"` complex line with `pending_finale` truthy: `oracle = Oracle(scene_seed(campaign_seed, f"finale:{id}", day))`; if `oracle.d100() <= FINALE_RESCUE_CHANCE` → emit `quest_world_resolved{by:"world_rescue:finale", summary}`; else → emit `build_catastrophe_events(line, world, day, scene, turn)` (the quest_catastrophe + the world_change, the latter only if `registry.owner_of_event("world_change") is not None`).
3. Guard everything on `complexity=="complex"` and `state=="暗"`; never double-fire (the 了结 state guard + pending_finale clear handle replay).

**Steps:**
- [ ] Write failing `tests/loop/test_lore_endgame_wiring.py`: (a) complex 暗 line advanced to stage>=1 with a seed that rescues → quest_world_resolved emitted, line 了结; (b) seed that fails → no rescue, line keeps brewing/surfaces at last stage (existing); (c) pending_finale complex + finale-success seed → world_resolved; (d) pending_finale + finale-fail seed → quest_catastrophe + world_change at region_scope(anchor); (e) stage 0 → no rescue roll; (f) non-complex line → never touched by endgame.
- [ ] Run → fail. Implement the run_lore hooks (factor logic into endgame.py helpers where it keeps run_lore readable).
- [ ] Tests green + full suite green. Commit.

---

### Task 3: e2e + 确定性 + 释放 region 上限 + region 封顶 + demo

**Files:** Test `tests/loop/test_endgame_e2e.py`; Create `docs/superpowers/specs/endgame-build-2026-06-21/demo.py`

**Steps:**
- [ ] Write `tests/loop/test_endgame_e2e.py`: build a world with a region(L1)⊃town(L2)⊃venues + a complex 暗 line anchored at the town (use `create_lore_line` + lore_advanced to set stage; or density). Drive run_lore across turns:
  - Path A (rescue): pin campaign_seed so a mid checkpoint rescues → line 了结(by:world_rescue); assert `density.count_tier(world, region, "complex")` dropped by 1 (cap released).
  - Path B (catastrophe): pin seed so rescues all fail + set pending_finale + finale fails → `quest_catastrophe` + a `world_change` event anchored at the region appears in the store; line 了结(by:catastrophe); cap released. (If cascade is registered, assert the world_change is region-anchored; do NOT require cascade to fully run unless cheap.)
  - Determinism: same campaign_seed → same path/events on a fresh rebuild.
  - region-bounded: the catastrophe world_change's `place` == region_scope(anchor), not the whole world.
- [ ] Run green + full suite green. Commit.
- [ ] Create `demo.py` (mirror density demo structure; real glm-5.1 via `make_provider`): a complex 暗 line nobody engages → run several turns + advance the clock past its lifespan → dump whether it got rescued or catastrophe'd, and if catastrophe, the resulting `world_change` + any cascade region evolution. Header with run command; NOT run in tests. Commit.

---

## Self-Review notes
- 三条出路全收口到了结 + 释放 region 上限(Task 1 apply + Task 3 e2e count_tier 断言)。
- 渐进式阈值前低后高(Task 1 monotonic 测)。
- 爆炸封顶 region(Task 2 world_change anchored at region_scope;Task 3 region-bounded 断言)。
- 确定性/rewind(Task 1 pinned roll + Task 3 重跑同序列)。
- 不新增 structured LLM 返回(模板 summary)→ harness 一致性不破。
