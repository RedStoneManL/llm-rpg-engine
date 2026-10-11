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
