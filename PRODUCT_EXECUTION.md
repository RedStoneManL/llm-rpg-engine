# RPG 产品与持续开发执行追踪

最后核对：2026-10-12 UTC。本文是当前仓库恢复工作的唯一执行入口；历史
Library 文档记录的是另一份后续代码快照，不能替代这里的源码与测试证据。

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
| 前端/运维 | CLI、trace viewer、portable handoff 存在；RCV-08 增加仅回环的离线玩家仪表盘首切片 | 没有 GM/editor UI、部署或生产 web runtime；网页仍不是通用 GUI/世界书编辑器 |

## 工作队列

| ID | 优先级 | 状态 | 根因与下一动作 | 验收 |
| --- | --- | --- | --- | --- |
| RCV-01 | P0 | 已验收 | 清除 trace CLI 测试中的旧绝对 checkout 假设 | `tests/app/test_trace_view.py` 38 passed；完整 gate 1712 passed / 1 deselected（16.66s） |
| RCV-02 | P0 | 已验收（本基线范围） | 对 `run_turn`/`EventBatch`/后台 hooks 做实际原子性与 owner 拒绝审计 | 已覆盖后台失败撤回、嵌套 savepoint、direct `apply_turn` owner fail-closed、撤回后的关闭/重开/完整 replay；新后台类别仍须随新增功能各自回归 |
| RCV-03 | P1 | 已验收（离线 CLI 实验闭环） | 显式 opt-in 的角色解释性记忆入口 | `--offline-private-memory` 才注册模块与固定本地生成器（不覆盖前台 provider/embedder）；`/memory` 仅通过宿主 handle 读取当前主角；temp DB CLI 覆盖默认关闭、写入、重开、跨角色隐藏、同一 CLI 会话内 undo/rewind 与公开出口隔离。仍非通用 ACL 或生产记忆系统 |
| RCV-04 | P1 | 已验收（离线 recap 边界） | 摘要/recap 与事实权威边界 | 真实 `run_turn → digest_fleet` 覆盖摘要异常的局部失败、成功摘要、冷重开 replay、receipt/revision/结果事件、私有记录不进入摘要提示或 resume；摘要保留为公共叙事文本，不能改写 ontology facts |
| RCV-06 | P1 | 已验收（离线 CLI 状态闭环） | CLI 回合/摘要/私有实验状态可见性 | 成功回合显示 `[已保存]`；真实摘要失败显示无正文的 `暂未生成`；启动明确私有实验开关；固定无网络演示覆盖默认/显式、续玩、`/memory`/`undo`；状态不新增事件或重试玩家回合 |
| RCV-08 | P1 | 已验收（离线玩家 GUI 首切片） | 仅 `127.0.0.1` 的玩家游玩/仪表盘 | 真实 Chromium/Playwright 覆盖公开 POV 的叙事、行动、角色/背包、位置/相邻地图、公开设定典、保存/刷新；Python 回归覆盖 action-id duplicate、撤销、冷重开与私有 sentinel；不包含 GM/editor、部署、真实模型或私有记忆启用 |
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
