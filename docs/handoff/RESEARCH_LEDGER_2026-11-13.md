# RPG / DSH 研究与验证交接账本

更新日期：2026-11-13（北京时间）。本文把此前 Work 对话中的研究结论、实测证据与未完成工作放回仓库。它是研究索引，不把建议设计当作已实现功能。当前恢复入口、分支及未完成改动的处置以本目录其他交接文件及根目录接手说明为准。

**当前结论：保留 Python 世界内核是推荐方案，不是已经证明优于现成引擎。DSH 尚未实际安装联调；第三方机制尚未接入运行时；100 回合真实模型游玩尚未完成。** 已完成的百回合是固定响应的确定性耐久验证，不能拿它代替用户后续要求的 6.1 Terra 百回合实测。

## 1. 用户意图与执行约束

- 目标是能长期玩下去的 RPG：世界持续变化、多条暗线并行、角色延续经历与目标、NPC 不知道其不该知道的事、玩家的行动有真实后果，失败与撤销不污染存档。
- 用户希望综合同类项目的长处，不接受“对方 README 声称有就算完成”。每个候选机制先运行真实实现、设计小规模消融，再进入最小适配，逐步合入。
- 用户明确要求先找根因，从机制、流程、数据结构与上下文边界改进，不能等用户逐个指出问题，也不能只不断添加叙事特判。
- 指定 **`gpt-6.1-terra` 担任 DM** 进行真实运行。固定响应、录制重放、模型 Judge 和真实生成必须分开统计；不静默换模型或计费通道，不把多阶段请求数当成玩家回合数。
- 最近请求是先调整能力，分段查看 20 / 30 回合，再达到连续 100 回合的体验与一致性验收。随后用户明确暂停本环境中的实现，要求把全部工作迁移到 Codex 能直接接手的分支。当前交接不表示已经完成该百回合目标。

## 2. 证据层级与历史检查点

| 检查点 | 已完成内容 | 不能外推的结论 | 仓库证据 |
| --- | --- | --- | --- |
| `91646fb6c3cfe418709ae0f869ba985c282e2003` / RCV-139 | 显式发言独立交付；原话、调查、风险与行动事务等能力；100 回合确定性世界推进 | 不代表 100 回合真实模型叙事或一般 NPC 自主生活已经通过 | `docs/verification/RCV-139-host-speech-and-endurance.md`；`docs/verification/rcv139/world_endurance.py`、`world_endurance.json` |
| 2026-11-10 综合评估 | 读取五个第三方项目的文档、部分源码；本引擎 109 项针对性回归重跑；重跑固定响应百回合 | 当时未运行第三方；DSH 安装、模型桥接和目标新架构均未实现 | [完整原报告](artifacts/RPG_DSH_Integrated_Assessment_2026-11-10.html)；本账本第 6–8 节保存其设计要点 |
| `9acb6f3f301dbeaab384650b0a1d17aa5f44a81a` | 固定上游版本，选定上游测试 178 通过、2 跳过；7 组独立实验；本引擎 13 项检查 | 0 次真实模型调用；未验证插件全量测试、Windows、NPC 行为质量或 DSH 宿主 | `docs/verification/DSH_VALIDATION_ROUND1.md`；`experiments/dsh-validation/`；`docs/verification/dsh-validation-round1/` |
| `59b4117` | Terra 真实生成 35 次，11 个剧情行动已提交；1 次撤销、2 次冷重开；修复新 NPC 同回合转述误拒 | 短局、单场景、全部作者与审查同模型；非独立盲评；不是 DSH 联调；不是百回合 | `docs/verification/TERRA_DM_LIVE_2026-11-11.md`；`experiments/terra-live/`；`docs/verification/terra-live-20261111/` |
| 2026-11-13 暂停实现时 | 定位重复导演桥段；开始近期主题去重 WIP；怀疑已接暗线仍会被按背景线降级 | WIP 不能当作验收通过；接线降级目前是待复现问题，不能写成已修复 | [未应用的 WIP 补丁](wip/director-novelty-UNVALIDATED.patch)；见本账本第 9 节 |

历史测试数字按当时报告保留，不是本次交接重新跑出的数字。RCV-139 文档记录全门禁 `2267 passed, 1 deselected`；Terra 修复报告记录 `2276 passed, 1 deselected`，补两个测试后另有 11 项专项通过。不要把不同提交的数字拼成当前分支的全量结果。

## 3. 第三方来源、固定版本与可借鉴范围

以下 SHA 来自当时报告/实验计划，是已核对的历史版本。继续研究时先复现固定版本，再另行评估上游变化；不要把 main 的新代码悄悄替换进旧实验。

| 项目 | 固定提交 | 已有证据 | 借鉴方向与限制 |
| --- | --- | --- | --- |
| [Agentic Roleplay](https://github.com/Firefox2100/dsh-agentic-roleplay) | `94a63f6fe658bd68ade340d9f683f4f5b5eb0efe` | 文档/部分源码，以及选定 core/state 93 项测试和独立实验 | 独立 NPC 意图，Event → Perception → Belief，反思、离场行动；但基础召回存在幕后真相影响排序的反例。是整体替代引擎候选，尚未准入。被测包声明 GPL-3.0-only，不直接复制实现。 |
| [Agent RP](https://github.com/hewzhew/dsh-agent-rp) | `f8b98d9eb4bbffa218db3a755da7a61b0c731b7c` | 文档和部分源码阅读；**没有执行独立实测** | 角色 / research / director / sections / editor 职责契约；作者资产与运行中故事分开。README 的完整 Agent/MVU 功能依赖 patched host；此限制只适用于该项目。不要照搬每回合全流水线。 |
| [LikeTavern](https://github.com/Amakurai/dsh-liketavern) | `d890a1bb79c0b0edf60b211fefdca649df17c632` | 文档/源码；选定测试 85 通过、2 跳过；摘要谱系回滚独立消融 | 分支与派生记忆回滚、中文检索、原文归档、角色资产及产品流程。声明 MIT。借鉴不变量，不把文件 WAL 重复套到 SQLite 外。 |
| [RP Tools](https://github.com/SiriusWJ/dsh-rp-tools) | `bcb9825d33af7fb8162119b6d4e9c04f0686cc7f` | 文档/部分源码阅读；**未独立实测** | DM 工具作用域、稳定 system / 动态 runtime 分离、随机裁决和轻量发布。不能据此证明完整世界模拟。 |
| [Portable Tavern](https://github.com/XCNXNXNX/dsh-portable-tavern) | `b1b319b60680f8d16acd3499775b57ded8096304` | 文档阅读；**未独立实测** | 角色/队伍、检定难度与加成、行动后果展示。浏览器持久化不替代我们的事务与来源存储。 |

Agentic 的固定版本 bundle 声明版本为 `0.12.0`、DSH 兼容范围 `>=0.2.2-rc.1 <0.2.3`。这是**上游声明**，不是本项目已经验证的兼容承诺。完整 DSH 插件发布方案仅获得官方扩展机制文档支持，未完成实际安装验证。

第一轮机器可读来源：`experiments/dsh-validation/plan.json` 与 `docs/verification/dsh-validation-round1/provenance.json`。后者包含源码文件 SHA-256、源码 clean 状态、Node `v24.22.1`、Python `3.12.14` 和依赖锁哈希。第三方源码不在本仓库，runner 会按版本在仓库外取得。

## 4. 第一轮消融：哪些真有收益，哪些必须拦下

| 机制 / 实验 | 完整实现与消融的实际差异 | 当前决定 | 仍缺什么 |
| --- | --- | --- | --- |
| 角色数据过滤 `actor_scope` | 实际 NPC prompt 按角色取数据时没有 B 的私密内容；仅取消感知库角色过滤后，A 的 prompt 含 B 私密记忆，人设仍隔离 | 可进入独立适配试验；不是完整 NPC 流程准入 | 公共 situation / earlier reactions、工具元数据及所有检索字段过滤 |
| 真相非干扰 `truth_noninterference` | 两个世界中角色可见资料相同，仅变 `event.text` 的幕后密钥信息，原召回结果改变；屏蔽排序输入的幕后文本后一致 | **阻止原基础召回直接接入** | 标签、参与者、重要性、地点、FTS、向量、reranker 仍需反事实审计 |
| 信念证据 `belief_evidence` | 不存在/越权感知引用均被拒；只关前置校验，写入层仍拦住；合法但无关引用却可支持“井底蓝龙”并写入 | 分层存储可借鉴；语义依据未通过 | 三类校验分开：存在、属于角色、支持结论；实际模型误解/反证场景；不要声称消融证明前置校验有增益 |
| 分支摘要谱系 `branch_rollback` | 完整快照+回滚+摘要展开删除子分支未来约定且保留过去；仅关闭摘要展开，未来内容仍能被检索；父分支均未改 | **优先最小适配试验** | 我们 SQLite 来源依赖、异步迟到、撤销、冷重开；不要复制另一套 WAL |
| 草稿分支发布 | 准备到一半故障，未发布子分支，父分支不变、临时草稿清理 | 借鉴“准备成功才发布” | 跨进程崩溃、宿主 inbox、模板变量、后台任务未覆盖 |
| 遗忘 `memory_policy` | 同 recall=4/associate=0 条件；旧经历在 0/7/30 天可原话召回，90/180 天原话模糊；关闭遗忘保原话；最近四条窗口找不到旧事 | 仅考虑用于角色回想；原话档案独立保留 | 同义问法本词面路径未命中；未测向量/reranker，不能据此否定整个插件语义检索 |
| 本引擎反事实对照 | 保持角色认知，只变秘密/幕后目标时，assemble_context、POV recall、recall_query 保持相同；去掉 POV 过滤的负对照可检出差异 | 当前读取边界通过这些样本 | 尚未实现的自主 NPC 不因此自动正确 |

严格区分：`probes-tests.json` 绿色表示“反例/干预测量按预期完成”，不是候选机制绿色。`observations.json` 的 gate 才是本轮准入状态。prompt 的 UTF-8 字节数不是 token、费用或延迟。

上游测试计数：Agentic 49 + 44；LikeTavern 54 + 31，另 2 个 Windows 专用场景跳过。LikeTavern 初次检索测试因缺依赖无法收集，已补真实依赖后通过；初始失败是环境问题，文件单独保留，不能算产品缺陷。`last-run.json` 只表示最后一次分组重跑，不能独自证明全部 178 项；完整报告和各组 JSON 应一并看。

复现入口是 `experiments/dsh-validation/README.md`、`run.py`、`probes.test.mjs`、`test_own_boundary.py`。固定版本、同场景/同预算、单机制消融、负对照、失败保留与分层证据要求见 `docs/designs/EXTERNAL_MECHANISM_ADMISSION.md`。

## 5. Terra 真跑：实际发生过什么

完整说明：`docs/verification/TERRA_DM_LIVE_2026-11-11.md`。逐份请求、响应、失败、重放、冷重开与撤销数据在 `docs/verification/terra-live-20261111/`；可读回放生成器为 `experiments/terra-live/review.py`。

本次交接另保留 [原用户阅读回放](artifacts/Terra_DM_Real_Run_2026-11-11.html)、[完整合成战役 SQLite 备份](artifacts/terra-live-campaign-2026-11-11.sqlite3.gz)。后者是包含实验状态的续跑存档，不是空白初态；不要直接覆盖它继续实验，先按主交接中的恢复命令复制到新的运行目录。使用 SQLite 备份而非手写 SQL 重建，以完整保留 FTS 等存储结构。

运行使用正式 `PlayerDashboard`、`GroundedAuthorStrategy`、真实 SQLite、真实工具循环与默认后台 hook，embedding 沿用 none。指定模型通过原生 agent / 本地带哈希 mailbox 响应，不是 HTTP API 压测。DM 只读当前请求，不能读战役、场景答案或评审文件；工具请求交给引擎实际执行。初跑 12 次 + 续跑 23 次 = 35 份真实响应。作者与正文审查均为 Terra，审查通过不等于独立质量评估。

- 初跑 4 个行动成功，第 5 个行动被拒。新信使合法在本回合登场，但一条事实权限校验仅认行动开始时已存在的人物，导致其 public `reported:` 转述不能落库。拒绝未推进时间。
- 修复统一候选及最终事件层的 `turn_speaker_material`，只纳入实际本地/合法抵达的公开角色；新 ID、位置、past/hidden 与来源权限有检查。不因此放开 canonical 真相写入或远方人物。
- 原失败响应保持不变，在克隆存档中修前/修后对照，修后落库；这是**录制重放**，不能算新增模型生成。随后 23 份响应是新生成。关键证据：`exact-failure-replay.json`、`speaker-fix-regression.xml`、`full-regression.xml`、`speaker-extra-regression.xml`。
- 全程累计提交 11 个剧情行动，含被撤销的约定；最终活跃历史不是 11 个均保留。另有 1 次撤销、2 次冷重开。模型主动调用过一次 `history_locate`。
- 阿岚隔日复述“青竹铃铛明晨响四次”；白禾没有私话/未说出口心念。撤销“西钟楼/紫铜罗盘”约定后，后续请求不含撤销内容，玩家检索为空，Terra 查历史后说明没有约定。
- 青山驿预置暗线在玩家停留枫桥时继续推进；这验证预置按日推进路径，不代表自由生成的长线会自动收束。
- 真实摘要 5 次，最后 4 条活跃；没有专门构造“未来约定已经进入压缩摘要”的真实模型场景，不能代替 LikeTavern 谱系消融。

尚存体验问题：第 5、7 个行动都由独立 `director_fired` 抽到“认错人”；几次对话偏短，可选后续不鲜明；船契线停留在钩子；“旧告示”知识来源被说成“我看过旧告示”的亲历措辞值得核验。尚未测大上下文 compaction、几十回合人物/剧情漂移、后台迟到写入、多模型对照。

`model-selection.json` 记录精确模型选择；`manifest.json` 与 `continued/manifest.json` 记录 driver 哈希、输入行动、启用 hook 与限制。原生 mailbox 周转时间不能当作 API p50/p95；没有 provider token 数据就不报真实 token/费用。

## 6. 整合设计：保留什么，新增什么

以下来自 2026-11-10 综合评估，是**目标方案**；除明确列出的现有能力外均不能写成已实施。

**保留一个世界事实提交者。** Python 内核拥有游戏时间、随机游标、事实、物品/资源、调查/任务、事件原文、行动幂等、事务与恢复。NPC 提出意图，规则裁决结果，叙述表达玩家可见结果。DSH 管模型连接、会话生命周期、交互与界面，不作为第二个可改写事实的 DM。

现有资产与差异化：

| 资产 | 为什么保留 | 主要实现入口 |
| --- | --- | --- |
| 行动一致性 | expected_revision、action_id/指纹、事件/收据/原输入/结果同事务；拒绝不残留半回合 | `app/gui.py`、`loop/turn.py`、`engine/store.py`、`kernel/turncommit.py` |
| 原话与历史来源 | 玩家查证零生成、只读；按日期/角色/收件人定位，查不到不编引号原文 | `engine/history.py`、`app/player_recall.py`、`context/access.py` |
| 调查和领域后果 | miss / lead / confirmed、有权观察者与有效地点/证据；不能靠叙述完成任务 | `kernel/investigations.py`、`context/investigations.py`、相关 strategy/系统 |
| POV / 受众隔离 | 已有角色可见投影、工具边界、显式收件人；不是从零开始信息隔离 | `context/viewpoint.py`、`context/assembler.py`、`context/capabilities.py` |
| 世界线与路线后果 | 预置多线推进、封路等会改变玩家合法行动 | `systems/`、`loop/` 及 RCV-135–139 证据 |

缺口：统一作者之外的 NPC 私有决策、一般化信念修订与承诺、离场目标/日程、跨世界线因果调度、玩家直接看懂后果的界面。解释性记忆仍是默认关闭的实验，不能算完整生产反思系统。

建议逻辑对象（优先复用事件/投影，不要求每项新建一张表）：

| 对象 | 核心字段 / 不变量 |
| --- | --- |
| ActionEnvelope | action_id、请求指纹、campaign/branch/actor、expected_revision、原输入/收件人、规则版本；身份由宿主绑定，模型 actor_id 不是授权 |
| WorldEvent / Fact | 时间、行动、原因链、地点、规则版本和有效期；仅裁决后进入权威历史 |
| Perception | 角色、事件引用、见/听/告知渠道、说话者/实际接收者、措辞与时间；收到说法不等于说法为真 |
| Belief | 角色命题、置信度、支持/反驳感知、版本/修订原因；可误解，不覆盖世界真相 |
| Intent / Commitment | 目标、行动、约束、期限、状态、下次检查时间；答应送信不等于已送达 |
| WorldProcess | 触发条件/依赖、进度、next_due、处理游标、预算、暂停/完成；跨线因果可追踪 |
| NarrativeCue | 来源、披露对象/条件、优先级、期限、消费标识；只提示可合法揭示的内容 |
| Branch / Job | 起点、资产/规则版本、随机游标、输入版本、幂等键、状态/错误；过期任务不回写新分支，派生索引可重建 |

目标单回合：绑定请求/恢复收据 → 建立可见时间快照并处理相关到期工作 → 受限行动提案 → 仅激活相关 NPC → 统一裁决/感知收据 → 受约束叙述及验收 → 短事务核对版本后原子提交 → 返回可恢复结果、延后整理。模型等待不占数据库事务；正式正文在提交成功后发布；提交前取消无本回合副作用，提交后断线按 action_id 恢复。

调度应区分确定性到期事件、条件依赖、角色/阵营决策和信息披露。先规则、后必要模型；按稳定到期时间/优先级/ID 与处理游标防饥饿。时间跳跃保留封桥再开桥等中间历史。承诺因封路延误/改道或取消；模型不可用不能自动宣布成功。导演调节披露/节奏，不代替因果。

记忆分两条路径：玩家查原始档案保持完整精确；角色回想可有模糊与不确定，但先构造角色可见候选集，再检索/重排，工具身份不可由模型随意切换。摘要/别名/向量是带来源版本的派生物，撤销、分叉或旧输入完成时要失效/拒写。

## 7. DSH 交付的目标边界

- Python 领域包 + TS 宿主适配 + 游戏前端，发布一个 bundle。首个开发版可要求 Python，普通玩家发行再提供管理安装/平台包；大向量模型保持可选。
- 从 `PlayerDashboard` 提取 `CampaignService`（拟议名，**不是已存在 API**）。GUI、CLI、DSH 共用命令，一份战役单写者。
- 拟议协议：`handshake`、`campaign.open/create/state`、`turn.execute/result`、`history.search/open_source`、`timeline.undo/branch`。会话绑定 campaign + branch + player_actor + capabilities，回复含 revision/public_result/receipt。
- 版本化 stdio JSON-RPC 或等效协议；stdout 只传协议、stderr 日志。Python 模型请求带 ID 发给 TS，TS 调 DSH 模型服务后回传，不能因等待回合结果而阻塞反向模型请求。
- 必须映射工具、结构化输出、推理设置、用量、取消与上下文限制；不支持时显式报错。插件热重载/卸载不删除存档；升级先备份与迁移校验。
- 宿主验证尚未执行：干净 profile 安装、启动、卸载、切会话、UI、嵌套模型请求、取消/崩溃、提交后恢复、平台兼容与版本矩阵。先固定响应测协议，再真实模型测实际体验。
- MCP 可作为工具入口，但不能代替生命周期、模型桥接、正式游戏界面与完整发布流程。

官方历史来源：[bundle 发布](https://deepseek-harness.github.io/deepseek-harness/en/develop/basic/publish)、[服务/依赖](https://deepseek-harness.github.io/deepseek-harness/en/develop/framework/service)、[模型适配](https://deepseek-harness.github.io/deepseek-harness/en/develop/practice/llm-adapter)。下一个 agent 做实际集成前应核对当前文档和固定版本；本交接不声称这些接口在未来版本保持不变。

## 8. 实施顺序与重新选择现成引擎的条件

旧方案的 P0–P5 是路线参考：P0 固定基线/共同场景/宿主试验；P1 应用服务与最小私有 NPC 可玩闭环；P2 认知/承诺/统一裁决；P3 通用调度与后台恢复；P4 游戏界面/分支/资产创作；P5 干净环境安装/升级/许可证/性能与版本门禁。**最新优先级是先完成当前引擎的长局可靠性与体验验证，不要照旧路线直接大规模接入。**

最小共同场景：酒馆私话只给 A，B 同场但没收到；离场调查账本；暴雨封桥使 A 的承诺受阻、另一阵营继续行动；调查失败留下有来源的可达线索；回访延续关系和原话，B 仅实际传播后获知；查原文、撤销、冷重开后世界/认知/任务/待办一致。

每个机制先同模型/同预算控制试验，再小步合入；保留当前内核、改进内核、Agentic 扩展三种共同场景的对照路线。若对方通过关键不变量、旧档迁移或只读方案可接受、实际领域改动与维护成本更低、许可证发布方案明确，就重新考虑整体采用对方。已有代码量不是排除它的理由。

调用预算原建议（未测性能，不能写成承诺）：普通回合新增 0–2 次相关 NPC 决策；重大交互暂定最多 4 个活跃角色、2 轮；反思/摘要按显著事件、场景切换或累计变化触发。记录各阶段模型/token/重试/工具轮数、正式结果时延、后台积压，不用请求数或字节数代替费用。世界投影规模是否慢需实测，不预先优化。

## 9. 接下来应验证的具体问题

| 优先项 | 目前证据 / 机制假设 | 下一步应保留的对照 |
| --- | --- | --- |
| 导演桥段重复 | Terra 第 5、7 行动两次“认错人”；实际 `director_fired` 确认。暂停时有 `DirectorNoveltyPolicy` WIP，拟以近期已提交导演事件做可回滚去重 | 窗口为 0 的原行为 vs 去重；相同 seed；撤销/重开/小表/中性无反转；重复率和对正常 pacing 的副作用。未经检查与测试的 WIP 不算修复 |
| 已接暗线被背景调度降级 | 先前检查提出：玩家接下已有线后仍可能按未接背景线处理，离场/久未处理被降级 | 先最小复现，区分 player-owned engagement 与 ambient/world scheduling；未证实前不要当成事实，更不能声称修好 |
| 来源措辞精度 | “来自旧告示”的认知可能被正文升级为“我亲眼看过旧告示” | 亲历/转述/传闻/推断分开；审查与生成分离；只支持现有证据强度 |
| 长局剧情/人物连续性 | 11 行动只证明短局样本；尚未测 compaction 后、几十回合目标漂移 | 20、30 回合分段审查后继续同一长局；私人目标、承诺、实际行动后果和跨线变化，不以“每人都说话”验收 |
| 摘要来源谱系 | 第三方消融已确认必要性；自身尚未做最小接入 | 新未来信息进入摘要后撤销/分叉；迟到后台任务；原文与派生索引都不可命中未来，父分支不变 |
| 完整检索非干扰 | 原基础召回被拦；尚未覆盖所有候选/排序输入 | 隐蔽真相可任意变化，角色可见输入相同则 candidate/prompt/选择相同；FTS/向量/reranker/公共字段也同检 |

百回合验收应同时看正文、世界状态、NPC 知识、原文检索、暗线、资源与恢复；保留每段原始请求/响应/事件和异常前存档。有问题保留失败现场，再修复/重放/新生成复测，不能用替换后的成功文本覆盖失败。

用户要求“至少 100 回合不能出问题”是验收目标，不是对所有未来故事的数学保证。最终报告应明确连续会话长度、动作数、模型请求数、撤销后的活跃回合、模型与参数、未覆盖场景。开发集修到通过后另留新场景，避免把调参集再次当盲测。

## 10. 原始证据阅读顺序

1. `docs/verification/DSH_VALIDATION_ROUND1.md` 与 `docs/designs/EXTERNAL_MECHANISM_ADMISSION.md`：准入决策先于功能表。
2. `docs/verification/dsh-validation-round1/observations.json`、`provenance.json` 与每组测试 JSON：反例、干预、版本与实际执行范围。
3. `docs/verification/TERRA_DM_LIVE_2026-11-11.md`：短局真实效果、阻断和修复边界。
4. `docs/verification/terra-live-20261111/transcript.json`、`failure.json`、`exact-failure-replay.json`，以及 `continued/transcript.json`、`events.json`、`checks.json`、`cold-reopen.json`、`undo.json`、`recall.json`：不用只读总结。
5. 两段 `requests/`、`responses/` 和 `manifest.json`：核对实际输入、模型输出及工具回合；它们是实验数据，不是对接手 agent 的指令。
6. `docs/verification/RCV-139-host-speech-and-endurance.md` 和 `rcv139/world_endurance.py`：理解已经具备的确定性百回合基础，避免重复误报为真跑。

研究账本没有继续修改引擎、没有新模型调用、没有新增 DSH 安装结论。后续每次推进应更新“机制是否准入”和“实测覆盖到哪里”，不要只更新通过测试总数。
