# RPG 究极 Harness 引擎 — 设计文档(standalone / `app` 分支)

> 状态:**设计定版(brainstorming 完成,转实现计划)**。只记已拍板决策;提案在对话里讨论,聊定才固化。
> 起草 2026-06-17。基线 commit 4ffac0c。

---

## 0. 这是什么 / 为什么 standalone

`app` 分支把已完成的 `rpg-engine` 技能(`skill` 分支)**重做成自包容独立程序**:自拥 agent loop、自管上下文、自管记忆 retrieval。`skill` 分支留作稳定版。
**痛点**:① 长程指令/"一会就忘";② 记忆污染 hermes;③ 人设定死/脸谱化;④ 暗线没 follow/跑偏;⑤ 趋同/平淡;⑥ 无限膨胀。
**第一性**:宁缺毋滥、宁严勿松、宁重勿妥协——游戏不好玩,再 economic 也是垃圾。

---

## 1. 架构:事件溯源微内核 + ContextSystem 注册表

**微内核**只做通用机制:事件存取、纯函数投影、写校验、召回、上下文组装、史官 digest、+ 一张**系统注册表**。不含具体玩法。
**`ContextSystem` 接口**(每个具体系统实现、注册即生效):`schema`(喂校验)· `validate(decl,world)->errors` · `apply(event,state)->state'`(投影)· `recall(query,world)->hits` · `inject(scene)->fragment+affordance` · `digest_extract(prose)->decls`(乙策略)· `created_ids(section,decl)->ids`(本段将创建的 id,供同一 commit 跨段引用预登记)· 声明自己的 event-types/entity-types/tags。
核心只剩**路由**:校验/投影/组装/召回/digest 时按 owner 分发给各系统。新系统 = 实现接口 + 注册。**事件类型去中心化**:每个系统注册自己的 event-types(不再是闭集)。
**创意工坊两层**:L1 代码插件接口(现在)· L2 声明式 manifest(以后,YAML 不写码就能加/分享;接口预留可被编译)。
**de-risk**:从已设计系统反向提炼接口,先在地点+角色两个上跑通再迁其余。

---

## 2. 核心不变量

1. 事件日志唯一真相源,只追加;态纯函数投影派生,不手维护。
2. 一切世界变更走事件 → 可重放、可倒带、双时序可查。
3. 子 agent 只发事件,不直接改卡。
4. **cache 友好分层**:稳定大块(宪法/策略/世界设定)前缀走 cache,易变小块(召回/导演/玩家输入/待修结构体)放尾。
5. **主上下文圣洁 + 三层后台分工**:主 LLM 只创作,判断/打分/综合/记账卸后台,只收压缩结果。
6. **杠杆在输入侧**:文字呈现即不可改 → 后台只"消化(本轮→事实)+ 投喂(相关事实→下轮)",**不事后审计**。
7. **诱导不禁止**:少用硬规则定死创造力。
8. **每道严格闸配一份注入的 affordance**:规则约束,注入告知。
9. embedder:本地 `BAAI/bge-small-zh-v1.5`(fastembed/ONNX,无 torch)。
10. **可观测优先(用户定,S0 就要)**:每个 LLM 调用(主叙事 + 后台舰队 + 世界脉冲 + 校验修复)经 **Langfuse** 追踪(trace/span/prompt/token/cost);每个内核步骤可 debug-log(承 `RPG_DEBUG` 约定);**debug 模式**可 dump 组装上下文 / turn-commit / 校验-修复全过程——为"把复杂多 agent 系统调度起来"提供眼睛。
11. **写路径完整性**(实现期补全):每段校验器强制其 `to_events`/`apply` 解引用的全部字段 → **过校验 ⇒ explode+apply 必不崩**;`to_events`/`apply` 全程防御 `.get()`+缺字段跳过告警 → **投影永不崩于已存事件**(存档可重载)。脏数据在**校验源头**截断并打回,不靠下游兜底。
12. **LLM 边界容错(思考模型友好)**:provider 无关;思考型模型(reasoning 计入 `max_tokens`、非流式占用连接)→ 超时 300s、`max_tokens` 实例级可配(Zhipu 默认 32768、上限≈131072)、`complete_json` 容忍 ```json 围栏与外围散文。

---

## 3. 一回合:I/O 与上下文构成(最终参照)

### 3.1 输入输出全景
```
玩家 输入两种:① 角色内行动("我推开酒馆门")
              ② OOC 元指令("DM 这人可信吗?" / /retcon / 剧透)
   │
   ▼ [系统路由]
   ① 角色内 → 组装上下文(3.2)→ 主 LLM
   ② OOC    → 直接查 god-truth 回答 —— 带外,不进叙事/事件/主角知识
   │
   ▼ 主 LLM:  甲={prose, turn-commit}   乙={prose}→史官提取 turn-commit
   ├─ prose(纯主角 POV 叙事)──────────→ 玩家看到的(+ 逐字存档案)
   └─ turn-commit ─→ §11校验闸 ─→ 事件 ─→ 投影(新态)
                                          └→ 后台舰队(史官/演化/打分/reflection)→ 投喂下一轮
```
**边界**:玩家**只看到 prose**;turn-commit 全程后台。god-truth 只在 OOC 主动问时露面。

### 3.2 组装的上下文(按 cache 三层)
```
层1·稳定前缀(走 cache)
  宪法/DM人格/叙事风格 · 当前 TurnStrategy 指令 + 写作软引导(POV/护栏/具身转场) · 世界设定基底
层2·半稳定(场景/态变才变)
  当前场景:时间 + 地点卡 + 出口表(navigate affordance)
  在场角色卡(当前投影态) · 主角卡 + 背包(物品投影) + 可感知的目标/暗线
  认知·可写(POV):主角已知的相关事实              → 可自由写进正文
  认知·在场 NPC 的 knows bundle                     → 供把 NPC 演得只用 TA 所知
  认知·连贯护栏:场景相关的隐藏真相                 → 标"⚠️只约束·别写出·别让主角察觉"
  各系统 affordance:本轮可声明项
层3·易变尾部(每轮新,不 cache)
  近期窗口(最近 N 轮逐字,即时连续) · 召回(多指针→相关旧原文/旧事实,长程连续)
  导演本轮投喂(要 surface 的暗线 / 暗骰触发的世界内事件) · 玩家本轮输入
```

### 3.3 turn-commit 信封(主 LLM/史官的结构化输出,按系统分段)
```jsonc
turn-commit {
  narration,                                   // 散文(甲与 prose 一体;乙=prose,史官不重写)
  world:    { affected_places, level },        // 世界演化
  places:   [{ name, parent, level, kind, seed }],
  moves:    [{ who, to_place | travel_intent }],
  cast:     [{ who, 关系/演化 deltas }],
  items:    [{ item, from, to }],
  factions: [{ org创建 | 成员变更 }],
  knowledge:[{ learned_by, fact, via } | broadcast{ fact, audience } | endowment{ who, scope }],
}
```
§11 闸按段 dispatch 给各系统 `validate` → 拆成事件入日志 → 投影 + 后台。

---

## 4. 本体论事实图(共享基底)

**C 混合**(自建本体+双时序,图算法借 networkx)。实体:Person/Place/Object/Faction/Thread(地点也是实体,跨类型关系直接成立)。
**双时序事实**:event-time + ingestion-time;supersede/invalidate;挂 provenance;点时查询。
**弧光=supersession 不覆盖**:旧态盖 `event_time_end` 仍在库;当前卡只显当前态(不臃肿),历史全留可查;弧光=被取代事实序列+reflection 摘要;超长线 reflection 分层 compact。
**实体三层**:`tracked`(完整卡)/ `mentioned`(只在原文,零成本可召回)/ `retired`(冻结,可重激活)。

---

## 5. 地点系统(三级动态地图,世界空间脊柱)

三级:L1 国家 / L2 城镇·地形(`kind` 分) / L3 细节。两类边:纵向 containment + 横向 adjacency(travel_cost)。HNSW 风味导航(语义固定三级)。
- 两正交轴:`detail`(stub/full)⊥ `activity`(active/dormant,由 scope 派生)。
- 成本阶梯:风味提及→不建;声明性提及→stub;进入/成焦点→full。**树生长跟主角,脉动不长树**。
- 三原语:`ensure_place(...,seed)` 登记(只产 stub)/ `entity_moved` 位置事实 / `materialize_place` 细化(仅主角进入支)。
- stub 带 `seed`(lore 内核,full card 从它长出,治跑偏);无 seed 被 §11 打回。
- **不卸载**;dormancy 是出-scope 默认态;再访 last_update 距今够久 → 懒派生 catch-up。两时钟:last_access(轮次=注意力)/last_update(天=演化量)。
- 地图切换四件套:出口表注入 + `navigate(from,to)` 路由函数 + 移动=声明意图harness落地 + `location_staleness` 移动压力。

---

## 6. 角色系统(反脸谱 + 演化)

- **卡范式**(反脸谱不强制):主体=叙事化"这个人是谁"(必填,自由写);可选附挂、能显式"无"不扣分:往事、藏着什么吗(yes/no 默认 no)、当前目标(必填);显式反填提示(给纯粹人以权力)。**机械处严,创作处松**。
- **演化(治定死)**:态从事件投影(永不冻结);`character_staleness` 检测"活跃却没变"→注入演化压力;arc 由 reflection 合成;世界下沉给环境增量+促升新血。

---

## 7. 物品系统(Object 切片)
物品=Object 实体(tracked/mentioned);持有=双时序关系(`held_by` Person/`located_in` Place);态=双时序事实;易手=事件 `item_transferred`(过 §11);背包=投影。

---

## 8. 势力 / 组织系统(模板化结构实体)
把 Faction 升成有模板、可管理、可召回的结构实体——消除"工会几级拉不通"的规范骨架(分组/职级**定义一次**,以后归一)。
- **模板**(形状固定内容自由):身份(名/类型/领域/HQ+据点→Place)· 内部规范分组/职级(职级链或命名小圈,各组织自定义一次命名一次)· 名册(仅 tracked 成员+`member_of` 边)· 对外关系 · 可选 lore/秘密。
- 创建=结构化产出过 §11 闸;genesis 建开局相关的几个,其余懒建。
- 管理查询层:取组织卡、成员/职级查询、**职级名归一**、受众解析。
- **是知识索引,不是授权引擎**;只建载荷剧情的少数势力。

---

## 9. 认知系统(谁知道什么 — 信息不对称)

模型:**上帝真相 + 一层稀疏认知覆盖**。
- **知识=获得即粘的状态(`knows` 边),非算出来的 access**:事件获得、永久保留;`believes` 覆盖只在信念≠真相时建。→ "刚升级不全知""降级保留旧知"免费成立。
- **受众=规范实体引用**(faction/place/必要时命名群体节点),经实体链接归一("资深"="7级"→同一 `faction:冒险者工会`)→ **严格集合过滤**(`事实.audience ⊆ 角色.归属`),可靠、不漂、不建数值权力系统。
- **授权三入口(都是有意事件)**:广播(fire-time 点时,打标签+推当前持标签 tracked 成员)· 禀赋(出生/身份变更,LLM 拿组织结构当候选范围**判深浅**,只给 standing 背景、不给过去 episodic 秘密)· 1:1 告知。
- **公共事实(secrecy=public)免建模**;只对剧情要紧的事实 × tracked 角色建 knows。
- **写作 = 两桶 + OOC,无戏剧反讽**(跑团:玩家视角=主角视角,无独立观众):
  - **可写(POV)**:主角已感知/已知 → 自由写。
  - **连贯护栏**:主角不知、但约束本场景的隐藏真相 → 给但标"⚠️只约束·永不写出·别让主角察觉"(由相关性检索只挑场景相关的隐藏真相,非整个 god-truth)。
  - **在场 NPC 的 knows bundle**:供把 NPC 演得只用 TA 所知。
  - **god-truth 流向玩家唯一通道 = 显式 OOC 问 DM**(带外,不进叙事/事件)。导演**不开反讽**,只通过主角能亲历的世界内事件制造张力。

---

## 10. 世界演化 / 波状传播
回合末 turn-commit 的 `world/places/moves` 段驱动。
- 两条 cascade 轴(分开):纵向下沉(containment,圈定波及+计算,**不吃 depth-3**)+ 横向连锁(新 world_change,level+1,**depth≤3**,合并同区)。
- 下沉单元=地点节点(一次 LLM call:地点态 + 聚合民众 + 在场 tracked 环境增量 + 促升)。
- 全覆盖+剪枝(碰到的节点/tracked 都有 verdict,剪枝盖 roll-up 戳)。
- 并行 subagent(`max_subagents`);backstops:depth≤3 + breadth(≤N 区/回合)。

---

## 11. turn-commit 校验闸(driver,逐段 dispatch)
唯一写接口,严格契约:代码强校验、不合格打回让产出方补,绝不兜底。
- **A. Schema**:必填非空(含 seed)、类型/枚举/层级自洽。**B. 引用**:`affected_places`/`parent`/`to_place`/受众实体 须解析到已存在或本回合声明;`who` 须已知实体。
- **校验=driver**:turn-commit 各段分发给对应系统 `validate`。
- **打回—修复(agent loop,非无状态重试)**:首轮 `[system,user]`;每轮校验出错→把 `[{字段,错误类型,预设提示}]` 作为**下一条 user turn 追加进同一对话**,模型看着**自己上一版输出 + 精确错误**增量改,直到过校验。**N=6**(play 层可配 `--max-repairs`/`GLM_MAX_REPAIRS`)。甲=authoring 对话;乙=散文冻结后的"史官抽取"对话(repair 只重抽、不重写散文)。provider 经 `complete_messages(messages)` 支持多轮。
- **跨段引用(已实现)**:`ContextSystem.created_ids` 声明本段将创建的 id;校验期把它们预登记入图(try/finally 还原)→ 同一 commit"建地点+移动到它"可一轮过。对应 §11.B 的"本回合声明"。
- **形状契约 + 兜底**:派发前强制"段=对象数组",`owner.validate` 包 try/except → 畸形输入变**可修复错误**而非崩(配合不变量 11)。
- **分级兜底**:非核心项修不好→丢该项+留其余+记日志;核心修不好→整回合打回上报。
- 三道防线:`schema.py` 思路(事件类型,现去中心化)· 本闸(写入边界)· `check.py`(投影后完整性)。harness 自生成的 director/oracle 事件不过闸。

---

## 12. 后台 subagent 舰队 + 消化投喂
每回合**提交后**消化本轮、备好下轮。主 LLM 看不到过程,只收压缩结果。
- **三层分工**:① 代码(投影/校验/recall取候选/check/navigate)② 干活队(廉价模型、窄上下文:重要度打分/reflection/世界脉冲/rerank)③ 史官 digest。
- **史官 digest = 消化+投喂(取代事后审计)**:每轮产**双视图同源**——人物志(per-character 影响)+ 地方录(per-place 带日期编年)= 结构化事实 ⊕ 散文编年。连续性靠**预防**(事实当场提取、相关时 recall 投喂);漏报并进 digest(独立穷举)。
- **阻塞 vs 异步边界**(断点4):**阻塞集 = {投影,知识获得 knows 边,当前场景相关 cascade}**(否则下轮 bundle/场景态过期);**异步集 = {importance,reflection,远区 cascade,reindex,compact}**。
- **后台产出轻量校验**(断点5):cascade/脉冲产出的 verdict/新事实做 referential 必查(不走完整修复;失败丢弃+记日志)。
- 重要度:打分 subagent + 锚定 rubric + 代码地板分 + 批量异步(不求绝对精确,只求排序一致)。reflection 触发=**重要度累积**(非数次数);声明"纯粹"/刚反思的豁免。
- 暗线=前向调度(冷了/到期/漂移→下轮 surface),非审计。
- 省钱不伤好玩:后台廉价模型,叙事最强模型。

---

## 13. TurnStrategy:双策略 A/B(可插拔)+ compare
唯一区别=turn-commit 职责归谁,其余内核共享。
- **甲 `AuthorStrategy`**:主 LLM 一笔出 `{prose, turn-commit}`;史官只做地方录。
- **乙 `ExtractStrategy`**:主 LLM 只出 `{prose}`;史官产出 turn-commit。**史官调用须带【现有实体 id 清单】,散文指向已知对象时强制复用 id**(防身份割裂:乙盲抽易把同一主角/地点另造成新 id 污染实体图)。
- dial:甲—hybrid(只声明要点级移动/大节拍)—乙 连续轴。"transit 会不会不写"交 A/B 实测,不靠硬规则。
- **compare 开关(test 专用)**:每回合甲乙在同一回合前快照各跑 → 两份候选并排,用户挑一份 commit 成正史(默认对齐式),两套从正史继续。**后台只对被 commit 的正史跑**(断点6)。选择=偏好数据。

---

## 14. 时间模型
**无全局 tick**;漂移态按 last_update+跨度懒派生。链式起点由 AI 定(地理锚点,一般主角位置);跨度过短允许不发起链式。
- **tracked catch-up 一律懒性**(断点3):时间跳跃后**只对下一轮进入 scope 的 tracked 追**,其余留 last_update 等再相关;不"跑所有 tracked"。
- tracked 冲突:`last_update==now` 跳过;先链式下沉,再补在场/将进场 tracked。

---

## 15. 记忆 / 上下文(S2+S3)
两层(Letta 式)+ 递归压缩,分层遵 cache 友好。最底层=逐字原文(不含 OOC),切片做 summary+时空人动作标注,向量索引。多指针召回(FTS+结构化+路标锚点+语义),全解析回逐字原文。召回评分 recency×importance×relevance。双轨纠错+倒带前确认:`/oops //retcon //veto //steer`→`rpg rewind`。

---

## 16. 暗骰 Director(继承 `skill` 分支)
每场景隐藏 d100,30→60% 频带+cooldown;两轴 dormant_thread/front_stage/crit;张力闸高潮阈值。开坑 slot-machine 反趋同(3-5 DISTINCT 暗线+不重复 trait)。自主加人/突发靠 director 驱动。

---

## 17. 分解 S0–S5(围绕内核重排)

| 子项 | 内容 |
|---|---|
| **S0** | 微内核:事件库 + 投影/校验/组装/recall/digest 五 driver + `ContextSystem` 注册表 + **可观测层(Langfuse 追踪 + debug-mode 日志/上下文 dump)** |
| **S1** | 本体论事实图(基底)+ 首批注册系统:地点、角色、物品、势力、认知 |
| **S2** | 召回 + 评分 + reflection/compaction |
| **S3** | 两层上下文组装器(cache 分层、系统 fragment+affordance 注入、POV/护栏) |
| **S4** | agent loop + 后台舰队(三层)+ TurnStrategy 双策略+compare + turn-commit 校验修复 |
| **S5** | I/O 外壳 + LLM 客户端(compare 开关、模型分级配置) |

复用 `skill` 分支 16 模块(store/projection/archive/recall/embed/vectorstore/oracle/director/seed/check/rewind/compact…)。
**构建方式(用户定)**:**地基优先、一步步整完所有系统,不走可玩切片**(重点是把这种复杂系统调度起来,简化版无意义)。S1 内**先地点+角色立稳 `ContextSystem` 接口、再迁其余系统**(合理建序,非 MVP)。**甲乙双策略 + compare 纳入 S4**(从一开始就能 A/B)。可观测层(Langfuse + debug)随 S0 一起落,贯穿所有后续子项。

---

## 18. 待定问题(随对应子项解决,不挡 S0/S1)
1. L2 `kind` 枚举确切集合。
2. travel_time 是否喂"跨度过短跳过链式"闸。
3. 图库:**默认 networkx**(纯 Python、起步),Kuzu 留作规模化备选。
4. reflection 产物挂回事实图的确切形态(S2)。
5. **S5 I/O 形态**:v1 本地 CLI;OpenAI 兼容端点(hermes 远程调)以后。
6. 促升新 tracked 卡是否过暗骰。
7. L2 创意工坊 manifest 格式(以后)。

---

## 附:Git 护栏(每个实现/修复 agent prompt 必带)
禁止 `git init` / `rm -rf .git` / `checkout --orphan` / 删除 `_legacy`、`docs`;只允许最小增量编辑;"从头重建"+多分钟运行=红旗,需 controller 复核。见 `docs/INCIDENT-2026-06-16-git-reset.md`。
