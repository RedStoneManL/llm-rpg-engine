# 活世界层 — 实现路线图（roadmap）

> 回合引擎(微内核/事实图/6系统/严格门+agent-loop修复/甲乙丙/记忆原语)已稳。本路线图覆盖设计文档里"让世界在玩家周围自己活起来"的剩余系统,逐阶段实现。每阶段开工时再写该阶段的详细 TDD plan。

## 当前状态（2026-06-19）
- ✅ §9 信息视野(viewpoint/guardrail)：**已激活**(Phase A)。甲/乙/丙 prompt 暴露 knowledge 段 → knows 事实落库 → assemble_context 渲染 [pov]。glm-4.7 实机验证:模型每回合自发用 knowledge 段、采用"实体.属性"键名,4 条 knows 事实入库,[pov] 块出现。
- ✅ §12 后台消化 digest_fleet：已接线(run_turn 后台跑,heuristic importance + 阈值 reflection + reproject)。
- ✅ Langfuse：**Phase A 完成**——`_do_post`(所有真实 LLM 调用咽喉)包 generation span(归一 token/cost),run_turn 包 turn span,digest 嵌其下。无 creds 全 noop。
- ✅ §16 暗骰 Director：**B1+B2 完成**。B1 核心 fire→directive→inject(`systems/director.py` DirectorSystem + `loop/director.py` run_director,post-apply 钩入 run_turn,种子 campaign_seed 入 meta,走严格门),glm-4.7 实机验证:directive 落库 + 真叙事自然织入「危机/另有目的」转折(720字)。B2 休眠暗线 store(thread_open/thread_advance 投影)+ 反趋同 seed_threads(3–5 条 distinct,trait/archetype 不重复)+ dormant 调度(复用 pick_thread_to_advance)+ 暗线浮现 directive。修了 never-two-in-a-row 的 off-by-one(实机发现)。
- ✅ 稳定性修复(Phase A 跟进):实机诊断证明 repair churn 是 prompt 缺口而非 bug——结构 prompt 没给 entities/facts/relations 形状、没列 place.kind 枚举,reasoning 模型据此瞎猜(kind='ruin'、facts 缺 subject/predicate/value)被严格门正确打回。补全形状+枚举后实测 **repair 6→0**(glm-4.7,2 回合)。`4f63cc6`。
- ✅ §10 世界演化 cascade：**C1+C2 已实现**(698 tests green)。C1 纵向下沉(`systems/cascade.py` CascadeSystem 拥有 place_evolved/populace_shifted/world_change + `loop/cascade.py` run_cascade,post-apply 钩入 run_turn(director 之后),eager 触发,per-round breadth cap 6,轻量校验丢弃,per-node try/except,cheap cascade_provider(RPG_CASCADE_MODEL),harness-owns-id)。C2 并行 fan-out(ThreadPoolExecutor,可配 RPG_CASCADE_CONCURRENCY 默认3)+ 横向连锁(adjacent_to∪siblings,level+1,depth≤3,region cap 3,merge 同区)+ 事件溯源懒延迟队列(world_change deferred 标记→queue,drain-at-start,consume watermark)+ 自触发 guard(deferred world_change 不再触发)。
  - **C1 实机验证(glm-4.7)✅**:山谷山洪→village/mill/shrine 纵向波及,verdict 合理。**C2 实机:纵向并发✅;横向 spread 未触发**(模型对局部事件保守不 spread)→ 已补"邻近区域注入 ctx"(`dfe38a0`),但**尚未实机确认会触发**(撞 session 限额)→ 下次 `python3 /tmp/verify_c2.py` 确认横向 spread+defer+drain。
- 实机捕获并修复:三个"功能空转"bug(单测 fake 掩盖真实模型行为,实机验证是关键)——(1) director 静态场景种子冻结(salt=turn);(2) cascade 实机 0 事件,真实模型省略 verdict 的 id(harness 注入 place_id);(3) cascade 跑在叙事大模型而非廉价模型(cascade_provider 接线)。
- ✅ §14 时间模型(D)：**已实现**(744 tests green)。无全局 tick;`arrive_day`(moves 上 opt-in)推进 `meta.day`;`detect_jump`(≥2 天=跳跃);`last_update` 戳在 entity.attrs(随 project 重建,rewind-safe);`run_catchup`(进入 scope 的 stale tracked 懒补,≤4/回合,cheap model)钩在 cascade 之后;新 `TimeSystem` 拥有 `time_advanced`。**修了 catch-up wired-but-inert**(`run_turn` 原传 prev_scene=scene 致空转 → 接 play_loop 的 prev_scene 追踪)。`loop/time.py`。
- ✅ §15 倒带(E)：**已实现**。`app/engine.py` `rewind(engine, turn)`=`store.retract_from_turn` + reproject;`last_turn`。OOC：`/rewind <n>`、`/undo`(=`/oops`)、`//retcon <n>`、`//veto`、`//steer`(v1 占位)。事件溯源天然支持,不复用 legacy `engine/rewind.py`(那个建在 legacy archive/compact 上)。
- **活世界层 A–E 全部实现完毕(744 tests)。** 唯一遗留:C2 **横向 spread 实机不触发**——机制+接线+单测都全,但 cheap model(glm-4.7)对"区域灾难蔓延到邻region"一律判 spread:false(根级决策也试过)。这是模型判断,非代码 bug → **待决:启发式自动 spread(大灾难+有邻) vs 用强模型做 spread 决策 vs 接受少触发**。D catch-up 实机验证见 `/tmp/verify_d.py`。

### Phase A 完成记录（2026-06-19）
- item 1 knowledge 段：`loop/strategy.py` 三套结构 prompt + `tests/loop/test_knowledge_wiring.py`(端到端:声明→knows→pov)。
- item 2 Langfuse：`kernel/observability.py` 加 `generation()` ctx-mgr;`llm/provider.py` `_do_post` 包 span + `_record_usage`(归一两种 token 方言);`loop/turn.py` turn span。
- item 3（双视图落盘）：暂缓(可选,非阻塞)。
- 遗留观察:reasoning 模型每回合 repair 轮数偏高(实机 T2=4,均收敛 0 丢弃)——待查严格门反复打回的具体段落,作为 Phase A 之后的健壮性跟进。

---

## Phase A — 激活已建能力（小，先做）
**目标**：让"接了线但空转"的真正产生数据。
1. **暴露 knowledge 段**：在甲/乙/丙 prompt 的可选段里加 `knowledge`（谁知道了什么），让 LLM 能记录信息获取 → knows 事实落库 → §9 视野不再空转(主角/NPC 各自所知、guardrail 真正生效)。校验器已支持该段。
2. **full Langfuse 接线**：在 provider.complete_messages / complete 外包 `get_tracer().span("llm", model, tokens)`;turn 级包一个 "turn" span。无 creds 仍 noop,有 creds 即得完整 trace/token/cost。
3. （可选）digest 产出的**双视图**(人物志/地方录)落盘到 campaign,供调试/续期。

## Phase B — §16 暗骰 Director（中大；"世界主动出事"）
**目标**：世界自己推进,不只被动响应玩家。
- 每场景隐藏 d100 + 频带(30→60%)+ cooldown；张力闸高潮阈值。
- 两轴：dormant_thread（休眠暗线,冷了/到期 surface）/ front_stage（当前推进）/ crit。
- 开坑 slot-machine 反趋同(3–5 条 DISTINCT 暗线、不重复 trait)。
- 产出 `director_fired`/`oracle_roll` 事件(importance.py **已为这俩留了打分权重**,目前悬空)→ 走严格门 → 注入下一回合上下文(自主加人/突发)。
- backstop：频带 + cooldown + 张力阈值(防过密)。
- 落点：新 `loop/director.py` + 一个 DirectorSystem 或 play 循环钩子;continues `skill` 分支的暗骰思路。

## Phase C — §10 世界演化 / 波状传播 cascade（大；旗舰）
**目标**：一个事件的影响**自动波及**嵌套地区/在场 tracked,世界连成一片。
- 两条轴(分开)：**纵向下沉**(containment,圈定波及+计算,不吃 depth-3)+ **横向连锁**(新 world_change,level+1,**depth≤3**,合并同区)。
- 下沉单元 = 地点节点(一次 LLM call：地点态 + 聚合民众 + 在场 tracked 增量 + 促升)。
- 全覆盖+剪枝(碰到的节点/tracked 都有 verdict,剪枝盖 roll-up 戳)。
- 并行 subagent(`max_subagents`)；backstop：depth≤3 + breadth(≤N 区/回合)。
- 阻塞 vs 异步(断点4)：当前场景相关 cascade 阻塞,远区 cascade 异步。
- 落点：`loop/cascade.py`,turn-commit 的 `world/places/moves` 段驱动;产出经"后台轻量校验"(referential 必查,失败丢弃)。

## Phase D — §14 时间模型（中）
**目标**：时间跳跃后的懒性派生,不"跑所有 tracked"。
- 无全局 tick;漂移态按 last_update+跨度懒派生。
- tracked catch-up 一律懒性(断点3)：只对下一轮进入 scope 的 tracked 追。
- 落点：facts 加 last_update;assemble/cascade 时按需 catch-up。

## Phase E — §15 倒带 / OOC retcon（中）
**目标**：可纠错、可倒带(事件溯源天然支持)。
- OOC：`/oops //retcon //veto //steer` → `engine.rewind`(截断/重放事件日志到某 turn)。
- 落点：`app/engine.py` rewind + `app/play.py` OOC dispatch 扩展。

---

## 建议顺序与理由
**A → B → C → D → E**。
- A 最便宜、立刻让"视野"和"观测"真正工作。
- B(director)让世界主动,且 importance 已为其留位,接入顺。
- C(cascade)最重、最旗舰,放在 director 之后(两者都产事件,共用严格门+digest,先把事件流跑顺)。
- D/E 收尾(时间、纠错)。
- 每阶段：先写详细 TDD plan → subagent 实现 + 评审 → 真模型实跑验证 → 提交。
