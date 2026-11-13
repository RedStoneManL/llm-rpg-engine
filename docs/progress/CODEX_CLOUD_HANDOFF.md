# Codex Cloud 接续说明：llm-rpg-engine

> **2026-11-13 接续提示：当前分支是 `harness`，先读 [最新完整交接](../handoff/README.md)。本页下方保留历史快照；旧分支、HOLD、模型、未推送与待办描述不覆盖最新用户指令。此次迁移只整理交接，百回合真实测试尚未完成。**

更新：2026-10-25（Asia/Shanghai）。本文件是本次跨环境交接入口，不是完整聊天逐字转录。

## 当前接续 · 人物初遇与消息来源（2026-11-07）

RCV-128 修复了当面初遇的临时人物、对话选择与重访。RCV-129 校正当前及条件抵达的 NPC 信念值、指定说话人来源和后台离场状态推演，普通作者的预览不再提前提供远处的私话。详见 [信息边界验收](../verification/RCV-129-npc-information.md)。文字层仍有亲历与传闻混淆的样本风险，需继续在跨镇长局中复核。

## 当前接续 · 多线世界（2026-11-07）

RCV-127 已修异地普通暗线冻结、改写后卡在旧阶段、明确接取任务因空闲降暗、主角线被后台提前结案。当前回合按游戏日推进其他已建立的线。80 次行动的受控长局中，从第 1 天到第 17 天，两条远线修前各 0 步，修后各 16 步，回访恢复当地线索。完整回归 2228 passed / 1 deselected。机械测试、原生样本与未来跨线世界后果分别记录。见 [RCV-127](../verification/RCV-127-world-continuity.md)。

## 当前接续：体验优先 / RCV-122–126（2026-11-04）

用户最新要求优先游戏体验并继续实际开发，旧“先输入档案／source session”的排序后移。
Hybrid 已改为结构规划与校验 → 最终散文；普通 Author 在结构改变后补最终正文，无变化仍一次调用。
正文重写只改文本，不重新执行效果。详见 [RCV-122](../verification/RCV-122-outcome-narration.md)。
RCV-123 已补当前人物 state 与主角对其态度的角色卡注入；专项 197 passed，完整 gate 2191 passed / 1 deselected（39.84s）。
RCV-125 已实现单件交付约定，依据实际物品转移结算，区分迟交／放弃并给下一回合近期公开结局卡。
生产提交 `f9127cf`；新增29例，最终 gate **2220 passed / 1 deselected（40.06s）**。
用户指定 Terra 测试：可用 `gpt-6.1-terra` 完成5回合／10条原生响应的精确请求回放，机械恢复检查通过；
人物反应连贯但平淡，不是远端 HTTP 或长局质量证明。见 [RCV-125](../verification/RCV-125-delivery-outcomes.md)。
下一项优先局面推进、节奏与等待时间；旧 HOLD 不阻塞。
整体次序见 [体验优先计划](../designs/PLAY_EXPERIENCE_PLAN.md)，来源协议不再是所有玩法的前置条件。
RCV-126 已更新[当前架构 HTML](../reports/2026-11-04/architecture-status.html)，生产源码基线 `f9127cf`；
报告保留模块／调用流程，明确新的体验优先顺序。以下 RCV-121 文档及更早“下一项”均为历史计划，以本增量和执行清单优先。

## 最新文档交付：RCV-121（2026-10-26，Asia/Shanghai）

用户要求继续前先给详细 HTML 模块／架构／调用流程与优化建议，已交付
[architecture-status.html](../reports/2026-10-26/architecture-status.html)，基于生产 HEAD `03cabc4`。
14 章节，15 个系统，96 个源码模块，四种可切换回合路径；只读源码／Registry 审计，无生产改动。
链接、符号位置和交互逻辑已核验；未重跑完整引擎 gate，也未声称 Chromium 渲染通过，详见
[RCV-121](../verification/RCV-121-architecture-report.md)。下一实现建议保持：原 input 同事务档案与安全 source session，
结果先行最终正文并列 P0。旧 HOLD 已解除，用户本轮“先报告”没有授权新的外部发布或改变私有数据边界。

## 当前接续点：RCV-120（2026-10-25 UTC，仅本地提交）

解除旧 HOLD 后已完成 [RCV-119 全量评估](../verification/RCV-119-current-environment-assessment.md)，并实际修复
其中的 FTS／语义过滤分歧：共用精确 day/entity 候选，向量先做宿主 ID allowlist 再排序，短 LIKE 查询转义。
最终专项 **71 passed**，完整 gate **2171 passed, 1 deselected in 127.06s**；唯一 deselect 为旧 slow real embedding。
见 [RCV-120](../verification/RCV-120-retrieval-scope.md)。没有 push，远端尚未备份这些本地增量。

B1／profiling 已由用户明确解除暂停，原生模型按 Luna medium 受控运行；RCV-118 两份真实摘要响应有独立证据。
本机未配置远程 provider 标准密钥、Chromium executable 缺失，只限制对应验收。没有阻塞本地开发的旧 HOLD。
下一首项为成功回合同事务原输入档案与 actor/campaign/revision 绑定 source read session，再接 locate/open/邻文工具；
结果先行最终正文并列 P0。现有资源先裁定已做，Hybrid 冻结正文问题未修；allowlist 不等于已完成 ACL。
不改凭据、不外传私人存档、不部署／push／PR／Library／Drive／邮件，私有解释默认关闭。

以下增量与原交接正文保留历史状态；冲突时以上当前接续点及用户最新指令优先。

## 最新增量：RCV-118 与本轮授权（2026-10-25 UTC）

已完成摘要／总概要独立有界维护，完整 gate **2142 passed, 1 deselected in 90.51s**，专项 **118 passed**。
Luna medium 两份原生响应已精确回放生产摘要请求并验证持久化；不是远程 API 验证，详见
[RCV-118](../verification/RCV-118-independent-recap-jobs.md)。仅本地提交。

用户现已明确解除 B1 detail retrieval／profiling 旧 HOLD，要求在新环境重新全量评估；指定真实模型使用
5.6 terra 或 Luna medium（当前使用可用的后者）。下文历史暂停与“不运行真实模型”条款不再阻止该授权范围，
但不改凭据、不外传私有数据、不部署、不自动 push／PR／Library／Drive／邮件、私有解释默认关闭仍有效。
原文读取的工程缺口须实际实现与验证，不能因为解除暂停就声称已完成。重新评估已完成，见 [RCV-119](../verification/RCV-119-current-environment-assessment.md)：
5,000 条未摘要旁白使上下文达 161 万字符；语义检索实际越过 day/entity 过滤、Hybrid 冻结正文等缺口已复现。
本机缺远程 provider 标准凭据及 Chromium executable，仅影响对应验收。下一实际修复为检索候选范围一致性。

## 云端接续增量：RCV-117（仅本地提交）

以下原交接正文描述 `d1833ad`／RCV-116 的历史基线。云端已从指定接续分支恢复并验证检查点祖先，
在独立 Python 3.12.14 venv 重跑得到 **2107 passed, 1 deselected in 116.43s**。
本次新实现为总概要来源链：host 在生成前绑定有序摘要、前序总概要及内容哈希；新事件 publish/replay
验证连续覆盖，旧来源保持 `legacy_unbound`，不迁移或补造原文。实现后完整 gate 为
**2135 passed, 1 deselected in 99.62s**，最终专项 **85 passed in 5.02s**。
验证与精确协议见 [RCV-117](../verification/RCV-117-recap-source-chain.md) 和
[设计增量](../designs/AGENTIC_HISTORY_AND_OUTCOME_PLAN.md)。未 push，远端仍不是本次本地增量的备份。

下一独立项是拆分分段摘要／总概要积压维护：总概要重试仍依赖本轮分段摘要成功。
原玩家输入同事务档案、结果先行正文和 agentic recall 尚未实现；locate/open/邻文工具仍与 B1 HOLD
重合，未经明确解除不得接入。所有真实模型、权重下载、私有解释默认启用和外传边界不变。

## 1. 先核对代码，不能从远端 main 的旧版本继续

- 仓库：`https://github.com/RedStoneManL/llm-rpg-engine.git`。
- 本次交接目标分支：`handoff/codex-cloud-20261025`。请在 Codex Cloud 环境／任务中明确选择此分支。
- 最新生产实现提交：`45c270dd2f145c55969629652a221f08b1d18ea4`（RCV-116）。交接文档提交是它的后继。
- 原工作分支是 `work`；它不是远端已存在分支的承诺。本文件不自行证明 push 成功，请以远端分支 HEAD 为准。
- 不要直接 checkout `main` 后重建已完成工作；不强推、不覆盖 main、不自动合并旧分支。

新任务首先执行（在实际仓库目录）：

```bash
git status --short
git branch --show-current
git log -5 --oneline
git merge-base --is-ancestor 45c270dd2f145c55969629652a221f08b1d18ea4 HEAD
```

最后一条应成功。如果没有该 object 或不是祖先，先报告分支不对，不要假装已恢复。
若用户手工 clone，可用：

```bash
git clone --branch handoff/codex-cloud-20261025 https://github.com/RedStoneManL/llm-rpg-engine.git
cd llm-rpg-engine
```

## 2. 用户目标及最近已确认的设计方向

用户要的是可长期游玩的单人 LLM RPG 世界引擎，不是聊天机器人外加一份摘要。
用户曾询问远程 GUI，因没有独立服务器已暂时放下；当前优先优化后端，不部署网站、不做账号／多人。

最近的关键纠正必须保留：**摘要不是记忆的终点。玩家记得的细节可能被摘要漏掉；问“哪天我跟某人说过什么”时，
需要 agentic 地定位历史片段、展开原文、读前后文再核对，不能拿摘要生成貌似精确的原话。**

已认可的主线：

1. 本地持久记忆：原文／来源保留，分段目录和混合索引帮助定位，agent 自主查证；不是固定 top-k 摘要注入。
2. 结果先行叙事：先裁定硬结果再生成最终正文，不能只修结构却继续展示“交易成功”等矛盾文字。
3. 长期目标、承诺、未决问题与局部后果；导演提供机会而不是直接宣告未经验证的结果。
4. 角色的经历、来源知识与私人解释分层；先一位 NPC 的有界闭环，再扩大，不让 NPC 共享全知记忆。

详细设计见 [AGENTIC_HISTORY_AND_OUTCOME_PLAN](../designs/AGENTIC_HISTORY_AND_OUTCOME_PLAN.md)。
用户已要求逐步实施，不需要每个普通本地代码修改都重新问许可；但不能把设计中的每一项表述为已实现。

## 3. 工作权限与暂停项

- 可做：源码审计、离线确定性实现／测试、本地提交、代码恢复包。每个可验收阶段保留原始失败证据与版本。
- 本次用户明确授权将当前代码和详细交接说明提交到上述 GitHub；该授权不是任意外传凭据或用户存档。
- 不做：force push、覆盖 main、部署、创建 PR、付费／真实模型调用、修改凭据、自动上传 Library/Drive/email。
- 旧 RCV-05 的 **B1 detail retrieval 和 profiling 是 HOLD**，源于此前审查拒绝。不要重试旧路线或改名绕过。
  用户现已明确要 agentic 记忆设计；这不妨碍来源协议、索引正确性和其他独立开发，但若实际接入触及原暂停范围，
  先指出具体重合与所需授权，不能仅凭泛泛“继续”认定已经解除。也不要把整个记忆研究都无差别阻塞。
- 私有解释记忆保持默认关闭。现有 opt-in 是固定本地生成器实验，不是生产 NPC 记忆。
- 不删除合理失败测试、扩大 skip/xfail 或用样例专用黑名单换绿；不要把工具失败改述为成功。
- 读取当前环境的 AGENTS.md／适用技能；前一工作区未发现 AGENTS.md，但新环境必须自己核对。
- 不自行启动并行子 agent，除非当前用户／适用指令明确授权。

## 4. 当前产品真正能做什么

### 入口与界面

- `python -m app`：CLI REPL，普通游玩必须明确选择 `--provider`。
- `python -m app.gui`：127.0.0.1-only 玩家 GUI；普通模式也必须明确选择 provider。
- `--demo`：固定离线 demo，只接受空目录或已有 demo provenance 的存档；不能给普通存档套 fake 策略。
- `--gm`：显式独立启动、只读的 canonical 世界总览，不能当成玩家 DTO，也不能展示私有解释。
- 新开局支持 pitch／flavor／blueprint／角色卡／世界书；GUI 支持 `--new-game` 及非交互来源。
- 已保存 campaign 夹带创世来源会明确拒绝，不能静默假装导入；存在 provider-free 的创世预览。
- 玩家可见：状态、物品、同场角色、任务、目标、时钟、公开 Codex、POV 地图与出口成本、近处观察、recall、journal。
- 操作包括普通行动、明确交流、会话软方向、undo/rewind 和 GUI 已提交 action 的响应丢失恢复。
- 不包含远程可访问站点、完整编辑器、GM 写入或多人。

### 内核与世界

- SQLite EventStore 是持久权威，JSONL 是镜像；Registry 为事件／commit section 指定 owner。
- EventBatch 暂存前台与相关后台事件；最终短事务内做 revision、preflight、最终 payload 校验和原子发布。
- 普通后台异常局部 savepoint 回滚，前台仍可发布；取消必须导致整回合不发布。
- 整 turn 撤回保留历史，replay 只应用活动事件；创世 turn 0 受保护。
- 显式 action-id 与请求指纹／host envelope／结果 sidecar 支持已完成行动恢复；不代表任意并发和自动重试都已完成。
- 跨实例刷新用同一快照的 history+revision，撤回带 expected revision；失效后重置作者会话缓存。
- 系统包括 ontology、place、character、object、faction、knowledge、time、scene、narrative、lore、director、cascade、codex、resources。
- 后台随玩家回合同步运行，不是离线后自主推进的常驻世界服务器。

### 普通作者的边界

CLI／GUI／compare 开启 candidate 和最终 EventBatch 的旅行、知识、world/fact、人物关系、势力、地点拓扑、物品、任务权限。
普通作者不能随意改远端／隐藏 canonical 状态；native import 保持独立策略，不能被普通模式开关误伤。
摘要、recap、私有解释均无事实或权限权威。`reported:*` 表示来源人物这样说，不证明所说内容是真的。
只有宿主签发并核验的明确交流 receipt，才允许将精确玩家原话记录为 NPC 听到。

### 隐藏发现现状：不要看旧草案就重复实现

RCV-113 首版和 RCV-114 修复已在本分支：`/discover`／GUI 附近线索核验是无目标意图。
宿主要求已保存的本地 public host clue 精确匹配一个相邻 undiscovered 路线；无匹配或歧义在作者前无写拒绝。
成功同事务写发现，只揭示不移动；下一回合才可进入。失败／取消不消费，undo 后新 ID 可重试。
`docs/designs/UNAPPROVED_HIDDEN_DISCOVERY_BC.md` 是早期草案，不是最新启用状态。

## 5. 记忆现状：不要把底座当完整能力

三层必须分开：

- canonical 事实／知识与原始事件：世界历史与来源。
- 公共旁白、分段摘要、总概要：作者语境，不是世界事实。
- opt-in 私有角色解释：actor-scoped host handle，默认不注入作者／工具／recap／GM／公共历史。

现有可复用组件：

| 文件 | 实际能力与局限 |
| --- | --- |
| `engine/archive.py` | SQLite chunks + FTS5 trigram、短词 LIKE；旧 chunk ID 为 scene+turn，需审计撤回重写碰撞，不能直接作为新来源协议 |
| `engine/vectorstore.py` | SQLite 存 float 向量，NumPy 全量 cosine 排序；没有完整 actor/revision/branch 过滤接口，不是成熟 memory service |
| `engine/embed.py` | FakeEmbedder 只测接线，不具语义；可选 FastEmbed 懒加载本地权重，默认 none；本轮未启用／下载模型 |
| `memory/recall.py` | 近期／重要性／相关性排序；不是逐步定位并展开原文的 agentic loop |
| `llm/tools.py` | 作者已有 tool registry 与执行回路，但没有本次目标的完整历史展开工具 |
| `systems/interpretive_memory.py` | 私有解释存取、绑定、原子性与隔离；固定 OfflineInterpretationGenerator，不代表智能 NPC 认知 |

“本地数据库”不等于“所有模型推理在本地”；存储、embedding、作者 provider 要分别说明。
原始玩家输入也未形成统一的成功回合来源档案；GUI envelope、sidecar、CLI transcript、heard_player 不能混作同一物。
缺失旧原话必须标明不可查证，不得从 summary 反向补写。

## 6. 最近两个实际实现

### RCV-115 — `bc285386495e6c7e6e35d543fe810f760ad29799`

旧逻辑只注入最近两个 bucket 原文，摘要失败后较老原文虽在数据库却从作者上下文消失。
`NarrativeSystem.inject` 现在保留尚无摘要且未被总概要覆盖的旧原文，成功摘要后才替换。
真实 4 个跨场景／14 个同场回合，验证每段经历恰好一次、私有文本隔离、冷重开、摘要成功和撤回。
限制：持续失败会增长上下文；没有积压调度或预算感知召回。

### RCV-116 — `45c270dd2f145c55969629652a221f08b1d18ea4`

- bucket 保存 `source_event_ids`；新 summary 的 `deltas.source` v1 含 bucket 首事件 ID、有序全部 IDs 和原文 SHA-256。
- host 在调用 summarizer 前冻结 source；模型不能自报来源；投影验证 scene、完整源集合／文本、未被摘要状态。
- 新 bound bucket 不吞后来文本，seq 不参与摘要 hash，staging／durable 一致。
- 旧无 source summary 按旧规则兼容，不迁移、不假装 provenance 已验证；不改变旧 bucket 划分。
- 总概要压缩只越过连续已摘要前缀，不跨越未摘要空洞。
- **没有**新增历史检索工具、原玩家输入事件、公共 API 或 agentic recall；来源 hash 不证明摘要语义正确。
- **没有**将总概要升级为完整来源链；它仍使用旧 `summarized_through_index`，后续须小心旧档兼容。

## 7. 测试基线与环境准备

上一环境：Python 3.12.14、pytest 8.4.2；临时 venv 路径曾为 `/tmp/llm-rpg-engine-testenv`，不能假定新机器存在。
系统曾有 Node／Chromium／Playwright，但这些不是核心 Python gate 的前提；浏览器验收需重新检查安装。

建议为云环境准备 Python 3.12 与项目依赖；不提供任何模型密钥：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pytest==8.4.2 numpy pyyaml
.venv/bin/python -m pytest -o "addopts=-m 'not slow'" -q
```

`requirements-dev.txt` 还含 fastembed 和图分析工具；核心离线 gate 不应为此下载模型或启动 telemetry。

迁移前已实际验证上述最小安装方式：2026-10-25，新建不继承 system-site-packages 的临时 venv，
安装 requirements.txt、pytest 8.4.2、NumPy 2.5.4、PyYAML 6.0.3 后，运行上述完整离线命令，
得到 **2107 passed, 1 deselected in 32.12s**。这验证了本次环境的干净安装，非永久锁定依赖保证；
新云环境仍须实际重跑，不能把历史成绩当成本机通过。未调用真实模型或下载 embedding 权重。
`pyproject.toml` 默认排除 slow。不要为了变绿去掉失败用例或扩大 skip。
若依赖／版本造成额外 skip 或失败，明确报告差异，不能直接引用旧通过数。

最新生产代码的实际完整结果：**2107 passed, 1 deselected in 32.65s**。
唯一 deselected：`tests/test_embed_real.py::test_real_embedding`（既有 slow，未调用真实 embedding）。
修改前重建 venv 后全量是 2099 passed / 1 deselected in 41.68s。不是历史报告中的 6133 或旧 README 的 1712。

RCV-116 专项：

```bash
.venv/bin/python -m pytest tests/systems/test_narrative_system.py tests/test_recap_integrity.py tests/loop/test_fleet.py tests/test_world_continuity.py tests/context/test_assembler.py -o "addopts=-m 'not slow'" -q
```

实际 57 passed in 0.86s；全新 bundle clone 再跑同组 57 passed in 1.09s。
失败证据和精确命令另见本次纳入版本控制的 `docs/verification/RCV-116-45c270d.md`。
只有本地确定性测试和先前记录的部分 Chromium 流程证据；不能外推真实模型语义质量。

## 8. 下一项怎么推进

不要继续堆小型界面按钮，也不要重做 owner skip／现有地图／明确 provider guard。

1. 首先复现恢复版本的 gate，核对现有 source v1 与设计。
2. 选择独立、可验收切片：总概要的来源摘要链／覆盖范围校验与兼容；随后拆分摘要与总概要积压维护。
3. 审计原玩家输入／明确交流／旁白的存储语义，设计同事务输入来源，不能把完整 prompt 当原话。
4. 本地目录与混合索引作为可重建派生物，绑定 campaign、actor、活动 history 和 revision；撤回或异步落后时不返回旧分支。
5. agent 的 locate→open→邻文／重查→证据回答协议应是模型可反复调用的流程，不是固定一次召回；触及 B1 HOLD 时报告具体范围。
6. 不依赖检索启用的另一路是结果先行正文：先从资源、移动、物品交接和明确交流落地。
   `HybridStrategy` 当前 prose-first 且修复时冻结正文，结构修复／丢弃可能与正文脱节；必须调整真实顺序，不能只加提示。
   publish 前仍要校验 revision；正文失败不重复扣款，action-id 恢复原已保存正文。

每项：先读实际路径→红例→共性修复→专项→全量→本地 commit→报告确切结果。连续剧情质量单独评估，不以通过数代替。

## 9. 本轮技术调研，供选型而非直接安装

研究结论：自己做 RPG 特有的来源／可见性／分支协议，复用现成搜索组件，不从零写向量数据库。
SQLite 原文＋FTS＋可替换向量接口作为初版；是否换 sqlite-vec 或 Qdrant 要通过本项目数据对比，未选定或安装。
中文短人名、别名、否认／改口、时间顺序、摘要漏词与多跳证据都需测。FakeEmbedder 不能证明语义召回。

- [OpenViking](https://github.com/volcengine/OpenViking)：L0 简介／L1 概览／L2 原文，目录式按需阅读；借鉴结构，不照搬可写全库工具。
- [Letta 实验](https://www.letta.com/blog/benchmarking-ai-agent-memory/)：agent 迭代语义搜索与文本匹配并打开文件；不是“文件名即可记忆”。
- [Hindsight](https://arxiv.org/abs/2512.12818)：经历／观点区分，时间、语义、词汇与图检索组合。
- [Graphiti](https://github.com/getzep/graphiti)：时态与 episode 来源；不要自动抽取后直接覆盖引擎 canonical 图。
- [Agent Zero Memory，2026-08](https://arxiv.org/abs/2608.29606)：混合查证与已打开证据引用；论文作者的领先成绩不等于游戏实测，引用约束不保证理解零误差。
- [sqlite-vec](https://github.com/asg017/sqlite-vec)：SQLite 本地向量扩展，官方仍提示 pre-v1／破坏性变更风险。
- [Qdrant client](https://github.com/qdrant/qdrant-client)：有本地持久模式，适合比较原型；未因此选择独立服务。

这些链接是 2026-10-25 核对过的调研材料；后续选择新版本时重新验证文档与许可证，不照排行榜声称 SOTA。

## 10. 找代码和证据

| 关注点 | 优先入口 |
| --- | --- |
| action 原子性、恢复、authority | `loop/turn.py`, `engine/store.py`, `tests/test_action_integrity.py` |
| replay／owner | `kernel/projection.py`, `kernel/registry.py`, `tests/kernel/test_projection.py` |
| 作者正文／结构／工具循环 | `loop/strategy.py`, `llm/provider.py`, `llm/tools.py`, `loop/compare.py` |
| 记忆／摘要 | `systems/narrative.py`, `loop/fleet.py`, `context/assembler.py`, `tests/test_recap_integrity.py` |
| POV／明确交流／发现 | `context/access.py`, `context/capabilities.py`, `context/native_authoring.py`, `systems/knowledge.py` |
| 玩家入口 | `app/play.py`, `app/gui.py`, `app/engine.py`, `app/player_*.py` |
| 私有解释 | `systems/interpretive_memory.py`, `tests/systems/test_interpretive_memory.py` |
| 资源裁定 | `loop/resources.py`, `systems/resources.py` |

`PRODUCT_EXECUTION.md` 是累计执行记录，但首页部分概述仍是早期状态；结合最后更新、源码和本文件阅读。
`docs/HANDOFF.md`、`docs/MODULE_INDEX.md` 和早期 HTML report 是辅助资料，不能替代当前 commit 的验收。

## 11. 本次不迁移的东西

- 不包含个人 campaign 数据库、JSONL、调试 trace、浏览器 profile、密钥或实际 env。
- 大量 `review_material/` 和根目录恢复包过去是 untracked；不要对整个目录 `git add .`。
- 本地 RCV-116 bundle/source/diff 已验证；仅小型文本验证记录随本次交接纳入 Git，完整包不塞进源码仓库。
- 早期 Library 上传长期 network 失败，Drive 只交付过很老的版本且内容下载校验受限。它们都不是当前源码恢复前提。
- GitHub 本分支若推送验证成功，即作为本次代码恢复入口；旧“禁止任何 push”描述由这次特定目标授权更新，不代表其他外传已授权。
- 新云环境不会自动继承此对话、临时文件、已安装依赖、用户存档或私人权限；不得声称完整 session 已迁移。

## 12. 可直接用作 Codex Cloud 首条任务

> 接续本仓库，不从 main 旧版本重建。先完整阅读 docs/progress/CODEX_CLOUD_HANDOFF.md、
> docs/designs/AGENTIC_HISTORY_AND_OUTCOME_PLAN.md，以及 docs/progress/PRODUCT_EXECUTION.md 的最近记录；核对当前 HEAD
> 包含 45c270dd2f145c55969629652a221f08b1d18ea4，按新环境指令恢复离线测试并报告实际结果。
> 用户要本地 agentic 分段记忆：摘要只导航，精确内容需找原文，来源／角色／分支必须隔离；还要先裁定硬结果再写最终正文。
> 从总概要来源链或其他已授权、无暂停依赖的高价值切片实际实现并验收，不只写计划。不调用真实／付费模型，不改凭据，
> 不部署、不外传私人数据、不强推或改 main；B1/profiling 原暂停不绕过。每个阶段本地 commit，保留红例、测试命令和局限。
