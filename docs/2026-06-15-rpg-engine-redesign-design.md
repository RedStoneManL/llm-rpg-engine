# RPG Engine 重构设计 (rpg-engine)

> 跑团 DM skill 的重写。在 hermes 框架内,用"事件溯源 + 投影 + 暗骰导演"把 LLM 的自主叙事**框**在确定性 harness 里。
>
> 日期: 2026-06-15 · 状态: 设计已批准 · 取代: `openclaw-imports/rpg-dm`(封存)

---

## 1. 设计哲学

**充分利用 LLM 自主性,同时用 harness + 工具把它框住,省得跑偏。**

- LLM 负责**写**(剧情/人设/氛围/裁定),自由发挥。
- LLM **绝不手动维护一致性**:canonical 状态由 harness 从结构化事件流**推导**。
- "有趣"与"记得"不靠模型自觉:由确定性代码(暗骰、投影、召回、闸门、hook)兜底。

一句话:**WHAT 由骰子/harness 决定(防趋同、防遗忘、防漂移),HOW 由 LLM 决定(保证质量与自洽)。**

## 2. 目标 / 非目标

**目标(对应用户原始痛点)**

| 痛点 | 本设计的解 |
|---|---|
| 长期记忆差、"一会就忘" | 事件溯源 + 逐字归档 + 多指针召回;"第一次见面说的话"能**逐字**索引回原文 |
| 长程指令遵循头痛 | 宪法 ≤10 条常驻 + 按需 reference + **机器强制**(hook/check),不靠模型记 600 行 |
| 污染 hermes 记忆 | 数据全在 skill `storage/`,零进 hermes 记忆;只漏一行书签 |
| 人设创建后定死、脸谱化 | **活体角色**:角色随事件投影演化;`check` 报告长期未演化 |
| 暗线没设计完就跑偏 / 埋了不 follow | 开坑**完整性闸门** + 触发器激活 + 调度器盯线 |
| 故事趋同、归于平淡 | **暗骰导演 + 随机表**:机械注入熵 + 主动点火节奏 |

**非目标**

- 不迁移旧本子(`isekai_venture` 等已封存,只读冻结)。
- 不做玩家行动的掷骰判定(纯叙事裁定)。
- 不引入向量库以外的重型外部服务(自包含单机)。
- 不改动 hermes 的记忆系统 / `SOUL.md` / Monika persona。

## 3. 架构总览

```
玩家输入(口语/命令)
   │
   ▼
┌──────────────────────────────────────────────┐
│  LLM(DM)— 自主叙事                            │  ← autonomy
│  上下文 = pre_llm_call 注入的 working_memory   │
│  前台输出 = 纯小说正文(OOC 走 / 命令)          │
└───────────────┬──────────────────────────────┘
                │ ① post_llm_call hook 机械捕获正文(逐字, 排除OOC) → archive
                │ ② 模型: rpg log-event(状态增量, 封闭枚举)
                ▼
        events(SQLite, append-only)  ◄═══ 唯一真相源
                │  rpg project(纯函数 fold)
                ▼
   state · characters(活体) · threads · timeline · promises · villains · pacing  (投影/缓存)
                │  rpg compact(弧/阈值)
                ▼
        working_memory.md ──► 下一回合(pre_llm_call 注入)

  旧细节:  rpg recall "q" = 向量(sqlite-vec) + 硬索引 + 锚点 → 同源逐字原文
  每场景:  导演暗骰 → 双轴事件(隐形埋线 / 前台即时 / 暴击)
  边界:    rpg check = 完整性闸门(暗线/角色/反派/承诺/时间线)
  纠错:    /oops /retcon /veto /steer = rewind + reproject(倒带前确认)
```

## 4. 记忆子系统(事件溯源核心)

### 4.1 事件日志 — 唯一真相源

- **只追加**:事件只增不改不删;"改"是漂移之源。纠错用 retraction 标记(见 §5)。
- **唯一真相源**:所有其它文件都是它的派生投影,**不可能互相矛盾**,且 `rpg project --rebuild` 可整体重算。
- **载体**:canonical 存 **SQLite**(`day/actor/thread/type` 索引 + FTS5 全文),查询代价与日志长度无关;并导 `events.jsonl` / 按弧 markdown 作**可读镜像**(给 git/人看,SQLite 为权威源)。

**事件 schema**(每条小而结构化):

```json
{
  "id": "ev_00427", "arc": "arc05", "day": 97, "scene": "s0181",
  "type": "relationship_change",
  "actors": ["雷德", "艾拉"],
  "summary": "雷德替艾拉挡下一击重伤;艾拉第一次说『别再为我拼命』",
  "deltas": { "艾拉.trust": "高→极高", "艾拉.flags+": ["认清心意"] },
  "thread_refs": ["th_ella_romance"],
  "chunk_ids": ["c_arc05_181"],
  "secrecy": null,
  "roll": null
}
```

**事件类型(封闭枚举)** — 约束 LLM 的发事件粒度,**不准记流水账**:
`action` · `dialogue_beat` · `relationship_change` · `character_reveal` · `character_development` · `thread_open` · `thread_advance` · `thread_resolve` · `promise_made` · `promise_kept` · `world_fact` · `combat_result` · `item_change` · `level_change` · `location_change` · `villain_knowledge_gain` · `player_choice` · `landmark` · `oracle_roll` · `director_fired`

### 4.2 投影(projection = 纯函数 fold)

```
rpg project:
  state ← 最近弧快照(checkpoint)
  for ev in 快照后的事件(按 day,scene 排序): apply(state, ev)
  写出 state.json / characters/* / threads/* / timeline / promises / villains / pacing
```

`apply` 分发表(事件 type → 改哪个投影):

| type | 投影动作 |
|---|---|
| `relationship_change` | 角色信任/关系更新 **+ 追加演化日志** |
| `character_development`·`reveal` | **改写角色人设字段** + 演化日志(→ 活体角色) |
| `thread_open` | 暗线建档(缺 终点/节点/揭示条件 → `check` 拦) |
| `thread_advance`·`thread_resolve` | 暗线 progress/clues/状态更新 |
| `promise_made`/`promise_kept` | promises 加/销 |
| `villain_knowledge_gain` | villains 知情 +1(缺 来源/渠道/延迟 → `check` 报作弊) |
| `level/item/location_change` | `state.json` 数值 |
| `director_fired`·`oracle_roll` | `pacing.json` 节奏状态 |
| 任意 | `timeline` 追一行 `(day, 一句话)` |

性质:**确定性、可重放、幂等**。`state = fold(snapshot, events)` → 不可能漂移。

### 4.3 工作记忆(每回合必载,小且封顶)

- `working_memory.md`:当前场景 + 近况 recap + 在场角色一行现状 + 活跃明/暗线下一拍 + 未兑现承诺 + 反派能力边界 + 世界铁律。
- 由 `rpg compact` 重生;由 **`pre_llm_call` hook 自动注入**模型上下文 → 模型不可能"忘了读"。

### 4.4 归档与粒度(逐字 · 无损)— 解耦"捕获"与"切片"

| | 捕获 capture | 切片 + 索引 index |
|---|---|---|
| 谁做 | **机械**:`post_llm_call` hook 抓每回合正文,逐字落盘 | **语义**:LLM/embedding 切片 + summary + 元数据 + 向量 |
| 时机 | 实时 | **事后批量**(compact 时,不在叙事热路径) |
| 可变? | 不可变(immutable 真相) | 派生·可重建(`rpg reindex` 重切,原文不丢) |

- **捕获只存故事正文**,排除 OOC:`/` 命令、`/dm` 讨论、OOC 不归档;玩家**口语指令不进档**,但改变 canon 的指令落成 `player_choice` 事件。
- **多指针 → 同一原文块**:向量索引(语义)+ 硬索引(人物/时间/地点/线)+ 事件 refs + 锚点 landmark,全部解析到同一**逐字原文 block**。block = `{指针→原文span, summary, 时/地/人/动作, 向量}`,其四元数据本身即硬索引 key。
- **锚点 landmark**:first_meeting / 表白 / 承诺 / 死亡 / 背叛 / 反转 等高光时刻显式 pin,直存 `chunk_id`;LLM 标 + harness 自动候选兜底。

> 验收用例:"你还记得第一次见面我说的话吗" → 查 first_meeting 锚点 → `chunk_id` → 取**逐字原文** → 模型真看到原话再推进。**全程不依赖 compact 的运气。**

### 4.5 召回 `rpg recall "<query>"`

三路混合,按问题类型路由,**只回相关切片**:

- 要原话/某场景 → 向量 + FTS + 实体过滤 → 逐字 block
- 要现状 → 读投影(characters/threads/...)
- "还记得吗" → 锚点 → block
- 向量库:本地 **sqlite-vec(或 FAISS)单文件** + 本地中文 embedding 模型(bge-m3,fastembed/ONNX 优先);FTS+结构化为零依赖兜底。

### 4.6 rollup / 快照(热区有界,防退化)

- **热区** = 当前弧事件,全细节,小。
- **弧光收尾** `compact`:① 把该弧细粒度事件压成弧摘要,原始事件转**冷存**(只标冷不删,`recall` 可翻);② 给 state/characters/threads 打**快照**。
- 重算状态 = **最近快照 + 只重放当前弧**,永不从 ev#1 重放 → 热查询集恒为"一条弧"大小。
- **散文块从不 lossy 压**:永远逐字躺在 FTS 里,Day500 也能一跳召回。

## 5. 纠错 / 倒带(rewind + reproject)

事件溯源的红利:投影是纯函数,**回滚状态不用手动撤**——丢掉那回合的产物、重投影,状态自动回到从前。

| 命令 | 语义 | 实现 |
|---|---|---|
| `/oops <更正>` | 倒带**上一回合**,带更正重叙(类 btw) | `rpg rewind <turn>` |
| `/retcon <到某场景>` | 回到更早某点,丢弃其后,重走 | `rpg rewind` |
| `/veto`(或口语"刚那段不要") | 抹掉刚才那个导演事件连同所在回合,重走 | `rpg rewind` |
| `/steer <方向>` | **软转向**:不撤销,只让后续往指定方向走 | 后台指令注入 |

- 底层原语 `rpg rewind <turn>` = 把 ≥该回合的 事件+原文块 标 `retracted`(append-only 友好,留审计)→ reproject。
- **倒带前确认**:口语识别到纠错意图 → 先问"要倒带到 X 重叙吗"再执行;显式命令保底。
- 跨**已 compact 弧**的深度 retcon:冷数据永远保留 → 总能重建,只是要重算该弧快照(慢);退旧弧时提示"将重建该弧"。
- 默认倒带按原 roll 重放(纠正"理解歪",命运不变);`/retcon --reroll` 连命运一起换。

## 6. 导演 / 神谕子系统(涌现内容)

根因:LLM 的**趋同(mode collapse)**+**被动(归于平淡)**。解法:harness 掷骰给具体种子,LLM riff。

### 6.1 开坑 `rpg seed <genre>`(老虎机式,可逐项重 roll 再锁定)

- 掷**世界框架**(基调/中心冲突/势力数)
- 掷 **3-5 条线**:每条 `{类型 × 速度(快/中/慢) × 与他线纠缠 × 固定结局假设 × 关键节点 × 揭示条件}`,全过 §8 完整性闸门
- 掷**开局 NPC**:`role × 动机 × 秘密 × 关系钩 × 特质组合`(随机特质组合治脸谱化 + 趋同)
- 掷**主角钩子** + 1-2 条藏在主角身上的暗线
- LLM 把种子编织成开场 + 写世界圣经

### 6.2 暗骰节奏引擎(全程屏风后,玩家看不见)

- **单位**:每**场景**检定一次(场景边界 = `location_change`/时间相变化事件;若拖过 N 回合强制检定)。
- **暗骰**:每场景暗掷 d100,旱涝调节 + 冷却,**概率带 30%→60% 封顶**:

| 距上次事件 | 触发概率 |
|---|---|
| 刚出过(冷却 1 场) | ~15% |
| 1 场 | 30%(基础) |
| 2-4 场 | +6%/场 → 36/42/48% |
| 5 场 | 54% |
| ≥6 场 | **60% 封顶** |

- 计数器与骰子全隐藏 → 预测不到;旱涝防长期冷场,冷却防扎堆。数字皆 `data/oracles/` 可调旋钮。

### 6.3 双轴结果(都由暗骰决定)

```
              量级轴(暗骰 + 旱涝 + 高潮阈值)
        小 ──────────── 大 ──────────── 暴击
类型轴  隐形埋线 │ 小钩子    大伏笔        史诗级伏笔   ← 前台永远无感,随时可埋
(暗骰)  前台即时 │ 小复杂化  突发事件      大成功/大失败 ← 受张力门控
```

- **隐形埋线**前台无感 → 无害 → **重要时刻也能埋**。
- **前台即时事件**受张力门控,且**不是硬抑制,是抬高"高潮阈值"**(crit 逻辑):张力越高,前台事件要掷越高才落地;**暴击级照落**(挡不住的高潮 twist)。
- **暴击区** = 大成功(机遇/强援/突破)或 大失败(背叛/伏击/重创),正负皆可。

### 6.4 隐形埋线(纯休眠)

- 暗骰决定"埋线" → 后台**登记休眠暗线 + 隐藏激活触发器**(到某地/某关系度/某物),**前台此刻什么都不显**。
- 日后玩家**撞到触发器** → 暗线首次浮现、开启。
- 休眠暗线**照样过 §8 完整性闸门**(埋时强制填终点/节点/揭示)→ 不跑偏,只休眠。激活靠 trigger + 调度器盯 → 不掉线。

### 6.5 多线调度

- 每条线带 `速度 + 上次推进 + 触发条件`;导演每场算各线**该推度 = f(速度, 几场没推, 条件满足, 随机)**。
- **只推最该推的一条(或都不推)**,防同时涌 / 防集体烂尾;把选中线下一拍浮给 LLM。
- **线池变薄自动开新线**(防枯竭)。明线 = `visibility=可见` 的线,进工作记忆。

### 6.6 随机表 `data/oracles/`

- 默认表包,**按 genre 分**(isekai/都市/悬疑…),**可加权** → 受控的随机(偏向用户口味)。
- 旧 SKILL 的暗线 schema / 突发事件类型 / NPC 管理规则 → 全部变成**表内容 + 调度逻辑**(不浪费)。

## 7. 指令遵循重构

**三层文档**(把规则从"模型必须记住"迁走):

| 层 | 内容 | 在不在上下文 |
|---|---|---|
| **宪法**(≤10 条铁律) | 玩家定大方向 / 前台只出正文 / 叙事后必发事件 / 反派非全知 / 冲突以投影为准 … | **常驻** |
| **按需 reference** | 日轻风格、暗线 schema、角色塑造经验(原 REFLECTION)、修罗场写法 | 开本/recall 时载 |
| **机器强制** | 暗线完整性、反派全知、角色演化、时间线、承诺 | **不进 prompt**,`check`/hook 兜 |

**回合协议**(大半被 hook 自动化,模型只剩 2 件事):

```
① pre_llm_call hook → 自动注入 working_memory + 未结 recall   〔自动〕
② 模型:输出小说正文(前台)                                  ← 模型
③ post_llm_call hook → 自动逐字归档本回合正文                〔自动〕
④ 模型:rpg log-event '<状态增量>'                           ← 模型
   └ stop hook:是叙事回合却没 ④ → 拦回去补                  〔自动〕
⑤ 边界:rpg compact / rpg check                            〔自动/提示〕
```

## 8. 完整性闸门 `rpg check`

何时跑:`compact` 时自动 / 手动 / `log-event` 时即时(反派守卫)。输出一份边界体检报告:

| Linter | 级别 | 治痛点 |
|---|---|---|
| 暗线开坑完整性(终点/节点/揭示条件/速度) | 🔴拦 | 暗线跑偏 |
| 暗线 follow 体检(N 场未推进) | 🟡提示+下一拍 | 暗线掉线 |
| 角色演化体检(卷入 N 事件未更新) | 🟡提示+建议 | 人设定死 |
| 反派全知守卫(知情无来源/渠道/延迟) | 🔴拦 | 反派全知 |
| 承诺老化 | 🟡提示 | 承诺遗忘 |
| 时间线矛盾(日期倒错/死人行动/瞬移) | 🔴拦 | 时间线乱 |
| 工作记忆落后于投影 | 🟡自动刷新 | 状态不同步 |
| 悬空引用(thread_advance 指向未开线 / promise_kept 指向未知承诺) | 🟡提示 | 纠错残留 |

## 9. CLI 接口(模型通过 Bash 调用的确定性脚本)

```
rpg new / seed <genre>      开本 / 掷开局骨架(可重 roll)
rpg log-event '<json>'      追加事件(叙事后)
rpg project [--rebuild]     从事件流重算投影
rpg compact                 重算投影 + 重生工作记忆 + 弧快照
rpg recall "<query>"        三路混合召回
rpg check                   完整性闸门体检
rpg roll / oracle           即兴熵注入
rpg director                每场景节奏检定(暗骰)
rpg thread spawn|advance    暗线开/推
rpg npc spawn               抽新 NPC 种子
rpg rewind <turn>           倒带原语(/oops /retcon /veto 走它)
rpg status / pin            状态 / 防 curator 归档
rpg reindex                 重切语义切片 + 重建向量索引
```

## 10. hermes 集成与改动

- **隔离(零污染)**:数据全在 skill `storage/`,hermes `memory()` 与 curator 均不扫 → 完全不进 hermes 记忆。
- **书签(可关)**:每活跃本一行 `🎲 本名 · 当前弧 · 同伴 · storage 路径`,供 Monika 自然提"要不要继续",不灌剧情。
- **hermes 改动(极小、自限定)**:
  1. `config.yaml` `hooks:` 加三条 → 指向 skill 的 `pre_llm_call`/`post_llm_call`/`stop` 脚本。
  2. 首次授权(`shell-hooks-allowlist.json`)一次。
  3. **关键自限定**:每个 hook 脚本第一步检查"当前 session 是否在跑团",否 → 立即 no-op 退出 → 日常 hermes 零影响零开销。
  4. `rpg pin` 防 curator 90 天归档。
- **不碰**:hermes 记忆 / `SOUL.md` / Monika persona。

## 11. 行动判定 = 纯叙事

玩家行动成败由 DM 依虚构逻辑 + 当前状态裁定,**不掷玩家骰**。暗骰只服务导演事件。

## 12. 数据布局

```
rpg-engine/
├── SKILL.md                 # 宪法(≤10 条)+ 触发词 + 回合协议
├── reference/               # 按需文档(风格/schema/塑造经验/修罗场)
├── bin/rpg                  # CLI 入口
├── engine/                  # 确定性代码: events/projection/recall/director/check/rewind
├── data/
│   ├── oracles/<genre>/     # 随机表(可加权)
│   └── templates/
├── hooks/                   # pre_llm_call / post_llm_call / stop 脚本(自限定)
└── storage/campaigns/<id>/
    ├── events.db            # 真相源(SQLite + FTS + sqlite-vec)
    ├── events.jsonl         # 可读镜像
    ├── snapshots/<arc>/     # 弧快照(checkpoint)
    ├── projections/         # state/characters/threads/timeline/promises/villains/pacing
    ├── working_memory.md
    ├── archive/             # 逐字原文块
    └── index.md / NOTES.md
```

## 13. 迁移 / 清理

- 旧 `openclaw-imports/rpg-dm` 原样**冻结只读**(封存本子留作存档,置于 `_legacy/`)。
- 旧 ~2000 行死 Python(`core/` `dm/` `utils/`)移入 `_legacy/`,不删留考古。
- **不做数据迁移**:新系统只服务新本子。

## 14. 技术选型

- Python(**skill 独立 venv,不污染 hermes venv**) · SQLite(FTS5) · sqlite-vec(单文件向量) · **本地 bge-m3 embedding**(fastembed/ONNX 优先 → 免 torch;否则 sentence-transformers) · FTS+结构化兜底(无 embedding 也能跑)。
- 自包含、单机、离线、无外部服务、永不撞 API 余额墙。

## 15. 验收标准

1. **逐字召回**:Day200 问"第一次见面我说的话",`recall` 经锚点取回**逐字原文**,与当时一致。
2. **状态不漂移**:`rpg project --rebuild` 重算结果与增量投影一致;角色信任/暗线进度等任意时刻自洽。
3. **活体角色**:角色经历 N 个重大事件后人设字段确有演化;`check` 能报未演化。
4. **暗线**:开坑即完整(闸门拦不完整);休眠暗线撞触发器才浮现;`check` 报掉线。
5. **不趋同**:同 genre 连开两本,骨架(线/NPC/钩子)明显不同。
6. **不平淡**:连续 N 场无导演事件的概率符合 §6.2(连冷 6+ 场 ≈3%)。
7. **遵循**:无 `log-event` 的叙事回合被 `stop` hook 拦回。
8. **零污染**:跑团一整本后,hermes `memories/` 无任何剧情条目。
9. **纠错**:`/oops` 秒级倒带上一回合并正确 reproject。

## 16. 开放的实现细节(评审/实现时定)

1. ~~embedding 选型~~ **已落地:本地 `BAAI/bge-small-zh-v1.5`(fastembed/ONNX,免 torch)+ numpy-cosine 向量库**。
   - 促因①:GLM/智谱 `embedding-3` 集成已验证(key 通)但账户**余额为 0**(429/1113),弃 API 改本地;充值后可作可选后端。
   - 促因②:**`BAAI/bge-m3` 不在 fastembed 的稠密 `TextEmbedding` API**(混合稠密+稀疏模型)→ 改用 BGE 家族中文原生 `bge-small-zh-v1.5`(dim512,~90MB)。**已真验证**:近义 cosine 0.91 vs 无关 0.38。pluggable,可换 `jina-embeddings-v2-base-zh`(768)/`multilingual-e5-large`(1024),或 sentence-transformers 跑真 bge-m3(需 torch)。
   - sqlite-vec 暂未用(numpy 暴力 cosine 对单本几千块足够),留作日后加速。
2. `post_llm_call` / `pre_llm_call` hook 的精确 `extra` 字段(实现时验证助手正文字段名;兜底 = 模型 `rpg log-turn`)。
3. 新 skill 落位(建议 `~/.hermes/skills/creative/rpg-engine/`)。
4. 语义切片用 LLM-at-compact 还是 embedding-drift 分段(起步 LLM,可换)。
5. genre 默认表包先做哪几个。

## 17. 实现分期(6 期,每期独立可测)

| 期 | 子系统 | 状态 |
|---|---|---|
| **P1 事件核心** | 事件 store + schema + 投影 + CLI 骨架 | ✅ 已实现(见 `docs/plans/2026-06-15-phase1-event-core.md`) |
| **P2a 归档·召回·工作记忆** | 逐字块(FTS5 trigram)+ FTS/结构化/锚点召回 + working_memory + debug 基建 | ✅ 已实现(`docs/plans/2026-06-16-phase2a-archive-recall.md`) |
| **P2b 语义/向量召回** | Embedder(fake+bge-small-zh-v1.5)+ numpy-cosine 向量库 + reindex + 语义融进 recall | ✅ 已实现(`docs/plans/2026-06-16-phase2b-semantic-recall.md`) |
| P3 纠错/倒带 | rewind + reproject + /oops /retcon /veto /steer | 待 |
| P4 导演/神谕 | oracle 表 + 暗骰 + 双轴 + 休眠暗线 + 多线调度 | 待 |
| P5 完整性闸门 | `rpg check` linters | 待 |
| P6 遵循 + hermes 集成 | 宪法 SKILL.md + 3 hook + 部署 | 待 |
