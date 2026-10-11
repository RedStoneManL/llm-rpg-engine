# RPG 产品与持续开发执行追踪

最后核对：2026-10-11 UTC。本文是当前仓库恢复工作的唯一执行入口；历史
Library 文档记录的是另一份后续代码快照，不能替代这里的源码与测试证据。

## 可复现基线

- 初始恢复基线：本地分支 `work` 的 `6b544111a1b0e26fbc232ba0dcb3df33e92b8dbb`。
  这不是当前 HEAD；当前实现检查点另见本页更新记录与 `git rev-parse HEAD`。
- 没有恢复到历史报告中的 `9928c1d2`，也没有 `AGENTS.md`、PreparedTurn、
  专用角色解释记忆或 GUI 子项目实现；不得把历史的 6133 通过当作本基线结果。
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
| 可见性和记忆 | 旧 POV/knowledge 过滤与 recall 模块存在 | 没有历史所述的私有角色解释记忆入口、隔离存储、冷启动/replay/retraction 验收；默认不启用新记忆 |
| 前端/运维 | CLI、trace viewer、portable handoff 存在 | 没有可核对的 GUI 实现；测试仍残留旧工作区路径 |

## 工作队列

| ID | 优先级 | 状态 | 根因与下一动作 | 验收 |
| --- | --- | --- | --- | --- |
| RCV-01 | P0 | 已验收 | 清除 trace CLI 测试中的旧绝对 checkout 假设 | `tests/app/test_trace_view.py` 38 passed；完整 gate 1712 passed / 1 deselected（16.66s） |
| RCV-02 | P0 | 已验收（本基线范围） | 对 `run_turn`/`EventBatch`/后台 hooks 做实际原子性与 owner 拒绝审计 | 已覆盖后台失败撤回、嵌套 savepoint、direct `apply_turn` owner fail-closed、撤回后的关闭/重开/完整 replay；新后台类别仍须随新增功能各自回归 |
| RCV-03 | P1 | HOLD | 角色解释性记忆恢复 | 仅在离线 provider/temp DB 下，证明同角色读取、跨角色隔离、冷启动、异常/取消回滚、replay；此前默认禁用 |
| RCV-04 | P1 | 排队 | 摘要/recap 与事实权威边界 | 摘要后台失败不污染事实或已提交回合；公开/GM/CLI 不泄漏私有文本 |
| RCV-05 | HOLD | HOLD | B1 detail retrieval 与额外 profiling | 原历史工具审查拒绝；不换路绕过，也不重试 |
| M1-01 | P1 | 已验收（默认禁用数据切片） | 角色私有解释记忆的最小持久化/重放边界 | 默认 app 拒绝事件；opt-in 隔离 registry 的 actor 分组、撤回和重开 replay 通过；参数分组不是授权，读取接入仍 HOLD |
| M2-01 | P1 | 已验收（离线宿主前置层） | 仅可信 Python 宿主的私有记忆读取 handle | world/store/session/role identity 绑定、关闭撤销和跨 session/store 反例通过；默认 app/tools/DM 未接入，非生产认证 |
| M3-01 | P1 | 已验收（新公开历史 API） | 完整历史的私有记忆过滤 DTO | `Engine.public_history()` 过滤 active/retracted 私有行及显式引用；既有 CLI/GUI/GM/raw-store 未接入 |
| M3b-01 | P1 | 已验收（续玩输出隔离） | resume recap 不读取 opt-in 私有记忆 slice | real store→projection→`_print_resume_recap` 覆盖 active/retracted、当前/其他角色私有记录；其余出口未测 |
| M4-01 | P1 | 已验收（离线宿主原子写入） | batch/source/output 绑定的私有解释写入 | 红例驱动修复后，stage/publish 双重绑定复验、savepoint rollback、篡改/跨生命周期拒绝通过；默认 app 禁用 |
| M4-02 | P1 | 已验收（实际 payload 终检） | preflight 后的待插入副本不可绕过 M4 绑定 | `final_validate` 在同一 SQLite 事务内、preflight 之后、insert 之前校验；前台 source body 和私有 record 的 actor/source/type/id/turn 篡改均原子拒绝 |
| M4-03 | P1 | 已验收（savepoint 表示保真） | 失败 hook 不得把缺失的 `retracted` 字段规范化为 `False` | savepoint 精确恢复字段存在性与值；缺键 source 的既有 memory writer/validator 在失败 hook 后仍可发布 |

## 执行规则

- 每一个完成且已验收的小阶段做本地 git commit；禁止 remote push/PR。
- 每个关键检查点保存 git bundle、源码版本和测试证据到 Library；临时磁盘不算交付。
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
