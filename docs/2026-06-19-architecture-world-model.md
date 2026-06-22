# 世界模型架构示意（rpg-engine-app · `app` 分支）

> 截至 2026-06-19 的**当前**架构(v1 + 活世界层 A–E + context/continuity 重构 P1/P2 已落地;P3 工具层规划中、图中以"拉/PULL"虚线标出)。配套:设计 spec `2026-06-17-rpg-ultimate-harness-design.md`、重构 spec `superpowers/specs/2026-06-19-context-continuity-tools-redesign.md`。

## 一句话心智模型

**事件溯源的世界 + 一圈可插拔系统**:每回合把"该让叙事模型看的东西"**推**进上下文 → 叙事模型写一回合(散文 + 结构化变更)→ 严格闸校验 → 变更落成**事件** → 投影回**共享事实图** → 一队后台舰队消化、并主动给下一回合埋料。**"写"永远走校验闸,"读"靠推(+ 将来的工具拉)。**

---

## 图1 · 回合循环(各系统怎么一起转)

```
   ┌────────── 玩家输入 ──────────┐
   │                              ▼
   │                  ① assemble_context  ──── 把多个系统的产出"推"进上下文(图2)
   │                              │
   │                              ▼
   │                  ② 叙事模型 (甲/丙 · glm-5.1)
   │                     产出 TurnCommit:
   │                     narration(散文) + 结构化段:
   │                     moves/places/cast/facts/knowledge/world/quests
   │                              │
   │                              ▼
   │                  ③ 严格校验闸 ──不合格──▶ agent-loop 修复
   │                     (机械处严)            (模型看着自己上一版 + 精确错误改, N≤6)
   │                              │ 通过
   │                              ▼
   │                  ④ to_events ─▶ 事件存储(append-only, 唯一写入口)
   │                              │
   │                              ▼
   │                  ⑤ project:各 system.apply() 把事件折进
   │                     【共享 FactGraph + 各自 slice】(图3)
   │                              │
   │                              ▼
   │                  ⑥ 后台舰队(post-apply · 非阻塞 · 廉价模型 glm-4.7):
   │                     digest ─▶ director ─▶ cascade ─▶ catch-up   (图3 下半)
   │                     消化本轮 + 主动给【下一回合】埋料 / 演化世界
   │                              │
   └──────────────────────────────┘  回到 ①(下一回合的上下文已被舰队备好)
```

---

## 图2 · 上下文怎么被各系统影响(**推 PUSH vs 拉 PULL**)

```
                        ┌───────────────────────────────────────────┐
   各系统 inject() ─────▶│        叙事模型这一回合看到的上下文          │
   + assemble 组装        │   (cache 友好: stable → scene → volatile)  │
                         └───────────────────────────────────────────┘
   ▲ 推(强制·每回合必看·延续性命脉)                       ▲ 拉(按需·P3 规划中)
   │                                                      │
   ├─[stable 层]  Ontology: 世界规则                      │  ┌─────────────────┐
   │              Narrative: 往昔概要(recap 老场景摘要)   │  │  叙事模型按需查的  │
   │                                                      └─→│  只读工具 (P3):    │
   ├─[scene 层]   Place: 当前位置 + 出口                     │  map / characters  │
   │              Character: 在场角色卡                      │  / factions /recall│
   │              Knowledge→viewpoint: pov(主角所知)/        │  战争迷雾:POV/DM    │
   │                 guardrail(真but主角不知·只约束勿泄)/npc  │  两套入口分流       │
   │              Lore: 明账(活跃任务) + 暗线环境推送        │  └─────────────────┘
   │              Narrative: 最近 1–2 章原文(逐字)           │
   │              Director: 暗骰导演提示(本回合该出的转折)    │  ╳ 工具只读,改不了状态
   │                                                         │    一切"写"仍走 ③ 校验闸
   └─[volatile 层] recall: 按当前输入语义召回的相关过去片段
```

---

## 图3 · 数据底座 + 后台舰队(如何"读写世界")

```
        ┌──────────────────────────────────────────────────────────┐
        │   事件存储 (event store, append-only, 不可变, rewind 可截断) │
        └──────────────────────────────────────────────────────────┘
              │  project()  ▲ append (来自 ④ 或 后台舰队 ⑥)
              ▼             │
        ┌──────────────────────────────────────────────────────────┐
        │     共享 FactGraph  (entities / 双时态 facts / relations)   │ ← 唯一真相
        │     + 各系统私有 slice (director 暗线 / story 明账 / recap)  │
        └──────────────────────────────────────────────────────────┘
              ▲ apply() 写            │ inject()/查询 读
              │                       ▼
   ┌──────────┴───────────┐   ┌───────────────┐
   │ 内容系统(玩家提交驱动) │   │ 后台舰队(⑥自驱)│  每回合提交后跑、主动演化:
   │  Ontology(图本体)     │   │ digest 史官:重要度打分→反思→人物 arc;       │
   │  Place 地点           │   │   维护 recap(老场景压摘要)+ 暗线漏报补     │
   │  Character 角色       │   │ director 暗骰:隐藏 d100→可能给下回合埋"转折" │
   │  Object 物品          │   │ cascade 波及:叙事声明的 world 区域→逐地演化  │
   │  Faction 势力         │   │   (廉价模型填后果)+ 至多再蔓延一圈          │
   │  Knowledge 认知       │   │ catch-up 时间:时间跳跃→只追"进入场景的"过期  │
   │  (Lore / Narrative)   │   │   tracked,补它们这段日子的漂移              │
   └───────────────────────┘   └───────────────┘
```

---

## 系统表(谁拥有什么 / 写哪 / 推什么进上下文)

| 系统 | 拥有的事件 / 段 | apply 写入 | inject 推进上下文 |
|---|---|---|---|
| **Ontology** 本体 | entity/fact/relation_*；段 entities/facts/relations | **共享图**(就是图本身) | 世界规则(stable) |
| **Place** 地点 | place_created/linked/materialized/entity_moved；段 places/moves/links | 图:Place + contained_by/adjacent_to/located_in | 当前位置 + 出口(scene) |
| **Character** 角色 | character_created/evolved/relationship_changed；段 cast | 图:Person + 角色卡事实 / arc | 在场角色卡(scene) |
| **Object** 物品 | object_*；段 objects | 图:Item + held_by(单值) | 持有物 |
| **Faction** 势力 | faction_created/member_changed；段 factions | 图:Faction + member_of(多值) + rank-as-fact | (弱 / 待补) |
| **Knowledge** 认知 | knowledge_set/broadcast；段 knowledge | 图:`knows:{fact_key}` 事实 | → 驱动 **viewpoint**(pov/guardrail/npc) |
| **Director** 暗骰 | oracle_roll/director_fired/thread_*(harness 自产) | slice:暗线 + 待发导演提示 | 本回合导演转折(scene) |
| **Cascade** 波及 | world_change/place_evolved/populace_shifted；**段 world** | 图:地点 state/民心 + slice 延迟队列 | (无;后果经 Place 显现) |
| **Lore** 事件线 | lore_created/lore_advanced/quest_*(harness+narr 自产)；**段 quests** | slice:统一暗/明/了结事件线 | 明账·强推(scene) |
| **Narrative** recap(P2) | narration_recorded/scene_summarized/recap_recompressed(harness 自产) | slice:分层 recap | 近原文 + 往昔概要·强推 |

> 注:Lore/Narrative 的 slice 也是事件溯源的(随 project 重建),所以 rewind 安全。

---

## 三条贯穿原则(设计内核)

1. **写入唯一口 = 严格校验闸**;读靠推(命脉强制)+ 拉(参照按需)。"autonomy within deterministic guardrails"——LLM 自主发挥,但被确定性护栏框住。
2. **事件溯源**:世界 = 事件累积投影,所以**天然可倒带**(`/undo` 截断重放)、可重算、可双时态查"某天某人知道什么"。
3. **强叙事 + 廉价舰队分工**:叙事模型(glm-5.1)管"发生什么 / 波及哪 / 蔓不蔓延"的**故事判断**;廉价舰队(glm-4.7)管"每地怎么变 / 打分 / 摘要 / 漂移"的**机械活**。对应设计 §12 的三层分工。

---

## 已知最大空洞:没有"场景推进"

`app/play._build_scene` 让 `meta.scene` **静态**(不随回合推进)。这一处连累三家:
- **recap 不分层**(永远 1 个 scene 桶 → 老场景不被压成摘要 → 原文无限涨)。
- **director 节奏带空转**(pacing 的频带/冷却基于场景序数,静态则不动)。
- **cascade scene_subtree** 的"当前场景 vs 远区"判定打折。

→ 一个小小的"场景推进"机制(判定一个 scene 何时结束、推进 `meta.scene`)能一次盘活三处。是当前优先级最高的待决项之一。
