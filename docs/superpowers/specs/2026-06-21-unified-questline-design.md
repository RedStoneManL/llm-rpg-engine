# 事件线 / 任务 统一系统 设计 (Unified Event-Line / Quest System)

> 2026-06-21 · rpg-engine-app `app` 分支。
> 把现有的 **LoreSystem(暗线)** 与 **StorySystem(明线/storylines 明账)** 合并成**一套**"事件线 = 任务"系统——它们本就是同一种对象的两个状态(对齐记录见下)。这是动架构,本 spec 是蓝图。
> 缘起:lore A/B 真机对比里,模型不由自主把 lore 当 storyline 推(`storyline_advanced unknown id` 污染明账)——这正是"两系统其实是一个"的铁证。
> 配套:`2026-06-20-lore-event-line-design.md`(被本 spec 取代/吸收)、`lore-AB-2026-06-20/`(对比结论:暗态披露用 B)。

## 一句话
一条事件线 = 一个任务,带状态 **暗(未浮现,世界自走)/ 明(已浮现,玩家驱动)/ 了结**。**推进是状态内的事**(暗骰 vs 玩家),**转换是状态间的事**(浮现/搁置/了结)。整套就是一个"活世界版"RPG 任务系统:多数任务在水下自己酿,玩家碰到的才浮上来。

## 核心模型

### 状态 + 两套推进(关键:明暗推进方式不同)
| 状态 | 谁推进(advancement) | 玩家看到 |
|---|---|---|
| **暗** | **引擎暗骰**(时间/概率,每回合 d100+threshold),过了就沿**预设 stages** 走一格、漏线索 /(复杂线)悄改世界。自走、廉价、无 LLM——"没人管会怎样" | 只有环境零碎线索/风声(站点直推 B) |
| **明** | **玩家行动 + 叙事模型**,涌现式,脱离预设轨道(玩家抢了方向盘),叙事在**任务段**记 advance | 进"任务明账",强推(延续命脉) |
| **了结** | — | 收束(或留远景) |

预设 stages = "无人干预的默认走向";一接取就被玩家改写。

### 三类转换
- **暗 → 明(浮现/接取)**,两条路:
  - **玩家拉**:玩家主动跟线索(查/问/追)→ 叙事模型判定真卷入 → 接取(进明账、暗骰停、转玩家驱动)。
  - **世界推**:暗线酿到"爆点"stage → 不管玩家追没追,自己炸进前台(灾祸/摊牌)→ 强制浮现。(= director 那套"埋种子择机炸",**第二阶段并入**。)
- **明 → 暗(搁置)**:玩家不再跟(离开此地 / 数回合不碰)→ 退暗。**处理按 (a):退回时若该线已被玩家改动,JIT 重写剩余轨迹**(一次廉价 LLM 调用,从当前现实续写默认走向),让暗骰能接着自走;未改动则直接续用原 stages。下两档退回 = 休眠 + lifespan 倒计时,复杂线退回 = 继续往终局酿(★6 一致)。
- **→ 了结**:明态玩家收尾(叙事 resolve)/ 暗态世界了结(复杂线封顶终局救场掷骰、或 lifespan 过期)。

### Bug 的结构性修复
推进通道**按状态分死**:暗骰**只**碰暗态;任务段**只**碰明态(明账里的);浮现是**单独的 op**(不是 advance)。叙事模型平时只在明账里看到/推进明态任务,暗态只给它环境线索去"也许触发浮现"。**没有两套可混 → 污染消失。**

## 重构:合二为一
新建/改名一套 **QuestSystem(`systems/quest.py`)**,吸收 LoreSystem + StorySystem:

**数据** `world["systems"]["quest"] = {"lines": { <id>: {`
- `state`: "暗"|"明"|"了结";`complexity`/`about`/`secret`/`anchor`/`l3_anchor`/`description`/`trigger`/`stages`/`threshold`/`stage_idx`/`clues_dropped`(暗态用,沿用 lore L1)
- `summary`(明态明账一句话,沿用 storyline)、`surfaced_turn`/`resolved`(状态元数据)
`}}`

**事件(harness/narrator 混合)**:
- `quest_created`(暗骰生成的暗态线 / narrator 直接开的明态线;deltas 带 `state`)
- `quest_advanced_dark`(暗骰推进,harness)——原 `lore_advanced`
- `quest_surfaced`(暗→明,带触发来源 player/world)
- `quest_advanced`(明态推进,narrator 任务段)——原 `storyline_advanced`
- `quest_demoted`(明→暗,带 JIT 续写的新 stages)
- `quest_resolved`(了结,带 by=player/world + 终局文案)

**commit 段** `quests`(取代 `storylines`):narrator 只对**明态**线声明 `advance`/`resolve`,以及对**当前可浮现**的暗线声明 `surface`(接取)。暗态推进**不在** commit 段(只走暗骰)。

**引擎侧** `loop/quest.py`:`run_quests(registry, store, world)`(原 run_lore 扩展)——暗态线暗骰推进 + 世界推浮现判定;`jit_resequence(line, world, provider)`——(a) 方案的明→暗续写。

**披露(对比已定)**:明态 = 完整任务明账(强推,= 原 StorySystem inject);暗态 = 玩家附近环境线索(B 站点直推,= `station_push_fragment`)。两者是同一池子按状态的两个视图。

**保留不返工**:lore L1 的暗骰引擎 / fetch_lore / B 披露 / 世界钟挂钩;StorySystem 的明账 inject 逻辑。A 的 tool-loop 基础设施留给将来 P3 只读工具(map/recall),本系统不用。

**向后兼容**:现有 storylines 相关测试需迁移到新 `quests` 段 + QuestSystem;迁移期保证明账行为(open/advance/resolve)等价。lore 相关测试迁移到统一系统。这是迁移不是删功能。

## 不在本期
- director 并入(第二阶段:它的"种子线→择机炸"= 世界推浮现的一种)。
- 密度生成 L3(首入二级地图播种 / 上限)——暗态线先靠手工/现有创建;密度生成随后。

## 复杂场景验证(实现后做,看效果)
一个 glm-5.1 实机脚本(仿 lore-AB 的 compare),跑一条**完整生命周期**:
1. 世界播几条暗态线(不同复杂度,散在 venue)→ 暗骰自走、漏线索(看暗态推进 + B 披露)。
2. 玩家跟某条线索 → **浮现暗→明**(看接取、进明账)。
3. 玩家推进该明线几回合(看明态 narrator 推进、明账更新)。
4. 玩家走开 → **明→暗 搁置 + (a) JIT 续写**(看续写后暗骰接着自走)。
5. 一条复杂暗线酿到爆点 → **世界推浮现**(看强制炸场)。
6. 收尾:玩家 resolve 一条 + 一条复杂线无人管走到封顶终局(救场掷骰)。
dump 每步:各线 state、暗骰/narrator 哪个推的、明账、披露内容、JIT 续写前后、终局掷骰。验证状态机 + 两套推进 + 三类转换都真的转起来。

## 自检
- 明暗推进分离(暗骰/narrator)+ 通道按状态分死 → bug 结构性消除。
- 状态机:暗⇄明(浮现/搁置)、暗/明→了结;(a) JIT 续写处理玩家改动后的搁置。
- 合并保留两边已建资产(暗骰引擎 + 明账 + B 披露),是迁移非重写。
- director / 密度生成 明确划到后续,本期聚焦"合二为一 + 状态机"。
