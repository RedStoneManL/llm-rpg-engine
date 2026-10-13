# RPG 产品与持续开发执行追踪

最后核对：2026-10-13 UTC。本文是用户指定的持续执行准则与当前仓库恢复工作的唯一执行入口；历史
Library 文档记录的是另一份后续代码快照，不能替代这里的源码与测试证据。仓库中没有另一份可核实的
“整体架构 prompt”文件能取代它；设计原则、已验收事实、开放风险和下一项必须在本文件持续更新。

## 持续执行约定（用户确认）

- 检查点（本地 commit、可独立恢复的完整 bundle/source/证据、Library 保存）完成后，自动选择并推进下一项
  已授权的高价值工作；不能把“阶段完成”当作等待新授权的理由。仅在需要新权限、遇到真实技术阻塞或用户
  明确暂停时停下并报告。
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
