# 密度生成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Spec: `docs/superpowers/specs/2026-06-21-density-generation-design.md`. Integration map (exact signatures/shapes): `docs/superpowers/specs/density-build-2026-06-21/integration-map.md`. READ BOTH before any task.

**Goal:** 暗线按地图密度自动生成——玩家首入 L2 城镇时引擎按密度播种一批暗线(LLM 写骨架、引擎掷数值/上限/节奏),此后随游戏内时间密度门控补充。生成走现有 `lore_created` 管道。

**Architecture:** 新模块 `loop/density.py`(纯逻辑:密度解析/上限/复杂度掷骰 + 生成 LLM 调用 + run_density 编排);LoreSystem 加 gen 子状态(seeded/last_refresh_day)+ 两个事件;PlaceSystem 存 density attr;run_turn 加一个 backstage 钩子。

**Tech Stack:** Python3, pytest (`PYTHONPATH=/root/rpg-engine-app python3 -m pytest`), 事件溯源, Oracle 确定性掷骰, FakeLLMProvider 离线测。

## Global Constraints
- `python3`,never `python`;`PYTHONPATH=/root/rpg-engine-app`;suite 当前 989 passed,保持全绿。
- 停在 `app` 分支。禁止 git init/reset --hard/rebase/checkout 切分支;禁删 `_legacy/`、`docs/`。
- commit message 结尾**必须**:`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 事件溯源纪律:每个状态变更是事件 + 可重放(防双重 apply)apply 分支;gen 状态(seeded/last_refresh_day/density)全折叠自事件 → rewind 安全。
- autonomy within harness:**所有数值由引擎定**(密度默认 0.3、BASE 10、上限 15/8/2、复杂度 70/25/5、refresh 间隔 3 天、threshold 50、stage_count simple2/med3/cplx5、lifespan 复杂度默认);**LLM 只写故事内容**(about/secret/description/trigger/stages/l3_anchor 选择)。
- 容错:provider 为 None / JSON 坏 / 任何异常 → **优雅跳过生成,绝不炸回合**(backstage try/except + 函数内 guard)。
- 确定性:复杂度掷骰 + refresh 掷骰走 `Oracle(scene_seed(campaign_seed, key, salt))`,可重放。
- region-less 世界(无 L1,如 demo)必须优雅降级:密度用默认、region 作用域退化为 town。

---

### Task 1: 密度解析 + 上限统计 + 复杂度掷骰(纯逻辑)+ PlaceSystem 存 density

**Files:**
- Create: `loop/density.py`(本任务只放纯逻辑函数)
- Modify: `systems/place.py`(attr-copy 加 `"density"`)
- Test: `tests/loop/test_density_logic.py`, `tests/systems/test_place.py`(加 density attr 测,若已有该文件则追加)

**Interfaces — Produces(后续任务依赖,签名固定):**
```python
# loop/density.py
def _ancestor_of_level(g, place_id: str, day: int, level: int) -> str | None
def resolve_density(world: dict, town_id: str, day: int) -> float   # L1 ancestor 的 density attr,无则 0.3
def region_scope(world: dict, town_id: str, day: int) -> str        # L1 ancestor id,无 L1 → town_id
def count_tier(world: dict, scope_id: str, complexity: str) -> int  # 数 anchor==scope(simple/medium)或 region 内(complex)的 未了结(state in 暗/明, status active)线
def roll_complexity(oracle, world: dict, town_id: str, region_id: str) -> str | None
    # d100: 1-70 simple / 71-95 medium / 96-100 complex; 降级:complex 满(region≥2)→medium;medium 满(town≥8)→simple;simple 满(town≥15)→None
DENSITY_DEFAULT = 0.3; CAP_SIMPLE = 15; CAP_MEDIUM = 8; CAP_COMPLEX = 2
```
- `count_tier` 口径:simple/medium 按 town anchor 计;complex 按 region_scope 计(数该 region 下所有 town 的 complex)。实现 complex 计数时:遍历 lines,line 的 anchor 的 region_scope == 传入 scope 且 complexity==complex。simple/medium:line.anchor==scope。
- **Consumes:** 整合 map 的 `_ancestor_of_level` 写法;`world["systems"]["ontology"]` FactGraph;`world["systems"]["lore"]["lines"]`。

**Steps:**
- [ ] 写失败测试 `tests/loop/test_density_logic.py`:构造一个带 L1 region(density=0.5)→L2 town→L3 venue 的 world(直接 append place_created 事件 + project,或手搓 FactGraph);断言 `resolve_density` 返回 0.5;无 L1 的 world 返回 0.3;`region_scope` 返回 L1 id(有)/ town id(无);`count_tier` 对预置若干 lines 数对(含 complex 跨 town 按 region 计、了结线不计);`roll_complexity` 用固定 seed 的 Oracle 得确定序列,且 region 满 2 complex 时降级 medium、town 满 8 medium 时降级 simple、simple 满 15 → None。
- [ ] 跑测试看失败。
- [ ] 实现 `loop/density.py` 上述函数。
- [ ] `systems/place.py`:attr-copy 循环加 `"density"`(只在 deltas 含时存)。加/追加测试:place_created 带 density → `g.get_entity(id).attrs["density"]` 等于该值;不带则 attr 缺失(resolve_density 走默认)。
- [ ] 跑 `tests/loop/test_density_logic.py` 与 place 测全绿;跑全量 `python3 -m pytest -q` 保持绿。
- [ ] commit。

---

### Task 2: 生成 LLM 调用 `generate_lore_batch`

**Files:**
- Modify: `loop/density.py`(加生成函数 + id 生成 + 骨架校验)
- Test: `tests/loop/test_density_generate.py`

**Interfaces — Produces:**
```python
def generate_lore_batch(provider, *, town_id: str, kind: str, flavor: str,
                        venues: list[str], existing_abouts: list[str],
                        specs: list[dict]) -> list[dict]
    # specs: [{"complexity": str, "stage_count": int}]; 返回可直接喂 create_lore_line 的 skeleton 列表
    # 每条引擎补齐:id(唯一,如 f"gen_{town_id}_{shorthash}")、anchor=town_id、threshold=50、complexity=spec、lifespan_days=复杂度默认
    # LLM 出:about/secret/description/trigger/l3_anchor(必须 ∈ venues,否则回退 venues[0])/stages(len==stage_count,每个{hint})
    # provider is None / complete_json 抛异常 / 返回结构不合法 → 返回 [](容错,绝不抛)
GEN_THRESHOLD = 50
```
- **Consumes:** Task1 无直接依赖;`provider.complete_json(system,user,schema)`;`systems/lore.py` 的 `_LIFESPAN_DEFAULTS`(import 复用,或本地常量 simple3/med7/cplx20)。
- id 唯一性:用 town_id + 序号 + 内容 hash(不可用 Math.random/time——确定性环境)。用 `about` 文本的稳定 hash(hashlib)拼 town_id;撞 id 时加序号。

**Steps:**
- [ ] 写失败测试:`FakeLLMProvider(json_responses=[{...一批 N 条骨架...}])`,调 `generate_lore_batch(fake, town_id="青石镇", kind="settlement", flavor="边陲集镇", venues=["市集","码头"], existing_abouts=[], specs=[{"complexity":"simple","stage_count":2},{"complexity":"complex","stage_count":5}])`;断言返回 2 条,各有齐全 required 键、complexity 等于 spec、anchor=="青石镇"、threshold==50、lifespan_days 按复杂度(simple3/cplx20)、stages 长度==stage_count、l3_anchor ∈ venues、id 唯一。再断言:provider=None → [];provider 抛异常 → [];模型回 l3_anchor 不在 venues → 回退 venues[0];模型回 stages 数不符 → 截断/补齐到 stage_count(择一,在测试里固定行为)。
- [ ] 跑测试看失败。
- [ ] 实现:构造 system/user prompt(给 town 风味、venues、existing_abouts 去重、specs 的复杂度+stage 数,要求产 JSON 数组,每条 about/secret/description/trigger/l3_anchor/stages);定义 schema;调 complete_json;逐条引擎补齐 + 校验 + 容错。
- [ ] 跑测试全绿 + 全量绿。
- [ ] commit。

---

### Task 3: LoreSystem gen 子状态 + 事件 + `run_density` 编排 + run_turn 钩子

**Files:**
- Modify: `systems/lore.py`(加 `lore_seeded`/`density_refreshed` 事件到 event_types()/apply;gen 子状态 `world.systems.lore["gen"]`)
- Modify: `loop/density.py`(加 `run_density` 编排)
- Modify: `loop/turn.py`(加 backstage 钩子,resolve prev_l2 before apply,调 run_density after demote)
- Test: `tests/systems/test_lore_gen_state.py`, `tests/loop/test_run_density.py`, `tests/loop/test_turn_density_hook.py`

**Interfaces — Produces:**
```python
# loop/density.py
def run_density(registry, store, world, protagonist, prev_l2, *, provider,
                day: int, scene: str, turn: int) -> list[dict]
    # 1) 当前 town = _l2_ancestor(protagonist 所在);若 None → []
    # 2) 首入播种:gen[town].seeded 非真 → 掷 specs(target=round(density*BASE),逐 slot roll_complexity 去 None)→ generate_lore_batch → 逐条 create_lore_line → 追加 lore_seeded{town}事件;置 seeded
    # 3) refresh:seeded 已真 且 now_day - last_refresh_day >= REFRESH_INTERVAL_DAYS → Oracle(scene_seed(seed, f"density:{town}", day)).d100() < density*100 → roll_complexity 生成 1 条(generate_lore_batch specs=[1])→ create_lore_line → density_refreshed{town,day}
    # 返回所有追加的事件(lore_created + lore_seeded/density_refreshed);空 → []
BASE = 10; REFRESH_INTERVAL_DAYS = 3
```
- **Consumes:** Task1 `resolve_density/region_scope/count_tier/roll_complexity`;Task2 `generate_lore_batch`;`create_lore_line`;`_l2_ancestor`(loop/lore_disclosure 或 loop/turn import);`Oracle/scene_seed`;`world["meta"]["campaign_seed"]`、`["day"]`。
- LoreSystem gen 状态:`empty_state` 加 `"gen": {}`;`lore_seeded` apply → `gen.setdefault(town,{})["seeded"]=True`(replay 安全:已真则 no-op);`density_refreshed` apply → `gen.setdefault(town,{})["last_refresh_day"]=event.day`。event_types() 加这俩。
- run_turn 钩子:**apply_turn 之前**算 `prev_loc=_protagonist_location(world,...)` 已有(L265);用它算 prev_l2。apply 后、demote 钩子之后,按 map 的钩子模板调 run_density(provider=cascade_provider),非空则 project。registry.owner_of_event("lore_seeded") 守卫。

**Steps:**
- [ ] 写失败测试(LoreSystem):append `lore_seeded`/`density_refreshed` → project → `world.systems.lore["gen"][town]` 有 seeded/last_refresh_day;重复 apply 幂等。
- [ ] 实现 LoreSystem gen 状态 + 事件;跑绿。
- [ ] 写失败测试(run_density,用 FakeLLMProvider canned + 直接构造 world):首入未播种 town → 生成约 round(density*BASE)条 lore_created + lore_seeded;再调一次(seeded 已真,未到 refresh 间隔)→ [];推进 day 过 refresh 间隔 + 掷中 → 1 条 + density_refreshed;掷不中 → 仅可能 density_refreshed 不生成线(或不更新——按实现固定);town 为 None → []。
- [ ] 实现 `run_density`;跑绿。
- [ ] 写失败测试(run_turn 钩子):用 build_engine + FakeLLMProvider(narrator 段 + cascade canned 骨架),玩家 entity_moved 进一个新 town → 回合后该 town 有自动生成的暗线 + seeded;provider(cascade)为 None → 回合正常、无生成、不炸。
- [ ] 实现 run_turn 钩子;跑绿 + 全量绿。
- [ ] commit(可按子步多 commit)。

---

### Task 4: 端到端离线验证 + 确定性 + region-less + 容错 + demo 脚本

**Files:**
- Test: `tests/loop/test_density_e2e.py`
- Create: `docs/superpowers/specs/density-build-2026-06-21/demo.py`(可选真机 glm-5.1 脚本,仿 unified-quest demo.py 结构)

**Interfaces — Consumes:** 全部前序。

**Steps:**
- [ ] 写端到端测试:region-less world(只 L2 town + L3 venues,仿 unified-quest demo 的 _seed_events 但不预种 lines)+ FakeLLMProvider(cascade canned 骨架 + narrator 段)。玩家进 town → 自动生成暗线 → 跑 run_turn 数回合 → 断言:(a) 暗线被 run_lore 酝酿(stage_idx 推进 / clues_dropped 增长);(b) station_push_fragment 含生成线的 ambient [id];(c) 生成线 anchor==town、complexity 分布合理;(d) 同 campaign_seed 重跑 → 同复杂度序列、同条数(确定性/rewind);(e) cascade provider=None → 无生成、回合不炸;(f) 推进 day 触发 refresh 再长出线。
- [ ] 跑全绿 + 全量绿。
- [ ] (可选)写 demo.py:仿 unified-quest demo.py,进一个只给风味的空镇,dump 自动生成的暗线(about/complexity/l3_anchor/stages)、走几天看 refresh。**不在测试里跑真机**(需 .env.local)。
- [ ] commit。

---

## Self-Review notes
- 每个 LLM 不能定的数值都在 Global Constraints 列死(引擎定),reviewer 以此为准绳。
- region-less 降级在 Task1(resolve_density/region_scope)+ Task4(e2e)双重覆盖。
- 容错(provider None/坏 JSON/异常不炸)在 Task2(generate_lore_batch)+ Task3(run_turn 钩子 try/except)+ Task4(e2e 显式)三层。
- 确定性在 Task1(roll_complexity 测)+ Task4(重跑同序列)覆盖。
