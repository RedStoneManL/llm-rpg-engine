# 开局世界 Bootstrap（长程初始化）— 设计 (2026-06-22)

> 把老 hermes `rpg-dm` skill 的 `seed_campaign` 思路（oracle 掷"具体互异种子" → LLM riff，专治 mode-collapse + 被动）移植/升级到新 `rpg-engine-app`（branch `app`）。当前 `new_game()` 只播 1 个占位 L2 镇 + 通用主角，没有世界框架/地图/NPC/暗线/钩子。

**Goal:** 把开局从"空占位"换成一次**确定性、可 reroll、分层锚定**的世界初始化：玩家给基调 → 引擎 oracle 掷结构 + LLM 写内容 + harness 校验 → 生成宏观区域骨架 + 起始本地图 + 势力 + 开局 NPC（带秘密）+ campaign 主线暗线 + 开场叙事，全部 append 成 genesis 事件。

**Architecture:** 半交互老虎机。oracle 从**可扩展维度表** distinct 抽出结构（防趋同），LLM 在抽中的类别里写具体内容，每步走 `complete_structured` 严格校验→修复。世界按**距离分三档细节**（起始 L1 详 / 邻 L1 画到 L1 / 远 L1 粗 seed），宏观邻接图在 bootstrap 钉死以**防反应式生成漂移**。事件溯源 → reroll = retract genesis + 重掷。

**Tech Stack:** 复用 `engine.oracle`（`Oracle`/`scene_seed`，确定性、rewind-safe）、`llm.structured.complete_structured`（validate→repair）、lore 系统（`loop.lore.create_lore_line`）、character/place/faction 系统、T9 `secrecy` 字段、kernel 事件溯源。

## Global Constraints
- 一切掷骰用 `Oracle(scene_seed(campaign_seed, f"genesis:{step}:{i}", attempt))` — 纯重放一致、rewind-safe。**禁止** `random`/时间调用（会破坏重放）。
- 每个 LLM 生成步走 `complete_structured`（严格逐字段 prompt + 缺字段报错 + repair 循环），与 density `generate_lore_batch` 同款。**所有 structured 返回都受此约束**（既有 harness 统一律）。
- 数字（region 数、L2 数、势力数、NPC 数、暗线数/complexity、阈值、lifespan）一律**引擎掷定**；LLM **只写故事内容**。
- 维度表是 `data/oracles/genesis/*.json` 纯数据：**加轴/加词条只改数据、不改码**。
- 离线 FakeLLMProvider 全覆盖；保留一个真 GLM 探针（不入单测套件）。
- Python3，`PYTHONPATH=/root/rpg-engine-app`；提交在 `app`，per-feature，消息结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

## § 1 世界模型：分层骨架，锚死防偏移

世界 = 若干 **L1 region（王国/大地图）**，带**邻接图**（region 间 `place_linked` → `adjacent_to`，承载"东/西/邻"方向）。bootstrap 一次性钉死宏观骨架，细节随距离递减：

```
[宏观区域图 — bootstrap 全画, 锚住方向]
  雪原王国(远·粗seed) ─ 河谷王国(起始·详) ─ 商盟自由港(邻·画到L1)
                             │
                        铁峰山脉(邻·画到L1)

起始 L1「河谷王国」(详):
  ├ L2「石桥镇」(settlement) ─ L2「黑森林」(wilderness) ─ L2「渡口镇」(settlement)
  │    └ L3 集市 / 酒馆 / 码头 (venues — 给 density/lore 锚点)
  └ density 参数挂在 L1 region
```

**三档细节（距离递减）：**
1. **起始 L1 region（详）**：生成它的 L2 布局 = 1 个起始镇（settlement）+ 1-2 个相邻 L2 地域（镇或山林，kind 掷自维度表），L2 间邻接；起始镇下挂 2-4 个 L3 venues。
2. **相邻 L1 regions（画到 L1）**：name + 基调 + 主导地形 + 1-2 势力 + 与起始 region 的邻接边。**不展开内部 L2**。
3. **远方 L1 regions（粗 seed）**：name + 一句 seed，挂上宏观邻接图（锚住"东边是雪原王国"）。

**防偏移机制（你的核心诉求）：** 宏观区域邻接图 + 各 region 的 seed/地形/势力在 bootstrap 即钉死为事件。玩家之后往某方向走，引擎已知该方向是哪个 region，反应式生成只能在此骨架内填 L2/L3，**不会漂**。进入某个"画到 L1"的邻国时，其 L2 才反应式生成（受其 L1 seed 约束）。

**非城邦地域：** 山脉/森林/荒野/遗迹 = `kind=wilderness`/`dungeon` 的地点，与镇平级（L2）或作 L3 特征。地点 kind 维度表保证起始邻域不全是城。

**Level/kind 语义（沿用现有）：** L1=region（大地图/王国），L2=settlement|wilderness|dungeon（城镇/野外），L3=venue（镇内场所）；`parent` → `contained_by`；`place_linked{a,b,travel_cost}` → `adjacent_to`。

---

## § 2 防趋同骨架：可扩展维度表 + oracle distinct 抽

**维度表 = 一条轴上的抽象类别小列表（非世界内容）。** v1 这几张（`data/oracles/genesis/`）：

| 表 | 抽象类别（可扩） | 管什么 |
|---|---|---|
| `thread_types.json` | 身世 / 阴谋 / 物品 / 势力 / 情感 | 强制 N 条主线跨不同类型 |
| `npc_roles.json` | 掌权者 / 知情者 / 走卒 / 对手 / 盟友 / 边缘人 | 强制开局 NPC 跨不同角色 |
| `place_kinds.json` | settlement / wilderness / dungeon | 强制起始邻域不全是城 |
| `tone_axes.json` | 悬疑 / 冒险 / 权谋 / 生存 / 恩怨 | 给世界框架定调 |
| `terrains.json` | 平原 / 山地 / 森林 / 水乡 / 荒漠 / 雪原 | 给 region 主导地形 |

- oracle **distinct 抽样**（`_draw_distinct`，sample-without-replacement，移植自老 `seed.py`）→ 强制互异。
- LLM 在抽中类别里写具体内容（"身世线 in 河谷王国 = …"）。
- **可扩展**：加轴 = 加一个 JSON + 在对应生成步引用；加词条 = 改 JSON。先这套，玩起来不够再加。
- 每条目 `{"weight": int, "name": str, ...可选维度提示}`，weighted draw（沿用 `Oracle.draw`）。

---

## § 3 生成流水线（"长程"，多步，每步 strict-gen + repair）

玩家给基调（**自由 pitch 文字，可选 genre 提示**）→ 引擎按序执行。每步：oracle 掷结构 → `complete_structured` 让 LLM 写内容（缺字段报错+repair）→ append 事件。

1. **世界框架** `frame`：掷 tone_axis × 中心冲突模板 × 势力数(3-5) × region 数(3-5)。LLM 据 pitch+掷值写：world 名、一句中心冲突、基调。→ 存为 `meta`（genre/tone/conflict）+ 一个 `campaign_framed` 事件。
2. **宏观区域图** `regions`：掷 region 数 + 邻接布局（线性/星形，确定性）+ 每 region 的 terrain（distinct 抽）。LLM 命名 + 一句 seed。起始 region 标记 detailed，其余 neighbor/far。→ `place_created`(level=1,kind=region) ×N + `place_linked` 邻接边 + 起始 region 带 `density`。
3. **起始本地图** `local_map`：掷起始 L1 内 L2 数(2-3) + 每个 kind（distinct，至少 1 settlement 作起始镇）+ 起始镇 L3 venue 数(2-4)。LLM 写各地点 seed。→ `place_created`(L2,parent=起始region) + `place_created`(L3,parent=起始镇) + L2 邻接。
4. **势力** `factions`：掷势力数（=框架值）。LLM 写每个 faction 的 name/seed/动机，distinct。→ 经 faction 系统建 faction 实体，锚到 region。
5. **开局 NPC** `npcs`：掷 NPC 数(2-4) + 每个 role（distinct）+ 2 个 trait（distinct）。LLM 写 sketch/goal + **一条秘密**。→ `character_created`{id,tier=tracked|mentioned,sketch,goal} + 秘密写成 `fact_asserted`{subject,predicate,value,**secrecy:"secret"**}（接 T9）；可选关系/faction 成员。主角也在此步定（或沿用既有 protagonist）。
6. **主线暗线** `threads`：掷 3-5 条 + 每条 type（distinct）+ complexity（掷，campaign 级偏 medium/complex）+ speed→threshold。LLM 写 about/description/trigger/secret/stages/l3_anchor（锚到已生成的 venue）。→ 复用 `loop.lore.create_lore_line`。**额外掷 1-2 条 anchor=主角的暗线**（藏在主角身上）。
7. **开场 + 世界圣经** `opening`：LLM 把以上织成 (a) 开场叙事（主角视角，落在起始镇的某 venue），(b) 一段"世界圣经"摘要。→ 开场存 `narration_recorded`；世界圣经存为可注入片段（`fact_asserted` on 一个 `world` 实体，或 narrative 片段——实现时择一，默认 narrative 片段供 inject）。

全部 genesis 事件 `turn=0`，`day=1`，`scene="genesis"`。生成顺序保证引用安全（先地点后 NPC 后暗线后开场）。

---

## § 4 半交互 reroll

生成后打印结构化摘要（框架/区域/势力/NPC/暗线一行行）给玩家。命令：
- `reroll`：整体重掷（attempt+1，retract 全 genesis 重跑）。
- `reroll <类>`（**仅叶子步**：`threads`|`npcs`|`factions`）：只重该步（retract 该步事件 + 该步 attempt+1 重跑）。这三步无下游依赖，可独立重掷。
- **地图/区域属上游**（暗线 l3_anchor、NPC 位置都依赖它）：要换地图就用整体 `reroll`，不做单独的 map/region 重掷——避免级联失效。
- `开始` / 空行确认 → 进入正常 play_loop。

v1：**整体 reroll + 叶子步按类重掷**。逐条精修（只换第 3 条暗线）延后（YAGNI）。

---

## § 5 确定性 / rewind 安全

- 掷骰种子：`scene_seed(campaign_seed, f"genesis:{step}:{i}", attempt)`，`attempt` 是该步的 reroll 计数（存在 genesis 状态里/或一个 `genesis_attempt` 事件）。
- reroll = `store.retract_from_turn`-风格的 genesis 选择性 retract + 重跑 → 纯重放一致（沿用 `engine.rewind` 机制）。
- 无 `random`/时间调用。LLM 内容非确定（可接受：reroll 本就要新内容；结构掷骰确定）。

---

## § 6 接入点（复用，不重造）

- **替换 `app/engine.py::new_game()`** → 新 `bootstrap_world(engine, pitch, *, attempt=0)`（旧 new_game 保留为"极简兜底"或删除，实现时定）。
- 复用：lore（暗线）、character、place、faction、T9 secrecy、`complete_structured`、Oracle。
- **顺手修 density `l3_anchor` 悬空**：起始镇现在有真 L3 venues，density 生成的 l3_anchor 能命中真地点。
- `app/__main__.py` / `app/play.py`：首次空 store 时跑 bootstrap（取 `--pitch` 或交互问一句基调）+ reroll 循环，再进 play_loop。
- 新文件：`loop/bootstrap.py`（流水线编排）、`data/oracles/genesis/*.json`（维度表）。生成各步可拆小函数（每步一函数，独立可测）。

---

## § 7 测试

沿用项目惯例（offline 全覆盖 + 真模型探针）：
- **离线**（FakeLLMProvider，确定性）：掷骰确定性/rewind 重放一致；distinct 抽不重复；每步事件结构正确（地点 level/parent/邻接、NPC 秘密带 `secrecy="secret"`、暗线经 lore 系统、region 邻接图）；防偏移（宏观邻接钉死）；reroll 整体/按类正确 retract+重跑；空 pitch/LLM 畸形→repair→兜底不崩。
- **真 GLM 探针**（不入套件）：一次完整 bootstrap，人看开局质量 + 区域骨架不偏 + NPC 秘密确实标 secret + 暗线锚到真 venue。

---

## 已定（经 2026-06-22 brainstorming）
- 形态：**半交互老虎机**（玩家给基调 → 掷+生成 → reroll → 开叙；"长程"在生成流水线）。
- 防趋同：**可扩展维度表 + oracle distinct 抽**（非内容表；加轴/词条只改数据）。
- 地图：**分层骨架锚死防偏移**（起始 L1 详 / 邻 L1 画到 L1 / 远 L1 粗 seed + 宏观邻接图钉死）。
- (a) 基调：自由 pitch 为主 + 可选 genre 提示。 (b) region 数：3-5（1 详 + 2-4 邻/远）。 (c) reroll：整体 + 按类。

## Out of scope (YAGNI)
- 逐条精修 reroll（只换某一条）。
- bootstrap 时生成全世界 L2/L3（远处靠反应式）。
- 预设 genre 锁定的内容表（用维度表 + pitch 替代）。
- 玩家掷骰/规则书（纯叙事裁定，沿用现状）。
- 世界圣经的富结构（v1 一段可注入摘要即可）。
