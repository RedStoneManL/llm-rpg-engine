# 密度生成 设计 (Lore Density Generation)

> 2026-06-21 · rpg-engine-app `app` 分支。统一事件线/任务系统的**自动生成层**:让暗线**按地图密度自己长出来**,不再手工种。这是"活世界"的核心——玩家走到哪,哪里就自发酝酿出可被发现的暗线。
> 前置已就绪:统一 quest 模型(暗/明/了结)、`create_lore_line`→`lore_created`、暗骰 `run_lore`、ambient 披露、lifespan/过期(simple3/med7/cplx20 + run_lore 到期清理)、世界钟 `meta.day`、`engine.oracle.Oracle(seed)` 确定性掷骰、地图层级 L1 region/L2 town/L3 venue。
> 缘起:用户早先定的"密度生成 per region、首入二级地图播种、每档上限、复杂度 70/25/5、渐进式披露"。架构已批,本 spec 落具体机制。
> 配套:`2026-06-21-unified-questline-design.md`、`2026-06-21-quest-lifespan-design.md`、`2026-06-20-world-clock-design.md`。

## 目标
玩家**首次进入一个 L2 城镇**时,引擎按该地区**密度**自动生成一批暗线(LLM 写骨架,引擎掷复杂度+定机制);此后随**游戏内时间**密度门控地持续补充。生成完全走现有 `lore_created` 管道,生成的线即是暗态 quest——会被暗骰酝酿、被 ambient 披露、被玩家 surface、被 lifespan 过期。引擎不写故事内容(交 LLM),但**所有数值/掷骰/上限/节奏由引擎定**(autonomy within harness)。

## 设计

### 1. 密度 `density`(L1 region 属性,优雅降级)
- L1 region 的 `place_created` deltas 可带 `density`(0..1,默认 **0.3**)。L2 城镇**继承**其 region 的密度:从 town 沿 `parent` 链上溯到最近一个带 `density` 的地点;**找不到(无 L1,如 demo 世界)→ 用默认 0.3**。
- "region 作用域"(给复杂线上限用)= town 的 L1 祖先 id;**无 L1 → 退化为 town 自身 id**(region-less 世界也能跑)。

### 2. 上限(每类,确定阈值;用户定 15/8/2)
- simple ≤ **15** / town(L2)
- medium ≤ **8** / town(L2)
- complex ≤ **2** / region(L1 作用域;无 L1 则 / town)
- 统计口径:只数**未了结**(state ∈ {暗,明})的线。了结/过期的不占额。

### 3. 复杂度掷骰(70/25/5,确定性)
每个待生成 slot 用 `Oracle(scene_seed(campaign_seed, town_id, "density:<n>")).d100()` 掷:1–70 simple / 71–95 medium / 96–100 complex。**降级规则**:掷到 complex 但 region 已达 2 → 退成 medium;medium 但 town 已达 8 → 退成 simple;simple 但 town 已达 15 → 该 slot 跳过(不生成)。确定性:同一 seed+town+序号 → 同结果,rewind 安全。

### 4. 首入播种(town-entry)
- 在 `run_turn` 里,玩家移动落定后,检测**当前 L2 城镇是否已播种**(town 上有 `lore_seeded` 标记 / lore 系统记录)。**未播种 → 播种一次**,置标记。
- 批量大小:`target = round(density * BASE)`,**BASE = 10**(可调) → density 0.3 → 3 条。对每个 slot 掷复杂度(§3,带降级),凑出本批的 `[{complexity, stage_count}]`。
- `stage_count` 按复杂度:simple **2** / medium **3** / complex **5**(可调)。
- 调一次 LLM **批量生成**(§6)拿到骨架,逐条走 `create_lore_line` → `lore_created`(state 暗)。

### 5. 密度门控补充(refresh,clock 驱动)
- 世界会持续呼吸:当前 town 上记 `last_refresh_day`。`run_turn`(或 fleet)里,若 `now_day - last_refresh_day >= REFRESH_INTERVAL_DAYS`(**=3**,可调):掷 `d100`,**< density*100 → 生成 1 条新暗线**(复杂度掷骰+降级+上限;到顶就不生成)。更新 `last_refresh_day = now_day`。
- 补充只在**玩家所在 town**发生(空城不生成,省 token、聚焦)。

### 6. 生成 LLM 调用(引擎定数值,LLM 写内容)
`loop/density.py::generate_lore_batch(provider, *, town, kind, flavor, venues, existing_abouts, specs) -> list[skeleton]`
- 输入:town id/kind/flavor(地点 seed 文案)、该 town 的 L3 venue 列表(给 `l3_anchor`)、现有线的 `about` 列表(**去重,别撞已有**)、`specs=[{complexity, stage_count}]`(引擎掷好的)。
- 一次调用产**一批**(N 条),让模型写得彼此**不同且贴合本镇风味**。
- 输出每条骨架:`{about, secret, description, trigger, l3_anchor(必须是本镇某 venue), stages:[{hint}]×stage_count, }`。**引擎补**:`complexity`(=spec 的)、`threshold`(默认 **50**,可调)、`lifespan_days`(=复杂度默认 simple3/med7/cplx20)、`anchor`(=town id)。
- 用 **cascade_provider(便宜的后台模型)**——这是后台世界生成,不是玩家面前的叙事;真正被 surface 时由主叙事模型润色。provider 缺失/报错/JSON 不合法 → **优雅跳过本次生成**(绝不让生成失败炸掉回合);log 一条。
- 模型可选给 `lifespan_days` 时夹到合理范围,否则用默认(本期先用默认,模型调寿命留作后续微调)。

### 7. 事件 / 状态
- 生成产 `lore_created`(已有,state 暗)。无新事件类型用于"线"本身。
- 播种/refresh 标记:`lore_seeded`{town}、`density_refreshed`{town, day} 两个轻事件,apply 到 place 或 lore 系统状态(记 seeded / last_refresh_day),**rewind 安全**(折叠自事件)。或复用 place 状态存这俩字段——实现时择一,要可重放。

## 复用 / 影响
- 生成→`create_lore_line`→现有暗骰/ambient/surface/lifespan 全自动接上(密度生成只负责"生出来")。
- `Oracle`/`scene_seed` 给确定性复杂度掷骰 + refresh 掷骰。
- `run_turn` 已检测移动/scene 边界(scene-progression),town-entry 播种挂同处。
- cascade_provider 已存在(catch-up/cascade 用的便宜模型)。
- lifespan 的 `_LIFESPAN_DEFAULTS` 复用给生成的 lifespan_days。

## 不在本期
- 复杂线终局(world-rescue 掷骰)= 下一步 L4(本期只管生成 complex 暗线 + lifespan 到期标 pending_finale)。
- 模型自定 lifespan/threshold 的精细化(本期引擎默认)。
- 跨 town 的 medium 关联(用户早先明确"先不考虑跨town的medium")。
- 派系/director 并入(后续)。

## 复杂场景验证(实现后)
**离线(FakeLLMProvider 喂固定骨架 JSON)**:
- 首入 town(density 0.3,BASE 10)→ 播种约 3 条暗线,复杂度分布符合掷骰,各带 about/secret/stages/l3_anchor(∈本镇 venue)/lifespan_days;再次进同一 town **不重复播种**(seeded 标记)。
- region 已有 2 条 complex → 再掷到 complex 被降级,complex 计数不超 2;town simple 满 15 → slot 跳过。
- 推进游戏钟 ≥3 天 + density 掷中 → refresh 生成 1 条;<3 天不生成;到顶不生成。
- 确定性:同 seed 重跑 → 同复杂度序列、同条数(rewind 安全)。
- 生成的线随即被暗骰酝酿、进 ambient、可被 surface、到 lifespan 过期——端到端接上。
- provider 报错/坏 JSON → 跳过,回合不炸。
**(可选)真机 glm-5.1**:进一个只给了风味的空镇,看自动长出的暗线是否贴合、彼此不同、l3_anchor 合理;走几天看 refresh。

## 自检
- 所有数值(密度、上限、批量、节奏、复杂度、阈值、寿命)引擎定;LLM 只写故事内容 → autonomy within harness。
- region-less 世界(无 L1)优雅降级(密度默认、region 作用域退化为 town)→ demo 世界能跑。
- 复杂度/refresh 掷骰走 Oracle/scene_seed → 确定性、rewind 安全。
- 生成失败绝不炸回合(provider/JSON 容错)。
- 生成只在玩家所在 town(省 token、聚焦);了结线不占上限额。
- 接现有管道(lore_created→暗骰/ambient/surface/lifespan),不另起炉灶。
