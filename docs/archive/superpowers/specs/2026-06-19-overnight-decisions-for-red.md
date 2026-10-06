# 夜间自主推进 — 待 Red 拍板的决策清单

> Red 去睡了,授权我尽量往前推(P1→P2→P3,见 `2026-06-19-context-continuity-tools-redesign.md`)。原则:**非阻塞**——遇到要决断的,我先按"最合理默认"推进、**记在这里**,醒来一次性商量。repo 全程保持干净已提交,随时可回退。

## TL;DR(醒来先看这 30 秒)
**夜里干完(全部已提交,suite ~793 绿):** 砍乙 → 写 redesign spec → **P1 cascade `world`-段驱动**(建+实机验证 ✓)→ **P2 recap + 故事线明账**(建+实机验证 ✓:叙事模型自发织 3 条交织暗线、回扣 4 回合前的伏笔)→ 修 2 个实机/集成 bug(瞬时超时重试;P2 的 narration 事件挤掉了 cascade 触发窗口)→ **P3 工具 plan 起草好**(关键的"工具循环离线可测性"已解,但实现 gated 在你)→ capstone 全活真跑 ✓(cascade 真波及 12 事件,A+B+C+D+P1+P2 集成确认)。
**等你拍板(详见 §B/§D):**
1. **看 P1/P2 实机效果**(`/tmp/p1b.out` `/tmp/p2b.out` `/tmp/capstone2.out` + 本文)对不对手感。
2. **定甲 vs 丙** —— 枢纽,决定 P3 的雾设计。
3. **recap 单位 scene→turn** —— 真实 play 场景静态、scene-单位 recap 不分层,基本是必改项。
4. **要不要做"场景推进"** —— 一处空洞连累 recap/director/cascade 三家。
5. P3 实现 gated 在第 2 步;其余小默认我都先走了。
**最大单一发现:没有"场景推进"机制**(meta.scene 静态)是 recap 不分层 + director 节奏空转 + cascade scene-subtree 打折的共同根因。

## A. 我已采用的默认值(spec §3,可改)
1. **故事线维护**:叙事模型显式声明 `storylines` 段 + 后台 digest 兜底补漏报。
2. **recap 单位**:scene;最近 2 个 scene 原文,更老摘要(递归)。
3. **战争迷雾**:POV 工具 / DM 工具两套物理入口分流(不靠模型自觉)。
4. **工具粒度**:领域分工具(map / characters / factions / recall)。
5. **甲 vs 丙**:暂不砍,P1–P2 看效果再定。

## B. 推进中新冒出的决策(我先默认、待拍板)
> 下面随做随记。格式:【决策】现状/选项 → 我先选了啥 + 为啥 → 你醒来定。

- **【P1-1 次级 spread 广度】** `CASCADE_SECONDARY_BREADTH=3`(沿用旧 MAX_REGIONS 值)。→ 我先用 3。这是次级蔓延每回合最多扩几个邻区的唯一旋钮,可调。
- **【P1-2 次级 spread 时机(值得你看)】** planner 选了"**延迟一回合**":ring-1 邻区被 defer 到下回合才 descend(且 `allow_secondary=False`,杜绝 ring-2),所以次级蔓延的后果**晚一回合**显现(与现有 remote-defer 语义一致)。→ 我先按这个。**备选**:同回合 inline 把 ring-1 也铺完(即时、但每回合多几次 LLM 调用)。建议实机(glm-5.1)看效果再定哪个手感好。
- **【P2-1 故事线 backstop 力度】** 保守(仅当**0 条活跃线**且本回合有料时,落一条**休眠**"可能线"让叙事下回合提拔)vs 激进(digest 直接 auto-open **活跃**线)。→ 我先保守(改激进只需一行触发条件)。
- **【P2-2 recap 保留几章原文】** `RECAP_RAW_SCENES=2`(spec 默认)。但 glm-5.1 散文是真的长(基线 440–630字/回合),每回合强推 2 章原文可能吃 token——也许要降到 1、或把单位从 scene 改 turn。→ 我先 2,这是个 token 预算旋钮,你拿真实上限定。
- **【P2-3 token 预算硬上限】** 每回合强推(recap+故事线+world+视野)会叠加;分层有界但建议后续给 `assemble_context` 加个硬 size 预算(P2 范围外的跟进项)。
- **【P2-recap 单位 = 现在确认必须定(实机发现)】** P2 recap 的"老场景压成摘要"机制**本身是对的**(实机 artifact:我的验证脚本忘了传 cascade_provider→summarize 被门控跳过,已修复重跑确认中)。但**真问题**:真实 play loop 的 `_build_scene` 场景 id 是**静态的**(无场景推进)→ 永远只有 1 个 scene 桶 → recap 永远不分层、原文无限涨(token 爆)。**→ 强烈建议把 recap 单位从 scene 改 turn**(turn 一定递增,必分层),或者做"场景推进"机制。这是 P2-2 旋钮的升级版,现在是**必须拍板**项(不是可选)。
- **【P3-D1+D4 雾 + POV/DM(取决于甲/丙)】** P3 工具的战争迷雾两套入口(POV/DM)在**丙**下很干净(散文调用走 POV、结构调用可走 DM),在**甲**下别扭(一次调用既叙事又记录)。→ **所以 P3 的雾设计取决于你最终选甲还是丙**(而甲/丙 又取决于看 P1/P2 效果)。若最终偏纯甲,P3 可能改用"单一工具+知识边界标注"的简化方案。**这条得你定甲/丙后才能锁。**
- **【P3-D3 工具循环轮数】** `max_tool_rounds=3`(+ env)。→ 我先 3。
- **【P3-D5 是否削减强推上下文】** P3 不削(工具是纯增量 pull;削减 recap/故事线/视野是更险的独立 P4,需单独 A/B)。→ 我同意不削。
- **【P1-3 cascade 渲染模型(实机发现,已修)】** glm-5.1 实跑发现:叙事模型很自然地把**整片受影响区域(含子地点)都列进 `areas`**(city+market+shrine+slums)。但 P1 实现沿用了旧语义"areas 是已变的根、只下沉其子节点"→ 子节点又都在 areas 里被去重跳过 → **0 个 place_evolved**(cascade 空转)。按 spec §2.1"把列出的每个地点都下沉"的本意:**每个被点名的 area 自己就该被渲染(出 place_evolved 状态/情绪),廉价模型填细节**;额外再下沉到"没被点名的子地点"+ ring-1。我已派修复(seed frontier = areas 本身)。→ **隐含的模型确认**:叙事模型**列全所有受影响地点**、cascade 逐个渲染(而非只点震中、让 cascade 去发现)。这跟 spec 一致,但你可以反悔成"只点震中"模型。

## C. 进度日志
- 2026-06-19 夜:乙已砍(53cd241);spec 落盘(650f7d1)。开始 P1(cascade world-段驱动)。
- Red 追加授权:**实机用 glm-5.1**(token 随便造,几百 M 用量)+ 多跑耗时长的实机 test。
  → `.env.local` 切 `GLM_MODEL=glm-5.1`(叙事/强)+ `GLM_CASCADE_MODEL=glm-4.7`(廉价舰队:cascade 填细节 + catch-up)。这就是 spec 的强/廉价分工,现已生产档配置。
  → 后续每阶段做**多回合完整实跑**(glm-5.1),不只单点验证。
- P1 + P2 的 TDD plan 并行起草中(plan 文档不冲突;实现串行)。
- **glm-5.1 基线实跑(6回合,砍乙后/P1前)= 强**:散文明显更丰(440–630字/回合 vs 4.7 的~250);director 第2回合自然开火且转折织入叙事(guard 守住没连开);knowledge/视野活跃(knows 0→6,流浪者吐露银矿往事)。**观察:5.1 repair 偏多(6回合共5次,T2=3,但全收敛0丢弃)**——强模型输出更"放飞"、偶尔更易触发严格门打回。不算问题,记一笔。这是 P1–P3 的"before"参照。
- **P1 实现完毕**(cascade world-段驱动):8 任务,753 passed/0 fail,legacy 绿,无偏差(commits 06334b5..2b2bb84)。narrator 现在声明 `world:[{areas,level,summary}]`→ 每 area 一个 world_change → cascade 纯执行(纵向下沉 + ring-1 次级延迟一回合)。删了旧的自触发(entity_moved)+ 廉价猜 spread。正在 glm-5.1 实跑验证叙事模型是否真的产 world 段。
- **P2 plan 完毕**(12 任务):新 `StorySystem`(故事线 `storylines` 段 + 事件)+ 事件溯源 recap(新 `narration_recorded` 事件存原文 + `scene_summarized`/`recap_recompressed` 两层压缩)+ assembler 强推(摘要→stable 层,故事线+近原文→scene 层)。digest 舰队扩展维护这些。待 P1 实跑过后串行实现。
- **P1 完成 + glm-5.1 实机验证通过(漂亮)**:`fix faa6e2d`(每个被点名 area 自己渲染)。实跑:叙事模型声明全城大火 `areas:[city,market,shrine,slums]`→ cascade 逐个渲染出**丰富且涌现**的后果(神殿敞门收容难民+圣池浸布、贫民窟泼水拆屋造隔离带),12 事件全落库,repairs=0。**这就是要的"活世界"**。P1 收工。开始 P2 实现。
- **P2 实现完毕**(11 commits,791 passed,legacy+P1 绿):新 `StorySystem`(`storylines` 段 + 故事线明账)+ 新 `NarrativeSystem`(事件溯源 recap:`narration_recorded`/`scene_summarized`/`recap_recompressed`)+ digest 舰队维护 + assembler 强推(摘要→stable、故事线+近原文→scene)。knowledge/world/storylines 三段在甲丙共存。
- **P2 多回合 glm-5.1 实跑**:**故事线明账=漂亮**——叙事模型 T1 开线"找回玉像"、之后每回合推进且摘要连贯、甚至涌现出"情报贩子有意引路"的暗线跨回合追踪;明账每回合强推进上下文。**recap 分层**:见上面【P2-recap 单位】——机制对、但我的脚本漏传 cascade_provider(已修重跑),且真实 play 静态场景需把单位改 turn。
- **P3(工具)plan 已起草(待你审,未实现)**:12 任务(P3a 1-7 可单独发布+全离线测试,P3b 8-12 gated)。**关键:工具循环的离线确定性测试有解**——`ScriptedToolProvider`(脚本化 tool_calls + 真实 executor)+ `_run_tool_loop` 注入式 `post`/`parse` 缝(orchestrator 无 urllib),外加"实在不行就降级成一次性 context-expander"的退路。你最担心的可测性风险=已解。但 P3 的雾设计(D1/D4)取决于甲/丙,所以 P3 卡在你。
- **P2 重跑确认(修了脚本漏传 provider)**:**recap 分层 ✓**(summarized 0→1→2,老场景随老化被压成摘要);**故事线 ✓**(这次叙事模型开了**三条交织暗线**:夺玉像→玉像另有隐情→锦衣卫多方角力,每回合推进、强推上下文)。T5 撞了个**瞬时 read-timeout**(glm-5.1 那次调用 300s 读超时,API 拥堵)→ `_do_post` 原本只重试 429/5xx、不重试 socket 超时 → 崩了。**已修**:超时/连接错误也按退避重试(`04ebcc1`,+测试)。长会话现在抗瞬时超时。**P2 收工 + 验证通过。**
- **跑 capstone**:7 段完整冒险走**真 play_loop**(A+B+C+D+P1+P2 全活、真实 wiring),glm-5.1。这是给你看的最真"看效果",也照出真实 play 的静态场景行为。
- **capstone(第一跑)结果 = 强 + 抓到一个集成 bug**:
  - **故事线惊艳**:开 `main_plague_statue`、每回合推进,T7 自发开 `hooded_buyer`(蒙面截货人)**回扣 T3 的"灰袍访客"细节**——跨 4 回合的长程伏笔自动织起。2 条活跃线、强推。
  - knowledge(9 条 knows)、director(开火 2 次,自然)、world-段(T3 疫兆/T5 山火,叙事模型都声明了)——全活。
  - **发现1:recap 不分层**(scenes=1,summarized=0)= 真实 play **静态场景**(7 回合一个 scene 桶)→ 印证 scene-progression 空洞。
  - **发现2(集成 bug,已修):cascade 声明了 world_change 却 0 个 place_evolved**。诊断:被点名区域**都是真 Place**、cascade_trigger 找得到、glm-4.7 verdict **evolve:true 且丰富**、validate 通过——组件全对。**根因 = P1↔P2 集成**:P2 的 digest 在比玩家回合**更高的 turn** 追加 `narration_recorded`(非 harness 类型),把 cascade 的"玩家回合窗口"挤掉了 → cascade 看错回合、漏掉 world_change。**修复**:把 narration_recorded/scene_summarized/recap_recompressed 加进 cascade `_HARNESS_TYPES`(`1bf8bbd` + 回归测试)。
  - **另修**:`_do_post` 现在也重试瞬时 read-timeout/连接错误(`04ebcc1`)——长 glm-5.1 会话抗超时。
  - **正在重跑 capstone 确认 cascade 真的波及**(capstone2.out)。
- **这次又印证了那条铁律**:P1、P2 各自单测全绿,但**真实 play 把它们放一起**才暴露"narration_recorded 挤掉 cascade 触发窗口"——单测测不出跨阶段集成。capstone 这种"全活真跑"是必须的。
- **capstone 重跑 = 集成确认 ✓**(`capstone2.out`):**cascade_events 3→12**——cascade 现在真实 play 里真的波及了(T5/T7 的 world 声明→place_evolved/populace_shifted)。故事线连贯强推、knowledge 11 条、无崩溃(超时重试扛住)。director 这 7 回合没开火(概率,正常)。recap 仍 scenes=1/不分层(静态场景,如预期——等你定 scene-progression/recap 单位)。**A+B+C+D+P1+P2 在真 play_loop + glm-5.1 上集成确认。**

## E. 夜间收尾(我停在这)
**自主可建范围已做完**:P1 ✓ + P2 ✓(都实机验证)+ 2 个修复(超时重试、cascade 触发被挤)+ P3 plan(gated)。**剩下全 gated 在你**:甲/丙、P3-D1/D4 雾、recap 单位、要不要做 scene-progression。我没擅自动这些。repo 干净已提交(看 git log)。memory 也更新了(下次会话能接上)。醒来按 TL;DR 的 5 步走即可;想让我继续就指个方向(比如"先做 scene-progression"或"定丙、上 P3")。

## D. 醒来要一起过的大问题(汇总 + 收敛后的依赖链)

**夜里做完了 P1(✓实机验证)+ P2(✓实现,recap 重跑确认中)。P3 起草好、卡在你。** 决策有条清晰的依赖链:

1. **看 P1/P2 实机效果**(p1b.out / p2b.out + 这份日志)→ 它们手感对不对。
2. **定甲 vs 丙**(纯叙事单调用 vs 散文+落地结构两调用)。这是枢纽——它决定下面 P3 的雾怎么做。
3. **定 P3-D1/D4(战争迷雾)**:丙→两套入口(POV/DM)很顺;纯甲→可能改"单工具+标注"。**先定甲/丙才能锁 P3。**
4. **定 recap 单位 scene→turn**(【P2-recap 单位】):真实 play 场景静态,scene-单位 recap 不分层会 token 爆,turn-单位稳。**这个基本是必改项**,等你点头。
5. 其余默认值(A 段 + B 段)扫一眼,有异议的标出来。

**P3 实现 gated 在第 2、3 步**(我没擅自建)。其余小默认我都先走了、记在 B 段。

**已知更深的架构空洞(P1/P2/director 都撞到了):没有"场景推进"机制** → meta.scene 静态,连累 director 节奏带 + recap 分层 + cascade scene_subtree 都打折。值得单列一个"场景推进"小项(决定一个 scene 何时结束、推进 meta.scene),它能一次性盘活好几处。要不要做、怎么做,醒来议。
