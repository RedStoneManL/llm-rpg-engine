# RPG 产品与持续开发执行追踪

最后核对：2026-10-12 UTC。本文是用户指定的持续执行准则与当前仓库恢复工作的唯一执行入口；历史
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
