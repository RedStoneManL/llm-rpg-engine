# 事件线 / Lore 系统 设计 (Lore / Event-Line System)

> 2026-06-20 · rpg-engine-app `app` 分支。用户的**主目标**——"活世界"的心脏。
> 设计在 2026-06-18..20 与用户交互成形;**开放分叉由我在用户授权("没啥需要拍板的,接着推进")下拍定,逐条标注,备用户复核。**
> 地基已就绪:世界钟(`meta.day`+band,`kernel.clock.expired()`)、scene-progression、§9 KnowledgeSystem、cascade、place levels、DirectorSystem(暗骰范式)全部 built。
> 配套:[[rpg-app-lore-system-design]](memory)、`2026-06-20-world-clock-design.md`、`2026-06-20-scene-progression-design.md`。

## 一句话
万物皆"暗线/事件线":掷复杂度(70%简单/25%中等/5%复杂)生成 skeleton,每条独立每回合掷暗骰推进阶段、漏线索;离开地点的下两档休眠、复杂线静默发展直至(被封顶的)世界级后果;按 per-region 的 `lore_density` 围绕二级地图生成,带每档上限。点在于:小事件常见,所以异常不会一眼"剧透"出大任务。

## 已定决策(逐条;★=我替用户拍的)

1. **统一类型 + 复杂度分布**:每条线掷 **70% simple / 25% medium / 5% complex**。即便 simple 也带**线索**(织进环境叙事),无"纯布景"层。
2. **skeleton 创建时预生成**(LLM):`about` / `secret`(=一条 §9 guardrail 事实) / `complexity` / `stages`(阶段提纲) / `threshold`(暗骰速度) / 复杂线另加 `difficulty`+两套终局(success/fail)+`resolver`。**每阶段的丰富内容 JIT** 生成(到达时才写)。
3. **每条线独立、每回合掷**:廉价 d100+threshold(无 LLM);只有要"写一段 beat"时才 JIT 调 LLM。过阈值→推进一个 stage→漏线索/影响世界。
4. **世界影响=有机线索**:酿线的世界变化(走正常 event/cascade 管线)兼作玩家察觉得到、但不知所以的伏笔——须**微妙、可否认**。
5. ★**复杂度≈空间尺度**:simple/medium 锚在**二级地图(level-2 镇)**;complex 锚在**大地图(level-3 region)**。于是 simple/medium 是镇尺度(离开即休眠),complex 是区域尺度(离开仍酿、能波及区域)。
6. ★**休眠(下两档统一)**:离开锚定地点 → simple+medium 都**冻结**(停更新内容),但 **lifespan 照世界钟继续走**;走完→**清空**(错过了);未过期回到地点→**重启**(恢复更新)。(用户曾偏向"只 medium 回来还在";我选**统一**——更少特例、更稳;若用户要分化,simple 改"离开即清"即可。)
7. **复杂线静默发展**:离开也继续;**无干预最终也对世界有后果**,但**封顶**(见 9)。
8. ★**世界救场掷骰(分阶段拦截)**:复杂线每推进一个 stage,引擎替"世界其他人"过一个 check(`difficulty`)——过了→该线被**世界自己摆平**(温和/中性终局 success,可留远景"听说某镇的乱子被XX平了");一路没过、拖到终局→坏终局 fail 落地。早拦早收。(用户"可以")
9. ★**爆炸半径封顶**:复杂线 fail 终局最多波及**其锚定 region**(镇沦陷/势力易主/某地永久变样)——够痛、可恢复、**绝不毁灭世界**。引擎硬约束(impact 不出 region 子树)。
10. ★**密度生成**:`lore_density ∈ [0,1]`(per region,默认 0.3)。**首次进入二级地图 → 播种一批**(量∝density)。**定期刷新**:每条活跃二级地图在世界钟上每隔 `REFRESH_INTERVAL`(默认 1 游戏日)过一个密度门暗骰,过了→生成 `round(density × LORE_BASE)` 条新线。
11. **每档上限**(用户定 2026-06-20):per 二级地图 simple ≤ **15**、medium ≤ **8**;per region complex ≤ **2**。达上限不再生成该档。(稠密世界;靠下面的渐进披露防上下文淹没)
12. **secret = §9 guardrail**:每条线的 `secret` 写成 KnowledgeSystem 的 guardrail 事实(真but主角不知,约束叙事勿泄)。

## 复用 / 范式
- 每回合暗骰 ⟂ `DirectorSystem`(seeded d100,rewind-safe via `scene_seed(campaign_seed, ordinal, salt)`);lore 用 `(campaign_seed, line_id, turn)` 同样可重放。
- lifespan/到期/刷新间隔 ⟂ `kernel.clock`(`expired(born,lifespan,now)`、`to_units`)。
- 影响下沉 ⟂ `cascade`(world_change → 逐地演化);爆炸半径封顶 = 限制 cascade 的 region 子树。
- 线索注入 ⟂ inject(scene 层,director 式"本回合可织入的暗线线索")。
- 新建 `LoreSystem`(ContextSystem),owns lore 事件 + slice。

## 阶段分解(各阶段 = 独立可测的一块,逐个 spec→plan→build)

- **L1 · 核心 LoreSystem**(本 spec 详述,先建):事件线实体 + skeleton + **每回合分阶段暗骰** + 推进掉线索 + inject 本回合暗线 beat。创建走 harness 函数(密度生成留 L3)。
- **L2 · 生命周期 + 休眠**:lifespan(创建时 LLM 按游戏内时长定,挂 `clock.expired`)+ 休眠(离开锚地冻结、回来重启、过期清空)+ JIT 每阶段丰富内容。
- **L3 · 密度生成**:`lore_density` per region;首入二级地图播种;密度门刷新暗骰(世界钟);每档上限;复杂度 70/25/5 掷;skeleton 的 LLM 生成调用。
- **L4 · 复杂线终局**:爆炸半径封顶(region 子树内)+ **checkpoint 渐进救场掷骰**(见下)。

---

## 用户确认 + 细化(2026-06-20,review 后)
- **★5/★6/★8/★9/★10 全部确认**。**★6 休眠统一**(simple=medium)按原案。**★5 medium 锁单镇**(不跨 town;跨区域感交给 complex)。最小锚定 = 城邦(level-2),绝不 level-1(太碎,线索没处驻留/累积)。**★11 上限改 15/8/2**。
- **★8 细化 —— checkpoint 渐进救场**:复杂线的 skeleton 标几个 **checkpoint**(后段的若干 stage,**不是每个 stage 都骰**);**前段不骰**(玩家窗口期,世界袖手);到 checkpoint 才掷世界救场,且**救场成功率前低后高**(早:世界几乎搞不定→线继续走;晚:越来越可能出手),防"全被 NPC 提前解决"。一致性:**成功/失败都对世界有影响**(成功=世界摆平+轻痕/传言;拖到末 checkpoint 仍没成功=region 封顶坏结局),所以"复杂线最终总有痕"成立,曲线只决定轻痕 vs 重创。

## 渐进式披露 / trigger(2026-06-20,用户提出 —— 解决上下文淹没)
范式 = **skill manifest**:lore 线在上下文里默认只占一行,触发了才逐级加载。不是给 inject 打"上限"补丁,是分级披露架构(15+8 条线 = 一屏短索引,只有被触发的展开)。
- **skeleton 加字段**:`description`(一句话进索引)+ `trigger`(很短、**绑定具体地图**的触发条件)。两者随 skeleton 由 LLM 生成。
- **inject 两层**:常驻层 = 当前地点(+region)各线只给「短描述+trigger」紧凑索引(廉价);展开层 = **被触发的**线才加载当前阶段内容/线索/JIT beat。可多级(索引 → 当前阶段 → JIT 丰富 beat),同 skill→body→引用文件。
- **触发判定**:① 到绑定地图(便宜,引擎直接判)+ ② 线已酿到可冒头 stage。**v1 先纯地图+stage 闸**;**recall 语义匹配(玩家动作/话题对得上 trigger 描述)作为 L3 之后的增强**(待用户最终点头,默认如此)。
- **落点 = L3**(披露与密度是一对:密度造出很多线 → 才需要披露)。L3 生成 skeleton 带 description+trigger + 把 inject 换成"索引常驻 + 触发展开"。
- **对已建 L1 的影响**:L1 的 inject(现"线索一股脑塞")**降级为占位**,被本架构取代;L1 内核(实体/暗骰/事件)不动,不返工。

---

## L1 详细设计(先建这块)

### 数据
`world["systems"]["lore"] = {"lines": { <line_id>: {`
- `complexity`: "simple"|"medium"|"complex"
- `about`: 一句话主题
- `secret`: 真相(写成 §9 guardrail;L1 先存字段,L2/L3 接 KnowledgeSystem)
- `anchor`: 锚定 place id(simple/medium=level-2,complex=level-3 region)
- `stages`: [{"hint": 该阶段漏的线索一句话, "impact": 可选的世界影响描述}]  (skeleton 预生成的提纲)
- `threshold`: int 0..100(暗骰速度;越高越快推进)
- `stage_idx`: int(当前阶段,起始 -1 = 未起)
- `status`: "active"|"resolved"|"expired"
- `clues_dropped`: [str]  (已漏线索,供 inject + 防重复)
`}}`

### 事件(harness-authored,无 commit 段)
- `lore_created` — deltas = 整个 skeleton({id,complexity,about,secret,anchor,stages,threshold})。apply: 建 line(stage_idx=-1,status active,clues_dropped=[])。
- `lore_advanced` — deltas = {id, stage_idx, hint}。apply: 置 line.stage_idx、append hint 到 clues_dropped。

### 创建(L1:harness 函数)
`create_lore_line(store, skeleton, *, day, scene, turn) -> event`:校验 skeleton 必备字段 → append `lore_created`。(L3 用密度生成 + LLM 产 skeleton 再调它;L1 测试直接构造 skeleton。)

### 每回合暗骰(post-apply fleet 函数,镜像 `run_director`)
`run_lore(registry, store, world) -> list[event]`(在 `run_turn` 里 director/cascade 之后、非阻塞 try/except,guarded by `registry.owner_of_event("lore_advanced")`):
对每条 `status=="active"` 的 line:
1. seeded 掷骰 `roll = d100(seed(campaign_seed, line_id, next_turn))`(rewind-safe,无 LLM)。
2. `if roll <= threshold and stage_idx+1 < len(stages)`:推进 → `stage_idx += 1`;取 `stages[stage_idx].hint`;emit `lore_advanced{id, stage_idx, hint}`。
3. 到末阶段(`stage_idx+1 == len(stages)`):L1 暂置 `status` 不变(终局 resolve 是 L4);只是不再推进。
(每回合最多推进一档;简单、廉价、确定可重放。)

### inject(scene 层,微妙)
`LoreSystem.inject` → Fragment:把**当前场景锚定地点**的活跃 line 的**最新线索**汇成一句"〔可织入的环境暗线〕…"提示(director 式,给叙事模型当弹药),**不点破**是任务。只列锚在当前 place(或其 region)的线,避免泄漏远处的线。L1 先列 anchor==当前 location 的;跨 region 过滤留 L3。

### L1 不含(留后续)
lifespan/休眠/过期(L2);密度生成/上限/复杂度掷/LLM 产 skeleton(L3);世界救场掷骰/爆炸半径/终局(L4);JIT 每阶段丰富内容(L2)。L1 的 hint 用 skeleton 预置的提纲串(离线可测)。

### L1 测试(离线、确定性)
- `LoreSystem`:owns lore_created/lore_advanced、无 commit 段、requires ontology;apply 建线/推进、append clue。
- `run_lore`:高 threshold(=100,必过)→ 每回合推进一档直到末阶段即停;低 threshold(=0,必不过)→ 永不推进;seeded 可重放(同 seed 同结果)。rewind:重投影复现 stage_idx。
- guard:registry 没 LoreSystem → `run_lore` no-op。
- inject:活跃线的最新线索进 scene 层 Fragment;无活跃线 → None;只列当前 location 锚定的线。
- 集成:一条 threshold=100 的 3-stage 线,跑 3 回合 → stage_idx 0→1→2、clues_dropped 攒 3 条、inject 含最新线索。

## 待复核(用户回来看)
决策 5/6/8/9/10/11(★)都是我拍的合理默认——尤其 **6 休眠统一**(vs 用户曾偏"只medium")、**11 上限数值**(纯起始值)、**8/9 复杂线终局形态**。任何一条要改,改对应阶段的实现即可,不影响 L1 核心。
