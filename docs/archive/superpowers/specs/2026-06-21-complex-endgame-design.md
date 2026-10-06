# 复杂线终局 + 世界救场掷骰 设计 (Complex-Line Endgame, L4)

> 2026-06-21 · rpg-engine-app `app` 分支。统一事件线/任务系统的终局层:**没人管的复杂暗线,世界自己给它一个了断**——要么被世界其他力量化解,要么酿成封顶在本地区的终局灾难。消费 lifespan 的 `pending_finale`,释放 region 的 complex 上限。
> 前置已就绪:统一 quest(暗/明/了结)、暗骰 `run_lore`(逐 stage 推进 + 复杂线末 stage world-push surface)、lifespan(复杂暗线寿命到 → `pending_finale=True`,留给本层)、density(complex 上限 ≤2/region,了结即释放)、cascade(`world_change`→区域演化,已 breadth/depth/budget 封顶 + harness 校验)、`Oracle/scene_seed` 确定性掷骰。
> 缘起:用户早先定的"复杂线有 checkpoint 每个掷骰但不要上来就骰,解决阈值前低后高渐进式(不然全 npc 解决),要有顶点爆炸范围,世界里其他人也能解决,掷骰失败一个结局成功解决另一个结局"。
> 配套:`2026-06-21-quest-lifespan-design.md`、`2026-06-21-density-generation-design.md`、cascade。

## 目标
玩家不介入的 complex 暗线,有三条出路收口:① 世界其他力量在某个 checkpoint **救场成功** → 悄然了结;② 一路没人救、玩家也不接(暗骰末 stage world-push 成明 → 被弃 → 退暗 → 寿命到 `pending_finale`)→ **终局灾难**(封顶本 region 的 `world_change`,交 cascade 演化)→ 了结;③ 玩家介入(surface→明→resolve,现有路径)。无论哪条,complex 线都会**了结,释放 region 上限**。

## 设计

### 1. 世界救场 checkpoint(暗骰酝酿期,渐进式)
`run_lore` 推进一条 complex 暗线到新 stage 时(checkpoint),若 `stage_idx >= RESCUE_GRACE_STAGES`(**=1**,不在 stage 0 就骰):
- 掷 `Oracle(scene_seed(seed, f"rescue:{id}", stage_idx)).d100()`;**成功阈值随进度升高**(前低后高):`chance = RESCUE_BASE + round(stage_idx/(max(1,n_stages-1)) * RESCUE_RANGE)`(**BASE=10, RANGE=40** → 约 10%→50%,可调)。`d100 <= chance` → 救场成功。
- 成功 → emit `quest_world_resolved{id, by:"world_rescue", summary}` → apply 置 `state=了结`、`resolved={by:world_rescue, summary}`。**世界其他人解决了,玩家没赶上**。
- 失败 → 继续酝酿(下个 checkpoint 再骰,阈值更高)。
- **末 stage 不在这里骰**:complex 线到末 stage 仍走现有 **world-push surface**(`quest_surfaced{by:world}` → 明),给玩家最后的接手机会。渐进式保证:早期几乎没人救(威胁未显)、中后期救场概率升高,但只要没救成就会浮上水面给玩家。

### 2. 终局灾难(pending_finale,最后清算)
一条 complex 暗线 `pending_finale=True`(lifespan 寿命到、无人理会)→ `run_lore` 里做**最后一次救场掷骰**(last-chance,高成功率 `FINALE_RESCUE_CHANCE`=**60**,可调;`Oracle(scene_seed(seed, f"finale:{id}", day))`):
- 成功 → `quest_world_resolved{by:"world_rescue:finale", summary}` → 了结(惊险化解)。
- **失败 → `quest_catastrophe{id, summary, anchor}`**:
  - apply 置 `state=了结`、`resolved={by:catastrophe, summary}`。
  - **封顶本地区的爆炸**:emit 一条 `world_change{place: <region_scope(anchor)>, level: 1(或 anchor 的层级), summary}` —— 交给**现有 cascade**做区域演化(cascade 已 breadth/depth/CASCADE_NODE_BUDGET 封顶 + harness 校验,天然把爆炸范围限在本 region 邻域,不会蔓延全世界)。**失败的那个结局**。
- 灾难/救场的 `summary` 用**模板**(确定性、不加后台 LLM 调用):`secret`+`about` 拼一句中文(救场:「<about>:外力介入,事态平息」;灾难:「<about>失控,<secret>,波及<region>」)。**真正的丰富演化由 cascade 的 `_node_verdict`(LLM + harness 校验)产出** —— L4 不新增 structured LLM 返回,复用已合规的 cascade。

### 3. 事件 / 状态
- `quest_world_resolved`{id, by, summary} → LoreSystem apply:`state=了结`,`resolved={by, summary}`,`status="resolved"`,`pending_finale` 清除。replay 安全(已了结则 no-op)。
- `quest_catastrophe`{id, summary, anchor} → LoreSystem apply:同上 `by=catastrophe`;harness(run_lore)在 emit 它的同一趟**另 emit** `world_change{place:region, summary}`(若 PlaceSystem/cascade 注册了 `world_change`)。
- 两者都 LoreSystem `event_types()` 注册 + 状态守卫。
- complex 线一旦了结(任意 by)→ 不再被暗骰/checkpoint 触碰(`state!=暗` 守卫),且 density `count_tier` 不再计它 → **region complex 上限释放**(解决 density review 的已知限制)。

## 复用 / 影响
- `run_lore`(loop/lore.py)是唯一改动的编排点:推进 complex 暗线后插 checkpoint 掷骰;遍历到 `pending_finale` 复杂线时做 finale。掷骰/灾难逻辑抽到 `loop/endgame.py`(`world_rescue_chance`、`roll_world_rescue`、`build_catastrophe_events`)便于单测。
- `Oracle/scene_seed` 给确定性、可重放掷骰(key 区分 rescue/finale + stage/day)。
- catastrophe 复用 cascade(emit world_change),不另造爆炸机制;region 封顶由 cascade 现有的 caps 保证。
- lifespan 的 `pending_finale` 是 finale 的触发器(本层消费它)。

## 不在本期
- 救场/灾难 summary 的 LLM 润色(本期模板;cascade 已提供丰富区域演化)。
- 玩家"参与世界救场"的主动 roll(本期世界救场是 autonomous 的;玩家路径仍是 surface→resolve)。
- medium/simple 线的终局(它们 lifespan 到点直接了结,无灾难——只有 complex 有爆炸范围)。

## 复杂场景验证(实现后)
**离线(seeded Oracle,确定性)**:
- complex 暗线 brew:在 stage 0 不掷;stage≥1 按渐进阈值掷;构造 seed 使某 checkpoint 救场成功 → `quest_world_resolved` → 了结(by:world_rescue);构造 seed 使全程失败 → 末 stage world-push surface(现有)。
- `pending_finale` 复杂线:finale 掷骰成功 → world_resolved;失败 → `quest_catastrophe` + `world_change{place:region}` 一并 emit → 了结(by:catastrophe);确认 region complex 计数从满→释放(density `count_tier` 减一)。
- 确定性:同 seed 重跑 → 同救场/灾难序列(rewind 安全)。
- region 封顶:catastrophe 的 world_change 锚在 region_scope(anchor),cascade 不越出本 region 邻域(breadth/budget 封顶)。
- 状态守卫:已了结的复杂线不再被 checkpoint/finale 触碰(幂等重放)。
**(可选)真机 glm-5.1**:一条 complex 暗线无人管 → 看它要么被救场了结、要么终局炸成 world_change 后 cascade 把本镇/邻域演化成什么样(rich content 来自已合规的 _node_verdict)。

## 自检
- complex 线三条出路都收口到"了结",释放 region 上限 → 解决 density 已知限制。
- 渐进式阈值(前低后高)→ 不会上来就被 npc 解决,也不会全堆给玩家。
- 爆炸封顶 region(复用 cascade caps),不蔓延全世界 = 用户要的"顶点爆炸范围"。
- 掷骰走 Oracle/scene_seed → 确定性、rewind 安全。
- 不新增 structured LLM 返回(模板 summary + 复用 cascade)→ 不破坏 harness 一致性;backstage 成本不增。
