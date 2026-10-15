# RPG 产品与持续开发执行追踪

最后核对：2026-10-15 UTC。本文是用户指定的持续执行准则与当前仓库恢复工作的唯一执行入口；历史
Library 文档记录的是另一份后续代码快照，不能替代这里的源码与测试证据。仓库中没有另一份可核实的
“整体架构 prompt”文件能取代它；设计原则、已验收事实、开放风险和下一项必须在本文件持续更新。

## 持续执行约定（用户确认）

- **项目完成前持续推进。** 阶段验收、备份、测试通过或单轮结束都不是停止条件；每个检查点后自动选择并推进
  下一项已获授权、可独立完成的高价值工作。某条路线受阻时继续其他已获授权的工作；只有确实必须由用户解决的
  问题才准确报告，不擅自扩大权限、绕过拒绝，或把这条持续开发指令解释为外传、付费模型或其他新授权。
- 修复必须面向共性根因与可复用边界，禁止为单一样例堆条件分支、删除合理失败测试或把离线替身当作产品
  质量结论。每轮优先做一次跨模块审视，沉淀公共 DTO、事务/可见性边界或持久化协议，而不是散弹式补丁。
- 离线/fake/Chromium 回归用于验证确定性边界和可恢复性；同时要安排受控剧情场景的原生 agent 可玩性、
  一致性与信息可见性评估。该评估不新增外部付费模型调用、不修改凭据；结果要与模拟测试的结论分开记录。
- 有限 native 验收必须按证据强度分层记录：原始候选、实际普通入口请求消息和本地确定性提交可证明该候选在
  当时的产品边界、持久化与恢复协议下被如何处理；它们不能证明付费/远程模型质量、长期剧情质量或浏览器点击
  行为。若回合触发摘要，必须逐请求比对既有受控摘要消息与原 response，不能把旧 fake/占位摘要改写成
  “真实”摘要。一次已收束的同类 native 切片只保留工件和结论，不为增加测试数反复重放；只有发现新的共享
  边界、用户明确要求或后续功能使其失效时才重开验收。
- 上下文按任务复杂度慷慨使用，并以 agentic 渐进披露（先读取相关模块/测试/证据，再扩展）保持可审计；
  不以机械 token 阈值中断有效开发。

## 可复现基线

- 初始恢复基线：本地分支 `work` 的 `6b544111a1b0e26fbc232ba0dcb3df33e92b8dbb`。
  这不是当前 HEAD；当前实现检查点另见本页更新记录与 `git rev-parse HEAD`。
- 没有恢复到历史报告中的 `9928c1d2`，也没有 `AGENTS.md`、PreparedTurn 或 GUI
  子项目实现；不得把历史的 6133 通过当作本基线结果。角色解释记忆现仅有本台账
  所列、默认关闭的离线 CLI 实验入口，不能与历史所称生产能力混同。
- 初次全量离线 gate（2026-10-11）：`PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest`
  得到 **1705 passed、7 failed、1 deselected**。失败均为 `tests/app/test_trace_view.py`
  将子进程 cwd/PYTHONPATH 固定为失效的旧绝对路径 `/root/rpg-engine-app`；原始失败
  应保留到该根因修复后的回归记录中。
- 测试依赖仅临时安装在 `/tmp/llm-rpg-test-deps`；没有调用付费模型、没有远程 push。

## 当前能力与缺口

| 区域 | 已从源码核对的能力 | 未验收/缺口 |
| --- | --- | --- |
| 回合持久化 | `EventBatch` 在最终 SQLite publish 时做 revision/preflight/整批事务，撤回按 turn 保留历史 | 仍需审计所有后台写入是否共享同一批次，及失败/取消/重开完整回放 |
| 事实和投影 | Registry 为事件与 commit section 指定单一 owner；projection 忽略 retracted events；直接 `apply_turn` 现在先检查全部 section owner 并 fail-closed | 旧历史流中的无 owner event 仍为兼容性而由 projection 记录 debug 后忽略；须审计其迁移/读档边界，不能把它与新写入混同 |
| 叙事与摘要 | narration、digest/recap 与 `working_memory` 已有旧实现 | 需验证摘要失败的局部性、回放一致性以及摘要不作为事实权威 |
| 可见性和记忆 | 旧 POV/knowledge 过滤与 recall 模块存在；`--offline-private-memory` 在显式启用时注册私有解释 slice、固定本地生成器和当前主角 `/memory` 宿主读取 | 仍没有通用 event-level ACL、GUI/GM/raw-store 安全出口或真实模型验收；默认不启用私有记忆 |
| 前端/运维 | CLI、trace viewer、portable handoff 存在；`app.gui` 已有仅回环的玩家游玩页、显式启动的只读 GM 总览与公开 Codex 浏览 | 没有世界书/角色卡编辑器、GM 写入能力、账户/多人协作、部署或生产 web runtime；玩家图仍需从出口文本升级为严格 POV 的可视关系图 |

## 工作队列

| ID | 优先级 | 状态 | 根因与下一动作 | 验收 |
| --- | --- | --- | --- | --- |
| RCV-01 | P0 | 已验收 | 清除 trace CLI 测试中的旧绝对 checkout 假设 | `tests/app/test_trace_view.py` 38 passed；完整 gate 1712 passed / 1 deselected（16.66s） |
| RCV-02 | P0 | 已验收（本基线范围） | 对 `run_turn`/`EventBatch`/后台 hooks 做实际原子性与 owner 拒绝审计 | 已覆盖后台失败撤回、嵌套 savepoint、direct `apply_turn` owner fail-closed、撤回后的关闭/重开/完整 replay；新后台类别仍须随新增功能各自回归 |
| RCV-03 | P1 | 已验收（离线 CLI 实验闭环） | 显式 opt-in 的角色解释性记忆入口 | `--offline-private-memory` 才注册模块与固定本地生成器（不覆盖前台 provider/embedder）；`/memory` 仅通过宿主 handle 读取当前主角；temp DB CLI 覆盖默认关闭、写入、重开、跨角色隐藏、同一 CLI 会话内 undo/rewind 与公开出口隔离。仍非通用 ACL 或生产记忆系统 |
| RCV-04 | P1 | 已验收（离线 recap 边界） | 摘要/recap 与事实权威边界 | 真实 `run_turn → digest_fleet` 覆盖摘要异常的局部失败、成功摘要、冷重开 replay、receipt/revision/结果事件、私有记录不进入摘要提示或 resume；摘要保留为公共叙事文本，不能改写 ontology facts |
| RCV-06 | P1 | 已验收（离线 CLI 状态闭环） | CLI 回合/摘要/私有实验状态可见性 | 成功回合显示 `[已保存]`；真实摘要失败显示无正文的 `暂未生成`；启动明确私有实验开关；固定无网络演示覆盖默认/显式、续玩、`/memory`/`undo`；状态不新增事件或重试玩家回合 |
| RCV-08 | P1 | 已验收（离线玩家 GUI 首切片，独立复核修正） | 仅 `127.0.0.1` 的玩家游玩/仪表盘 | Chromium 覆盖 response-loss→刷新→同 action-id 恢复零增量；Python 覆盖活动叙事、revision-bound undo、写入同源边界、POV 选路、显示失败后已提交状态与 ambient embedder 禁用；不包含 GM/editor、部署、真实模型或私有记忆启用 |
| RCV-09 | P1 | 已验收（显式只读 GM 总览） | 启动时隔离的 canonical 世界总览、地图、系统、时间线与公开 Codex | player/GM 路由互斥；GM 无写路由且不显示私有角色解释；其引用型私有泄漏已 fail-closed |
| RCV-10 | P1 | 已验收（正常玩家入口与恢复上下文） | 正常 GUI 接既有 `AuthorStrategy`/provider、原 action-id 的冷重开恢复 | 不降级为 demo；host scene/prev_scene 同事务写入 sidecar；旧缺失 envelope 明确 409；实测 server cold restart 恢复 |
| RCV-11 | P1 | 已验收（修正后：玩家地图与行动边界） | 统一 canonical/POV 世界地点图 read-model，并把玩家出口文本升级为可视、只读地图 | 同一 map DTO 算法分别消费 POV 与 canonical 图；玩家 DOM/API 不含隐藏地点/边或私有文本；三项 repair/final-payload/缺起点 fail-closed 修正已补；下一项才是 event-owned 发现状态 |
| RCV-13 | P0 | 已验收（普通作者安全材料与 GUI 恢复闭环） | 普通 CLI/GUI（含 compare）对主角旅行与明确玩家对 NPC 原话使用发布边界；native importer 保留独立的 native policy + receipt 审查 | CLI/compare 最终 apply 现在与 GUI 同样开启 `enforce_player_travel`；`heard_player` 必须携带宿主重算验证的精确 receipt，默认普通入口不签发，native importer 只显式转交其已审查的 receipt；普通 `AuthorStrategy` 仅接收 native 安全投影后的文本，不接触 raw events、隐藏资料或新 receipt；已提交 action 的 GUI 原话/收件人不匹配走未确认的 409 恢复路径，绝不作为可丢弃的 400；receiptless RCV-12 action-id sidecar 兼容与最终 event payload 复核另见本轮末项，不能把 a715e09 单独当验收版本 |
| RCV-18 | P0 | 已验收（普通作者知识权限边界） | 普通玩家作者不得把自由玩家文本洗成 NPC 知识 | 候选与最终 EventBatch 双校验：普通入口只允许 protagonist 的通用知识；NPC 的玩家原话必须精确匹配 host receipt；trusted native import/history 保持独立 policy。专项 100 passed；完整 gate 1914 passed / 1 deselected |
| RCV-14 | P1 | 已验收（普通作者上下文契约对齐） | 核对真实 CLI/GUI `AuthorStrategy` provider 消息，并移除与 POV 行动范围矛盾的“完整世界视野”指令 | 两条正常入口均实际传入主角长期目标、当前 `scene_capabilities` 行动范围和持久公开地点观察；私有 sentinel 不入 prompt。普通作者可在已授权范围内推进到有意义结果、风险或分岔，不被要求把每次视线变化当作停点；未提供的远方/隐藏/未发现地点不得由普通作者写成已发生的 world 变化 |
| RCV-19 | P1 | 已验收（玩家目标与任务账本） | GUI 已有叙事/物品/地图/Codex，但不显示主角长期目标或引擎已有的明态任务，长局缺少可见的推进锚点 | DTO 仅从 protagonist POV 和 LoreSystem 明态/此前明态后已结的公开一行摘要生成；真实 state/WSGI 刷新覆盖目标、任务 open/advance/resolve、撤回/重开，暗线/secret/private sentinel 不泄漏；不新增写入口、作者权限或自动剧情 |
| RCV-20 | P1 | 已验收（出口行动建议） | 玩家地图列出了严格 POV 的可达出口，却不能把它变成可编辑的行动起点 | 每个现有 `scene.exits` 只生成浏览器本地的“填入行动”建议；空框填入、已有输入追加而不覆盖，点击不提交/不移动/不建 action-id，仍由既有回合验证决定移动是否合法 |
| RCV-21 | P1 | 已验收（出口成本提示） | 玩家可选择已知出口，但无法看见同一 POV 路由已有的移动成本 | DTO 仅把 `SceneCapabilities.routes` 中当前地点的公开 cost 写入 GUI 专用平行 `exit_costs`，保持 native `available_exits` 形状不变；浏览器只读显示，不改移动、路线、发现、提交或权限 |
| RCV-23 | P0 | 已验收（普通玩家本地世界观察边界） | 普通 CLI/GUI/compare 作者拿到的是 POV 安全材料，却仍可用 `world` 段改写任意 canonical 地点 | 候选与 final `EventBatch` 双校验仅允许 action-start 或合法同回合到达地点的 `level=1`、显式 public、有范围的观察；world 模式写入 action-id 指纹/sidecar，跨模式恢复 fail-closed；native importer 继续由独立 native policy 管理 |
| RCV-24 | P0 | 已验收（普通玩家 canonical fact 边界） | RCV-23 后普通作者仍可用 `facts` 段为远端/隐藏 canonical 实体写入事实 | 普通 CLI/GUI/compare 的候选与 final `EventBatch` 只允许主角的 public 自事实，或当前公开人物的 public `reported:*` 来源陈述；局部地形改用 RCV-23 world observation；模式进入 action-id 指纹/sidecar，跨模式恢复 fail-closed；native importer 保持独立 policy |
| RCV-25 | P0 | 已验收（普通玩家角色／关系 canonical 边界） | RCV-24 后普通作者仍可用 `cast.evolve` 改写 POV 外远端角色 canonical 状态，或借通用实体／关系段触碰隐藏拓扑 | 普通 CLI/GUI/compare 的候选与 final `EventBatch` 禁止通用 `entities`／`relations`；仅允许新鲜、公开的角色创建，或主角／当前公开人物的 `mood`／`state` 演化，以及主角指向当前公开人物的关系变化。模式进入 action-id 指纹/sidecar，跨模式恢复 fail-closed；native importer 保持独立 policy |
| RCV-26 | P0 | 已验收（普通玩家本地物品交接与可玩性） | 普通作者可把主角背包物品直接转交给 POV 外远端角色；同时此前没有受明确契约保护的正常本地交接路径 | 普通 CLI/GUI/compare 的候选与 final `EventBatch` 只允许把 action-start 主角背包中、玩家 POV 可见的物品交给当前公开人物，或放在当前公开地点；不允许创建资产、操作非背包物品或交给远端／隐藏目标。模式进入 action-id 指纹/sidecar，跨模式恢复 fail-closed；同场公开人物的实际交接、duplicate 与冷重开已验收；native importer 保持独立 policy |
| RCV-27 | P0 | 已验收（普通玩家任务账本本地边界与可玩性） | 普通作者可将并未在当前位置披露的远端暗线直接 surface 为明账，或推进远方任务 | 普通 CLI/GUI/compare 的候选与 final `EventBatch` 只允许接取 fresh public task、surface 当前 station-push 披露的暗线、推进/收束当前城镇任务或无 host anchor 的普通公开任务；宿主自动 lore 生命周期事件不被误作作者 payload。模式进入 action-id 指纹/sidecar，跨模式恢复 fail-closed；普通 GUI 控制器链的离线 fixture 覆盖接取、推进、duplicate 与冷重开；native importer 保持独立 policy |
| RCV-28 | P1 | 已验收（CLI 软转向正常流程） | `//steer` 仍只是占位提示，玩家无法在不倒带的情况下表达后续叙事偏好 | `//steer <方向>`/`/steer <方向>` 仅保存在当前 CLI 会话，作为明确标记、JSON 引用的 Author/Hybrid prompt 偏好；不写 EventStore、transcript、receipt、路线或事实，`clear` 后重置策略会话以免旧偏好残留。普通旅行/发布边界不因偏好放宽 |
| RCV-29 | P1 | 已验收（玩家 GUI 公开时间方位） | 引擎已维护公开 day/band，CLI/Author 可见，但玩家仪表盘只有回合与版本，长局无法从页面判断游戏内时间 | DTO 只读取已有 `meta.day/band` 并显示在 player status；真实 dashboard 回合、undo、WSGI 刷新和冷重开均从 EventStore replay 同一时钟。无新时间事件、写路由、权限或后台时钟 |
| RCV-30 | P0 | 已验收（普通 Author/Hybrid 发布契约对齐） | RCV-23～27 已收紧普通玩家作者的 world/fact/entity/item/quest 发布边界，但两份实际模型提示仍保留 GM/全局事实示例，Hybrid 甚至声称可见完整世界，容易让正常行动先产出必拒候选 | 两个提示均只说明普通回合可发布的本地公开观察、public fact、人物／物品／任务范围；真实 `PlayerDashboard → HybridStrategy → run_turn` 离线双调用提交公开移动，并验证安全上下文不含私有 sentinel。不是浏览器点击或真实模型质量验收 |
| RCV-31 | P1 | 已验收（CLI compare 后台 provider 契约） | `/compare on` 的候选生成后会走一次 `PreparedCandidate → run_turn` 持久化，但该最终调用漏传既有 cascade provider 作为 catch-up provider，与普通 CLI/GUI 路径不一致 | compare 最终 apply 显式同时传入 cascade/catch-up provider；真实 CLI compare 的 stale-NPC 场景实际触发专用 catch-up，关闭/重开后 canonical 演化仍在。未做浏览器或真实模型调用 |
| RCV-32 | P0 | 已验收（普通玩家势力 canonical 边界） | 普通 CLI/GUI/compare 作者仍可输出 `factions`，直接改写长期势力创建、成员、位阶或群组，且当前玩家 DTO 没有可审查的公开势力遭遇／加入 capability | 候选与最终 EventBatch 双校验拒绝任何普通 factions 写入；普通 GUI、compare preview、final-payload 篡改与 action-id mode binding 均零写通过。宿主／导演／native import 保持独立 policy |
| RCV-33 | P0 | 已验收（普通玩家地点拓扑／人物移动边界） | travel gate 只验证主角目的地，普通作者仍能以 `links` 改写既有地图，或以 `moves` 调度既有人物；`materialize` 也没有普通入口权限 | 候选与最终 EventBatch 双校验仅允许全新 public 前沿地点的一条当前位置直连，以及新建公开人物随主角／留在当前位置；既有拓扑、既有人物移动和 materialize 均零写拒绝，普通公开前沿创建＋移动仍可用 |
| RCV-34 | P0 | 已验收（后台钩子局部原子性／冷重开） | 台账只对 digest/memory 明确验证过“写入后异常”局部回滚；director、cascade、catch-up、lore、demote、density 的同类风险没有统一 SQLite/JSONL 回放证据 | 七个 post-foreground hook 均实测先写 sentinel 再异常时只回滚自身，前台回合保持；SQLite 与 JSONL 重开一致。维持既有“后台非致命、前台不回滚”语义；取消语义见 RCV-65 |
| RCV-35 | P1 | 已验收（debug trace 私有解释隔离） | Debug trace 是本地 operator 原始 prompt/output 产物；已有 CLI/GUI/GM 记忆隔离，却未以实际 `--debug` 回合验证私有解释不进入 trace/viewer | 真实 opt-in memory + `--debug` 回合写入私有记录后，trace 原文与 viewer 全记录均不含私有正文；trace 继续只作为本地 operator 诊断，不是玩家 API |
| RCV-36 | P1 | 已验收（当前严格 POV 地图浏览器复验） | RCV-11/20/21 已实现 POV 关系图、出口成本和仅填入行动，但之后多轮 GUI 组合变更缺少当前执行环境的完整浏览器证据 | loopback Chromium 实测隐藏地点不进 player API/DOM，图为仅 POV 节点/边；选点与“填入行动”零写入，390px 无溢出 |
| RCV-37 | P1 | 已验收（GUI 会话软方向） | CLI 已有非持久 `//steer`，但 GUI 玩家不能表达后续叙事偏好；不能把偏好误写成世界事实或 action-id 恢复输入 | GUI `/api/steer` 只更新 controller 会话、变更/清除重置策略缓存；实际行动才将其传入 prompt。偏好不进入 EventStore、transcript、host envelope 或 request fingerprint，冷重开 duplicate 仍恢复已提交结果 |
| RCV-38 | P0 | 已验收（CLI recall POV 边界） | 玩家 OOC `/recall` 直接把 canonical world 交给系统 fan-out；猜测隐藏 id 可把未发现地点名称和种类当作查询结果输出 | 查询前用当前主角 scene 的 `pov_world(..., redact_facts=True)` 投影；公开资料仍可检索，隐藏地点猜测无结果，命令继续零回合／零写入 |
| RCV-39 | P0 | 已验收（旧无主事件重开边界） | 兼容读取中无 owner 的旧事件会跳过系统 apply，却仍先推进 `meta.day/scene/timeline`，使恢复后的当前场景与实际投影脱节 | projection 先解析 owner；无 owner 时整条事件仅 debug 记录后忽略，不更新 meta/timeline。真实 SQLite/JSONL 旧 campaign 重开保持只读，已验证当前场景仍来自最后拥有者事件 |
| RCV-40 | P1 | 已验收（玩家 GUI POV recall） | CLI recall 已有安全投影，但 dashboard 没有等价的长期游玩只读查询；另起实现容易重新把 canonical world 接到浏览器 | 共享 `player_recall` 只接受当前 protagonist 的 redacted POV；GUI `GET /api/recall` 与页面查询只返回受限文本、零事件／receipt／revision 写入，隐藏 id 不命中而公开资料可用 |
| RCV-41 | P0 | 已验收（GUI 撤销作者缓存） | CLI `/undo`/`/rewind` 会清空多回合作者缓存，但 GUI 撤销只重置上一场景，可能让下一次作者请求携带已撤回的玩家交流 | GUI `undo()` 在成功 rewind 后同样 reset 策略缓存；真实 controller 的多回合 Author 回归确认第二次 prompt 不含已撤回行动，canonical world 仍由 replay 决定 |
| RCV-42 | P1 | 已验收（GUI 启动模式如实标识） | 正常已保存 campaign 的玩家页也固定写成 `OFFLINE DEMO`／离线试用，容易误导用户把真实 normal author 流程当替身 | HTTP shell 仅按 `dashboard.demo` 渲染 `OFFLINE DEMO` 或 `SAVED CAMPAIGN` 与相应本地说明；不改变 DTO、provider、行动、事件或权限 |
| RCV-43 | P0 | 已验收（冷重开 catch-up 作用域恢复） | catch-up 的“本回合进入视野”判断只保存在 CLI/GUI controller 内存；重开会把持续同场的 stale NPC 误当新进入，或若粗暴禁用又会丢失真实抵达后的待处理 catch-up | 从最新仍未撤回 action receipt 的首 seq 前，在单一 SQLite 只读 snapshot 重放 action-start scene；CLI 和 GUI 均采用它。回归同时证明持续同场不误演化、真实抵达后重开仍会演化、撤回 receipt 不作锚点 |
| RCV-44 | P0 | 已验收（撤销／回退 catch-up 作用域恢复） | RCV-43 修复了冷重开，但 CLI `/undo`／`/rewind` 与 GUI undo 在 revision 改变后仍强行清空作用域锚点；下一动作会把持续同场 stale NPC 误作新进入 | 成功 rewind 后 CLI 与 GUI 都从最新存活 action receipt 重建 prior scene；真实 `行动→undo→行动` 回归确认没有额外 catch-up／`character_evolved`，策略缓存 reset 语义不变 |
| RCV-45 | P0 | 已验收（乐观版本冲突后的 scope／世界恢复） | CLI 在 `RevisionConflict` 后虽重放 world 并 reset 作者缓存，却保留过期 `prev_scene`；GUI 还会把已确定未提交的冲突泛化为 500，内存 world 与下一行动 scope 都可能过期 | CLI/GUI 都在**无同 ID receipt**的冲突后只读投影当前 store、重建最新存活 action 的 action-start scope 并 reset 策略；GUI 返回可安全丢弃的 400。已有同 ID durable receipt 仍走保守 409 恢复，绝不误称未提交 |
| RCV-46 | P0 | 已验收（GUI 过期撤销后的 authoritative refresh） | 另一实例已保存行动后，GUI 对过期 revision 的 undo 会拒绝，却保留旧内存投影，使紧随其后的 `/api/state` 仍与 SQLite 不一致 | action conflict 与 stale undo 共用只读刷新：重放 store、重建 action-start scope、reset 策略；undo 仍不写入/不撤回任何事件，返回 confirmed 400 后 `/api/state` 立即反映 peer action |
| RCV-47 | P0 | 已验收（GUI 被动读路径跨实例同步） | RCV-46 只在过期写请求被拒绝时刷新；另一实例已发布而玩家只请求 state/recall 时，dashboard 仍可能展示旧 projection | state、recall、action 入口先比较缓存 revision 与 SQLite；仅不同时只读重投影、重建 scope、reset 一次。被动 GET/recall 不写事件、receipt 或 transcript |
| RCV-48 | P0 | 已验收（GM 只读跨实例同步） | 独立 GM dashboard 也缓存启动时 projection，却没有玩家写入口或 revision-bound 拒绝路径；另一玩家实例发布后，GM 的普通 GET 会永久停在旧世界 | GM `state()` 先比较缓存 revision 与 SQLite；仅不同时只读重投影后再构建 canonical、私有解释仍过滤的 DTO。GM GET 不写事件、receipt 或 transcript |
| RCV-49 | P0 | 已验收（CLI 被动读路径跨会话同步） | 长运行 CLI 在两条输入之间可被另一 CLI／dashboard 写入；`/recall`、`/speak` 和下一普通行动原先都从旧 projection 构建 scene | 每条有效输入前比较缓存 revision 与 SQLite；仅不同时只读重投影、恢复最新存活 action-start scope、reset 策略缓存。被动 OOC 读取不写事件、receipt 或 transcript |
| RCV-50 | P1 | 已验收（CLI transcript canonical turn） | 可选 `transcript.jsonl` 以 REPL 本地计数写 `turn`，冷重开、rewind 或 peer publish 后可与 durable campaign 回合脱节甚至重复编号 | 每个普通行动以 `store.next_turn()` 标识当前 canonical 回合；冷重开追加记录、rewind、peer publish 和失败后重试不再凭本会话计数伪造编号 |
| RCV-51 | P1 | 已验收（CLI transcript revision identity） | canonical turn 在 rewind 后会被新分支复用；仅有 `turn` 仍无法区分已撤回分支与重写后的同回合记录 | 每个成功 transcript row 同时记录 publish 后的 store revision；不改 EventStore、回合语义或 transcript 的非权威定位 |
| RCV-52 | P1 | 已验收（CLI transcript receipt linkage） | 普通 CLI 回合实际有自动持久化 action receipt，但 transcript 未记录其 action ID，无法精确映射到 receipt／recovery evidence | 每个成功 single/compare transcript row 记录既有 receipt action_id；不新增 CLI 自动重试、读取接口或持久化协议 |
| RCV-53 | P1 | 已验收（CLI 玩家态势查看） | 终端 REPL 是正常游玩入口，但玩家在回合间没有只读查看当前位置、同场角色、背包和已知出口的命令 | `/status` 只从主角 redacted POV 和既有 scene capability 构建态势，不写事件、receipt、transcript 或权限；隐藏地点/事实不输出 |
| RCV-54 | P1 | 已验收（CLI 长期目标与公开任务） | `/status` 补齐近景态势后，CLI 仍缺 GUI 已有的长期目标和公开明态任务，长局缺少推进锚点 | GUI/CLI 共享同一 protagonist POV objective read-model；只显示目标与 Lore `明` 态摘要，排除暗线、stages、provenance、私有解释与 GM 状态 |
| RCV-55 | P1 | 已验收（CLI 公开 Codex 浏览） | GUI 已有公开世界设定典，但终端 REPL 只有模糊 `/recall`，不能枚举或按关键词筛选公开设定 | `/codex [关键词]` 只读取既有 Codex public index，按 kind/title/body 筛选；零事件、receipt、transcript、revision 写入，且不接 raw store、GM 或私有记忆 |
| RCV-56 | P1 | 已验收（CLI 显示名明确交流） | `/status` 只显示同场角色的公开显示名，而 `/speak` 过去实际只接受内部 ID，玩家无法直接使用状态页信息发起既有的明确交流流程 | `/speak` 只将唯一的当前公开显示名（空格名可加引号）或既有 ID 解析为 canonical recipient；重名、隐藏或过期名称 fail-closed，既有 host receipt 仍为最终同场授权 |
| RCV-57 | P1 | 已验收（CLI 主角公共状态） | GUI 已有主角可见事实，终端 `/status` 却无法让玩家查看自身的公开状况 | CLI/GUI 共享 protagonist POV character read-model；只显示非 secret、非 hidden 的当前事实，不读取 canonical/raw history/私有解释，也不写入状态 |
| RCV-58 | P1 | 已验收（CLI 已结任务） | `/status` 已显示公开进行中任务，却遗漏既有安全任务账本中的公开完成项，长局终端无法确认任务进展 | 复用 `player_objectives.completed_quests`；只显示先前已公开的已结摘要，暗线／仅后台过期项继续排除，命令零写入 |
| RCV-59 | P1 | 已验收（CLI 公开行动日志） | GUI 有最近公共叙事面板，长运行终端只能依赖滚屏，无法以受限方式回看当前分支的最近行动 | `/journal` 仅消费 `public_history()` 的活动 `narration_recorded` 文本并限至最近 8 条；私有记忆、撤回叙事、raw history 与任何写入路径均不进入 |
| RCV-60 | P0 | 已验收（CLI journal 私有引用 fail-closed） | `public_history()` 会拒绝普通事件显式引用私有记录；未处理的 OOC journal 读取可能中断 REPL | `/journal` 将该 DTO 边界转成无正文的通用不可用提示；不回显私有或相关普通正文，零写入 |
| RCV-61 | P0 | 已验收（续玩 recap 私有引用 fail-closed） | 启动续玩直接读 narrative 投影；普通 narration 若错误引用私有记录，可绕过 public-history 的 fail-closed 边界 | 真实 engine 的续玩文本只从 `public_history()` 提取公共 narration/recap；DTO 拒绝时压制叙事正文且继续启动，不输出异常或私有内容 |
| RCV-62 | P0 | 已验收（玩家 dashboard 局部 fail-closed） | player state 读取 narration 时会传播 public-history 私有引用拒绝，导致整个安全只读页面变为 500 | state 仅压制 narration 并标记不可用；地图、背包、任务等手工 POV DTO 仍安全返回 200，零写入、无正文泄露 |
| RCV-63 | P0 | 已验收（创世保护的玩家倒带） | 无玩家回合时 CLI `/undo` 取 turn 0，旧实现会撤回全部创世事件；`/rewind 0` 同样可误删世界 | CLI 与 `app.engine.rewind` 仅接受正玩家 turn；空 undo/非正 rewind 明确拒绝且数据库、JSONL、revision 全不变 |
| RCV-64 | P0 | 已验收（维护 CLI 创世保护） | 独立 `engine.rewind`／`bin/rpg rewind` 仍接受 0，可绕过 RCV-63 的 app 层保护并撤回 turn-0 事件 | legacy rewind API 与维护 CLI 同样只接受正 player turn；`--last` 正常回退保留，非正输入零写入 |
| RCV-65 | P0 | 已验收（后台取消全回合原子性） | RCV-34 覆盖七钩子异常的局部 rollback，但除 digest 外取消仅隐含依赖运行时继承关系，未以写入后取消证明所有入口都不会半发布 | director、cascade、catch-up、lore、demote、density 显式将 `asyncio.CancelledError` 上抛；七钩子均实测 partial write 后取消，前台、receipt、revision、SQLite/JSONL 与冷重开完全不变 |
| RCV-66 | P0 | 已验收（GM 历史局部 fail-closed） | 普通事件引用私有记忆时，GM 虽不应显示任何相关正文，却会因 `public_history()` 拒绝而使整个只读 canonical dashboard 变为 500 | GM 将时间线与 recap 作为同一公开历史切片压制，显式 `history_unavailable`；canonical 地图、实体与事实继续只读返回 200，零事件／receipt／revision 写入 |
| RCV-67 | P0 | 已验收（native brief 历史局部 fail-closed） | 安全 native-author 导出先构造 player DTO，却再次直接读取 `public_history()` 填充已知事件；私有引用会让导出失败，且调用方可能记录异常 | 只压制 `known_events`／`public_narrative` 并标记 `history_unavailable`；其余已有的 POV、背包、出口与授权安全 brief 保持离线、零 campaign 写入 |
| RCV-68 | P1 | 已验收（CLI 严格 POV 地图） | GUI 有严格 POV 关系图，CLI 只有当前出口文本；终端长局无法查看已知地点与路径，又不能接 canonical 图补齐 | `/map` 仅消费既有 `scene_capabilities` POV contract，显示节点、路径和成本；无发现、移动、receipt、transcript 或 revision 写入 |
| RCV-69 | P0 | 已验收（journal 跨入口活动分支回归） | CLI journal 已有单实例私有引用边界，但缺少 peer publish→undo 与冷重开组合证据 | 纯回归覆盖公开 peer narration 及时出现、撤回后消失、冷重开不复活；私有引用在冷重开后继续 fail-closed、零写入 |
| RCV-70 | P0 | 已验收（status 跨入口任务账本回归） | CLI `/status` 已有 POV 公开任务 read-model，但缺少 peer publish→撤回、暗线隔离、冷重开及 OOC 零写入的组合证据 | 纯回归覆盖 peer 公开任务即时出现、同回合暗线不泄漏、撤回后隐藏、冷重开不复活，并逐字比较 SQLite/JSONL 与 revision，确认 CLI 读取零写入 |
| RCV-71 | P0 | 已验收（撤销 optimistic revision 原子性） | CLI/GUI 撤销先读当前 turn、再软撤回；peer 若在其间发布，旧请求可能撤掉新分支 | `EventStore` 在同一 SQLite 写事务内复核可选 expected revision；CLI/GUI 都携带该版本，冲突时只读重投影、清策略缓存并确认“未撤销”，不改 peer 分支 |
| RCV-72 | P0 | 已验收（跨实例投影快照一致性） | CLI、玩家与 GM 被动刷新分别读取 revision 与 events；peer 在两读之间发布时，旧投影可能被错标为新版本 | 共享重投影 helper 从同一 `EventStore.snapshot()` 获取 revision 与 history；启动、CLI、玩家、GM 与 rewind 均复用，下一次刷新才推进到 peer 版本 |
| RCV-73 | P0 | 已验收（CLI undo 选回合／版本绑定） | `/undo` 的最后活动回合与 expected revision 仍是两次读取；peer 夹在中间发布会让旧回合被错误地按 peer 新版本批准 | 从同一 `EventStore.snapshot()` 取得活动回合 frontier 和 revision；后续 retraction 仍在 SQLite 写事务复核，冲突只读刷新且不撤回 peer 分支 |
| RCV-74 | P0 | 已验收（被动同步比较／显示版本原子性） | CLI、玩家和 GM 仍先独读 revision 决定是否 refresh；peer 紧随该比较发布时，旧投影可被显示为新版本，CLI transcript 也可能记录错误 revision | 比较与重投影统一使用同一 snapshot；投影 DTO 只标记自身 revision，CLI transcript 改记 durable receipt revision。快照后 peer publish 留给下一次读取，不错标当前世界 |
| RCV-75 | P1 | 已验收（GUI 不可恢复 action 的当前状态回显） | 已提交 action 的 sidecar／信封损坏或不匹配时，409 只给通用提示；浏览器保留 pending，但仍显示提交前投影，玩家必须手动刷新才能判断当前世界 | 409 仍不恢复原 narration、metadata 或异常；仅尽力附带既有安全 player DTO，前端立即渲染它并保留明确结束恢复。DTO 构建失败仍保守 409；WSGI 与真实 loopback 浏览器均验证当前状态同步；不新增重试、写入或权限 |
| RCV-76 | P1 | 已验收（同场角色公开观察闭环） | GUI/CLI 只显示可交谈人物的 sketch/名称；已经被当前公开场景授权的心情／状态没有共用玩家 read-model，玩家与作者/交流 selector 易发生展示漂移 | 新共享 DTO 仅剥离既有 receipt-safe current-scene projection 的 id、sketch、公开 mood/state；GUI 面板显示详情、CLI `/status` 同步显示，选择器仍用同一人集。NPC 知识、传闻、arrival preview、路线授权与私有/隐藏人物均不进 DTO |
| RCV-77 | P1 | 已验收（玩家近处公开观察） | 已有 durable `public_place_observations` 只供作者 prompt；玩家 GUI/CLI 无法查看这些受限、本地的现场观察，只能从叙事滚屏回忆 | GUI/CLI 共享只读 DTO，只保留当前地点或当前公开出口的显示名、观察类型、范围与摘要；剥离 raw place id、event id、author/source、历史 scene/day/turn。hidden、非本地和未显式 public 的观察不进入玩家出口，零写入 |
| RCV-78 | P1 | 已验收（近处／出口观察语义区分） | RCV-77 的安全投影允许当前位置和已知出口观察，但界面把两类行都平铺，邻近地点的已知观察会被误读成主角眼前所见 | GUI/CLI 只根据既有 `is_current` 标记为当前项显示“这里”，其他项显示“已知出口：地点”；不改变 DTO 范围、观察来源、路线、发现、提交或权限 |
| RCV-79 | P0 | 已验收（观察跨实例 active-history 回放） | RCV-77/78 新的玩家观察 read-model 仅覆盖单实例状态，尚未证明 peer 发布、撤回和关闭重开时不会停留在已撤回观察 | 真实双 dashboard 回归覆盖 peer 发布→被动 state 刷新→revision-bound 撤回→刷新→冷重开；观察严格跟随 active durable history，读取不新增 player 写入 |
| RCV-80 | P0 | 已验收（CLI 观察跨会话 active-history 回放） | RCV-79 覆盖 dashboard，但长运行 CLI `/status` 仍未验证 peer 的公开观察在发布、撤回和冷重开后是否同步 | 真实 CLI + peer Engine 回归覆盖三态 status 输出、只读 byte 不变与冷重开；不改生产逻辑或用户界面 |

RCV-75～77 的可恢复代码检查点已再次核对如下（均为本地 `work` 分支提交；不代表外传）：

- RCV-75 生产修复：`18798c1ea399ab653a9a9b4798017fc487733b01`；随后浏览器证据文档检查点：`860e96d5d8f94afdf23bb2532aa819d5f33e81f2`。
- RCV-76：`e7b5de4be191102b32e2cb0dd55f65e4f7d78001`。已确认 GUI/WSGI、CLI、冷重开和 390px loopback 浏览器均只显示公开同场人物的 sketch／mood／state；明确交流下拉框仍是同一人集的 id/label 投影，hidden id、private sketch/mood、NPC evidence、reported knowledge、arrival preview 与路线授权均未进入玩家 DTO 或 DOM。
- RCV-77：`96f1ff76970b3631f0cc1030940d0984cfb600a3`。
| RCV-05 | HOLD | HOLD | B1 detail retrieval 与额外 profiling | 原历史工具审查拒绝；不换路绕过，也不重试 |
| M1-01 | P1 | 已验收（默认禁用数据切片） | 角色私有解释记忆的最小持久化/重放边界 | 默认 app 拒绝事件；opt-in 隔离 registry 的 actor 分组、撤回和重开 replay 通过；读取只接入 RCV-03 的受限 CLI 实验 |
| M2-01 | P1 | 已验收（离线宿主前置层） | 仅可信 Python 宿主的私有记忆读取 handle | world/store/session/role identity 绑定、关闭撤销和跨 session/store 反例通过；RCV-03 仅以当前主角的无参数 `/memory` 使用它，tools/DM 仍未接入 |
| M3-01 | P1 | 已验收（新公开历史 API） | 完整历史的私有记忆过滤 DTO | `Engine.public_history()` 过滤 active/retracted 私有行及显式引用；RCV-03 的 CLI 回归验证其不含私有正文；GUI/GM/raw-store 仍未接入 |
| M3b-01 | P1 | 已验收（续玩输出隔离） | resume recap 不读取 opt-in 私有记忆 slice | real store→projection→`_print_resume_recap` 覆盖 active/retracted、当前/其他角色私有记录；RCV-03 再验证 `/recall` 与下一轮 provider 上下文隔离，trace/raw-store 仍无公开出口承诺 |
| M4-01 | P1 | 已验收（离线宿主原子写入） | batch/source/output 绑定的私有解释写入 | 红例驱动修复后，stage/publish 双重绑定复验、savepoint rollback、篡改/跨生命周期拒绝通过；默认 app 禁用 |
| M4-02 | P1 | 已验收（实际 payload 终检） | preflight 后的待插入副本不可绕过 M4 绑定 | `final_validate` 在同一 SQLite 事务内、preflight 之后、insert 之前校验；前台 source body 和私有 record 的 actor/source/type/id/turn 篡改均原子拒绝 |
| M4-03 | P1 | 已验收（savepoint 表示保真） | 失败 hook 不得把缺失的 `retracted` 字段规范化为 `False` | savepoint 精确恢复字段存在性与值；缺键 source 的既有 memory writer/validator 在失败 hook 后仍可发布 |
| AID-01 | P1 | 已验收（仅持久化 sidecar） | 为未来 action-id preflight retry 保存可选、版本化的结果元数据 | `action_receipts` 保持五列与原 payload 指纹；sidecar 与 event/revision/receipt 同事务，写入失败全回滚，duplicate 不覆盖首份元数据；旧库打开、无 action ID 与非法 metadata 均有回归；仅保证 `append_many`/`publish` 路径，表本身无 FK；尚未实现 retry 入口 |
| AID-02 | P1 | 已验收（完成态 preflight） | 显式 action ID 的重试必须在任何生成前发现已完成动作 | 单一短 SQLite snapshot 读取 receipt/撤回/sidecar；活跃 duplicate 抛出带状态化 metadata 的 `ActionAlreadyCommitted`，撤回后要求新 ID；不重建 `TurnResult`、不处理 in-flight/并发或自动重试 |
| AID-03 | P1 | 已验收（显式 response-loss 恢复） | 同一显式 action ID、完全相同的原请求信封可在成功提交后恢复原 `TurnResult` | sidecar 保存版本化 request fingerprint、commit/repair/drop/首次 caller-visible events；duplicate 在 batch/provider 前恢复原结果并从当前完整 history 投影 world。input/scene/memory role/prev_scene/required sections/max repairs/固定离线生成配置/expected revision 任一变化、旧/损坏/未知 metadata、撤回 receipt 均 fail-closed；JSONL mirror 只读比对快照，不自动同步；不处理 in-flight/并发、CLI 自动重试或非显式 action ID |

## 执行规则

- 每一个完成且已验收的小阶段做本地 git commit，并生成可独立 clone 的完整 git bundle、源码归档和
  验证证据，交由父线程直接发送给用户并保存到 Library；临时磁盘不算交付。
- GitHub push 当前被执行环境策略拒绝，即使本次目标已获用户确认也不再重试或换路。仍禁止 force push、
  main 改动、PR 与部署。
- 新记忆不得成为事实、权限或发布权威。缺少 owner 的生产写入必须在调用前拒绝，不能靠投影层忽略。
- 不能删除合理失败测试、扩大 skip/xfail 或只针对样例加分支。所有测试结果都注明 commit、命令与实际结果。

## 本轮记录

- 2026-10-11：环境恢复后核对 Library `PRODUCT_EXECUTION.md` v7；它描述的
  `9928c1d2` 不存在于当前 repository。创建本台账，先恢复可移植的真实全套 gate，
  然后进入 RCV-02 原子性审计。
- 2026-10-11：RCV-01 将 trace subprocess 的 cwd 与 `PYTHONPATH` 从旧机器路径改为
  测试文件推导的仓库根目录，并保留继承的 `PYTHONPATH`。先前完整 gate 的 7 项
  `PermissionError: /root/rpg-engine-app` 全部消除；目标文件 38 passed，完整 gate
  `1712 passed, 1 deselected`。下一项为 RCV-02，不把这次测试可移植性修复表述为
  回合/记忆架构已恢复。
- 2026-10-11：RCV-02 审计发现 `EventBatch.savepoint()` 没有回滚 savepoint 内的
  `retractions`，使失败后台钩子可能在最终 publish 时撤回早已提交的 turn。现保存
  savepoint 前的撤回意图和事件标记，异常时一并恢复；针对性 59 passed、完整 gate
  `1714 passed, 1 deselected`。这只关闭 savepoint 的局部原子性缺口，owner
  fail-closed、重开和完整 replay 尚待审计。
- 2026-10-11：RCV-02 第二切片将 `apply_turn` 的 section owner 检查前置为统一
  preflight；未知 section 在任何 owner 的 `to_events` 调用前抛出 `TurnRejected`，
  不再只写日志并丢弃。针对性 43 passed、完整 gate `1715 passed, 1 deselected`；
  保存新的恢复 bundle 后继续重开/replay 审计。
- 2026-10-11：在全新临时目录实际恢复最新增量 bundle：先将新 clone 固定到
  `6b54411`，再 `fetch` bundle 并 checkout `recovery/HEAD`。恢复 HEAD 与源工作区
  均为 `d58822d1d2f936b1f086cbddff25973671b73fa2`，tree 均为
  `aabd5669fb5f4b88c142c41a146f918204a17b74`，且恢复 checkout clean。该 bundle 是
  增量包，明确要求已有 `6b54411` 基线；不是单独完整仓库快照。
- 2026-10-11：随后生成完整历史 bundle 并直接 clone 到另一全新目录（无预置
  基线）。恢复 HEAD `472ffde2a67a7e568c808a6530dd61ca9155b2b9`、tree
  `8d8b8c132b395a43b604c36ca9c82cac6e224cf4`、checkout clean；bundle SHA-256
  `fead3f028fd80062a440413d315ffda6d1ae0512ffeed13a045b9cb49c730e3e`。该完整包
  作为后续阶段的持久恢复起点。
- 2026-10-11：提交链关系已核对：`7a3c82c` 是 RCV-01 的可移植 trace CLI 代码修复；
  `74071f6` 只记录该修复的证据并以 `7a3c82c` 为父；随后 `7e31198`（savepoint
  撤回回滚）、`d58822d`（owner preflight）、`472ffde`（增量恢复演练）和
  `9ef6746`（完整恢复演练）依次累积。完整自包含 bundle 已保存到 Library，但从
  Library 签名 materialization URL 实际下载连续两次收到 403，尚不能宣称“从
  持久化字节恢复已验收”；本地生成 bundle 的独立 clone 已通过，等待连接器下载恢复。
- 2026-10-11：RCV-02 的重开/replay 子项新增真实 `run_turn` 回归：第一回合产生的
  事实与 narration 全部撤回后，关闭并重新打开 SQLite，常规投影和以完整（含撤回）
  历史重放的结构签名都回到 pre-turn world，且撤回标记原样保留。针对性 41 passed，
  完整 gate `1716 passed, 1 deselected`。RCV-02 以当前已注册后台机制为范围验收；
  将来新后台类型必须重跑相同原子性矩阵。
- 2026-10-11：M4 的独立源码顺序审查发现，旧的 publish validator 只检查
  `EventBatch.events`，而 `EventStore.append_many()` 会在 preflight 前深拷贝输入；因此
  preflight 可以改动真实待插入副本而不改变 batch。新增 `final_validate(events)`：它在同一
  SQLite transaction 内、preflight 后、任何 insert 前取得精确复制 payload。记忆写入同时
  固定 batch 视图摘要和去除临时 `seq` 的发布视图摘要。六个红例分别修改 source body 及
  private record 的 actor/source/type/id/turn，并确认 events、revision、action receipt 和
  JSONL mirror 均未变化。目标 27 passed；完整 gate `1747 passed, 1 skipped, 1 deselected`
  （17.29s）。这不会启用默认 app 或将记忆变成事实/权限权威。
- 2026-10-11：M4-02 提交 `05ec5648e64cbe6e045a047844fedcd8e4551c6e` 后生成完整历史
  bundle（SHA-256 `e9f2e51cc4ca20023e8afda0e75a76bf34582e21d4fec3655c70bb3c1dd044b9`）及
  source archive（SHA-256 `75cb1aafcd12eb9a3db02b0d2d254a52090046952fb78a908a6840ada68505aa`），
  并保存到 Library。全新临时目录从该 bundle clone 后，HEAD 与 tree 分别为
  `05ec5648e64cbe6e045a047844fedcd8e4551c6e` 与
  `b99c6c0dae8c17706ccd307d8f467ef050926732`，checkout clean，且记忆目标测试 27 passed。
  Library 的本执行台账保持同一 ID，更新至 v17；此前 Library materialization 403 的限制仍
  存在，不能把这次本地 bundle clone 说成已从 Library 下载恢复。
- 2026-10-11（doc-only，代码冻结于 `8f27a52`）：复核临时离线测试环境时，最初的
  `1747 passed, 1 skipped, 1 deselected` 中的唯一 skip 是既有 YAML blueprint 测试在
  缺少可选 PyYAML 时触发的 `pytest.importorskip("yaml")`，不是本轮新增 skip、xfail 或
  测试修改。仅向 `/tmp/llm-rpg-engine-testenv` 安装 PyYAML 6.0.3 后，目标 YAML 测试
  `5 passed`，完整 offline gate 为 `1748 passed, 1 deselected`（16.80s）。唯一 deselect
  是仓库初始便以 `slow` 标记排除的真实 embedding 测试，可能需要模型下载/网络；本轮未运行
  或改动它。独立审查材料已另存 Library，M4 不因此记录为独立审查已通过。
- 2026-10-11：独立审查提出手工合法 event 可省略 `retracted`，而 `EventBatch.savepoint()`
  失败路径先前会无条件写回 `False`，从而改变写入前已有 memory source 的完整摘要并使其
  validator 误拒绝。savepoint 现保存键是否存在及原值，异常时恢复该精确表示。红例覆盖缺键
  source + savepoint 前已有 writer/validator + savepoint 内撤回失败后的正常 publish；控制例
  覆盖缺键、显式 False、显式 True 以及 retraction/validator 列表。目标 29 passed，完整
  offline gate `1750 passed, 1 deselected`（17.42s）。本切片没有接入 `run_turn`。
- 2026-10-11：受限、默认禁用的离线 `run_turn` memory staging 提交为 `76b7f58`，并已 push
  到 `recovery/m4-savepoint-fix` 且 `ls-remote` 核对同一 SHA。专项矩阵 9 passed；完整
  offline gate `1760 passed, 1 deselected`（18.07s）。默认/缺 owner、合法 opt-in、role
  存在性与入口绑定、旧 narration、普通错误、PermissionError、取消和 savepoint 表示保真均有
  离线覆盖。独立静态审查尚待结论，不能据此称生产启用：narration 没有 event-level viewer ACL；
  普通错误会记录异常栈，第三方异常消息可能含私有正文；且 action_id receipt 去重发生在
  generator 之后，持久化不会重复记录但 generator 可能再次执行。没有真实模型、B1 或 profiling。
- 2026-10-11：私有 memory 的后台边界修复提交为 `5408eb1`，已 push 到
  `recovery/m4-savepoint-fix` 并以 `ls-remote` 核对完整 SHA。`digest_fleet` 直接入口统一
  使用公共 DTO；memory stage 成功后立即重投影；普通私有生成异常仅记录固定安全诊断。专项
  `tests/test_action_integrity.py tests/systems/test_interpretive_memory.py tests/loop/test_fleet.py` 通过；
  全量为 `1763 passed, 1 deselected`（17.57s）。fleet capture-provider 是防回归：正常旧格式
  record 会被 subject 选择器偶然跳过，不能表述为已证实旧路径泄漏；world 测试只验证提交后的
  完整 store replay，尚无 cold reopen 覆盖；修前 red 未运行。独立复核待结论。
- 2026-10-11（独立复核口径更正）：上述 world 回归只比较 `interpretive_memory` slice，
  不是完整 world；它使用完整 engine registry，`quiet` 仅令 `_run_demote_on_leave` 返回空，
  而已注册的 `quest_demoted` owner 分支仍可能无条件重投影。因此不能称最小 registry、不能
  称已排除其他投影来源，也不能称修前 red 已验证。fleet capture-provider 确实触发真实 provider，
  但正常旧格式私有 record 也会被旧 subject 选择器跳过，故仍仅为防回归。后续须补最小 registry
  与 world cold-reopen/replay 证据；本轮结论不扩大。
- 2026-10-11：测试强化提交 `818e4a9` 已 push 到 `recovery/m4-savepoint-fix`，并以
  `ls-remote` 核对远端完整 SHA。`tests/test_action_integrity.py` 为 30 passed；完整 offline
  gate 为 `1765 passed, 1 deselected`（19.10s）。撤回回归现在先证明 narration/private
  非真空及 source 关联，再验证整 turn 撤回、完整 receipt 行在 rewind/cold reopen 前后保留、
  语义 world 回到 seed、可信 JSONL mirror 在成功/rewind/reopen 三时点均等于 SQLite 完整历史。
  mirror 写故障回归确认 SQLite 提交和 `mirror_pending`，恢复 `sync_jsonl()` 后不重跑 turn 且
  events/revision/receipt 不变、`mirror_error` 清空。未解决：action_id 去重在 generator 之后，
  可能重复执行 generator；narration 没有 event-level 来源/可见性 ACL；未调用真实模型，生产
  行为仍未验收。
- 2026-10-11：`815da8de02573e2017deba50db871dffe82aaab8` 已 push 到同一恢复分支并以
  `ls-remote` 核对。`tests/test_action_integrity.py` 与 `tests/systems/test_interpretive_memory.py`
  专项 62 passed；完整 offline gate `1767 passed, 1 deselected`（17.60s）。离线 generator
  现在在生成前固定 writer/source binding，且只收 source 的深拷贝 DTO。TOCTOU 证据仅为
  EventBatch/staging 边界：前置 seed、revision、receipt 数和 JSONL bytes 不变；不是 run_turn
  端到端异常承诺，也未比较 receipt 全内容。前置 action_id 幂等、显式 narration 来源/可见性
  ACL 与真实模型行为仍未解决/未调用。
- 2026-10-11：`6ca9dcce0e33d3cef32c1a2be634a142eab94419` 已 push 到同一恢复分支并以
  `ls-remote` 核对。两份专项 67 passed；完整 offline gate `1772 passed, 1 deselected`
  （17.33s）。memory source 现为 canonical narration append 现场创建的 in-process host
  binding（exact batch/source object/id/turn/role），helper 不扫描 JSON narration 重建授权。
  复制同 ID source 的值相等漏洞已有红→绿 identity 证据；wrong turn/role/cross-batch（含伪造
  JSON provenance）均在 generator 前拒绝。默认仍禁用；未实现 action_id 前置幂等、通用
  event-level ACL 或真实模型验收。
- 2026-10-11：`4b2de8ab17aaa2951165f4f93fc588f73f05b13d` 已 push 到同一恢复分支并以
  `ls-remote` 核对。两份专项 70 passed；完整 offline gate `1775 passed, 1 deselected`
  （17.87s）。memory helper 现显式接收调用方 expected batch，先做 binding 类型检查，再要求
  binding.batch identity 与调用方一致；完整合法 left binding 用于 right workflow、复制 source、
  wrong turn/role、malformed binding 均在 generator 前拒绝。默认仍禁用，真实模型未调用；
  前置 action_id 幂等、通用来源 ACL 仍待设计。
- 2026-10-11：AID-01 只增加可选 `action_result_metadata` SQLite sidecar，调用方须传入带整数
  `version` 的 JSON object。`EventStore.append_many` 与 `EventBatch.publish` 都可传递该参数；它在
  既有 events/revision/action receipt 事务内写入。保证范围仅为这两个写入路径，sidecar table 本身
  没有 FK。duplicate 在先序列化/验证传入 metadata 后，才查询 receipt 并早退；因此不会覆盖首份
  metadata，但并非完全忽略第二份 metadata。红例覆盖 batch 提交、metadata 写入故障时 events/
  revision/receipt/metadata 全回滚，以及带不同 metadata 的 duplicate 保留原值。`tests/test_action_integrity.py`
  为 43 passed；正式完整离线 gate（`/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA`）
  为 `1778 passed, 1 deselected`（17.72s）；此前移除默认选择条件的诊断出现的 `1 skipped` 不是同口径
  结果。随后新增旧 DB 打开后自动创建 sidecar、metadata 缺少 `action_id` 的事务前拒绝，以及 list/
  缺 `version`/bool `version`/非 JSON/NaN metadata 的零写入拒绝回归；专项 50 passed，正式完整 gate
  为 `1785 passed, 1 deselected`（17.43s）。本切片仍不添加 `run_turn` retry/preflight 流程、不改
  receipt schema 或 fingerprint、未调用真实模型。GitHub push 已被执行环境策略拒绝；本轮起改用完整
  文件备份直接交付用户，且不再重试或绕过。下一项仍是 action-id 重试协议的单独设计与验收。
- 2026-10-12：AID-02 为显式 `action_id` 增加完成态 preflight。`run_turn` 现在在创建 batch、资源准备、
  strategy 或 provider 调用前读取 action receipt；活跃 receipt 抛出 `ActionAlreadyCommitted`（提供 receipt
  及可选 sidecar metadata），已撤回 receipt 则明确拒绝并要求新 ID。两个离线回归分别证明 duplicate 与
  已撤回 duplicate 都在 strategy 前停止。专项 `52 passed`；正式完整 gate `1787 passed, 1 deselected`
  （17.47s）。这不是结果 hydration、自动 retry 或 in-flight/并发锁，且没有调用真实模型。
- 2026-10-12：AID-02 一致性加固引入仅供 `run_turn` 使用的短只读 SQLite snapshot，原子读取 receipt、
  撤回状态和 sidecar 原文，事务后才解析 metadata。缺失/正常/损坏/不支持版本分别得到
  `missing`/`available`/`corrupt`/`unsupported_version` 状态；损坏数据仍明确表示 action 已提交，
  不泄漏原文或抛裸 JSON 错误。旧 receipt、损坏/未来 sidecar 与撤回 action 均证明 batch/resources/
  strategy/provider/memory generator/conversation commit 零调用，且 history/revision/receipt/JSONL 不变；
  两连接 WAL 受控交错证明单次读取自洽。专项 `58 passed`；正式完整 gate `1793 passed, 1 deselected`
  （18.63s）。仍不处理 in-flight、请求等价或 `TurnResult` 重建。
- 2026-10-12：AID-03 在 `run_turn` 的完成态 preflight 上实现了受限的 response-loss 恢复：首次带
  显式 `action_id` 的成功提交与同一 SQLite transaction 的 sidecar 保存稳定请求指纹（scene、player
  input、expected revision、memory role；不含 provider、credentials、strategy/session state）以及原
  commit/repair/drop/caller-visible events。相同信封重试在创建 batch、资源解析、strategy、provider、
  offline memory generator、后台 hook 和 conversation thread commit 前返回首个结果；其 world 则从读取
  snapshot 的当前完整 history 投影，故随后第二 action 不会被旧响应回退。实际离线端到端用例覆盖含
  opt-in private memory 的“提交成功但调用方丢响应”、SQL events/receipt/metadata/revision 与 JSONL bytes
  全不变、input/scene/memory role 的前置拒绝、第二 action 后的旧 response 恢复及关闭重开；专项
  `tests/test_action_integrity.py` 为 `63 passed`。正式 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  `1798 passed, 1 deselected`（18.15s）；唯一 deselect 是既有 slow real embedding 测试，未调用真实模型。
  这不增加 CLI stable action-id、in-flight/并发处理或自动 retry，也不改变私有记忆默认禁用状态。
- 2026-10-12：针对 AID-03 独立复核的三项边界修正：恢复回执的 `mirror_pending` 不再固定为 false，
  而是只读解析 JSONL 并与同一 SQLite snapshot 的完整 history 比较；缺失、不可读、解析失败或任意不同
  均为 pending，重试不写入也不自动同步，只有显式 `sync_jsonl()` 后才清除。请求信封升级为稳定语义配置：
  除 scene/input/revision/role 外还包含 `prev_scene`、排序规范化的 required sections、非负 max repairs、
  memory enabled 状态和固定 `OfflineInterpretationGenerator` 的非明文配置摘要；不散列 provider、凭据、
  strategy/session 状态。恢复前先验证 sidecar 为完整 turn result：result 必须为对象，repair 非负，drop 为
  字符串列表，commit/events 的必要结构齐全，且 caller-visible event id/内容逐项属于 receipt 范围；非空
  list/string 等错误容器、旧 turnless v1 结构、负 repair 与缺字段均 `ActionAlreadyCommitted(corrupt)`，
  不触发生成或写入。专项 `tests/test_action_integrity.py` 为 `76 passed`；正式 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  `1811 passed, 1 deselected`（18.61s），唯一 deselect 仍是既有 slow real embedding，未调用真实模型。
- 2026-10-12：复核发现 `base_revision` 缺失时旧校验会放过 `metadata.get()` 的 `None`，随后直接下标
  访问造成裸 `KeyError`。恢复前现要求该键存在且为 `None` 或精确 int；缺失也统一为
  `ActionAlreadyCommitted(corrupt)`。专项新增删除该键的零调用、零写入用例，`77 passed`；正式 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  `1812 passed, 1 deselected`（18.16s）。唯一 deselect 仍为既有 slow real embedding；没有真实模型或远端操作。
- 2026-10-12：RCV-03 从原 HOLD 变为受限的离线 CLI 实验闭环。`--offline-private-memory`
  是唯一开关：它在 store 打开/重放前注册 `InterpretiveMemorySystem`，并只构造一个固定文本的
  `OfflineInterpretationGenerator`，不替换前台 provider/embedder；无 flag 时 registry 不含 owner，`/memory` 明确拒绝。开启时普通
  `run_turn`（含 compare 最终 apply）向既有原子 staging 传递当前主角和该 generator；`/memory` 没有
  role 参数，只能通过宿主 handle 读取当前 scene protagonist。真实 `app.__main__.main` temp-DB 输入/输出
  回归证明非空私有文本的写入、重开同角色读取、NPC 私有行隐藏、同一 CLI 会话内的 `/undo`/`/rewind`
  整 turn 撤回；没有把撤回后再冷重开表述为已测。默认重开、
  `/recall`、resume recap、`Engine.public_history()` 和记录过的 provider prompts 均不含该文本。未启用
  真实模型、embedding、网络、CLI 自动 retry 或并发去重；这不是通用访问控制、GM/GUI/raw-store 或 trace
  viewer 的生产授权。
  本阶段专项 `tests/app/test_offline_private_memory_cli.py tests/app/test_play.py
  tests/app/test_resume_recap.py tests/systems/test_interpretive_memory.py -rA` 为 `56 passed`；正式完整 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  `1813 passed, 1 deselected`（18.88s），唯一 deselect 是既有 slow real embedding；未调用真实模型。
- 2026-10-12：RCV-03 独立复核指出原 provider prompt 断言只覆盖首个写入私有记录的回合，
  不能证明后续玩家回合隔离。没有改生产功能：CLI 回归现在先保存主角和 NPC 的非空私有 sentinel，
  再关闭重开并运行第二个真实玩家输入。它断言本次新增 provider calls 非空、含
  `PUBLIC_SECOND_TURN_CONTROL` 公开控制文本、且不含任一私有 sentinel；第二回合写入后 `/memory`
  有两条主角记录，`/undo` 后同一 CLI 会话只剩一条，`/rewind 1` 后为无。随后专项 `56 passed`
  （0.55s）和正式完整 gate `1813 passed, 1 deselected`（18.78s）均通过。此证据不覆盖 compare，
  也不把 undo/rewind 后再冷重开表述为已测；开关只固定私有生成器，前台 provider/embedder 不变。
- 2026-10-12：RCV-04 以真实 `run_turn → digest_fleet` 入口（保留 digest、仅隔离无关后台
  hooks）验收 recap 边界。三个不同 scene 使首场景老化：摘要 provider 异常时，`complete_structured`
  的失败停留在摘要本地，第三个前台 narration/fact/private record 和该 action 的 receipt、revision、
  turn-result sidecar 均已提交，而没有 `scene_summarized`；关闭重开后投影、私有 slice 和事实一致。
  成功路径故意生成与事实矛盾的 `SUMMARY_FALSE_CLAIM_COINS_999`：它只成为 narrative scene summary
  和公开 resume journey，ontology 的 `hero.coins` 仍为前台 fact 的 13。捕获的真实 recap prompt 含公开
  narration、不含私有解释 sentinel；resume 与 `public_event_history` 亦无私有正文。专项
  `tests/test_recap_integrity.py -rA` 为 `2 passed`（0.09s）；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  `1815 passed, 1 deselected`（18.70s）。唯一 deselect 是既有 slow real embedding 测试；没有改动
  取消/撤回语义，也没有启用真实模型、B1、profiling 或远端操作。
- 2026-10-12：RCV-06 把可验证的 action/recap 结果传达给 CLI，而不改写其持久化语义。
  `TurnResult.recap_status` 是非事件的轻量状态（`not_due`/`saved`/`pending`）；显式 action-id 的
  turn-result sidecar 同时保存它，完成态恢复返回相同状态，旧 sidecar 缺该字段时只由其已保存的
  caller-visible events 兼容推导，不触发新生成。普通与 compare 最终 apply 在成功 publish 后才输出
  `[已保存] 第 N 回合，版本 R`；只有成功生成摘要时输出 `已更新`，实际摘要 provider 失败才输出
  `暂未生成；本回合已保存`。状态本身不是 event、不会触发后台任务、不会重试玩家 action，也不改变
  rewind/cancellation/replay。玩家可见的 generic turn error、逐回合 transcript 与结构化 provider/recap
  失败日志均不再回显异常正文，防止错误消息携带私有/提示文本。
  新 `scripts/demo_offline_cli_status.py` 用临时 campaign 和 fake provider 演示默认实验关闭、显式开关、
  三个保存回合、真实 recap failure、`/memory`、`/undo`、冷重开；`README.md` 写明可复制命令和
  限制。`tests/app/test_cli_turn_status.py` 覆盖同一真实 CLI 路径以及 generic error 脱敏，RCV-04
  回归补充 action-id 恢复 recap status 的零生成断言。专项
  `tests/app/test_cli_turn_status.py tests/test_recap_integrity.py tests/loop/test_fleet.py tests/app/test_play.py tests/test_action_integrity.py -rA`
  为 `113 passed`（1.01s）；演示脚本标准错误为空且含各预期状态。正式 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  `1817 passed, 1 deselected`（17.61s）；唯一 deselect 仍为既有 slow real embedding。无真实模型、
  embedding、网络、B1、profiling、push、PR 或部署。
- 2026-10-12：RCV-07 对 RCV-06 的三个恢复/呈现边界做了最小修正。恢复 v1 `turn_result` 时只有
  `recap_status` 键**缺失**才按旧格式由事件推导；显式 `null`、list、dict 与未知字符串均在任何生成/
  写入前 fail-closed 为 `ActionAlreadyCommitted(corrupt)`，避免 unhashable JSON 容器造成 `TypeError`；
  pending 也在 response-loss 恢复中原样保真。`play_loop` 单独记录 `run_turn` 已经 publish 的状态：若随后
  narration/status/transcript 展示失败，玩家看到的是“本回合已保存，但结果显示未完成；请检查当前世界状态”，
  transcript 记为 `turn_output_failed_after_commit`，不再错误承诺回滚或诱导盲目重试。digest 的外层异常
  不再臆测 recap pending；只有 `digest_fleet` 实际到达摘要尝试并返回的状态才是 pending，reflection 在 recap
  之前失败时保留 `not_due`。相关 CLI/recap 异常 sentinel 以 `caplog` 验证不进入日志、CLI 或 transcript；
  演示脚本独立固定 `RPG_EMBEDDER=none`，不改变生产默认值。专项
  `tests/test_action_integrity.py tests/test_recap_integrity.py tests/app/test_cli_turn_status.py -rA` 为
  `89 passed`（含 digest cancellation 原样上抛且整 turn 不发布）；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  `1825 passed, 1 deselected`（18.44s），唯一 deselect 仍为既有 slow real embedding。交付包验证见本阶段
  verification evidence。没有更改 compare、
  B1、profiling、真实模型、网络或远端操作。
- 2026-10-12：RCV-08 新增 `app.gui`，但范围刻意限定为本机 `127.0.0.1` 的离线玩家首切片，而不是
  恢复完整 GUI/GM/editor。页面的 action、撤销都调用既有 `run_turn`/`rewind`，明确 action-id/revision
  继续使用既有 AID-03 publish/response-loss 路径，未另建写事务。`--demo` 只为**空** campaign 写入使用
  现有事件类型的石桥镇样例（主角、物品、两地点邻接、公开 codex、开场叙事）；动作由固定本地策略和
  `FakeLLMProvider` 驱动，未调用网络、真实模型、embedder 或凭据。浏览器不能传 campaign 路径，服务不
  提供文件、GM 或 raw-store endpoint；玩家 DTO 只手工摘取 `pov_world` 的角色/物品/地图字段、公开
  `codex` 与 `Engine.public_history()` 的 narration，绝不序列化 canonical world/事件库。前端用
  `textContent` 写入行动、叙事和设定文本，CSP 仅允许 self；私有解释默认不注册。
  `tests/app/test_gui.py -rA` 为 `5 passed`，覆盖保存、duplicate action-id 恢复、undo、冷重开、坏请求
  零写入，以及 secret/private-memory sentinel 不进 DTO。真实 Chromium/Playwright 在本地端口实际输入
  HTML-looking action、行动后地图更新、刷新仍保留、移动布局单列、相同 action-id 第二请求 duplicate，
  并断言 API/DOM 没有私密 sentinel；desktop/mobile 截图已保存为交付证据。正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  `1830 passed, 1 deselected`（21.29s）；唯一 deselect 是既有 slow real embedding，未调用真实模型。
  `README.md` 与 `scripts/demo_offline_gui.py` 说明离线启动、范围及限制；没有 B1、profiling、push、PR 或部署。
- 2026-10-12：RCV-08 独立复核发现并闭合六个本地 GUI 边界，均未增加新的持久化/部署系统：(1) `Engine.public_history()`
  是审计 DTO，会保留 retracted 标记；玩家 narration DTO 现只取 active 行，撤销后同进程刷新与冷重开均不复活。(2)
  undo 请求必须带当前精确 revision；服务端锁内再次校验，两个同 revision 的请求只有第一个能撤回，第二个零写入。(3)
  `/api/action` 与 `/api/undo` 均要求明确 numeric `127.0.0.1:port` Host、匹配 Origin（浏览器省略 Origin 的同源
  fetch 允许）和 `application/json`，cross-site Origin、错误 Host、普通 form 都在 controller 前拒绝。(4) 浏览器用
  sessionStorage 留存尚未确认的原 action-id/input/revision；刷新后“恢复上次请求”重发相同 envelope，成功才清除，实际
  Chromium response-loss→refresh→recover 证明 commit 发生后 revision 不再增加。(5) 若 action 已发布而 DTO/JSON 展示
  失败，HTTP 500 只说“请刷新查看当前状态”，不含异常正文或未提交暗示；真实 WSGI 红例确认 receipt/revision 已存在。(6)
  离线 demo strategy 的邻接选择改为同 protagonist/day 的 `pov_world`，不是 canonical graph；隐藏出口排在 canonical
  第一位仍只走第二个公开出口，只有隐藏出口则不移动，叙事/API 均无 sentinel。GUI construction 明确传递 `embedder=None`；
  `build_engine` 改为仅**省略** embedder 才读取环境，故 `RPG_EMBEDDER=fastembed` 也不会构造 GUI embedder，同时保留普通
  app 的既有默认。POV 规则仍允许当前主角/地点、同场人、已发现实体和无限制 metadata 的 legacy 地点，并未把它表述为“所有地点
  都先发现”。专项与 engine 回归在本记录后续正式 gate 前均通过；无真实模型、网络、B1、profiling、push、PR 或部署。
- 2026-10-12：复核继续关闭前端 pending 的两条边界。`api()` 不再把 HTTP 200 的 JSON 解析失败伪装成成功对象；action
  只有拿到 `ok:true`、整数 revision 和 state 并成功 render 后才清除 sessionStorage envelope。损坏/缺字段的 200 保留原
  action-id/input/revision，刷新后仍可恢复，实际 Chromium 证实 upstream 已提交时 revision `1→2→2`。相反，带合法 JSON
  error 的 HTTP 400 被视为服务端已确认的未提交拒绝：清除 pending、允许修改和新 action；800 个中文字符的 UTF-8 过长反例在
  刷新后不锁死并可正常提交。`app.__main__.main` 已逐行核对：它不传 `embedder` 给 `build_engine`，所以新的 sentinel
  仅使 GUI 的显式 `None` 禁用 embedding；普通 CLI/省略调用仍按 `RPG_EMBEDDER` 选择。回归用替身证明普通省略构造会调用环境
  resolver，而 GUI 在 `RPG_EMBEDDER=fastembed` 时仍为 None、零构造/零调用；没有运行真实 embedding 或模型。
- 2026-10-12：最后复核指出不能把“任意带 message 的 HTTP 400”当作未提交证据：`make_app` 之前把 controller/DTO/
  JSON 展示阶段的任意 `ValueError`/`TypeError` 宽泛转为 400，可能在 receipt 已写入后错误清掉浏览器 envelope。现将
  400 捕获收窄到 `_read_json_request` 的 request body 长度/解码/JSON shape 与显式 `GuiRequestError` 前置校验，且这些响应
  才带机器可识别的 `confirmed_rejection:true`；客户端仅凭该字段清 pending。post-publish DTO 的 RuntimeError、TypeError、
  ValueError 均返回中性未知结果、没有该标记且 receipt/revision 保留。真实 Chromium 将一次上游已提交 action 替换成**无该标记**
  的 400，仍保留/刷新/恢复到相同 revision `1→2→2`；800 中文字符的真实带标记 400 则清 pending，刷新后恢复按钮隐藏。专项
  覆盖三种 post-publish exception、同源/preflight 标记与此前 POV/undo/embedder 边界。此实现已自验；本记录不声称此 commit
  之后又做了一次新的独立复核。
- 2026-10-12：RCV-09 在同一 `127.0.0.1` GUI 模块加入**启动时显式**的 `--gm` 只读世界总览，而不是在玩家页前端隐藏一个
  tab。默认 player server 仍只有 `/api/state`、`/api/action`、`/api/undo`；`/api/gm/state` 在该模式是 404，HTML/API/错误都
  不含隐藏地点或秘密 sentinel。GM server 则只拥有 GET `/api/gm/state`，player state/action/undo 路由均不存在，切换模式要求
  重启。GM DTO 从 canonical projected ontology 读取人物、物品、地点、当前事实和真实 `adjacent_to` 边；时间线按客观事件、公开
  narration、recap、retracted 标记分类，保留撤回审计。它特意过滤 `character_interpretation_recorded`，不把私有解释记录自动
  交给 GM，更不会回流玩家 DTO、前台生成或上下文。`--demo` 增加 canonical-only 北岸旧塔、守望者和 world_change，以离线验收
  GM 可见隐藏地点/世界事件、玩家不可见；地图 SVG 节点和边由该 DTO 的真实地点关系动态绘制，非手写静态地图。GM 没有任何编辑、
  删除、推进或行动按钮；不引入账户/认证/公网部署。专项 GUI/引擎/AID 测试通过；实际 Chromium 访问独立 GM server 验证桌面/手机
  布局、3 个真实节点/2 条边、零写入按钮和无私有解释，另一个 player server 真实保存行动且 GM endpoint 仍为 404。正式 gate 为
  `1843 passed, 1 deselected`（20.66s，唯一既有 slow real embedding）；全新目录从完整 bundle clone 后可 `--gm --demo`
  启动并返回 `gm_read_only`、3 nodes/2 edges，GUI 专项 18 passed。该实现已自验，尚未声称独立复核通过。
- 2026-10-12：RCV-09 复核补齐 timeline 的引用型泄漏边界：原 GM 仅按 `PRIVATE_EVENT_TYPES` 丢弃私有行，若一个普通
  narration/summary 的 `thread_refs` 或 `chunk_ids` 指向私有 memory event，GM 会输出该普通行正文，而既有 player
  `public_event_history` 已对这种 dangling-private reference fail-closed。GM 现在先复用 `engine.public_history()` 再构造
  timeline，因而仍显示普通 secret fact 与普通 retracted event，却在 active 或已撤回私有 ID 被普通行引用时拒绝整个 DTO；HTTP
  只返回中性“请求结果未能显示”且日志不含私有/普通正文。新反例同时覆盖 active/retracted private ID、thread_refs/chunk_ids
  与 GM HTTP 输出。专项 `tests/app/test_gui.py -rA` 为 19 passed；正式 gate 为 `1844 passed, 1 deselected`（19.45s，唯一
  既有 slow real embedding）；全新 bundle clone clean、GUI 专项 19 passed 且 `--gm --demo` 可启动。该小修已本地提交和归档，
  但不能表述为独立复核通过。
- 2026-10-12：RCV-10 开始把 player GUI 正常入口接向现有 CLI 语义：只有显式 `--demo` 使用固定
  OfflineDemoStrategy/FakeLLMProvider；正常模式要求已有 campaign，并通过 CLI 同形的 `--provider/--model/--base-url/--max-tokens/
  --max-repairs` 构造 provider 后交给 `AuthorStrategy` 与既有 `run_turn`，不再把任意行动硬编码成演示移动。provider 未就绪或
  campaign 为空时启动前给本地配置/初始化提示，绝不降级 demo，浏览器不传任何凭据。公开 Codex DTO 统一供 player/GM 只读浏览，
  加入标题/类别/正文搜索与 details；仍不创建编辑器、虚构来源或混入私有解释。已新增 fake 注入的非平凡 normal action、duplicate/
  reopen、配置错误、Codex special-HTML 边界回归。实际 Chromium 在 demo 强制离线 server 验证桌面/手机搜索、无结果提示与 DOM 无
  HTML 注入；normal production 接线仅以 fake 注入验收，未声称真实模型生成质量。专项通过，正式 gate 为 `1847 passed, 1 deselected`
  （21.15s，唯一既有 slow real embedding）；已本地提交，完整 bundle/source/diff/evidence 已生成并将持久化到 Library。
- 2026-10-12：RCV-10 retry-context 修复本地提交 `8858793`：GUI 将可信 host scene/prev_scene 写入既有 result sidecar，
  冷 dashboard 重建后同 action-id 仍按原请求指纹恢复；旧 sidecar 缺字段 fail-closed，且 GUI 补传 CLI 同样的 catchup provider。
  专项 25 passed、正式 1850 passed/1 deselected。Chromium 已验浏览器刷新恢复移动，但尚未把同 campaign 的 GUI server/engine
  停止再重启后恢复称为完成；该差异保留在验证记录。
- 2026-10-12：RCV-10 后续将旧 sidecar 缺 GUI envelope 的恢复改成明确 409，并在浏览器增加“查看状态后结束恢复”；
  同一 campaign 的服务/engine 停止后重启、带原 sessionStorage envelope 的恢复已实际验证，旧 action 不会把较新的世界 cursor
  回退。该检查点为 `3f03892`；正式 gate `1851 passed, 1 deselected`。这不是远端 push 或真实模型验收。
- 2026-10-12：按用户要求进行跨模块架构回顾，并由一个只读原生 subagent 审阅受控玩家剧情路径（未调用外部模型或网络）。
  共性根因不是 SVG 缺失，而是玩家 GUI 的 POV exits、作者上下文与持久化 move 校验原先各自推导：正常 `AuthorStrategy`
  可提出存在但不可见/不相邻的地点，`PlaceSystem` 又刻意只做通用实体存在性校验。RCV-11 因此新增纯
  `context.capabilities.SceneCapabilities`：它从 action-start `pov_world` 投影稳定的可见地点、路线、出口和允许移动集；
  player DTO/地图、离线策略和作者上下文共同使用它。GUI 的 `run_turn` 显式启用 TravelPolicy：主角只能去 action-start
  POV 的相邻出口，或在同一批次创建地点并声明从当前位置直连的 `links`；非法 proposal 必须同时修复 `moves` 与 narration，
  否则整 action 在写入前失败，避免“世界没动但文案说到了”的部分降级。相同策略作为 `EventBatch` publish validator 对最终
  payload 二次检查，防止后续 staging 绕过；NPC、genesis 与低层通用 `PlaceSystem` 不受此玩家 UI policy 影响。地点 event
  现保留可选 `label`，演示图不再把 opaque id 当玩家名。渐进发现尚未被夸大：legacy public place 仍按现有 POV 兼容规则可见，
  真正 event-owned discovery/route state 被列为下一优先项。
  新增回归覆盖 hidden canonical teleport、非地图 move 的 repair、narration 连带重写、同批新地点+直连路线、final payload
  mutation 的整批零写入，以及 player API/DTO 无秘密。专项
  `tests/context/test_capabilities.py tests/app/test_gui.py tests/context/test_assembler.py tests/systems/test_place.py -rA`
  为 `106 passed`（既有 fake backstage warning 属测试预期路径）；正式
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA`
  为 **`1856 passed, 1 deselected in 19.41s`**，唯一 deselect 为既有 slow real embedding，未调用真实模型。
  实际 Chromium/Playwright 在 loopback demo 验证桌面/手机 SVG 2 节点/1 边、选择详情、行动后当前位置高亮/公开叙事更新、
  player DOM/API 无隐藏旧塔或私有 sentinel；截图作为本阶段交付证据。下一自动项：以现有 `knows:<place>.discovered`
  为基础设计/实现 event-owned 的渐进地点与路线发现，先完成兼容/回放/撤回审计，再扩大默认 visibility policy。
- 2026-10-12：RCV-11 独立复核指出三个同一能力契约的 fail-open，原 `94974af` 交付保留但标为待修正：
  (1) 初次非法 move 产生 narration 重写义务后，repair 仅删 moves 会在下一轮丢失该义务；
  (2) final validator 会把 `retracted=True` 的 `place_linked` 当有效发现路线；(3) 主角有 identity 但没有
  action-start location 时，提示禁止移动而 proposal/final validator 却返回空。修复将义务的原 narration 跨 repair
  保存，只有 narration 实际改变才解除；final validator 仅从未撤回事件推导同批创建/直连；缺起点的主角 move 在
  proposal 和 final 两层均拒绝，NPC 豁免保持。反例覆盖“只删 moves 不改 narration”、唯一 route 被撤回的零写入、
  无起点的 proposal/final 零写入。专项为 `109 passed`，正式 gate 为 `1859 passed, 1 deselected in 18.93s`；
  没有重跑浏览器专项、没有真实模型。随后才继续 RCV-12 的 knowledge replay/route visibility 审计。
- 2026-10-12：RCV-12 继续把 event-owned discovery 收敛到同一 fail-closed capability 契约。主角对
  `visibility=undiscovered` 地点/路线的移动仅在 action-start POV 已可见，或同一 action 由受信 host
  明确授予地点与路线两条 `knows:<id>.discovered` 后才允许；普通叙事/事实/broadcast/endowment 不可写
  discovery namespace，最终 `EventBatch` payload 也再次校验。grant 采用事件顺序的**最终值**而非“曾经为真”
  语义：同批 `true→false` 在任何 provider/generator/write 前拒绝，`false→true` 则可在 host 授权、直连
  undiscovered 目标时真实发布并持久化。旧 receipt 的空 discovery grants 仅保留兼容；非空 legacy grant
  sidecar 不回退解析。真实 SQLite 回归覆盖同回合 host discovery+move、duplicate/retry、undo、冷重开、
  遗留 sidecar，以及本轮顺序覆盖；`tests/context/test_capabilities.py -q` 为 `15 passed`。正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1868 passed, 1 deselected in 19.20s`**；唯一 deselect 是既有 slow real embedding，未调用真实模型。
  这证明机制、重放和边界回归，并不等同于原生模型剧情质量验收；受控原生输入与导入脚本仍是未验证 WIP，
  本阶段未运行、未启用 B1/profiling、未 push/PR/部署。
- 2026-10-12：RCV-12 的首个原生试玩 hand-off 现只准备**真实玩家输入**，不伪造原生作者结果。新
  `scripts/export_native_turn_context.py` 用显式 `FakeLLMProvider` 和 `embedder=None` 从既有 campaign
  导出窄 player-POV brief：当前位置、主角公开背景、当前可见人物、随身物品、公开叙事/事件、可用出口和地图，
  再附一个由宿主提供的自由行动目标；它不导出 canonical graph、原始事件、私有解释、隐藏地点或发现 grant。
  独立 campaign 的实际导出含客栈主人玛拉、旧铜灯、两条公开叙事和到石桥集市的出口，隐藏旧塔 ID 与私有
  sentinel 均不在输出。`scripts/import_native_turn.py` 可直接执行，canonical 输入改为真实
  `TurnCommit.to_dict()` 的 top-level schema（旧 `sections` wrapper 仅兼容读取），显式拒绝 host-only
  `trusted_discovery_keys`/`host_envelope`/`memory_role`，并固定 Fake provider、禁用 embedder、不开 discovery
  grants、以 `enforce_player_travel=True` 调用既有 `run_turn`。这仍不是原生模型调用或剧情质量结论：下一步只能由
  原生作者依据该 brief 生成 proposal 后再导入。集中复核同时修复了无 `knower` 的 `broadcast` 会跳过 proposal
  discovery namespace 检查的问题；新回归和导入/导出离线执行回归共 `18 passed`。正式完整离线 gate 为
  **`1871 passed, 1 deselected in 19.60s`**，唯一 deselect 是既有 slow real embedding。对 `--all` 的全历史
  内容式 token/key 模式扫描（只计路径，不输出内容）为 16 个历史路径候选；本新增 diff 为 0，不能替代专用
  secret scanner。未调用真实模型/embedding/网络、未启用 B1/profiling、未 push/PR/部署。
- 2026-10-12：原生作者首回合以未改写的 proposal 只导入一次到独立 campaign：receipt 覆盖 seq `19..22`
  且 revision 为 3；公开 narration 原样持久化，主角仍在客栈、旧铜灯仍由主角持有、出口不变。实际 `clock_advanced`
  payload 是 `advance=false/days=0/bands=0`，所以时间未推进；`quest_created` 是 fleet 依据该 substantive
  clock event 自动建立的暗线 backstop，`lore_seeded` 是 Fake provider 生成零条 lore 后的地点 seed 标记，均不是
  作者写入的世界事实。作者叙事“把灯放在桌上”没有 `items.transfer`，故只作为临时叙述，canonical `held_by` 未变；
  不能把 event 名或 prose 当作状态效果。前后公开差异证据仅含时间、位置、可见人物/物品/出口和新增 narration，
  排除 raw store、后台 quest/lore、私有和隐藏内容。

  质量诊断表明原 brief 复用了 GUI 玩家 DTO，缺少结构化时钟；现有 world 也没有渡口原因/集市消息的带来源知识，
  因而“没有输入”不能推出“玛拉不知道”。最小治本修复 `context.native_authoring.native_authoring_context` 将
  player DTO 与作者补充上下文分离：作者仅得到公开 scene day/band、canonical 持有物、共场人物可观察字段，以及
  **NPC 明确 knowledge + 被引用事实明确 `secrecy=public`** 时才给出的 `speakable_evidence`（含来源/可信度）。
  空 evidence 明示为 `not_supplied_not_npc_unknown`；无可计算 deadline 时明示不推定日期；物品持有变化必须有
  `items.transfer`。它不向作者传 GM 秘密、隐藏实体、raw events 或任意 NPC 信念，也不自动编造/写入 clue。
  专项 `19 passed`，正式 gate 为 **`1872 passed, 1 deselected in 20.12s`**；唯一 deselect 为既有 slow real
  embedding。该机制已为下一次“拿灯去集市、找亲历者核实来源”的真实玩家行动准备 authoring material，但没有
  预置 NPC、结果或第二回合回应。
- 2026-10-12：对 `e555377` 的增量隐私审查发现 `speakable_evidence` 仅要求被引用 fact 为 `secrecy=public`，
  仍可能由在场 NPC 的 knowledge 把 hidden/secret/undiscovered subject 的 ID 泄漏给作者。该 checkpoint 已在
  验证记录标为 WIP、不可作为 privacy acceptance。修复 `4c5a251` 将共享 `pov_world` 的 subject 可见性作为
  **额外必要条件**：只有引用 fact 为 public 且其 subject 出现在同一玩家 POV projection 时才进入 authoring
  evidence。回归让可见 NPC 同时知道可见地点的公开事实与隐藏地点的“公开”属性；前者保留带来源/可信度材料，
  后者的 ID 与 sentinel 均不输出。专项 `tests/scripts/test_native_turn_tools.py -rA` 为 `3 passed`；正式完整 gate
  为 **`1872 passed, 1 deselected in 20.80s`**（唯一既有 slow real embedding）。原 checkpoint 完整 bundle 的
  全历史 782 blobs/567 paths 与源码归档 456 files 无实际 `.env`、私钥名、campaign DB/JSONL；只有两份 `.env`
  模板，唯一强模式是文档/示例占位。下一真实玩家行动仍未执行，也未预置第二回合。
- 2026-10-12：同一首回合的实际 payload 还暴露 Fleet 暗线 backstop 的泛化误判：`heuristic_floor` 对未知 event
  的默认分为 1，加任意 deltas 后达到 2；因此即便 `clock_advanced` 的 `advance=false` 也会成为唯一“substantive
  player event”，自动创建无叙事依据的暗线。`d28cc98` 将 clock bookkeeping 从 backstop 候选集中整体排除，
  不是针对本次对话/NPC 文本；`advance=false` 和真实推进 `advance=true/bands=1` 两种 clock-only 输入都不会造 quest，
  既有角色发展触发暗线的回归保持。专项 `tests/loop/test_fleet.py tests/scripts/test_native_turn_tools.py -rA` 为
  `18 passed`；正式完整 gate 为 **`1874 passed, 1 deselected in 19.23s`**（唯一既有 slow real embedding）。
  这不会重写已导入 campaign 的既有历史；下一真实玩家行动仍等待安全作者材料和新的真实 proposal，未执行。
- 2026-10-12：第二个原生作者候选（保留原文）尝试从客栈移动到集市、调查渡口消息来源，但 `clock: []`
  被 importer 经三次空修复后 fail-closed 为 `TurnRejected: Action still contains invalid sections: clock`；独立
  campaign 保持 revision 3、22 events、地点客栈、day 1/band 0，零写入。它不是路线/叙事质量通过，也没有被
  人工补 clock。根因是 native brief 的 schema 比正常 `AuthorStrategy` 过度缩减：未给 `items/knowledge/quests/world`
  的可选结构，也没有把 clock 的“恰好一个元素”或“route cost 不是时间换算”明确交给作者。`23b8016` 恢复这些
  通用 schema 与 action adjudication 规则：新信息须以 fact+`knowledge.told` 持久化；物品 custody 变化才用
  `items.transfer`；route cost 不自行换算 clock。它不提供市场证言、NPC 回答、隐藏事实或第二回合预置结果。
  专项 `18 passed`，正式完整 gate **`1874 passed, 1 deselected in 20.12s`**；唯一既有 slow real embedding。
  已为下一份真实 proposal 导出更新后的安全作者材料，尚未再次导入。
- 2026-10-12：真实作者按修复后的 schema 返回第二候选重试；原文另存并经 importer **只导入一次**，成功提交
  revision 4。它只将主角移动到石桥集市、保留“正在打听”的公开叙事和 `advance=false` 的晨间 clock；没有代写市场
  答案、NPC 证言、渡口原因或隐藏信息。导出的 post-commit 安全回执仅含玩家 POV、公开叙事、出口/地图和 schema。

  对正常 `AuthorStrategy` 的逐项审查确认，上一版 native hand-off 的“未经材料支持不得写事实”错误地把**创作自由**
  与**既存权威**混在一起。新的 native-only policy 保留普通公开场景/人物创作：可新建公开人物，并以
  `speaker.reported:<topic>` 加同值 `knowledge.told(via=speaker_id)` 保存一条带来源陈述；它记录“该人物这样说”，
  而不是把尚未裁定的内容覆写成世界真相。既存实体则只可逐字复述 brief 已给出的公开 `speakable_evidence`；既存事实
  覆写、既存角色 evolve、隐秘/受限事实、hidden/past facet、以及全由既存地点组成的拓扑改写均在 publish 前拒绝。
  该 policy 只包住离线 native importer，不收窄正常 `AuthorStrategy`。新增回归验证公开来源陈述可真实持久化且不产生
  `stonebridge_market.status`，并验证既存事实覆写与缺少同值/来源 `knowledge.told` 的报告拒绝。专项
  `tests/scripts/test_native_turn_tools.py -rA` 为 **`5 passed in 0.98s`**；正式完整 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1876 passed, 1 deselected in 21.02s`**，唯一 deselect 为既有 slow real embedding，未调用真实模型。

  完整可达 Git 历史安全扫描覆盖 1,218 个 path-addressed blobs（1,310 object rows）：没有私钥、AWS/GitHub/Slack
  token 命中；两类宽泛模式仅落在既有 `.env` 示例、provider 配置/测试和历史文档占位中，逐项检查未见实际凭据。以
  `6f56ac5..60195d3` 的本增量二进制 diff 做同一强模式检查为零命中，变更路径仅本台账、native author policy、导入/导出
  脚本和专项测试。扫描只记录路径/计数，不输出潜在秘密正文。随后生成完整历史 bundle、源码归档、增量 diff 与验证记录并
  做全新 clone 恢复后再保存到 Library；不调用真实模型/embedding、不启用 B1/profiling、不 push/PR/部署。
- 2026-10-12：第三份原生作者候选原文另存后，使用新的真实玩家输入经 importer **只导入一次**到独立 campaign，
  成功提交 revision 5（receipt `999486f9-236c-4bd6-876d-5bf0fe76a7c3`，seq `29..39`）。它让主角留在石桥集市，
  新建公开、同场的两名消息来源，并将他们各自的限定陈述保存为
  `market_witness_luoen.reported:ferry_observation` 与
  `market_cloth_seller_surui.reported:ferry_message_source`，以及同值、同 source-id 的 protagonist `knowledge.told`；
  没有写入 `stonebridge_market.status`、没有断言渡口原因/当前状态，clock 仍是 `advance=false/days=0/bands=0`。
  这证明“来源陈述”可保存但不是世界权威。实际导入产生的 `quest_created`（seq 39）是当时 Fleet 对
  `entity_moved` 的旧 generic importance fallback 自动生成的非作者暗线，未修改该已保存历史。

  该真实回合还暴露两个可复用缺口，均已在后续新回合前收窄：(1) `_build_scene` 只跟踪 ordinary cast，导致刚创建、
  同场但未 tracked 的公开消息来源没有进入下一份 native brief；现在仅在 player POV 中加入**同地点、公开、自己持有
  `reported:*` 事实**的人，并只以 `present_npc_persisted_report`/`self_reported` 原样提供该人的 source-bound evidence。
  这不是把所有 canonical 人物、隐私事实或世界真相暴露给作者。(2) Fleet backstop 现只接受明确的语义发展 event
  （character/relationship/world/place/populace/lore），不再把 sourced report 的 `entity_moved`、创建、fact/knowledge
  persistence 或 clock bookkeeping 误当剧情发展而凭空造 quest。新回归覆盖两名来源人的后续 brief 与整套报告持久化
  bookkeeping 的零 backstop；专项 `tests/loop/test_fleet.py tests/scripts/test_native_turn_tools.py -rA` 为
  **`21 passed in 0.85s`**，正式完整 gate 为 **`1877 passed, 1 deselected in 18.36s`**，唯一 deselect 为既有 slow
  real embedding。没有调用真实模型/embedding/网络，没有新增原生剧情、没有 push/PR/部署；下一回合仍须先得到新的作者
  proposal，不能由宿主补写。
- 2026-10-12：独立质量评审以第三次真实导入为依据，确认“有限亲历自述 + 同源转述”的 provenance 模型正确，
  但发现“既存人物只能逐字复述”会把自然追问逼成不断新建 NPC。native-only policy 现允许 **brief 中可见且在场的
  既存人物**新增一个此前不存在的公开 `reported:<topic>`；必须同时给 protagonist 写同值 `knowledge.told`，且
  `via` 为该人物 ID。它仍拒绝旧 `reported:*` 键的覆盖/升级、非在场人物、existing world facts、secret/restricted、
  hidden/past 和人物 evolve，因此新增的是“此人此刻这样说”的来源记录，不是世界真相。真实第四玩家输入已经固定为
  “询问洛恩候船位置、离开时点及从集市如何前往，先听后决定”，但本阶段没有代写其答案、proposal 或导入。

  路线也明确分成两个通路：公开询路只可形成上述 source-bound `reported:*`，不能新增地图边或移动；仅宿主在
  export/import 两端都显式传入 `--host-authorize-route DESTINATION` 时，才会暴露并临时授权一条**已存在、undiscovered、
  从当前地点直连**的路线。proposal 仍须同时写精确的 `discover_place`/`discover_route` knowledge，最终 publish 再校验；
  缺少授权、伪造其他目的地或企图重写拓扑均为零写入拒绝。默认仍无 route grant，不泄露隐藏地点。专项实际验证普通 brief
  不含 hidden destination、无参数 import 拒绝且 revision 不变、有参数时才发现并移动；连同 provenance 追问与现有
  capability 回归为 **`23 passed in 1.89s`**，正式完整 gate 为 **`1879 passed, 1 deselected in 20.64s`**，唯一 deselect
  是既有 slow real embedding。

  对实际 campaign 的只读核查确认 seq 39 `th_auto_aba5dff5` 仍是 `state=暗` 的 `quest_created`，没有后续
  `quest_surfaced`/`quest_opened`/`quest_advanced`；明任务账只渲染 `state=明`，因此它尚未成为玩家任务或目标。
  它仍保留在历史并可占用后台暗线容量，未被删改或误称清除。没有真实模型/embedding/网络、B1/profiling、push、PR、部署
  或 Library 上传；待连接恢复后才处理持久化上传。
- 2026-10-12：在完成上述 schema/权限闭环后，既有真实作者的第四候选按原文另存，并以真实玩家输入
  “询问洛恩候船位置、离开时点及从集市如何前往，先听后决定。”经 importer **只导入一次**。成功 receipt
  `6d0ee443-bf74-49c0-9d7e-d9903d9ec537` 覆盖 seq `40..47`、foreground revision `6`、turn 4；主角仍在
  石桥集市，day 1/band 0，clock 仍为不推进。它为既存、可见的洛恩新增两条 public `reported:*`：候船位置/时点与
  步行方向；每条均有同值 protagonist `knowledge.told` 且 `via=market_witness_luoen`，旧
  `reported:ferry_observation` 未变。路线仍是洛恩自述，未新建 place/link、未移动主角、没有 `.discovered` knowledge，
  玩家 POV 地图仍只有客栈/集市和一条公开边；`stonebridge_market.status` 仍为 null。

  receipt 内的 seq 46 `oracle_roll` 和 seq 47 `director_fired` 是既有后台 hook，均非作者 prose 或 quest；本次没有
  新 `quest_created`/surface/open/advance，旧 `th_auto_aba5dff5` 仍是暗态历史 line。安全回执只记录上述公共结构差异，
  不把路线陈述升级为世界权威。未生成第五候选、未改写/修复导入结果、未重跑测试、未上传。
- 2026-10-12：对下一玩家意图“我按洛恩所说，在集市靠河的一头观察有没有对应的下坡路和木桩，确认眼前情况再决定是否继续，
  暂不直接抵达渡口。”作宿主侧只读判定。当前 player POV 仍只见集市/客栈和一条公开边，唯一可移动出口是客栈；当前地点的
  合格 `visibility=undiscovered` 直连 host-route 候选数为 **0**。已知 hidden 目标被
  `host_route_discovery_grant` 拒绝（它不是 undiscovered），故不会因洛恩的方向陈述或本次观察尝试而授予目的地、路线、
  `.discovered` knowledge 或移动。这是当前 canonical 资料不足的安全结论，不是“路线不存在”的世界断言。

  因现有宿主规则已能 fail-closed，本阶段没有新增代码或路线数据：已导出第五作者的最小安全 brief，`host_authorized_routes=[]`，
  带三条洛恩的既存来源陈述及真实玩家输入；goal 明确只允许原地观察，不得把 prose 转成地点/地图边/发现权限。brief 不含隐藏
  地点 ID 或私有 sentinel。尚未生成第五候选或导入；若将来宿主先建立一个可公开、已存在且 undiscovered 的直连路线，才可由
  export/import 两端同传显式 host authorization 走既有受验收通路。Library 仍未重试。
- 2026-10-12：对“听到路线→可沿路核实”的架构根因复核确认，正常 `AuthorStrategy` 和 RCV11 capability 合约本来就
  支持同回合**真实新 ID** 的公开 `places` 加从 action-start 当前位置的 direct `links`，并允许主角原子移动到该新地点；
  它与宿主揭示既有 undiscovered 路线是两条不同路径。native policy 也并非完全禁止新地图，但原先只要求 link 一端为新
  place，可能让 fresh public place 搭接 hidden/其他既存拓扑；共享 `validate_commit` 也没有显式报告跨 creation section
  的同 ID owner 冲突。现修复为：所有策略先拒绝同一 ID 被多个 creation section 声称；native author 仍可创建 fresh、
  public place，但每个新 place 必须以唯一 public link 直接连到当前地点（可同回合移动），不得复用既存/hidden ID、接触
  其他既存地点、materialize 既存地点、用 generic `entities` 伪造 Place，或以 generic relation 改 adjacent topology。
  这条路径是新世界创作，不写 `.discovered`、不把传闻升级为 host discovery，也不改变当前 hidden 目标。

  新回归实际经 native importer 创建通用 `courtyard_steps` 并从当前客栈直连/移动成功；同一测试再尝试把 fresh marker
  搭接 hidden 既存地点被 policy 拒绝。共享 cross-section collision 回归覆盖同 ID 同时作为 place/cast 的 fail-closed。
  专项 `tests/scripts/test_native_turn_tools.py tests/context/test_capabilities.py tests/systems/test_place.py -rA` 为
  **`90 passed in 2.16s`**；正式完整 gate 为 **`1881 passed, 1 deselected in 21.50s`**，唯一 deselect 是既有 slow
  real embedding。没有为渡口或现有 hidden 目标写入路线，没有生成/导入第五候选，没有 B1、付费模型、push、部署或上传。

- 2026-10-12：真实第五候选只在集市边创建公开 `market_canvas_edge_clearing` 与到集市的单一公开直连，主角未移动；第六候选
  原文随后只导入一次，主角持旧铜灯移动到该空地并以 `world` 写入局部观察。它实际形成 `world_change`，但旧实现只把该内容
  留在 cascade audit/地点事实和完整 narration，下一 native brief 没有结构化地点观察；而且该类局部记录会被当作全局 cascade
  触发，产生非作者的暗线 hook。保留两份原候选和安全回执，未重放、修改或迁移真实 campaign。

  根因修复新增事件溯源的 `public_place_observations` DTO：只有 `world` 明确标记
  `public_observation=true`、`visibility=public`、`observation_kind=stable_layout|single_observation` 和
  `observation_range`，且地点同时处于玩家 POV 的当前地点或当前合法公开出口，才进入 normal `assemble_context` 和
  native `authoring_context`。DTO 保存 source event、地点、day、turn、scene、范围和摘要；稳定布局按地点/范围收敛，
  一次观察每地点有小窗口，不依赖重复整段历史。未标记 legacy `world_change`（含真实第六回合）安全地不提升为结构化观察，
  原公开 narration 仍保留；不以猜测回填可见性。native policy 只允许其 `world` 写入本回合起点或主角同回合目的地的 level-1
  明确公开观察，普通 world/global 变更仍走既有路径。显式公开观察不再触发 cascade 或 Fleet 暗线 backstop。

  新回归覆盖裁剪后仍可读取、重访相邻地点、stable/one-time 区分、source/location/time/range、隐私 sentinel 和未标记
  world_change 均不泄漏、native 越权拒绝，以及不触发 cascade/quest。专项为 **`51 passed in 2.04s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为 **`1886 passed, 1 deselected in
  20.12s`**。唯一 deselect 是既有 slow real embedding；未调用真实模型/embedding/网络、未启用 B1/profiling、未 push/PR/部署。

- 2026-10-12：对 revision 8 的下一次公开返回集市动作复核发现，native policy 已允许同回合移动到合法公开出口，却只把
  action-start 场景的 `present_people` 给作者；这会把普通“一次行动抵达后观察/交谈”错误地退化成先尝试、下回合才可写。
  现新增共享的 `conditional_arrivals` 投影：只枚举 `scene_capabilities` 已允许的公开出口，行内明确“仅当同一
  TurnCommit 提交对应主角移动后才可呈现”，并以 hypothetical POV 场景二次排除 `hidden/secret/undiscovered` 人物及其事实。
  它不改变行动开始时主角知识、不授予新路线/发现权限、也不预定输入以外的行动结果。native policy 仅在提交匹配移动时，才
  接受该行既存人物的公开 `reported:*`/`knowledge.told` 来源陈述；没有该移动仍 fail-closed。

  更新后的 revision 8 brief 因此为唯一公开出口 `stonebridge_market` 提供条件化到达材料：苏芮和洛恩；洛恩的三条既存
  source-bound 公开陈述仍带范围/不确定性，不是世界真相，也不在空地提前成为主角事实。专项
  `tests/scripts/test_native_turn_tools.py tests/context/test_place_observations.py -rA` 为 **`13 passed in 1.99s`**；新回归
  同时验证隐藏目的地人物的 ID/私有 sentinel 不进 brief，匹配移动可使用公开来源、不移动则拒绝。没有改写或导入既有 campaign
  历史；未调用真实模型、网络、B1/profiling、push、PR 或部署。

- 2026-10-12：`8712f58` 的正式独立全量 gate 为 **`1887 passed, 1 deselected in 20.71s`**（命令
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA`）；唯一 deselect 是既有 slow real
  embedding，不能与 `a436b1b` 的结果混用。完整 bundle verify 通过，fresh clone 的专项为 `13 passed in 2.15s`。源归档与
  tracked 源树均无 SQLite/DB/JSONL；全历史高置信凭据前缀扫描为零命中，仍非绝对保密担保。Library 读取恢复，但此 checkpoint
  的一次有序写入在开始前再次因 hosted-tools network 失败；未拆分、未循环重试。

  随后保存原样第七候选并以既有玩家输入经 importer **仅一次** 导入：revision `8 → 9`，主角抵达
  `stonebridge_market`。导入前条件化名单正是苏芮与洛恩，policy 无错误，旧未标记第六观察仍为空结构化输出。实际
  `world_change` 的宿主字段为 `source=author_world`、`day=1`、`turn=7`、`scene=s3`，其明确 public
  `single_observation` 已在下一 native context 的 `public_place_observations` 出现；同回合无 quest event，未见自动暗线
  误触发。候选叙事中的“没有替自己接着发问”作为质量观察保留，未悄悄改文。第七候选原文、只读安全回执和后续 proposal/import
  证据均留在 `review_material/`；不重放、不修改既有历史。

- 2026-10-13：第八真实作者候选原样保存后，经 importer **仅一次** 导入 revision `9 → 10`。它新增洛恩的两条
  public、source-bound `reported:*` 陈述（空地描述与其记忆的差异、下坡入口的限定标记），并以同值/同 source 各写一条
  `knowledge.told(knower=protagonist)`；地点仍为石桥集市、旧铜灯仍由主角持有、无 quest 事件。实际该 turn 的两个
  `knowledge_set` 接收者都是 protagonist，洛恩没有 `knows:*` 写入；不得把散文中的“你告诉洛恩”表述为系统已持久化的
  NPC 记忆。

  机制审查确认：正常 `KnowledgeSystem`/`AuthorStrategy` 支持将 `told` 写给任意既存 knower，但 native handoff 为防止
  作者伪造 NPC 认知，明确只接受 protagonist told（除宿主 discovery grant）。因此这是一个需单独设计宿主验证、行动局部、
  有来源的“公开交流回执”能力的缺口；不能通过从 narration 自动抽取或放开任意 native knowledge 绕过。代码未改变，未重跑
  gate，按请求未上传。

- 2026-10-13：`91c810d` 曾把整条非空 `player_input` 与公开共场收件人绑定。这只能校验“全文相等”，却不能区分行动、心理/旁白和
  对不同人物的说话；因此明确标为 **WIP，未用于 campaign、未补写第八事件，现已废弃**。不能由 narration/关键词猜测，也不能让
  native 作者自行授予收件人权限。

- 2026-10-13：替代的最小通路仍为 `knowledge.heard_player`，但 `communication_receipts` **默认空**。可信宿主必须显式选择一个
  公开直述片段与一个收件人（到达后场景还须选择合法目的地），经 `host_player_statement_receipt` 用公开当前/条件到达 POV 验证后，
  才发出 `{id, op, speaker, recipient, statement, when}` receipt。proposal 只能提交相同的 `receipt_id`、`knower`、`speaker`、
  `statement`，不得附加作者控制的知识字段；没有 receipt、改写、远程/隐藏人物、未匹配到达、重复覆盖均 fail-closed。
  `KnowledgeSystem` 以非作者可选的 digest key 写入 `knowledge_set`，value 仅为该片段且标 `source_kind=player_statement`；
  这不是 world fact、路线/地点发现或 NPC 亲历。例中宿主只选“我看见一根木桩。”给洛恩时，苏芮得不到心理或私下话，洛恩也不接收动作
  指令。既有历史完全不改。

  新正负回归覆盖默认无 receipt、显式当前/条件到达收件人、隐藏 sentinel 隔离、远程/改写/无匹配移动/重复覆盖拒绝，以及事件级
  source/via/key 形状。专项 `tests/systems/test_knowledge.py tests/scripts/test_native_turn_tools.py
  tests/context/test_place_observations.py -rA` 为 **`55 passed in 2.23s`**；正式完整离线 gate 为
  **`1889 passed, 1 deselected in 20.84s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、B1/profiling、
  push、PR、部署或 Library 上传；下一步仅在用户授权下继续新的候选回合。

- 2026-10-13：上述 `919abcc` 的交流回执边界经独立复核后仍有一个 **WIP 隐私缺口**：`pov_world` 可因同场关系保留一个
  canonical `hidden/secret/undiscovered` Person，而当前/条件到达的 `speakable_evidence` 仅检查该 subject 是否出现在 POV，可能让
  公开 NPC 的 `knows:hidden_person.public_fact` 泄露隐藏 ID/值。此状态没有用于导入候选；不得把 `919abcc` 单独称为完整隐私验收。

- 2026-10-13：`2a26b39` 把当前与条件到达两条 evidence 路径统一为 canonical visibility + POV subject 判定；两种非空反例均为
  “公开 NPC 知道隐藏同场 Person 的 public 属性”，现在不输出 hidden ID 或 sentinel。专项
  `tests/scripts/test_native_turn_tools.py tests/context/test_place_observations.py -rA` 为 **`14 passed in 2.53s`**；正式离线 gate 为
  **`1889 passed, 1 deselected in 21.11s`**。该判定只保护 native hand-off material；`heard_player` receipt 也只在 native importer
  路径有界校验，通用 `KnowledgeSystem` 不自行验证 receipt。`public_place_observations` 的公开性仍依赖上游正确显式标记、POV 与地点范围
  检查，未在本次审计中发现确定漏洞。

  修复后才保存第九作者候选原文，并在真实 campaign revision `10` 上以唯一的洛恩回执一次导入至 revision `11`。实际只写
  `market_witness_luoen` 对精确主角片段的 digest-key `knowledge_set`（`source_kind=player_statement`）；苏芮无该 key、集市没有对应
  world fact，未补写历史。候选没有替换叙事。无真实模型、push、部署或重跑候选。

- 2026-10-13：第九回合后只读导出发现一个端到端缺口：`heard_player` 虽已落为 recipient 的 `knows:<digest>`，原
  `speakable_evidence` 只接受有配套公开 canonical fact 的知识，导致洛恩下一 author brief 看不到自己已听到的原句。`native_authoring_context`
  现只在可信 EventStore 提供的 active event 中验证 `knowledge_set` 的 `source_kind=player_statement`、收件人、当前主角 via、精确 value、
  digest key 与 graph 的 `source_event` 全部一致，才为当前/匹配条件到达的安全收件人投影独立
  `recipient_heard_player_statement` evidence。默认无 trusted events 不投影；篡改 event、其他 NPC、撤回或任意私有 `knows:` 均不通过。
  该 evidence 明确是“收件人听到原句，不是世界真相”，并从 native policy 的可重写 canonical-fact sources 中排除；可用于叙事和收件人的
  新 `reported:*` 回应，不能把主角话伪造成 public world fact。

  真实 revision `11` 的只读 brief 现只给洛恩一条精确 recall：`protagonist.reported_player_statement:3350e0f966055bdd2d86`、原句、
  speaker=protagonist、`confidence=heard_exact_words`，没有新 communication receipt、没有预设回应、没有其他洛恩私有认知。专项
  `tests/scripts/test_native_turn_tools.py tests/context/test_place_observations.py -rA` 为 **`16 passed in 2.49s`**；正式 gate 为
  **`1891 passed, 1 deselected in 20.92s`**。仍无真实模型、push、部署或历史重写。

- 2026-10-13：独立第十 native 作者只得到该最小 recall、没有此前游玩聊天或其他洛恩认知；其原文候选已保存并从真实 revision
  `11` **一次** 导入至 `12`。该提交只有 clock、narration、summary 与 directive event，零 `knowledge_set`：洛恩保留第九回合的
  唯一原句 digest record，苏芮没有该知识，集市没有 matching world fact；主角仍在 `stonebridge_market`、旧铜灯仍由主角持有。
  这验证单次同场安全回忆，不声称长局、跨场景或所有普通作者入口已全面覆盖。没有重放/补写历史或新增 communication receipt。

- 2026-10-13：首次以真实产品调用链而非仅 native acceptance scripts 审计十回合已出现的材料。普通 CLI
  `play_loop`、GUI `PlayerDashboard.action` 和 compare 的最终 apply 都是 `AuthorStrategy`（compare 先产生候选）→
  `run_turn`；普通 `assemble_context` 已共享 `scene_capabilities`、`pov_world` 和 `public_place_observations`，所以公开地点观察属于
  正常作者路径。`conditional_arrivals`、显式 `host_player_statement_receipt` 的签发，以及经可信 event 回验的
  `recipient_heard_player_statement` recall 仍是 `export_native_turn_context.py` / `import_native_turn.py` 的 redacted native
  hand-off 材料；普通 UI/CLI 没有“选择一句公开直述并指定收件人”的宿主控件，因而默认不会签发此能力。普通 KnowledgeSystem
  仍可写其常规 `told` 等机制，不能借此把整段玩家输入、心理活动或 narrator prose 推断成 NPC 已听到的原话。

  审计同时发现两个会跨入口造成真实影响的缺口并在共同边界修复：(1) GUI 已开启主角 POV travel publish validation，CLI
  与 compare 最终 apply 原先仅有 prompt 指引、没有传 `enforce_player_travel=True`；现在两条 CLI 路径同样启用，隐藏目的地的
  author proposal 在修复/耗尽预算后整 turn 零写入。(2) `heard_player` 原先只有 native policy 检查，普通
  `AuthorStrategy → run_turn` 能把模型自称的对话写为 NPC memory。现在 `run_turn/produce_turn` 共同要求 host receipt；它逐字比对
  speaker/recipient/statement/condition，并由当前公开 POV 重算收件人与合法到达，拒绝重复/既有记录与附加作者字段。耗尽 repair
  预算时整 turn 拒绝，避免 narration 说“NPC 听到”而 durable memory 没有；action-id fingerprint 也包含该 capability。
  native importer 将它已经审计过的 receipt 显式传入同一 gateway，未放宽 native policy。

  新离线回归以真实 CLI play loop 证明伪造的隐藏旧塔移动保持 revision/位置不变，以真实 GUI action 证明伪造
  `heard_player` 不入 state，原 native importer 的显式 receipt 路径仍成功。专项
  `tests/app/test_play.py tests/app/test_gui.py tests/scripts/test_native_turn_tools.py tests/context/test_capabilities.py -rA`
  为 **`76 passed in 3.21s`**；正式完整 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1893 passed, 1 deselected in 21.34s`**。唯一 deselect 仍为既有 slow real embedding；没有真实模型、B1/profiling、push、PR、部署，
  也没有生成或导入新的真实作者候选。该覆盖是产品入口与该具体 capability 的回归，不把此前的 native recall 验收夸大为长局、跨场景或
  所有普通作者材料的完整验收。

- 2026-10-13：在 RCV-13 初始 checkpoint `a715e09` 的独立复核中发现两项阻断：其新 request fingerprint 无条件加入空
  receipt 字段，却没有保留 RCV-12 已写入的“含 `trusted_discovery_keys`、无 receipt 字段”中间信封恢复路径；并且
  `heard_player` 错误作为 `knowledge` section 进入 modular repair 后，策略可删掉 section 而保留“NPC 已听到私下话”的 narration。
  因此 `a715e09` 标为 WIP，不能作为发布/交付验收点。

  后续修复仅当本次请求的 receipt 集为空时，接受精确保留旧 discovery 字段和值的 receiptless RCV-12 hash；任何非空新 receipt
  或改变 discovery authorization 都仍在生成前 `RevisionConflict`。通信授权错误现在在首次 candidate validation 即整 turn 拒绝，
  不进入 section-only repair；final `EventBatch` publish 还逐一核对所有 `source_kind=player_statement` 的 materialized event
  是否与当前公开 POV 重算后的 receipt deltas 相等，条件到达还要求同一 final payload 的主角移动。新 SQLite sidecar 回归证明旧
  receiptless discovery 请求零生成恢复、改 discovery/新增 receipt 均冲突；另验证 repair 删除 knowledge 不会掩盖 narration、
  materialization 后篡改 event 零写入拒绝。专项 `tests/test_action_integrity.py tests/scripts/test_native_turn_tools.py
  tests/app/test_gui.py tests/app/test_play.py -rA` 为 **`144 passed in 3.21s`**；正式完整 gate 为
  **`1894 passed, 1 deselected in 21.19s`**。唯一 deselect 仍为既有 slow real embedding，无真实模型、B1/profiling、push、PR、部署或
  新真实作者候选。

- 2026-10-13：`31d5b5a` 的初次 final-payload 复核又发现两个绕过，故该 checkpoint 同样标为 **WIP，不是最终验收点**：原判定只
  枚举 `source_kind=player_statement`，materializer 若删除/改写该字段便能让同一 digest-key `knowledge_set` 跳过；arrival
  则以任意匹配 `entity_moved` 为真，没有排除 retracted move 或检查同一事件序列中主角的实际最终位置。后续修复把 receipt 的
  完整声明计划显式交给 final validator，保留整个 `*.reported_player_statement:*` digest namespace；每一计划记录必须恰好 materialize
  为完整相等的 delta（包含不可丢失的 `source_kind`），未计划的同类 source 或 digest-key 一律拒绝。arrival 在 active event 的顺序
  状态机中验证：知识事件前必须已实际抵达目的地，且发布时最终仍在该目的地；retracted move、晚到 move 与随后离开均不满足。

  精确回归在真实 `run_turn → EventBatch.publish` 路径篡改 value、删除/替换 source_kind、retract arrival move、以及在 knowledge 后插入
  离开 move；每种均在 publish 前 `TurnRejected` 且 revision 不变。专项
  `tests/scripts/test_native_turn_tools.py tests/test_action_integrity.py -rA` 为 **`97 passed in 2.77s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为 **`1894 passed, 1 deselected in 22.07s`**。
  唯一 deselect 是既有 slow real embedding；未调用真实模型/embedding/网络，未启用 B1/profiling、未 push/PR/部署或导入新真实作者候选。

- 2026-10-13：RCV-13 的普通作者上下文补齐不复制 native policy，也不把 GM/原始 event store 交给模型。普通 CLI
  `AuthorStrategy`、正常 `PlayerDashboard`，以及 compare 的 Author/Hybrid 候选现在复用
  `native_authoring_context` 的同一安全来源：仅公开、可见人物的可归因材料，和仅由 `scene_capabilities` 枚举的条件化公开到达资料。
  每个 `run_turn` 在策略调用前立即把活动事件快照投影为 `_safe_authoring_handoff` 这一段受限文本；raw events 不进入策略 scene、
  provider tool closure、GUI host envelope、action fingerprint 或 result sidecar。compare 在候选生成前走同样的预渲染投影。

  条件行明确不是行动开始时主角知识；它只列出既有合法公开出口，最终实际移动仍通过既有
  `enforce_player_travel` 发布校验。实现不从自由叙事做不可靠的“声称已抵达”自然语言解析；因此本项保证的是结构化移动与资料来源边界，
  不是对任意模型散文的通用事实判定器。可信 NPC 原话只在可信 active `knowledge_set` event 精确回验后进入投影；普通玩家输入仍不
  自动生成 receipt，模型伪造 `heard_player` 会在共同发布边界整 turn 拒绝。旧 context 直接调用保留无回执的兼容投影。

  新真实入口回归覆盖 CLI 合法抵达、普通 GUI action、compare 双候选的同一安全材料，隐藏人物 ID/私有 sentinel 不入 prompt/sidecar，
  以及自然语言输入不能签发 `heard_player`。专项
  `tests/app/test_normal_authoring_safe_context.py tests/app/test_play.py tests/app/test_gui.py tests/context/test_assembler.py tests/scripts/test_native_turn_tools.py tests/loop/test_compare.py -rA`
  为 **`78 passed in 2.76s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1898 passed, 1 deselected in 26.12s`**。唯一 deselect 是既有 slow real embedding；未调用真实模型/embedding/网络，未启用
  B1/profiling、未 push/PR/部署或导入新的真实作者候选。

- 2026-10-13：为使已验收的明确交流 capability 可由普通玩家实际使用，CLI 新增唯一的 turn-producing
  `/speak <当前公开同场角色> <逐字原话>`；GUI 增加独立的当前公开同场收件人选择和逐字原话字段。两端都不是从普通行动、
  心理活动或 narration 猜测对话：宿主只在当前 scene 用 `host_player_statement_receipt` 重算可见共场收件人后签发一份
  receipt，并把它传给既有 Author/Hybrid、`run_turn`、candidate/final publish 验证。正常 author prompt 仅显示这份明确授权，
  要求精确 receipt id/收件人/原话；它不成为世界事实或对其他角色的授权。

  GUI 初次请求把 host-signed receipt 连同原 scene/prev_scene 写入既有 action-id sidecar；冷重开只重放这一已保存 envelope，
  改动收件人或原话会 fail closed。GUI state 只通过既有 native redacted current-scene projection 提供可选择的收件人。实施中发现
  一个更高优先共同缺口：`pov_world` 曾在 visibility 判断前因 co-location 暴露 hidden/secret/undiscovered Person；现先拒绝受限实体，
  再处理 public co-presence，因此普通作者、player DTO 和 POV tools 都不再因同地点泄漏 hidden ID/正文。

  新回归覆盖真实 CLI `/speak` 的精确 durable knowledge、GUI 当前可见收件人/隐藏收件人拒绝、sidecar 冷恢复与篡改拒绝，及安全
  author prompt。联合专项
  `tests/app/test_explicit_speech.py tests/context/test_assembler.py tests/context/test_capabilities.py tests/llm/test_tools.py tests/app/test_gui.py tests/app/test_play.py tests/scripts/test_native_turn_tools.py tests/loop/test_compare.py -rA`
  为 **`127 passed in 2.95s`**；正式完整离线 gate 为
  **`1900 passed, 1 deselected in 24.53s`**。本机 Chromium/Playwright 对 loopback GUI 实测确认独立收件人/原话控件及提示文本渲染。
  唯一 deselect 仍为既有 slow real embedding；没有真实模型/embedding/网络、B1/profiling、push、PR 或部署。

- 2026-10-13：对 `5bbd5ab` 的恢复语义复核发现一个交付前回归：若 action 已提交而客户端以同一 action ID
  改写明确交流的收件人或原话，GUI controller 曾抛 `GuiRequestError`，HTTP 因而返回带
  `confirmed_rejection=true` 的 400；浏览器会清掉 pending，错误声称该动作未保存。现统一改为
  `GuiRecoveryError` 的中性 409，响应没有 `confirmed_rejection`，浏览器保留 pending 并要求用户刷新检查
  世界状态或明确结束恢复。新增真实 WSGI HTTP 反例：先提交，再冷重开，以相同 action ID 改原话，断言
  409 与精确中性 payload。定向
  `tests/app/test_explicit_speech.py tests/app/test_gui.py tests/test_action_integrity.py -rA` 为
  **`114 passed in 0.86s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1900 passed, 1 deselected in 27.27s`**。唯一 deselect 仍为既有 slow real embedding。此前 Chromium
  证据仅确认收件人/原话控件和提示文本存在，并未验证 publish 或恢复；本次以 WSGI 层协议回归补齐，未把它表述为浏览器端提交验证。

- 2026-10-13：随后补做 loopback Chromium 的有限产品恢复验证（临时 campaign、公开同场旅店掌柜、固定 fake provider，
  不代表真实模型剧情质量）。浏览器原生 input/change 与 submit click 先从 required 行动文本为空的无效表单变为有效，实际
  `POST /api/action 200` 并显示 `行动已保存（版本 3）。`。停止服务、以同 campaign 新建 dashboard/server、刷新页面后，
  原 pending 信封的可见恢复控件实际得到第二个 200、显示 `已恢复此前保存的行动。`，revision 保持 3。只改逐字原话后再次
  点击恢复，实际得到 409，页面保留 pending 并显示需要查看状态后结束恢复，revision 仍为 3。临时 DB、浏览器 profile 和
  请求运行数据均已删除，未放入 bundle/source/diff；这关闭“仅控件渲染”的产品证据缺口，但不扩展为真实作者质量或长局验收。

- 2026-10-13：复核普通 CLI 与 GUI 的真实 `AuthorStrategy` 调用链，而非只检查 native helper。`run_turn` 在策略调用前写入受限
  `_safe_authoring_handoff`；`assemble_context` 同时把主角当前 facts（包含长期目标）、`scene_capabilities` 的 action-start
  出口，以及当前/可达地点的持久公开观察拼入 provider user message。离线捕获式真实入口回归分别经 `play_loop` 和
  `PlayerDashboard.action` 验证三者均抵达 AuthorStrategy，私有 sentinel 未进入 prompt。没有发现“每次视线变化必须停下”的
  指令；系统提示本来要求推进局面并给出可回应钩子。唯一确诊矛盾是 world 段仍错误声称普通作者拥有完整世界视野、可点名任意地点，
  与上述 POV 行动契约相冲突；现改为仅当前/相邻公开地点或同回合新建直连公开地点，越界的世界推进留给宿主/导演系统。

  定向 `tests/app/test_play.py::test_cli_author_prompt_binds_goal_scope_and_public_observation`
  `tests/app/test_play.py::test_dashboard_author_strategy_uses_the_same_scoped_context`
  `tests/context/test_place_observations.py tests/context/test_capabilities.py -rA` 为 **`23 passed in 0.37s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为 **`1902 passed, 1 deselected in 21.61s`**。
  唯一 deselect 为既有 slow real embedding；未调用真实模型、embedding 或网络，B1/profiling 保持 HOLD，未 push/PR/部署，也未生成新的玩家回合。

- 2026-10-13：以完整 v2 实际 `[system,user]` 捕获（SHA-256
  `868fb13f9ab3bc28d37b207a275ebbd86124864779e426f31bf14dfb280b5338`）为唯一作者上下文，隔离的 revision 14
  campaign 经真实 WSGI `POST /api/action → PlayerDashboard → run_turn → AuthorStrategy` 单次传入本地 native 作者候选。provider
  收到的 bytes 与 v2 完全一致；候选未进入 repair，HTTP 为 `200 OK`，revision `14→15`，receipt 覆盖 seq `106..110`。
  候选将坡口路面险情作为普通 fact 和主角 `knowledge.told` 写入，clock 仍为同一晨间；冷重开以同一 action-id 返回
  `duplicate=true`、revision 仍为 15，守卫 provider 零调用。提交后的摘要尝试因刻意不提供新本地摘要而为 `pending`，它不是
  native 摘要质量验收、不是 repair，也没有使用 Fake 的“一句话摘要”当模型质量结论。原 rev14 snapshot 的 DB/JSONL 哈希保持不变；
  完整的原 prompt、候选、HTTP/receipt/recovery 证据都在 Git-ignore 的 snapshot acceptance 目录，未重跑或改写这条剧情历史。

- 2026-10-13：修复普通作者场景片段把 `travel_cost` 路径权重渲染成“日”的误导：`PlaceSystem.inject` 现显示
  `地点（路径成本 N）`，与 GM GUI 已有的“移动成本”术语一致；没有把路径成本转换为 day/band，clock 仍由 candidate 的独立
  `clock` section 裁定。新增/收紧 PlaceSystem、正常 CLI AuthorStrategy 和正常 GUI AuthorStrategy 回归，明确拒绝旧的
  `stonebridge_market(1日)` 文案。定向为 **`67 passed in 0.30s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1902 passed, 1 deselected in 21.17s`**。唯一 deselect 仍是既有 slow real embedding；未调用真实模型、embedding、网络、B1、profiling、push、PR 或部署。

- 2026-10-13：对隔离的 revision 15 campaign 仅运行既有 `digest_fleet` 摘要链、未重跑任何玩家 action。recap provider 的两次
  `[system,user]` 消息均先逐字节匹配已捕获的正常链输入：s4 场景摘要消息 SHA-256 为
  `e32400276651bafd8a0d8d287f129c846283812029daa24f0f66e72f8a6e690f`，随后 total-recompression 消息为
  `049254349ae903588ae03b732fe3f271f2ba148418cbd8acfe49a1b22e36a024`。以两份给定本地 native JSON response 通过同一结构化链后，
  临时副本从 revision 15/110 events 变为 revision 17/112 events：seq 111 是 `s4` 的 `scene_summarized`，seq 112 是
  `recap_recompressed`；结果的 `recap_status=saved`。再用“任何生成请求即失败”的 provider 运行同一 digest，结果零 provider calls、零 append，
  因为没有 aged raw scene。前台 `native-author-turn13-gui-v1` receipt 仍为 seq 106..110、revision 15；原始 isolated source 的 DB/JSONL
  哈希和状态亦未变。验收材料在 Git-ignore 的 snapshot acceptance 目录。

  **测试数据质量债务**：本次 s4 摘要是实际 native response，但历史 s1–s3 只有 fake 的“一句话摘要”占位，其中 s2/s3 重复；因此 total response
  正确地说明没有可合并的具体剧情、承诺、关系或未解问题。这只验证 normal recap chain 的消息绑定、写入与去重，不是长程记忆质量证据。未来类似真实验收须对
  所需历史 scene 取得真实 native recap；这些 legacy placeholders 保留为审计证据，禁止事后以虚构内容覆写。无真实模型/网络、B1、profiling、push、PR 或部署。

- 2026-10-13：下一项真实验收改为让**前台作者与必要摘要都经过普通产品入口**，而不是继续对同一回合做离线摘要补丁。复用未改动的
  isolated revision 15 source，以真实 `PlayerDashboard.action → run_turn → AuthorStrategy` 先做零写 capture；下一最小玩家输入是安全返回
  石桥集市、向洛恩说明坡口裂缝/湿泥，并只询问他本人今早是否见过明确的安全绕行落脚点，若不能确定则不把路线当作可通行。它是风险后的
  有界决策/来源核实，而非第 N 次局部观察。capture 唯一 provider `[system,user]` 消息 SHA-256 为
  `ae4cc53eaac4c3b3a591f3ff83ddf65b88656d49115463d75a965ad5a231d669`；捕获 provider 在首个 `complete_messages` 后停止，临时 copy revision
  仍为 15、零写入，source DB/JSONL 哈希保持原值。

  **下一份需要的真实材料仅为**对该 exact author message 的完整 `AuthorStrategy` JSON candidate。它通过结构/权限校验后，才在 fresh disposable copy
  中经同一 GUI 路径提交；同一 run_turn 的 recap provider 必须依序字节匹配既有 s4/total message，并使用已留存的两个 native 摘要 response。
  如果出现第三个 provider 请求，立即记录并停止，不自行补答。这样会一并验收 normal GUI 前台提交、post-commit recap 保存及工件落盘顺序；既有
  s1–s3 placeholders 继续只是审计材料，不会冒充真实长程记忆。Library 刚失败，按约定本阶段不重试。

- 2026-10-13：上述普通入口 native candidate 的一次隔离执行发现 **P0 边界绕过**，因此这一轮 HTTP `200` 绝不构成安全验收。真实
  `PlayerDashboard.action → run_turn → AuthorStrategy` 在 revision 15 的 disposable copy 上接受了候选，revision 到 16；它合法移动回集市，且
  后台 provider 请求顺序为 exact author → s4 summary（既有 hash）→ total recompression（既有 hash），无额外请求并保存 recap。但候选没有
  host-signed explicit communication receipt，却以普通 `knowledge.told` 写入了 `market_witness_luoen` 对主角自由文字的 durable
  `knowledge_set`（receipt seq 113）：`fact_key=market_riverward_stone_lip_6e84c2f9.坡口路面险情`，value 是“主角告知…”，
  `via=主角返回石桥集市后当面说明`。它未使用 `heard_player` 或 `source_kind=player_statement`，故既有 shared receipt validator 没有拒绝；
  新 NPC report fact 的缺省 secrecy 与自由 via 亦被实际接受。原 revision-15 source DB/JSONL 哈希不变。

  这是需要单独修复/回归的普通入口 transcript-laundering 缺口，不可通过手改候选、删测试或把这一 HTTP 成功表述为预期的安全通过。修复设计必须保留
  NPC 合法获得独立来源事实的能力，同时阻止作者把玩家自由输入伪装成普通 `told`；在明确边界和测试矩阵确定前，不导入该隔离剧情，也不声称旧塔目标、
  路径安全或长程记忆质量已解决。

- 2026-10-13：RCV-18 针对上述 `12901b1` 记录的实际绕过完成系统性收敛。普通 CLI、GUI 与 compare 在显式
  `enforce_player_knowledge_authority` 下，candidate 层拒绝将通用 `told`、`endowment`、`broadcast` 写给 NPC，及用
  `facts` 的 `knows:*` 谓词绕过；唯一允许的 NPC 玩家原话仍是 host-signed 的 `heard_player` receipt。最终 `EventBatch`
  在 materialization 后再次拒绝未授权 `knowledge_set`、任何 `knowledge_broadcast`、`knows:*` fact；仅 canonical
  `source_kind=player_statement` 形状交由既有精确 receipt payload validator 逐字段比对，伪造形状同样零写入拒绝。上述都是
  结构/来源校验，不依赖中文 `via`、任意 value 文本或 secrecy 缺省；后两项仍只是 contract-audit 事项而非 P0 自动拦截。

  该约束仅在 ordinary player Author 路径启用，避免误伤受独立 `native_authoring_policy_errors` 审查的 native import、历史重放及
  宿主/导演写入。Author 和 Hybrid 的普通产品提示亦改为只给 protagonist 写通用知识、明确 receipt 才可写 NPC。新增真实
  PlayerDashboard/CLI/compare 路径回归：四种候选别名（told/endowment/broadcast/knows fact）都在 repair 前整 turn 拒绝；四种
  materialization 后篡改（generic knowledge_set、broadcast、knows fact、伪 receipt 形状）均在 publish 前拒绝且 revision 不变；
  合法 `/speak` receipt、protagonist told 与 action-id response-loss recovery 保持通过。原 revision-15 campaign 只保留为
  Git-ignore failure evidence，未被本次修复写入、重放或导入。随后又将 `enforce_player_knowledge_authority` 纳入显式 action-id
  request fingerprint：普通入口不会从较弱模式签发的结果 sidecar 静默恢复；模式变化在任何策略/provider 调用前 `RevisionConflict`。
  旧 legacy 信封在普通模式启用时也加入模式位；因此只在旧模式（false）继续兼容恢复，绝不把无模式历史 sidecar 提升为普通入口的可信结果。
  原始 failure candidate 已逐字固化为测试 fixture，经真实 GUI→AuthorStrategy 路径在一次 provider 调用后拒绝且 revision 不变。
  专项 `tests/app/test_normal_authoring_safe_context.py tests/test_action_integrity.py -rA` 为 **`100 passed in 0.68s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1914 passed, 1 deselected in 21.69s`**。唯一 deselect 仍是既有 slow real embedding；无真实模型、embedding 或网络，B1/profiling 保持 HOLD，未 push/PR/部署。

- 2026-10-13：RCV-18 有限复核确认并修复两项共享 API/恢复边界。其一，
  `enforce_player_knowledge_authority=True` 不再嵌套在 `enforce_player_travel` 内：候选校验和 final
  `EventBatch` 校验各自按自己的显式开关执行，故任何调用者不能以 `travel=False` 静默关闭普通玩家知识边界。
  其二，新的 sidecar 明确记录 `authority_modes.ordinary_player_knowledge_authority`。普通入口对真正的
  旧无模式 sidecar 仅开启窄的、只读兼容路径：请求必须没有当前 discovery/communication grant、metadata
  没有新模式标记、receipt 事件与 metadata 中 caller-visible events 完全一致，并且按当前普通权限重验通过；
  任何 NPC generic knowledge/broadcast/`knows:*` 或无法从旧 sidecar 重建的 player-statement receipt 都中性
  `RevisionConflict`，零 generation、零写入。带明确 `false` 模式的较新结果仍是跨模式冲突，不能借兼容桥恢复。

  新回归覆盖 `(travel=False, knowledge=True)` 的 candidate 和 materialized final 双层拒绝、预模式合法主角
  `told` 的零写恢复、预模式 laundering 的零写冲突，以及既有跨模式冲突。定向
  `tests/app/test_normal_authoring_safe_context.py tests/test_action_integrity.py -rA` 为 **`104 passed in 0.73s`**；
  正式完整离线 gate `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1917 passed, 1 deselected in 21.85s`**。唯一 deselect 仍为既有 slow real embedding；未调用真实模型、embedding 或网络，
  未触及 B1/profiling、push、PR、部署或原 failure campaign。

- 2026-10-13：RCV-18 修复后的下一份原生候选只做了一次有限的普通入口验收，现已收束，不再重复同类问路。
  原候选、零写 AuthorStrategy `[system,user]` 捕获和执行结果均保存于 Git-ignore 的 acceptance 区；候选 SHA-256 为
  `382e726a95325a68d2ebbd4c24c228d2e9ac22b6ed2c739f1a8c27b92b0356f5`，捕获消息 SHA-256 为
  `03d86ec64b54d45cf023859979940f8ed866aac2446e8ee2c066a5aae4b384b1`。在原 revision 15 campaign 的 fresh disposable
  copy 中，`PlayerDashboard.action → run_turn → AuthorStrategy` 首次请求逐字匹配该 capture；本地确定性
  `NativeAuthorProvider` 仅按顺序返回该原始候选和两份既存、逐字比对的 recap response，严格三次请求、无第四次请求。
  action `rcv18-repaired-native-author-once` 从 revision `15→16`，receipt 覆盖 seq `111..119`，结果 `recap_status=saved`；
  主角回到 `stonebridge_market` 且仍持有旧铜灯，新增的 knowledge 只属于 protagonist，对洛恩没有新的
  `knowledge_set`。关闭重开后以同 action ID 重试返回 `duplicate=true`、revision 仍为 16，guard provider 零调用。
  原 source DB/JSONL 前后哈希保持不变。该结论只说明“给定 native 候选”在修复后能经普通入口按权限提交、保存并冷恢复；
  它不是付费 API、远程模型质量、浏览器点击或长局质量验收。候选中无 `secrecy` 的 reported fact 与自由 `via` 被现有
  contract 接受，这只是观测结果，未在本轮擅自扩大为新的 P0 修复。

  下一项改为 RCV-19：让玩家在既有 GUI 中看到自己的长期目标和已经公开的明态任务账本，并在撤回/重开后保持一致。
  该切片直接改善长期游玩的方向感，复用现有 `LoreSystem` 投影和 player POV，不新增任务写入、GM 读取、暗线暴露或作者权限。

- 2026-10-13：RCV-19 已完成。玩家 GUI 新增只读“目标与任务”面板，`build_player_state()` 的 `objectives`
  DTO 只读取主角已投影的当前 `目标`/`goal`（无当前 fact 时回退角色公开 goal）、LoreSystem 已处于 `明` 状态的
  `{id, summary}` 账本行，以及**此前已进入明账**才又结案的公开 completion 摘要。它不读取 `EventStore`、暗线、任务的
  secret/stages/provenance、GM canonical view 或私有解释；
  页面也没有任务写入、任务选择、自动剧情或额外 author capability。

  新回归经 dashboard state 与真实 WSGI `/api/state` 刷新，验证长期目标覆盖、任务 open→advance→resolve、两次 rewind
  后恢复各自正确摘要与关闭重开的一致性；暗线及 lore secret sentinel 均不进入 JSON。`tests/app/test_gui.py -rA`
  为 **`28 passed in 0.46s`**；抽取的浏览器脚本经 `node --check` 通过。正式完整离线 gate 为
  **`1918 passed, 1 deselected in 21.72s`**，唯一 deselect 是既有 slow real embedding；没有真实模型/embedding/网络、B1、
  profiling、push、PR 或部署。该切片改善的是已保存目标的可见性和回放一致性，不把任务摘要当成事实权威，也不声称暗线/GM
  细节可被玩家读取。

  有限浏览器复核暴露并修复两个呈现缺口：初版把“没有任务”与“已完成任务”渲染成相同空态，且 390px 宽的既有地图最小内容
  会使页面横向溢出。现在单独显示进行中/已完成两栏；仅此前明态再结案的任务可进入已完成栏，后台暗线即使结案也不能进入。
  使用既有临时 loopback campaign、当前代码、headless Chromium 和只读 `/api/state` 拦截响应实测空/进行中/完成三态分别可辨，
  390px 下 `scrollWidth == clientWidth == 390`。未保存截图、未写入该 campaign，未将浏览器模拟称作模型或长期剧情质量验收。

- 2026-10-13：RCV-20 已完成。地图的当前可达出口现在各有本地“填入行动”按钮：它只将 `前往〈可见地点〉。`
  放入行动框；原框为空时直接填入，原框已有内容时保留原文并换行追加建议。点击不提交、不移动、不发 action-id，也没有后端
  API、权限、发现规则或 author capability 变化；真正移动仍只能在既有 `run_turn`/scene capability 校验通过后发生。
  真正 loopback Chromium 点击确认已有文字未被覆盖、状态明确提示“仍需编辑并保存行动”、请求列表没有任何非 GET 项，且 390px
  下 `scrollWidth == clientWidth == 390`。此为输入辅助而非目标或任务推进。

- 2026-10-13：RCV-21 已完成。`build_player_state()` 从同一个已按主角 POV 过滤的 `SceneCapabilities.routes`
  取得当前地点到每个 `scene.exits` 的 `cost`，写入玩家 GUI 专用的平行 `scene.exit_costs`，再在出口文本中显示“移动成本”。
  既有 `scene.exits` 保持形状不变，以免将展示成本无关地扩展到 native author 的 `available_exits` 协议；没有重新查询 canonical 图，
  也不向 map DTO、移动提交、发现或 action envelope 写入任何新值。浏览器实际 `/api/state` 只含 `stonebridge_market` 的成本 `1`，
  页面显示 `→ 石桥集市（settlement · 移动成本 1）`，390px 下仍无横向溢出且没有非 GET 请求。

  首次 GUI 专项中新增 DTO 断言误将 demo place kind 写为 `town`；实际 DTO 正确为既有 `settlement`，该失败未被跳过或删除，
  修正期望后专项通过。该切片不表示行动已移动、目标已推进或可看到隐藏路线。

- 2026-10-13：RCV-22 已完成。玩家仪表盘新增只读“当前可交谈角色”面板；它与明确交流下拉框**共同**消费既有的
  `speech_recipients`，即 `native_authoring_context` 的主角 POV、当前场景、公开可观察人物投影。没有新增 DTO、人物查询、
  visibility 规则、通信授权或写接口；面板中的人并不获得 NPC 知识、通信 receipt 或行动权限。真实 dashboard 回归覆盖初始
  无人、同场两个公开人物、同场 hidden 人物不进入 state/JSON、通过既有公开出口移动后列表刷新为空，以及关闭重开后仍为空。
  `tests/app/test_gui.py -rA` 为 **`29 passed in 0.48s`**；浏览器脚本经 `node --check`。本机 loopback Chromium 以只读
  `/api/state` 响应分别渲染空态和两个公开标签，断言下拉框与面板完全同源、没有非 GET 请求，且 390px 下
  `scrollWidth == clientWidth == 390`。浏览器拦截仅验证展示，不替代上述真实 Python 隐私/移动回归；没有 campaign 写入、截图、
  真实模型、embedding、网络、B1、profiling、push、PR 或部署。

- 2026-10-13：RCV-22 的可恢复交付已额外保存到连接账号自己的 Google Drive 根目录，未改变任何共享权限。上传前以
  `8c78ec1` 检索未发现同版本文件；上传后官方 metadata readback 均显示 `shared=false`、唯一 owner 为当前账号，且大小与
  本地一致：完整 Git bundle（Drive ID `1WQWodLaoS_C_mczz87mkhDPeo5quvAw9`，commit `8c78ec1f18a77d2f51f5b3829e0706505e530c6c`，
  3,242,130 bytes，本地 SHA-256
  `d79b3a6c8ab4fefa8558dc0ba0843ba90b669c7bfaa1977b7691853bd14e59d0`）；源码归档（Drive ID
  `11PmZn-RhzcBFOxEyA1epxz8ISoOYrSOo`，2,784,821 bytes，本地 SHA-256
  `87dfde37cf83524658f4a124cab433b5c286077b4cf0a2209fcf177a1255d059`）；恢复校验说明（Drive ID
  `1_-D7_L47FdK8PSyuWI7_zY_9Z6i4OuoA`，1,941 bytes，本地 SHA-256
  `8714102e75d3dbdf4699e70cc3a9db9fa470a8a7a4ef20b373c1ec02fe890e47`）。

  官方 Drive raw fetch 已成功创建三份认证下载引用并再次报告相同文件大小；但该执行环境随后对这三个短期下载引用作本地
  materialize 时均返回 HTTP 403，未能取得可供 `sha256sum` 的云端字节。因此本次**只完成 metadata/size 回读，未完成
  云端内容 SHA-256 比对**；没有因此重传、覆盖、分享或发送邮件。待支持的下载引用可 materialize 后，仍应再做字节哈希回读。

- 2026-10-13：RCV-23 关闭普通玩家作者的一条 canonical world 越权路径。审查首先在一次性 demo campaign 中复现：正常
  `PlayerDashboard → run_turn → AuthorStrategy` 的 provider 候选虽未移动主角，却将 `world.areas` 指向玩家 POV 外、隐藏的
  `north_bank_old_tower`；旧代码接受并发布了该 `world_change`（revision `1→2`）。这不是 prompt 文案能可靠防止的边界，
  因为 final owner/materializer 也可在候选之后替换 payload。

  现为普通 CLI、GUI 和 compare 路径显式启用 `enforce_player_world_authority`。候选校验只接受 action-start 或本回合已合法
  到达地点的一项观察，且必须 `public_observation=true`、`visibility=public`、`observation_kind` 为 `stable_layout` 或
  `single_observation`、有 `observation_range` 且 `level=1`；这阻止 remote/hidden 写入和借高 level 触发全局 cascade。publish 前
  对 materialized `world_change` 再以相同 action-start POV 终检，故 owner hook 追加的远端 tagged event 同样整批拒绝、零 revision/
  receipt 写入。该新授权模式进入 request fingerprint 和 sidecar；旧 world-mode=false action 在当前 true 下不会恢复。native
  importer 未被普通入口开关误伤，仍由其独立 native policy 审查。

  新回归覆盖远端隐藏 candidate 的一次 provider 调用后整 turn 拒绝、final payload 篡改拒绝、本地公开观察的提交/duplicate 和关闭
  重开、`travel=False + world-authority=True` 的独立强制，以及 action-id 跨 world-mode 的生成前冲突。定向
  `tests/app/test_normal_authoring_safe_context.py tests/context/test_capabilities.py tests/app/test_play.py tests/app/test_gui.py
  tests/test_action_integrity.py -rA` 为 **`177 passed in 1.43s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1924 passed, 1 deselected in 21.16s`**。唯一 deselect 仍是既有 slow real embedding；未调用真实模型、embedding 或网络。

  同时以 revision 15 历史 campaign 的新鲜 disposable copy 做一次实际普通入口 field-check：先导出 action-start 的安全 brief，
  发现主角已经在 `market_riverward_stone_lip_6e84c2f9`，故纠正旧“前往坡口”措辞为原地核验；安全 brief 只给出旧铜灯、回集市出口、
  既有近处观察，且没有 host route/communication grant。固定本地候选经普通 GUI→AuthorStrategy 提交一条仅限坡口脚边裂缝/湿泥
  的公开 level-1 observation，明确停止、未移动、未写 knowledge、未把它升级成渡口路线；receipt `rcv23-local-field-check`
  为 seq `111..116`、revision `15→16`。同 action ID 关闭重开后恢复 `duplicate=true`、provider 零调用。固定 recap 替身未能生成
  摘要，结果诚实为 `recap_status=pending`，不影响已保存的前台回合；该证据只是本地确定性候选的产品链路/边界验收，不代表外部模型
  或长局剧情质量。原 campaign DB/JSONL 哈希保持不变，未导入或改写原存档。

- 2026-10-14：RCV-24 在 RCV-23 后的独立权限审计中关闭普通作者的 canonical fact 越权。一次性 normal
  `PlayerDashboard → run_turn → AuthorStrategy` campaign 证实：即使主角未移动，候选仍能以
  `facts=[{"subject":"north_bank_old_tower",...}]` 对主角 POV 外的隐藏旧塔写入公开事实，旧实现实际 revision
  `1→2`。该一次性 campaign 已删除；这不是模型质量或剧情结论。

  新的 `enforce_player_fact_authority` 在普通 CLI、GUI 和 compare 中显式开启。候选与 materialized final
  `fact_asserted` 双层校验只允许主角的显式 `public` 自事实，或 action-start 当前公开人物的 `public reported:*`
  来源陈述；局部地形/物理所见必须使用 RCV-23 的有界 `world` observation，其他地点、物品、隐藏实体和未经归因的
  NPC 真相都拒绝。候选边界失败不会只删 facts 后保留可能宣称未见真相的 narration；最终 owner hook 追加的远端
  `fact_asserted` 同样在 publish 前整批拒绝。该独立模式进入 request fingerprint 与 sidecar 的
  `authority_modes`，从 false 到 true 的同 action-id 重试在生成前 `RevisionConflict`，不静默恢复较弱边界的结果；
  native importer 继续受其独立 native policy 管理。

  回归覆盖远端隐藏 candidate 零写拒绝、materializer 追加远端事实的最终零写拒绝、主角 public fact 的提交/duplicate/
  冷重开零生成恢复，以及 `travel=False` 时独立模式绑定。`tests/app/test_normal_authoring_safe_context.py -rA`
  为 **`28 passed in 0.46s`**；联动 `tests/app/test_gui.py tests/app/test_play.py tests/context/test_capabilities.py
  tests/test_action_integrity.py -rA` 为 **`153 passed in 1.28s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1928 passed, 1 deselected in 21.77s`**。唯一 deselect 仍是既有 slow real embedding；未调用真实模型、
  embedding 或网络，也没有触碰 B1/profiling、push、PR、部署或 Drive/email。

- 2026-10-14：RCV-25 继续收紧普通玩家作者对 canonical 角色和关系的权限。一次性 normal
  `PlayerDashboard → run_turn → AuthorStrategy` campaign 先复现：候选以
  `cast=[{"id":"tower_warden","op":"evolve","predicate":"mood",...}]` 改写主角 POV 外远端角色，旧实现
  实际从 revision `1→2` 发布。该 campaign 只是隔离复现，未作为剧情写入或模型质量结论。

  普通 CLI、GUI 和 compare 现在显式开启 `enforce_player_entity_authority`。候选和 materialized final payload
  双层拒绝通用 `entities`／`relations`；`cast.create` 只接受不存在于 action-start canonical graph、且没有
  `past`／`hidden` 的新公开人物；`cast.evolve` 仅可更新主角或当前公开人物的可观察 `mood`／`state`；
  `cast.relationship` 仅可由主角指向当前公开人物。远端／隐藏角色演化、隐藏关系和 owner hook 在候选之后添加的
  `character_evolved` 都会整批拒绝、零 revision／receipt 写入。该授权模式写入 action-id request fingerprint 和
  sidecar `authority_modes`；从旧 false 模式到当前 true 的相同 action-id 会在生成前 `RevisionConflict`，不会恢复
  更弱边界的历史结果。native importer 没有被普通入口开关影响，仍由独立 native policy 审查。

  回归覆盖远端隐藏角色 candidate 的零写拒绝、final payload 篡改的零写拒绝、通用 hidden relation 拒绝、本地公开
  `mood` 演化与新公开人物创建的提交，以及跨 entity-authority mode 的零生成恢复冲突。定向
  `tests/app/test_normal_authoring_safe_context.py tests/app/test_gui.py tests/app/test_play.py tests/context/test_capabilities.py
  tests/test_action_integrity.py -rA` 为 **`186 passed in 1.52s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1933 passed, 1 deselected in 22.09s`**。唯一 deselect 是既有 slow real embedding；未调用真实模型、embedding
  或网络，也没有外部上传、push、PR、部署、B1 或 profiling 操作。

- 2026-10-14：RCV-26 同时处理权限与正常可玩性，不把此前的安全边界变成“玩家不能有持久化物品交互”。审计先在
  一次性 normal `PlayerDashboard → run_turn → AuthorStrategy` campaign 复现：候选把主角持有的 `old_brass_lantern`
  直接 transfer 给 POV 外的 `tower_warden`，旧实现从 revision `1→2` 发布。该 campaign 是隔离复现，不是玩家剧情写入。

  普通 CLI、GUI 和 compare 现在显式启用 `enforce_player_item_authority`。候选与 materialized final payload 双层校验
  只允许 action-start、主角实际持有且在其 POV inventory 中的物品，转给当前公开人物或当前公开地点；`object_created`
  与任意其他来源／目标的 `item_transferred` 均拒绝。因此普通作者不能凭 prose 创建资产、转移远端物品或交给隐藏／远端
  角色。`items` 契约已直接写入普通 Author prompt，明确这是可选的交接机制，不再让模型猜测。

  正常路径验收走真实 `PlayerDashboard.action → run_turn → AuthorStrategy` 控制器链，但作者响应是本地
  `FakeLLMProvider` fixture：向当前场景加入公开旅人后，该离线路径将主角旧铜灯交给该旅人；持有关系保存，
  玩家 `inventory` 变为空，同 action-id duplicate 零再生成，关闭重开后仍恢复同一持有者。反例覆盖远端隐藏 recipient
  的候选零写拒绝、materializer 在候选后追加远端 `item_transferred` 的 final 零写拒绝，以及跨 item-authority mode
  的生成前冲突。它不是浏览器点击、native author 或真实／外部模型玩法测试。第一轮专项曾有 1 项失败，因为既有 metadata
  精确映射没有预期新 `ordinary_player_item_authority=true`；该断言已按新持久化契约修正，未删除失败场景。

  定向 `tests/app/test_normal_authoring_safe_context.py tests/systems/test_object.py tests/app/test_gui.py tests/app/test_play.py
  tests/context/test_capabilities.py tests/test_action_integrity.py -rA` 为 **`229 passed in 1.68s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1937 passed, 1 deselected in 22.33s`**。唯一 deselect 是既有 slow real embedding；未调用真实模型、embedding 或网络，
  未触碰 B1/profiling、外部上传、push、PR 或部署。

- 2026-10-14：RCV-27 审计普通任务账本的既有系统性缺口。一次性 normal
  `PlayerDashboard → run_turn → AuthorStrategy` campaign 中，离线 fixture 候选在主角留在客栈时把锚定
  `north_bank_old_tower` 的远端暗线 `surface`；旧实现实际从 revision `2→3`，将未在当前 station-push 披露的线写进明账。
  该 disposable campaign 没有保留，也不是浏览器、native author 或真实模型玩法结论。

  新 `enforce_player_quest_authority` 在普通 CLI、GUI 和 compare 中同时进行候选与 final `EventBatch` 校验。普通作者可
  `open` 一个 fresh public task；只能 `surface` 当前 station-push 会提供 id 的本地暗线；只能 `advance`/`resolve` 当前城镇
  的明态任务或没有 host anchor 的普通公开任务。自动 density/lore 生命周期事件虽可出现在同一 publish batch，但不属于普通
  `quests` section 的 materialization，因此明确不被误拒；final validator 只审查 `quest_opened/surfaced/advanced/resolved`。
  模式写入 action-id fingerprint 与 sidecar，跨 false→true 恢复在生成前 fail-closed；native importer 不受此普通入口开关影响。

  正常路径用真实 Python GUI 控制器链 `PlayerDashboard.action → run_turn → AuthorStrategy`，但 provider 是本地、按玩家输入
  返回固定 JSON 的 `_InputKeyedAuthorProvider` fixture：先接取 fresh public task，再推进宿主锚定在当前客栈的明态任务；
  dashboard 账本更新，duplicate 零生成，冷重开恢复相同摘要。它不是浏览器点击、真实模型或 native author 验收。反例覆盖远端
  暗线 surface 的候选零写拒绝、final materializer 追加远端 `quest_advanced` 的零写拒绝及 mode conflict。首轮专项还暴露
  新 metadata key 与 host lore lifecycle 同批事件的集成问题；前者改为断言新标记，后者缩小为只校验普通作者可 materialize 的
  事件类型，未删除失败场景。

  定向 `tests/app/test_normal_authoring_safe_context.py tests/systems/test_quest_active.py tests/app/test_gui.py tests/app/test_play.py
  tests/context/test_capabilities.py tests/test_action_integrity.py tests/loop/test_compare.py -rA` 为
  **`218 passed in 1.70s`**；正式完整离线 gate `/tmp/llm-rpg-engine-testenv/bin/python -m pytest
  -o "addopts=-m 'not slow'" -rA` 为 **`1941 passed, 1 deselected in 22.82s`**。唯一 deselect 是既有 slow real embedding；
  未调用真实模型、embedding 或网络，未触碰 B1/profiling、外部上传、push、PR 或部署。

- 2026-10-14：梳理下一项时确认终端的控制序列过滤、无剧透工作提示、玩家／DM 输出分隔和 verbosity/style
  已在当前代码实现，不能重复开发。仍有明确产品占位：`//steer` 只说“后续实装”。RCV-28 将其落为
  session-only 的软方向：`//steer <方向>`（也接受 `/steer`）只在当前 `play_loop` 保存，下一正常回合把一段
  明确标为“非世界事实或权限”的 JSON 引用偏好送给 Author/Hybrid；它不替代明确行动，不能越过 POV、路线或发布边界，
  不写 EventStore、turn、transcript、action receipt 或安全投影。`//steer clear` 清除偏好，并重置 Author 对话缓存，
  因此已清除的方向不会从旧 conversation thread 残留到下一回合。

  离线 CLI fixture 实测：设置方向后首个 `PlayerDashboard` 之外的正常 `play_loop → AuthorStrategy` 回合收到该
  标记；清除后第二回合的全新 Author prompt 不再含该方向；设置／清除本身 revision 不变。另以“前往北岸旧塔”的
  soft direction 配合恶意 hidden-move candidate 验证，普通 travel gate 仍拒绝，revision 不变。该回归是本地
  Fake provider 的确定性 terminal-path/prompt-contract 验收，不是浏览器点击、真实模型、native author 或剧情质量结论。
  首轮专项有一项 fixture 失败：捕获器误把 `new_game` 的 genesis provider 调用当作普通 Author 调用；收窄为 system
  message 含“你是主持人（DM）”后通过，未删除该场景。未调用真实模型、embedding 或网络，也未触碰 B1/profiling、
  外部上传、push、PR 或部署。

- 2026-10-14：RCV-29 关闭玩家网页长局方向感的一处具体缺口：内核已有公开 `meta.day/band`，且它已经进入
  CLI/作者上下文，但 player dashboard 之前只显示回合和存储 revision。现在 `build_player_state` 只从既有公开
  meta 派生 `{day, band}`，浏览器状态行显示“第 N 天 · 时段 · 第 M 回合”。它不读取 EventStore/raw history、
  private memory、GM state 或新世界字段，也不增加时间写入、自动推进或作者权限。

  真实 dashboard controller fixture 以普通 `action → run_turn` 提交一个合法 `clock` 推进，随后验证 WSGI
  `/api/state`、revision-bound undo 和关闭重开都准确从同一事件重放 Day/Band（第 1 天晨 → 第 2 天中午 →
  第 1 天晨）。GUI 专项为 30 passed；同一 `_JS` 已由 Node 标准输入编译检查。首次 Node 检查曾错误使用
  进程替换，Node 无法读取匿名管道路径；改用 stdin 后成功，该工具调用失败不是 JS 语法失败。此证据是本地
  dashboard/controller + WSGI DTO 回归，不是浏览器点击或真实模型质量结论。未调用真实模型、embedding 或网络，
  未触碰 B1/profiling、外部上传、push、PR 或部署。

- 2026-10-14：在用户要求继续修复正常流程而非再叠加只读面板后，短审计发现一个会真实制造无效候选的提示契约错位：
  普通 `AuthorStrategy` 的完整范例仍要求 `secrecy:"secret"` 的 NPC 真相；`HybridStrategy` 的结构提示仍把普通
  玩家回合说成可见“完整世界视野”，允许任意 `facts` secrecy、`entities`／`relations` 和 level 1–3 的全局 `world`。
  这些都已经与 RCV-23～27 的 candidate/final EventBatch 发布校验冲突，会令正常模型先生成必修复或必拒的结构。

  RCV-30 将两份提示对齐为同一普通玩家可发布契约：`world` 仅允许 action-start 或合法同回合目的地的一项
  public、level 1、有范围的局部观察；facts 仅允许主角 public 自事实或当前公开人物的 `reported:*` 来源陈述；
  角色、物品、任务和 knowledge 都明确沿用既有本地／公开边界。Author 完整范例也改为 public `reported:*` 加
  protagonist `told`，不再以 secret canonical 真相作示范。没有放宽任何 validator、native import、权限模式或
  action-id 行为。

  验收先暴露一条旧 CLI 提示文本断言失配（原句“完整世界视野”已删除）；更新为检查新 local-public 形状后，定向
  `tests/loop/test_strategy.py tests/app/test_normal_authoring_safe_context.py tests/app/test_play.py tests/loop/test_compare.py -rA`
  为 **`85 passed in 1.26s`**。新增真实 Python GUI 控制器链 `PlayerDashboard → HybridStrategy → run_turn` 回归：固定本地 Fake provider
  先产生散文、再产生结构化 commit，普通公开移动成功发布；两条 provider prompt 都保持 redacted 安全上下文，私有
  sentinel 未出现。正式完整离线 gate `/tmp/llm-rpg-engine-testenv/bin/python -m pytest
  -o "addopts=-m 'not slow'" -rA` 为 **`1947 passed, 1 deselected in 22.36s`**；唯一 deselect 是既有 slow real embedding。
  该证据是离线 fixture 的完整策略/发布链，不是浏览器点击、真实模型或剧情质量结论。

- 2026-10-14：RCV-31 的只读入口对照发现一个普通 compare 流程的后台 provider 传递差异。`/compare on` 先用
  `run_compare` 生成甲／乙候选，再以 `PreparedCandidate → run_turn` 持久化甲；该最终调用此前传入了
  `cascade_provider`，却漏传同一引擎配置的 `catchup_provider`。普通 CLI 分支和 GUI 已同时传两者，因此 compare
  的 stale-entity catch-up 可能意外回退到前台 author provider，造成行为与模型预算来源不一致。

  现在 compare 的最终 `run_turn` 与普通 CLI/GUI 一样显式传入 `engine.cascade_provider` 作为 cascade 与
  catch-up provider。新增真实 CLI `/compare on → PlayerDashboard` 之外的 `play_loop → run_compare → PreparedCandidate → run_turn`
  回归，monkeypatch 只观察最终持久化调用，断言二者是同一配置对象；候选仍由本地 `FakeLLMProvider` 确定性生成。
  定向 `tests/app/test_play.py tests/loop/test_compare.py tests/app/test_gui.py -rA` 为 **`61 passed in 0.96s`**；正式完整离线
  gate `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1948 passed, 1 deselected in 26.22s`**；唯一 deselect 是既有 slow real embedding。该回归未使 catch-up
  实际执行，不是浏览器点击、真实模型、外部预算或长期剧情质量验收。

  后续补齐该证据缺口时，复用相同本地 compare 路径构造了一个 day 5 的 stale `innkeeper`：第一回合不在场，第二回合才
  进入 `present`。真实 `/compare on → run_compare → PreparedCandidate → run_turn` 的第二次最终 apply 因而实际触发
  `run_catchup`；单独配置的 `KeyedCatchup` 收到含 `实体 id：innkeeper` 的后台请求，而不是前台 Author/Hybrid 调用，写入
  一条 `character_evolved(mood=孤寂)`。关闭并从 SQLite/JSONL 重开后，事件仍存在、ontology 投影的 mood 与
  `last_update=5` 保持一致。该回归覆盖了修复所影响的实际后台分支与持久化/replay，而不是只观察参数；仍完全是本地
  Fake provider，不是浏览器或真实模型验收。

- 2026-10-14：RCV-32 的交叉 section 审计发现，普通 Author/Hybrid 的现有发布边界已分别覆盖 world、facts、
  cast／relations、items、quests 与 knowledge，却没有覆盖 `FactionSystem` 的 `factions` 段。该段可以创建长期势力或
  变更成员、位阶／群组；后者还可能作为后续势力知识广播的基础。当前玩家 GUI、CLI 安全 handoff 与 POV DTO 都没有
  公开势力遭遇、加入 receipt 或其他足以授权这种写入的窄 capability。因此普通玩家作者不能以自由叙事直接写 faction
  canonical 关系；宿主、导演及 native import 仍走各自独立 policy。

  本轮实现候选与最终 EventBatch 双层校验，并把 faction authority mode 纳入 action-id fingerprint/sidecar：普通
  CLI、GUI 和 compare 均启用该模式，任何 `factions` proposal 或 materialization 注入的 `faction_created`／`member_changed`
  都整体拒绝，不做只删 section 的修复。跨 false→true mode 的同 action-id 在生成前 fail-closed；不会把旧弱模式的
  已提交动作静默恢复成当前边界。回归覆盖 GUI 候选拒绝、final payload 篡改原子拒绝、compare preview 在 Hybrid 前拒绝、
  以及 recovery mode 绑定。专项 `tests/app/test_normal_authoring_safe_context.py tests/loop/test_strategy.py tests/app/test_play.py
  tests/loop/test_compare.py -rA` 为 **`90 passed in 1.17s`**；正式完整离线 gate `/tmp/llm-rpg-engine-testenv/bin/python
  -m pytest -o "addopts=-m 'not slow'" -rA` 为 **`1953 passed, 1 deselected in 22.53s`**，唯一 deselect 是既有
  slow real embedding。RCV-32 的完整恢复包与 fresh-clone 验收在本地 checkpoint 记录中另列；没有调用真实模型、网络或外部服务。

- 2026-10-14：RCV-33 审计补齐 `PlaceSystem` 的普通作者写面。既有 travel 校验只约束 protagonist 的目的地，
  因而普通 Author/Hybrid 虽看不到 canonical 地图，仍可在 `links` 段改写两处既有地点间的拓扑，或在 `moves`
  段调度既有人物；`materialize` 也没有玩家 capability。现在普通 CLI、GUI 与 compare 的候选及最终 EventBatch
  都启用独立 place authority：只允许全新、显式 public 的前沿地点，且每个新地点在同回合恰有一条从 action-start
  当前位置出发的 public 直连边；普通回合不得改写既有拓扑或实体化地点。非主角移动只允许同回合新建公开人物，
  且位置限于当前位置或主角的同回合目的地。该模式进入 action-id fingerprint/sidecar，跨 false→true 恢复 fail-closed；
  native import 保留独立 native policy。

  回归覆盖 ordinary GUI 对既有远方链接的候选零写拒绝、materializer 注入既有人物移动的最终 payload 原子拒绝，
  并实际经 GUI 创建一个全新 public 前沿、直连当前位置且移动主角成功。专项 `tests/app/test_normal_authoring_safe_context.py`
  为 **`49 passed`**（其后相关 capabilities/strategy/play/compare 子集为 **`61 passed`**）；正式
  完整离线 gate `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1956 passed, 1 deselected in 22.93s`**，唯一 deselect 是既有 slow real embedding。无真实模型、网络或外部服务。

- 2026-10-14：RCV-34 沿“后台写入是否共享可恢复边界”的开放风险做完剩余审计。当前产品语义不是把所有后台
  工作与前台动作绑成一次失败即整回合撤销：foreground publish 后，摘要、director、cascade、catch-up、lore、
  demote-on-leave 与 density 都是非致命服务；其失败不得丢失已保存的玩家回合，但也不得留下自己半写入的事件。
  既有测试只明确覆盖 digest/memory 的部分场景，因而新增一组统一的 write-then-raise 故障注入回归。

  对七个钩子逐一先写可识别 sentinel 再抛异常，断言前台回合仍返回、sentinel 不在活跃或历史事件中、JSONL 与 SQLite
  完全一致，并关闭重开再次验证无 sentinel。该项不改生产语义，只把已使用的每钩子 savepoint 事务边界、局部回滚和
  replay 事实固定为可回归证据。专项 `tests/loop/test_turn_density_hook.py -q` 为 **`14 passed`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1963 passed, 1 deselected in 22.11s`**，唯一 deselect 是既有 slow real embedding。无真实模型、网络或外部服务。

- 2026-10-14：RCV-35 完成公开出口审计中的 debug trace 分支。`trace.jsonl` 与 `python -m app.trace`
  是显式、本地、operator-only 的诊断工具，故保留原始玩家输入和完整 provider prompt/output，不应被误作玩家、GM
  或公开历史 API。此前私有解释已在 CLI、GUI、GM 和 provider context 中隔离，但尚未实际证明启用 `--debug` 时它不进入
  本地原始 trace。

  新回归先以 `--offline-private-memory` 跑一回合，关闭重开确认主角私有解释记录确实已持久化；再验证同一回合生成的
  `trace.jsonl` 原文和对每一条记录调用 `app.trace.show_record()` 的完整 viewer 输出均不含私有解释文本，而公开 player
  input 仍在 trace 中。没有删改 trace 的诊断内容，也没有把 trace 接到网页、transcript、外传或新访问控制路径。文档同步
  明确 trace 的本地敏感产物定位和分享前审阅要求。定向
  `tests/app/test_offline_private_memory_cli.py tests/app/test_trace_view.py -rA` 为 **`40 passed in 0.61s`**；正式完整离线
  gate `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1964 passed, 1 deselected in 22.69s`**。唯一 deselect 是既有 slow real embedding；无真实模型、embedding、网络或外部服务。

- 2026-10-14：在收到“继续严格 POV 地图关系图展示与浏览器验收”的队列后，先核对当前实现，确认 RCV-11 已经以
  `SceneCapabilities.map_dto()` 统一 player POV 图、RCV-20/21 已提供出口建议和成本；因此没有重复开发或重写 DTO。
  RCV-36 只在当前组合后的代码做一次 loopback Chromium 复验：临时 demo campaign 的 `/api/state` 与 player DOM 都不含
  `north_bank_old_tower` 或 private sentinel；地图显示 2 个可见节点和 1 条可见边，节点选择仅显示本地详情。390px
  viewport 下输入“我先观察周围。”再点“填入行动”，输入准确变为“我先观察周围。\n前往石桥集市。”，状态仍提示编辑并保存，
  浏览器捕获到的写请求为空，`scrollWidth == clientWidth == 390`。临时 campaign 与服务均在验收后删除，未保存截图。

  随后 `tests/app/test_gui.py -rA` 为 **`30 passed in 0.50s`**，组装后的 `_JS` 经 `node --check -` 通过。RCV-35 的
  正式完整离线 gate 仍是当前功能代码的最近全量证明：**`1964 passed, 1 deselected in 22.69s`**；RCV-36 仅更新验收文档，
  未运行真实模型、网络、外传或新写入路径。外部备份上传仍需单独批准；本地恢复包继续保留。

- 2026-10-14：RCV-37 补齐 CLI 已有、GUI 缺失的会话内软方向入口。这个方向不是新的剧情或权限输入：
  `PlayerDashboard` 仅在内存保存最多 500 字的文本，正常行动开始前才把它作为既有、明示“非世界事实或权限”的
  `_player_steer` prompt fragment 交给 Author/Hybrid。保存或清除不写 EventStore、JSONL、transcript、路线、
  facts、receipt 或 host recovery envelope；方向改变会 `reset()` 有会话缓存的策略，避免清除后旧 prompt 留在对话中。

  action-id 的稳定 request fingerprint 也显式剔除该会话偏好：它不是可恢复的 canonical 参数，响应丢失后的冷重开
  应恢复已发布结果而非要求旧本地偏好仍存在。回归经真实 GUI controller 的 `/api/steer → action → run_turn` 链确认：
  设置方向零 SQLite/JSONL 字节变化、作者 scene 能收到该 fragment、sidecar/host envelope 不含方向；清除后策略缓存
  已重置，同 action-id 的冷重开 duplicate 仍零生成恢复。另覆盖长度和跨站 JSON guard。浏览器 loopback 390px 实测
  保存/清除方向均可用；保存方向仅 POST `/api/steer`，行动保存才 POST `/api/action` 并从版本 1 到 2，无横向溢出。
  定向 `tests/app/test_gui.py tests/app/test_play.py tests/loop/test_strategy.py -rA` 为 **`71 passed in 1.01s`**；
  组装后的 JS 经 `node --check -` 通过。正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1966 passed, 1 deselected in 22.69s`**；唯一 deselect 是既有 slow real embedding。该项未调用真实模型、
  embedding、网络或外部服务；这是本地 Fake/demo 的产品交互与恢复边界验收，不是网络/真实模型质量证明。

- 2026-10-14：RCV-38 在继续前先核对既有 CLI／GUI 的读边界，而不是为 dashboard 另加一套回忆功能。发现
  CLI 的玩家 OOC `/recall` 仍直接将 canonical `engine.world` 传给各系统的 recall fan-out；在隔离 demo campaign
  上，输入猜测的 `north_bank_old_tower` 实际返回了隐藏地点 id 和 `ruin` 类型。这是现有、可复现的玩家信息泄漏，
  不依赖模型或新剧情。原失败输出保留在本轮验证记录中，未删除或改写该反例。

  现将 recall 输入统一收敛到由当前 `_build_scene(engine)` 派生的 `pov_world(..., redact_facts=True)`。这复用普通
  玩家上下文和 dashboard 的同一可见性／信念过滤，而不是在 recall hook 中按某个 id 打补丁。公开的“石桥”资料仍可
  检索；已经由 `knowledge_set` 明确发现、但 canonical 属性仍为 `undiscovered` 的“封门”也仍可检索；猜测隐藏地点
  得到 `无`，revision 不变。该修复没有给 GUI 新增 recall 出口，也没有改变 GM/operator、native author 或 canonical
  recall 的内部用途。

  定向 `tests/app/test_play.py tests/context/test_viewpoint.py tests/llm/test_tools.py tests/app/test_offline_private_memory_cli.py -rA`
  为 **`71 passed in 0.82s`**；随后补充的已发现地点回归与 POV/tool 子集为 **`43 passed in 0.26s`**。完整离线 gate、
  完整 bundle/fresh-clone 与哈希另记于该提交的恢复验证；无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-39 接续台账中“旧无 owner event 的兼容读取”开放风险，先复核而非假定现有 debug-ignore
  已是完整忽略。红例证明 `project()` 过去会先把无 owner 的 `day=9, scene=orphan_scene` 写进
  `meta.day/scene/timeline`，才发现没有 owner 并跳过系统 apply；因此 world slice 仍是 day 1，恢复界面却可能
  自称在 day 9 orphan scene。该类记录只能来自旧插件/旧版本数据库，当前 `append_many` 的 allow-list 不会接受它，
  但重开时仍必须保守兼容。

  现在 projection 在触碰 meta 前先查 event owner：无 owner 的历史记录仅留下原有 debug log，完全不参与当前
  投影。新增单元回归覆盖跨日无主事件不改 notes/day/scene/timeline；另用旧 allow-list 写入 SQLite+JSONL，再由
  当前 `build_engine` 实际重开，确认 meta 仍取最后拥有者事件且两个持久化文件字节完全不变。没有删除或迁移旧记录、
  改写 JSONL、放宽新写入校验，也没有改变已注册 owner 的 replay 行为。

  定向 `tests/kernel/test_projection.py tests/app/test_engine.py -rA` 为 **`34 passed in 0.24s`**。完整离线 gate、
  bundle/fresh-clone 与哈希另记于该提交的恢复验证；无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-40 补齐 dashboard 与 CLI 已有 `/recall` 的产品不对称，但不把 canonical world 新接到浏览器。
  抽取共享 `app.player_recall.player_recall()`：它只能查询由当前主角 scene 派生的
  `pov_world(..., redact_facts=True)`，并且仅返回匹配文本。CLI 与 dashboard 都通过该函数查询，避免以后两条
  玩家出口出现不同的可见性边界。dashboard 新增 player-only、GET-only 的 `/api/recall?q=...` 及“回忆已知线索”
  面板；查询不会创建事件、receipt、transcript、revision 或 action-id。隐藏 `north_bank_old_tower` 查询无结果，
  公开“石桥”仍有结果，SQLite/JSONL 字节不变；无 query 仅返回既有 400 确认拒绝。

  真实 loopback Chromium 在 390px 下先查询隐藏 id（DOM 显示“没有匹配的已知线索”），再查询“石桥”（显示已知
  客栈、集市等结果）；捕获的非 GET 请求为空、页面无横向溢出。该服务与临时 campaign 已删除，未保存截图。
  这是本地 Fake/demo 的 HTTP/浏览器边界验证，不是网络或真实模型质量结论。定向 GUI/play/POV/tool 子集、
  组装 JS 的 `node --check -` 与完整离线 gate 的实际结果另记于本提交验证；无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-41 的正常流程审计发现 CLI 与 GUI 对撤销后的多回合作者缓存不一致。CLI 在 OOC
  `/undo`／`/rewind` 改变 revision 后会 reset 策略；GUI 的 `PlayerDashboard.undo()` 只重新投影 world 和清空
  `previous_scene`。这不会改写持久化历史，但会让随后普通 GUI action 的 `AuthorStrategy` 线程保留已撤回的用户／
  助手交换，造成 prompt 与当前 replay world 脱节。

  成功 rewind 后 GUI 现同样调用策略 `reset()`，只清除瞬态缓存、不改变 soft direction、EventStore、JSONL 或
  action-id 规则。真实 `PlayerDashboard.action → AuthorStrategy → undo → action` 离线回归在多回合模式下保存第一
  个带 sentinel 的行动、撤销后验证 `_thread/_messages` 均为空；第二次真实 Author 请求含新行动且不含已撤回文本。
  背景 density 会与前台共享 fake provider，测试按第二行动文本选取前台消息而非误把最后一条后台请求当作者消息。
  定向与完整离线 gate、bundle/fresh-clone 的实际结果另记于本提交验证；无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-42 在撤销缓存审计后核对 normal GUI shell，发现任何 player dashboard 都固定显示
  `LOCAL · PLAYER VIEW · OFFLINE DEMO` 和“离线玩家试用”，即使启动的是已有 campaign + 正常 `AuthorStrategy`。
  这不改变引擎状态，却会错误暗示实际 normal author 流程只是固定替身，影响玩家对保存/恢复语义的判断。

  页面 shell 现只从已存在的 controller `demo` 布尔值选择 `OFFLINE DEMO` 或 `SAVED CAMPAIGN` 及对应本地说明；
  不把 provider 名称、模型、canonical world、事件或任何新数据插入 HTML。WSGI 以同一已种子 campaign 分别启动 demo
  和 normal dashboard，验证标签互斥且 normal HTML 仍不含隐藏地点 id。定向与完整离线 gate、bundle/fresh-clone 的
  实际结果另记于本提交验证；无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-43 回到正常游玩／重开流程而非增加展示件。catch-up 的语义是只对“自上一玩家回合起
  新进入当前 scope”的 stale tracked 实体作后台演化；CLI 的 `prev_scene` 与 GUI 的 `previous_scene` 却都只存在
  进程内存。实际 GUI 红例先让掌柜在客栈与主角同场，再保存一个不移动、推进至 day 5 的等待回合并关闭重开；旧代码
  将下一次原地观察的前场景置为 `None`，错误调用 catch-up provider 并写入 `character_evolved`。这不是模型质量
  问题，而是相同持久历史在持续会话与冷重开后的行为不同。

  修复新增 `EventStore.latest_active_action_prefix()`：它在一个短 SQLite read snapshot 内寻找最新且整段未撤回的
  action receipt，并读取其 `first_seq` **之前**的 active history。`app.play` 由该前缀只读投影 action-start
  scene；CLI `play_loop` 初始值和 `PlayerDashboard` 构造时的 `previous_scene` 都使用该恢复结果。没有读取 client scene、
  不写入 EventStore／JSONL、不会生成或调用前台模型；没有 active receipt 的新 genesis 继续保持原先 `None` 语义。
  仅选“当前 scene”会掩盖真实刚抵达的 catch-up，故没有采用该捷径。

  两个确定性 dashboard controller → `run_turn` 本地 fixture 覆盖相反分支：(1) 持续同场的 stale innkeeper 经冷重开后
  原地观察，catch-up provider 调用数为零且没有 `character_evolved`；(2) 主角实际从客栈进入含 stale traveler 的集市，
  关闭重开后下一回合仍按恢复的客栈起点触发一次 `character_evolved`。另断言撤回 action 的 receipt 不会成为恢复锚点。
  这是离线 Fake provider 的持久化／流程一致性证据，不是浏览器点击或真实模型质量验收；本项无浏览器专属交互面。
  相关 GUI／CLI／catch-up 专项为 `83 passed in 1.06s`；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1973 passed, 1 deselected in 22.40s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-44 继续 RCV-43 的同一完整回放准则，检查正常 OOC 撤销而不是再跑已收束的冷重开场景。
  发现 GUI `PlayerDashboard.undo()` 和 CLI `play_loop` 在 `/undo`／`/rewind` 改变 revision 后都直接置
  `previous_scene`／`prev_scene=None`。这会丢掉仍存活历史的 action-start scope；下一普通行动若与 stale NPC
  持续同场，就可能被错误当作“刚进入”并触发后台 catch-up。作者策略 cache 的 `reset()` 仍是必要的，不能以
  保留旧 prompt 来避免该问题。

  现在两条入口在成功 rewind 后都复用 RCV-43 的 `latest_active_action_prefix()`，只读重放**最新存活** action
  之前的 scene 作为下一动作的 catch-up anchor，同时保留既有策略 reset、revision 和撤回行为。没有写入补偿事件、
  放宽权限、改变 undo 的撤回范围或新增模型调用。离线 GUI 与 CLI 都实际走 `行动 → host time advance → 行动 →
  /undo → 行动`：host time 事件会留下同场 NPC 的 stale 条件，而被撤回的第二玩家行动不会带走第一 action receipt；
  修复后两个入口均不发出 `实体 id：innkeeper` catch-up 请求，也不写 `character_evolved`。CLI 的便宜后台 provider
  还会被既有 density hook 使用，断言因此精确识别 catch-up prompt，而不是错误要求该 provider 零调用。
  相关 GUI／CLI／time／action-integrity 专项为 **`170 passed in 1.49s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1975 passed, 1 deselected in 22.51s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-45 继续检查 RCV-43/44 的同一 action-start scope 语义，但覆盖另一条真实恢复入口：
  乐观发布在作者准备期间输给另一条已保存行动。旧 CLI 对 `RevisionConflict` 会重放 world、reset 作者缓存并提示刷新，
  却没有更新 `prev_scene`；下一次行动仍可能把失败请求之前的 scope 当作最新 durable action 的前场景。旧 GUI 则把
  此类已确定没有提交的冲突归到 generic 500，既不刷新内存 world，也没有给浏览器可安全丢弃的拒绝语义。

  现 CLI 与 GUI 都在**当前 action id 尚无 durable receipt**时，以同一
  `latest_active_action_prefix()` 只读重放当前 store，恢复胜出行动的 action-start scene，并 reset 策略缓存。GUI
  同时移除本次失败的内存 request context，返回带 `confirmed_rejection=true` 的 400，明确“世界状态已更新；此行动未提交”。
  若同一 id 已有 receipt，仍返回保守 409 recovery，不把已保存动作误作可丢弃请求；没有新增重试、事件、权限、provider
  或客户端 scene 信任。

  两个端到端离线回归均让独立 durable demo action 在第一作者调用期间实际赢得 SQLite publish race：第一个 CLI/GUI
  请求收到冲突，随后普通第二行动实际成功，传入的 `prev_scene` 均为胜出行动开始时的客栈而非旧 `None`。GUI 回归另经
  WSGI 断言精确 400 JSON、无失败 action receipt、内存 state 已投影到集市、同 id request context 已清除；已有 receipt
  的 409 分支保持原有测试。此为本地 deterministic controller／WSGI 流程验证，不是浏览器点击、真实模型或网络验收。
  相关 CLI／GUI／fleet 专项为 **`84 passed in 1.17s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1977 passed, 1 deselected in 21.98s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-46 沿 RCV-45 的 dashboard 恢复审计继续检查另一个 revision-bound 写入口。两份 dashboard
  同时打开同一 campaign 时，peer 已用正常 `PlayerDashboard.action()` 保存移动；旧 dashboard 随后以渲染时的旧 revision
  请求 undo，会正确拒绝，但旧实现只抛错误而不重投影。于是其下一次 `/api/state` 仍返回旧 revision/地点，虽然 SQLite
  已有 peer world，用户被告知“刷新”却得不到可刷新的当前 controller state。

  `PlayerDashboard` 现将 RCV-45 的已知 stale-revision 处理抽成共享只读恢复：project 当前 store、设定 revision、以
  最新存活 action receipt 重建 catch-up action-start scope，并 reset author strategy。action conflict 继续仅清除当前失败
  request context；stale undo 不撤回、写入或补偿任何事件，只返回“世界已变化；已刷新本地状态”的 confirmed 400。已有
  `expected_revision`/undo 绑定和已提交 action 的 409 recovery 语义不放宽。

  新 WSGI 双 dashboard 回归实际让 peer 走正常 demo action 保存到集市，再由旧页面提交 stale undo：400 后立即 GET
  `/api/state` 已是 peer 的 revision/集市场景，重建的前场景为客栈且策略 reset 恰一次。该回归先以旧代码实际失败
  `revision 1 != 2`，不是模拟 response。相关 GUI／CLI／action-integrity 专项为 **`154 passed in 1.57s`**；正式完整
  离线 gate `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1978 passed, 1 deselected in 22.89s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络、浏览器或外部服务。

- 2026-10-14：RCV-47 继续 RCV-46 的跨实例一致性审计，避免把“只有被拒绝的写入才刷新”误当完整恢复。
  实际双 dashboard 红例中，peer 用正常 demo action 从客栈保存到集市；旧 dashboard 不提交 action/undo，只 GET
  `/api/state`，旧代码仍回 revision 1 而 SQLite/peer 已为 revision 2。若只读 recall 先执行，同样会在旧 projection
  上查询，因而不能把 passive refresh 留给用户主动触发一个失败写操作。

  `PlayerDashboard` 现统一在 state、recall 和 action 入口比较缓存 `_revision` 与 store revision；仅不一致时才复用
  RCV-46 的只读重投影、最新存活 receipt 的 action-start scope 恢复和一次 strategy reset。revision 相同的连续 GET 不
  重复 reset。该检查不创建事件、receipt、transcript、重试或权限，也不读取客户端 scene。

  新 WSGI 回归让 peer 先发布，再让旧 controller **先**做 `recall`、随后 GET state：recall 已返回 peer revision，state
  显示集市，scope 为客栈，第二次 state 不再 reset；旧代码在第一 state assertion 实际失败 `1 != 2`。相关三条控制为
  **`3 passed in 0.30s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1979 passed, 1 deselected in 23.05s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络、浏览器或外部服务。

- 2026-10-14：RCV-48 复查与玩家页面隔离启动的只读 GM dashboard。它没有 action、undo 或 revision-bound
  拒绝分支，因此 RCV-47 不能自动覆盖它：先启动 GM，再由另一玩家 dashboard 正常保存客栈→集市移动后，旧 GM
  的 `/api/gm/state` 红例实际仍回 revision 1，而 durable store／玩家已为 revision 2。这个问题不会授权 GM 写入；
  它只是读页持有了过期 projection。

  `GmDashboard.state()` 现先比较缓存 `_revision` 与 SQLite revision；不一致时仅以既有 registry 和完整事件流重投影，
  再照原来的 canonical GM DTO 读取。刷新不创建事件、receipt、transcript 或 action，也不改变私有解释的过滤：后者仍
  由 `build_gm_state()`／`public_history()` 边界负责。新 WSGI 双实例回归让 GM 仅 GET `/api/gm/state`，断言 revision
  和主角地点已跟随 peer publish，同时 refresh 前后 event 列表严格相同，证明 GM GET 没有写入。此为本地 deterministic
  controller／WSGI 验证，不是浏览器点击、真实模型或网络验收。
  GUI／CLI／projection 联动专项为 **`76 passed in 1.17s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1980 passed, 1 deselected in 22.68s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-49 将同一跨会话一致性准则扩展到长运行的终端 REPL，而不重复 dashboard 路径。红例先启动
  普通 CLI，执行一条 `/recall`；随后另一 local dashboard 正常从客栈保存到集市；CLI 的第二条 `/recall` 在旧实现中
  仍使用 revision 1，即使 durable store 已是 revision 2。这不仅使 recall 读旧 POV，也让紧随其后的 `/speak` 或普通
  action 可能按过期 scene 签发材料。

  `play_loop()` 现在在每条有效输入、且在 `/recall`／`/speak`／正常 scene 构建之前，比较缓存 revision 与 store；只有
  不同时才只读投影完整历史、以最新存活 action receipt 重建 action-start scope，并 reset 作者策略缓存。它不修改 soft
  direction，也不为观察行为创建事件、receipt、transcript 或自动重试。新本地双入口回归在两条 `/recall` 之间让 peer
  dashboard 实际 publish，断言 CLI 已是新 revision／集市场景且策略仅 reset 一次；这是 deterministic CLI/controller
  验证，不是浏览器点击、真实模型或网络验收。GUI／CLI 子集为 **`72 passed in 1.31s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1981 passed, 1 deselected in 23.23s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-50 转查正常续玩可追溯性，而非继续增加同一同步回归。`--transcript` 是 CLI 明确提示的逐回合
  JSONL 记录，但旧实现的 `turn_no` 从每个 REPL 进程的 0 开始；实际 `行动→关闭→重开→行动` 红例把两条 durable
  玩家回合都写成 `turn: 1`，即 `[1, 1]`，尽管第二次 publish 已是 canonical 第 2 回合。这样 transcript 不能与
  action receipt／事件历史可靠对照。

  现在每条普通行动在生成前用既有 `store.next_turn()` 取得 canonical next turn；这也自然覆盖本会话 rewind、peer
  publish 及失败后重试，而不为 transcript 新建持久化来源或改动 EventStore。冷重开回归实际追加同一 transcript，断言
  记录为 `[1, 2]`、输入次序不变。该编号继续是 player transcript／trace 的标签，不授予任何恢复、权限或事实权威。
  CLI 状态与游玩子集为 **`34 passed in 1.19s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`1982 passed, 1 deselected in 23.34s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-51 继续审计 RCV-50 的恢复分支语义。canonical `turn` 在 `/undo`／`/rewind` 后按设计可被下一
  个新分支复用，因此仅把 session counter 改为 canonical turn 仍不足以让同一 transcript 的旧分支与重写分支可区分。
  每条成功 single/compare transcript 现附带当次 publish 后的 `revision`；这只是 EventStore 已有单调 revision 的
  可追溯标签，不改写 store、事件、receipt、回合编号或权限。真实 `行动 → /undo → 改写行动` 回归确认两条记录都可为
  canonical turn 1，但 revision 必为不同整数，输入顺序保持。正式完整离线 gate 为
  **`1983 passed, 1 deselected in 23.09s`**，唯一 deselect 是既有 slow real embedding；无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-52 核对 transcript 与现有 action receipt 的最后一段可追溯性。普通 CLI 虽未把 action ID
  交给玩家作为自动重试功能，`run_turn` 仍为每次成功 publish 分配 durable UUID receipt；旧 transcript 却不含该值，
  不能精确关联同 turn/revision 的恢复证据。现成功的 single/compare row 只复制既有 `result.receipt.action_id`，不新增
  retry 入口、读取 API、权限或写入。undo→重写回归断言两个同 turn 的记录同时拥有不同非空 action ID 与 revision。
  正式完整离线 gate 为 **`1983 passed, 1 deselected in 23.55s`**；唯一 deselect 是既有 slow real embedding，未调用
  真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-53 回到终端主玩家入口的实际可玩性：GUI 已有只读位置、背包和出口，而 REPL 只有行动与 recall，
  玩家无法在回合间查看当前公开态势。新增 `/status`，仅通过 protagonist 的 redacted POV、`scene_capabilities` 和已
  投影 ontology 输出 day/band、当前位置、公开同场角色、背包与已知出口/成本。它不是 canonical 查询、地图发现、GM
  视图或写入入口。专项先暴露 `held_by` 关系方向被误读、背包错误显示为空；已复用 GUI 的物品→角色关系方向修正。
  回归断言隐藏旧塔和私有 sentinel 不出现，SQLite 事件/revision 完全不变。正式完整离线 gate 为
  **`1984 passed, 1 deselected in 23.77s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-54 将 GUI 已有的长期方向感带回正常 CLI 入口，而不复制两套筛选逻辑。新的
  `app.player_objectives` 只消费主角 POV 投影和 Lore 的公开明态条目；`/status` 显示当前长期目标与公开活动任务，
  不读取暗线、stages、provenance、私有解释、GM 或 raw history。GUI 复用同一 read-model，回归覆盖目标覆盖、
  任务推进／结案、rewind 与冷重开，并断言 secret sentinel 不进 CLI／GUI JSON。正式完整离线 gate 为
  **`1984 passed, 1 deselected in 22.94s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-55 补齐终端的公开世界设定浏览。GUI 已有只读 Codex，但普通 REPL 过去只能以 `/recall`
  模糊查询当前 POV，不能查看既有公开设定典。新增 `/codex [关键词]`，直接复用既有 `codex_entries` public index，
  对 kind、title、body 做大小写无关筛选，并明确输出无匹配结果；它不接 EventStore 原始流、GM 视图或私有记忆，
  也不创建事件、receipt、transcript 或 revision。定向 CLI/Codex 回归为 **`37 passed in 0.67s`**，新增回归确认公开
  石桥条目可见、hidden id/私有 sentinel 不出现且事件与 revision 完全不变。正式完整离线 gate 为
  **`1985 passed, 1 deselected in 21.52s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-56 审计终端明确交流闭环时发现，`/status` 显示的只是公开显示名，而 `/speak` 实际将第一个
  token 原样作为内部 recipient ID 交给 receipt validator；玩家依状态页输入名字会被错误拒绝。现在 CLI 复用与 dashboard
  同源的 `native_authoring_context` 当前公开收件人清单，接受唯一显示名或 ID，并将其转换为既有 canonical ID 后仍交给
  `host_player_statement_receipt` 做最终验证。含空格显示名可加引号；重名、隐藏、过期或空原话均 fail-closed，不猜测
  目标、不给发现能力，也不新增 receipt 形状或授权。专项 CLI／explicit-speech 回归为 **`36 passed`**；其中
  `/status → /speak "公开同场的玛拉"` 实际持久化既有精确交流，两个同名公开角色则零写入拒绝。正式完整离线 gate 为
  **`1987 passed, 1 deselected in 22.16s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-57 继续检查终端正常游玩的可见状态。GUI 已有主角事实面板，CLI `/status` 却没有相同的
  公共角色状况，玩家只能看到地点和背包。新增 `app.player_character` 作为 GUI／CLI 共用的 protagonist POV
  read-model：它只接受已 redacted 的投影，过滤 `secret` 与 `hidden`，并将非标量值限制为安全短文本。`/status`
  现展示该公共状态；没有 canonical 查询、raw history、私有解释、事件、receipt、transcript 或 revision 写入。
  CLI 回归在同一 campaign 写入一个 public 状态与 secret sentinel 后确认前者出现、后者完全不出现，并比较读取前后的
  全部事件和 revision。GUI 回归同跑，避免两条玩家出口日后过滤漂移。正式完整离线 gate 为
  **`1987 passed, 1 deselected in 22.16s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-58 补齐 CLI 长局任务回顾的最后一项：`/status` 已经显示公开进行中任务，却没有显示同一
  `player_objectives` read-model 中此前公开、现在已结的任务。现仅追加 `completed_quests` 的摘要行；该模型要求
  `state=了结` 且历史上存在整数 `surfaced_turn`，因此后台暗线的 `quest_expired` 不会被提升成玩家完成项。
  回归用一个公开接取→结案任务与一条暗线过期项验证前者可见、后者及 sentinel 不泄露，并比较命令前后 revision／事件。
  正式完整离线 gate 为 **`1988 passed, 1 deselected in 22.22s`**；唯一 deselect 是既有 slow real embedding，
  无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-59 检查长会话的公开叙事回看。dashboard 已显示最近 public narration，而终端玩家必须依赖
  已滚出的输出；直接接 raw EventStore 又会破坏私有边界。新增 `/journal`，只枚举 `Engine.public_history()` 中未撤回的
  `narration_recorded` 正文，保留最近 8 条并标注既有 turn；没有暴露其他 event deltas、私有解释或重写分支的叙事。
  回归同时写入 public narration、opt-in 私有解释和随后撤回的 narration，确认仅第一者输出且 DB/JSONL revision 完全不变。
  正式完整离线 gate 为 **`1989 passed, 1 deselected in 24.03s`**；唯一 deselect 是既有 slow real embedding，
  无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-60 复核 RCV-59 依赖的 public-history 边界。该 DTO 对普通事件显式引用私有 memory id 会按设计
  抛出 fail-closed `ValueError`；若 CLI 直接传播它，单个 OOC 读会终止 REPL。`/journal` 现在捕获这一特定边界，
  只显示无正文的“当前公开叙事暂不可用”提示，不输出异常、私有内容或相关普通 narration，也不写入任何状态。
  回归构造私有记录及引用它的普通 narration，确认命令安全返回、两个 sentinel 均不出现且完整事件／revision 不变。
  正式完整离线 gate 为 **`1990 passed, 1 deselected in 24.57s`**；唯一 deselect 是既有 slow real embedding，
  无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-61 扩展同一边界到冷重开的实际启动路径。`_print_resume_recap` 旧版直接读取 projected
  narrative slice；即使 `public_history()` 会拒绝一条引用私有记录的普通 narration，该 slice 仍可能保留其正文。
  真实 Engine 现在只通过 `public_history()` 组装 recent narration 与 recap；private-reference DTO 拒绝时压制这些
  叙事文本而不使续玩启动失败。轻量测试 double 保留 legacy world-state fallback。回归构造私有记录和引用它的普通 narration，
  确认两类 sentinel 均不进入 resume 输出。正式完整离线 gate 为
  **`1991 passed, 1 deselected in 25.60s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-62 完成主玩家 dashboard 的同类审计。`build_player_state()` 为最近 narration 直接调用
  `public_history()`；私有引用 DTO 拒绝会使整个 `/api/state` 走 HTTP 500，即使地图、背包、任务和 POV 状态本身安全。
  现在 DTO 只把 narration 置空、显式标记 `narration_unavailable`；浏览器以通用不可用提示显示叙事区，其余安全面板继续
  200 返回。回归构造私有记录和引用它的 ordinary narration，确认 WSGI payload 无两个 sentinel、位置仍可见、事件与
  revision 不变；抽取后 JS 也通过 Node syntax check。正式完整离线 gate 为
  **`1992 passed, 1 deselected in 25.62s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-63 发现并复现首回合毁档风险：刚创世时 `last_turn()` 正确返回 0，但 CLI `/undo` 直接传给
  `retract_from_turn(0)`，实际把 15 个创世事件全部软撤回；`/rewind 0`／负数也无边界。现 CLI 对空 undo 返回无可撤销提示，
  非正 rewind 明确拒绝；`app.engine.rewind` 同时要求真正的正整数，保护其他调用方（含 `False`）不能绕过。
  回归从真实 demo 创世 campaign 运行 `/undo`、`/rewind 0`、`/rewind -1`，以及直接 API 反例，均确认 SQLite/JSONL 事件、
  retracted 状态与 revision 完全不变。正式完整离线 gate 为
  **`1994 passed, 1 deselected in 25.50s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-64 检查同一数据完整性不变量的独立维护入口。`engine/rewind.py` 与 `bin/rpg rewind 0`
  仍可直接调用 legacy store rewind，因此能绕过 RCV-63 的 app 层限制。现在 legacy API 同样验证正整数，维护 CLI 在写前
  打印通用拒绝；正常 `--last` 回退保持原行为。回归直接写入一个 turn-0 legacy event 后调用 API 反例，并通过真实
  `bin/rpg` 创建 campaign、log turn-0、rewind 0，确认 JSONL 保留该事件。正式完整离线 gate 为
  **`1996 passed, 1 deselected in 25.64s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-65 接续 RCV-34 的后台原子性，不把“异常非致命”与“取消可吞掉”混为一谈。此前 digest 和私有解释已显式
  把 `asyncio.CancelledError` 作为控制流上抛；director、cascade、catch-up、lore、demote、density 则只因当前运行时继承关系而
  恰好不会被 `except Exception` 捕获。现六处同样明确先上抛 cancellation，再保留普通 `Exception` 的非致命局部 rollback。
  七个 post-foreground hook 都做了实际 fault injection：先在批中写入 sentinel，再取消；每例均要求整个 action 未 publish、
  事件／revision／action receipt／JSONL 字节不变，并在关闭重开后复验。相反，既有 RuntimeError 回归继续确认普通后台故障只
  回滚自身并保留前台回合，二者语义没有被合并。专项
  `tests/loop/test_turn_density_hook.py tests/test_action_integrity.py -rA` 为 **`106 passed in 0.69s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`2003 passed, 1 deselected in 28.33s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-66 完成 GM 只读出口的同类 fail-closed 收敛。GM 的 canonical 地图、实体和事实本身是刻意供本地
  operator 读取的投影；但 timeline 先调用 `public_history()`，任何 ordinary narration／summary 的私有 memory 引用都会
  让整个 `/api/gm/state` 变成 500。现在只将时间线与 recap 作为同一不安全公开历史切片压制，返回显式
  `history_unavailable` 和无正文的通用页面提示；canonical 只读面板保持 200，且不会输出异常、私有内容或引用它们的 ordinary
  正文。回归构造 active/retracted 私有记录及引用它们的 ordinary rows，验证 DTO 与真实 GM WSGI 均只读：timeline/recaps
  为空、地图/地点仍在，SQLite 事件、JSONL 和 revision 严格不变；GM JS 也通过 Node syntax check。定向
  `tests/app/test_gui.py tests/app/test_offline_private_memory_cli.py tests/app/test_resume_recap.py -rA` 为
  **`52 passed in 1.11s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`2003 passed, 1 deselected in 27.16s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-67 补齐 native-author 离线导出的相同边界。`build_native_turn_brief()` 已复用 player DTO，
  但其 `known_events` 又直接读 `public_history()`，导致 ordinary row 引用私有 memory 时安全导出中断。现在该单一历史
  切片 fail-closed：`known_events` 和 `public_narrative` 为空，`history_unavailable=true`；玩家 POV、可见人物、背包、出口
  与既有 host-authoring 安全材料继续正常导出。真实 subprocess 回归从 campaign 写入 active private record 与引用它的
  ordinary narration，再调用 exporter，确认 JSON、stdout/stderr 均不含两个 sentinel，且 exporter 使用固定 Fake provider，
  不写 campaign。定向 native tools／GUI／public-history 回归为 **`59 passed in 3.35s`**；正式完整离线 gate 为
  **`2004 passed, 1 deselected in 25.72s`**，唯一 deselect 是既有 slow real embedding。未调用真实模型、embedding、网络或外部服务。

- 2026-10-14：RCV-68 补齐终端长局的地图定向。GUI 早已有严格 player POV 地图，但 CLI 只显示当前位置与直接出口，不能
  在不提交行动的情况下查看已知地点图。新增 `/map` 只复用 `scene_capabilities` 的现有 POV nodes/routes/cost，不读取
  canonical ontology 的隐藏关系，也不创建发现、移动、receipt、transcript 或 revision。真实 demo CLI 回归确认客栈、集市和
  成本 1 的公共路径可见，而 north tower、private sentinel 均不出现；读取前后的事件、revision、JSONL 字节完全相同，带参数
  的 `/map` 明确给出用法。专项 `tests/app/test_play.py tests/context/test_capabilities.py -rA` 为
  **`54 passed in 0.98s`**；正式完整离线 gate 为 **`2005 passed, 1 deselected in 28.02s`**，唯一 deselect 是既有
  slow real embedding。未调用真实模型、embedding、网络或外部服务。

- 2026-10-15：RCV-70 只补任务账本的跨入口回归，不改变生产逻辑。长运行 CLI 在另一本地客户端写入公开任务及同回合暗线后，
  下一次 `/status` 实测只显示公开任务；暗线的 about／secret sentinel 均不进入终端输出。peer 撤回该回合后，CLI 被动
  reproject，任务立即消失；对该次 OOC 读取逐字比较 SQLite、JSONL 和 revision 均不变。随后关闭重开，`/status` 继续不显示
  已撤回任务或暗线，且冷重开读取同样零写入。定向 CLI/GUI/POV 组合为 **`107 passed in 1.46s`**；正式完整离线 gate 为
  **`2007 passed, 1 deselected in 22.73s`**。唯一 deselect 是既有 slow real embedding；无真实模型、embedding 或外部服务调用。

- 2026-10-15：RCV-71 审计撤销的共享写边界时发现，CLI 与 GUI 虽都在请求开始时比较 revision，却在读取可撤回 turn
  和 `retract_from_turn()` 的短窗口内没有把该版本带入 SQLite 事务；另一实例在此窗口发布时，旧撤销可能软撤回对方刚写的
  分支。`EventStore.retract_from_turn()`／`retract_from_seq()` 现在支持 optional `expected_revision` 并在 `BEGIN IMMEDIATE`
  后、任何更新前复核；不匹配即 rollback、JSONL mirror 不触碰。`app.engine.rewind` 透传该参数，CLI `/undo`／`/rewind`
  与 GUI undo 都传入其已确认版本。冲突时 GUI 刷新后返回既有安全 400，CLI 重投影、重建 action-start scope、reset 策略并明确
  输出“未执行撤销”，不会假装回退成功。直接 store 字节回归、真实 CLI `play_loop` 竞态和 dashboard 竞态都验证 player turn
  和 peer branch 保留、SQLite/JSONL 不被失败撤销改变。开发中的首轮专项曾因 `play_loop` 函数内旧局部 import 遮蔽新 exception
  handler 而一例失败；移除两处重复局部 import 后三项专项为 **`3 passed in 0.20s`**，相关 store／CLI／GUI／action-integrity
  集合为 **`180 passed in 1.65s`**，正式完整离线 gate 为 **`2010 passed, 1 deselected in 23.43s`**。唯一 deselect 是既有
  slow real embedding；无真实模型、embedding 或外部服务调用。

- 2026-10-15：RCV-72 继续跨实例一致性审计，发现启动与被动刷新会分别读取 `store.revision`、完整 event history，
  再把前者标给后者；peer 恰在两读之间发布时，旧投影会被误标为新 revision，下一次 GET 因缓存“已最新”而跳过重放。
  现在 `build_engine` 与共享 `reproject_engine_world()` 都只消费一次 `EventStore.snapshot()`；CLI、玩家 dashboard、GM
  dashboard、成功 rewind 与 revision-conflict 恢复均复用该 helper。双 SQLite 连接回归在 snapshot 返回后立即由 peer 写入，确认
  当前 engine 保持 snapshot 的 revision，下一次 refresh 才投影 peer publish。定向 engine／CLI／GUI／action-integrity 集合为
  **`200 passed in 2.05s`**；正式完整离线 gate
  `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA` 为
  **`2011 passed, 1 deselected in 23.34s`**。唯一 deselect 是既有 slow real embedding；无真实模型、embedding、网络或外部服务调用。

- 2026-10-15：RCV-73 继续 RCV-71 的撤销原子性审计，发现 CLI `/undo` 仍先以一次读取找最后活动回合、再以另一
  `store.revision` 作为 optimistic guard。peer 若精确夹在这两步之间，旧回合会错误地携带 peer 的新版本进入 SQLite
  retraction，进而撤回 peer 分支。新增 `last_turn_and_revision()` 从单一 `EventStore.snapshot()` 同时取活动回合 frontier
  与 revision；`/undo` 继续将这份 revision 带入既有 `BEGIN IMMEDIATE` 终检。真实 `play_loop` 双连接回归在 snapshot
  返回后插入 peer narration，确认 CLI 输出未执行撤销、原玩家及 peer 两个回合仍 active、SQLite/JSONL 字节未因失败撤销改变，
  且控制器只读重投影到 peer revision。开发中首轮测试 mock 会在冲突后的故意 recovery snapshot 再次插入 peer 事件，造成
  断言的测试装置错误；改为仅在被测 frontier 注入一次后，定向集合为 **`44 passed in 0.53s`**。正式完整离线 gate 为
  **`2012 passed, 1 deselected in 24.90s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务调用。

- 2026-10-15：RCV-74 关闭 RCV-72 尚余的被动比较 TOCTOU。此前 CLI、player dashboard 和 GM dashboard 先单读
  `store.revision` 决定是否 reproject；peer 若紧随该比较发布，旧 world 会被当成最新，且 CLI `/status` 又直接打印新的
  store revision。现在 `refresh_engine_world_if_advanced()` 以一个 snapshot 同时完成比较与必要替换；若 peer 在 snapshot
  后发布，本次保留正确标记的旧投影并由下一读获取完整新快照。玩家/GM DTO 和 CLI status 只显示已投影 revision，CLI single／compare
  transcript 直接记录其 durable receipt revision，避免 peer 夹入时误归属。双 SQLite 连接回归覆盖 snapshot 后 publish 与真实
  CLI `/status` 输出；既有 RCV-73 undo race 的注入移到 selected-frontier helper 后，避免被新的被动 snapshot 正确提前消费。
  定向 engine／CLI／GUI／store 集合为 **`47 passed in 0.88s`**；正式完整离线 gate 为
  **`2014 passed, 1 deselected in 24.35s`**。唯一 deselect 是既有 slow real embedding；无真实模型、embedding、网络或外部服务调用。

- 2026-10-15：RCV-75 转向已提交 action 的浏览器恢复体验，未重复 revision race 审计。若同一 action ID 已有 durable receipt，
  但 sidecar/host envelope 损坏或请求的明确交流 envelope 不一致，原 `TurnResult` 必须继续 fail-closed；旧 409 只有通用
  文本，浏览器保留 pending recovery 却仍显示提交前的 state，玩家需要额外手动刷新才能判断现状。现在 player-mode 409 在不返回
  原 narration、sidecar metadata、host envelope 或异常文本的前提下，尽力附带既有 `dashboard.state()` 安全 DTO；浏览器仅在该
  DTO 存在时立即 render，再显示“不能安全恢复结果”的恢复结束选择。DTO 显示本身异常时仍只保留既有保守 409，绝不把已提交请求
  误作可丢弃 400，也不新增 retry、事件、receipt、权限或 GM 路由。两类真实 WSGI cold-recovery 反例（旧 host envelope、
  改写 explicit speech）断言 409 的 state 已为 durable revision／当前公开地点且 private sentinel 不出现，`confirmed_rejection`
  仍缺失。开发中首轮全量 gate 只暴露另一份同一 409 payload 的旧精确断言，已按同一安全 DTO 契约更新，而非删除测试。
  GUI／explicit-speech 专项为 **`48 passed in 0.84s`**，生成后的 JS 已通过 `node --check`；正式完整离线 gate 为
  **`2014 passed, 1 deselected in 23.68s`**。唯一 deselect 是既有 slow real embedding；未调用真实模型、embedding、网络或外部服务。
  随后用系统 Chromium + Playwright 做了实际 loopback 浏览器验收：390px 页面先由原生 click 发起一条 demo action（200），
  再让浏览器 sessionStorage 保留同一 action ID 但改写 action 文本并点击“恢复上次请求”。真实第二次 POST 收到 409，响应含
  safe state 且没有 `confirmed_rejection`；页面立即显示集市当前位置、隐藏 id／私有 sentinel 不在 DOM，“结束恢复”按钮可见，
  宽度 `scrollWidth=clientWidth=390`。该浏览器流程只写两次预期 `/api/action`，临时服务和 campaign 已删除；它验证恢复 UI，
  不代表真实模型或长期剧情质量。

- 2026-10-15：RCV-76 回到当前玩家的可观察场景信息，而不追加新的编辑面板。原 GUI 与 CLI 都能列出当前可交流人物，
  但实际只显示 sketch/名称；普通作者和 receipt validator 已通过 `native_authoring_context` 拥有更窄的、公开且同场的
  `sketch`／`mood`／`state` 观察材料。新增 `app.player_people` 只从这份既有 receipt-safe projection 剥离
  `id`、显示名和心情／状态，主动丢弃 author-only evidence、reported knowledge、communication receipt、条件到场、
  host route grant 与 raw/canonical facts。GUI 的人物面板显示这些公开详情，CLI `/status` 用同一 DTO；明确交流下拉框继续
  只复用相同人集的 id/label，未改变 receipt 形状或授权。

  定向回归加入两名公开同场人物的 mood/state、一名同场 hidden 人物及其 public-looking sentinel，覆盖 GUI DTO、WSGI、
  移动后清空与冷重开、CLI `/status` 零写入；hidden id、sketch 和 mood 均不出现。系统 Chromium + Playwright 还实际验证
  390px loopback 页面显示“公开同场的旅人（心情：谨慎）”、下拉框同源、一次真实 action 到集市后人物面板清空、只有预期
  `/api/action` POST 且无横向溢出。该浏览器证据是固定 demo/本地事实，不代表真实模型质量。正式完整离线 gate 为
  **`2015 passed, 1 deselected in 25.43s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-15：RCV-77 将已有 durable `public_place_observations` 安全投影接入实际玩家定向，而不重复读取
  canonical world 或 raw event history。新的 `app.player_observations` 仍先采用原投影的明确 public、主角 POV、
  当前地点或合法公开出口边界，再只留下显示地点（当前位置为“这里”）、观察类别、范围和摘要；特意剥离 place id、
  source/event id、day、turn 和 scene。GUI 的“近处观察”和 CLI `/status` 使用同一个 DTO，不新增写路由、发现、
  receipt、transcript 或权限。

  GUI/WSGI 与 CLI 回归各写入一条本地公开观察、一条 hidden 塔观察和一条未标记 world change：前者可见，后两者及
  provenance 均不出现，OOC/state 读取前后 revision 不变。生成的 JS 通过 `node --check`；系统 Chromium + Playwright
  在 390px loopback demo 实测显示“这里·客栈门前两步内（现场观察）：门前石阶潮湿。”，private sentinel 不在 DOM，
  非 GET 请求为空且无横向溢出。临时 campaign/server 已删除。正式完整离线 gate 为
  **`2017 passed, 1 deselected in 26.15s`**；唯一 deselect 是既有 slow real embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-15：RCV-78 复核 RCV-77 的真实玩家语义时发现，其来源契约有意允许“当前位置或当前公开出口”的观察，
  但两种范围原先在 GUI/CLI 的同一平铺文字中没有显式区别，可能令玩家把邻近地点的历史公开观察当作立即所见。
  现在只消费已有 `is_current`：当前位置继续标为“这里”，合法出口的观察明确标为“已知出口：地点”。这不扩展
  `player_place_observations` 的字段、地点范围或权限，也不写 EventStore、路线发现、receipt 或 transcript。

  GUI/WSGI 和 CLI 回归同时写入客栈本地与石桥集市（既有公开出口）的观察，以及 hidden/未标记反例；前两类只按
  正确范围显示，后两类不出现且读取零写入。系统 Chromium + Playwright 在 390px loopback demo 实测两行文字明确为
  “这里……”与“已知出口：石桥集市……”，无非 GET 请求和横向溢出；临时 server/campaign 已删除。JS 通过
  `node --check`，正式完整离线 gate 为 **`2017 passed, 1 deselected in 23.98s`**；唯一 deselect 是既有 slow real
  embedding，无真实模型、embedding、网络或外部服务。

- 2026-10-15：RCV-79 没有新增展示或生产写逻辑，而是补足 RCV-77/78 进入多实例 durable replay 后的 P0 验收。
  两个真实 dashboard 打开同一 SQLite/JSONL campaign：peer 以既有 `append_many` 事务发布当前地点的公开观察，旧 reader
  只通过 `state()` 被动重投影并显示它；peer 再以 revision-bound `retract_from_turn()` 撤回，reader 的下一次 state 和随后
  冷重开均不再显示该观察。首次测试装置误用单事件 `append(..., preflight=...)`（API 不支持该参数）而失败；改为产品已有
  的 `append_many` 后固定回归。专项为 **`3 passed in 0.31s`**，正式完整离线 gate 为
  **`2018 passed, 1 deselected in 26.37s`**；唯一 deselect 是既有 slow real embedding。此项无 UI 生产改动，未另作浏览器
  行为结论，也无真实模型、embedding、网络或外部服务调用。

- 2026-10-15：RCV-80 将同一 active-history 验收扩展到长运行终端 `/status`，不改变生产逻辑。独立 peer 事务发布一条
  当前地点公开观察，CLI 下一个 status 显示它；peer 用其当前 revision 撤回该 turn 后，下一次 status 与冷重开都不再显示。
  读取前后 SQLite/JSONL/revision 字节一致。首轮 fixture 误把 `world_change` 的显示摘要写在 delta 副本而非 durable event
  `summary`，因此期待的 sentinel 未出现；按 projection 的真实输入形状修正后通过。专项为 **`4 passed in 0.30s`**，正式完整
  离线 gate 为 **`2019 passed, 1 deselected in 25.14s`**；唯一 deselect 是既有 slow real embedding。此项无 UI 生产改动，
  无新增浏览器结论，也无真实模型、embedding、网络或外部服务调用。
