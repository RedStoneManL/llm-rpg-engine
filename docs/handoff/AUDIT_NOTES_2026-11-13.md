# 交接只读审计：2026-11-13

本页只记录本次迁移交接的核对与风险定位，不包含新的运行时实现、真实模型调用或全量回归结论。当前 `harness` 的运行时代码以 `59b4117` 为基线；从旧工作区保存的导演改动仅为补丁附件。

## 1. 未完成改动完整保留

原工作区只有一个 tracked 修改：`loop/director.py`。归档文件为 [director-novelty-UNVALIDATED.patch](wip/director-novelty-UNVALIDATED.patch)。

- 与原工作区 `git diff --binary HEAD -- loop/director.py` **逐字相同**。
- 大小：5128 字节。
- SHA-256：`10306671b34577c8df3ad80b65be383372261345172add54eaeeee3c117da3cd`。
- 在目标基线 `git apply --check` 返回 0。仅检查可应用性，**没有应用、运行或验收补丁**。
- 意图：对近期 `director_fired` 做 twist/event-type 冷却；独立 seeded draw；零窗口用于旧逻辑消融；候选耗尽时放宽；记录 selection 审计字段。具体默认窗口只是未验收的草案，不能当成产品标准。
- 下一执行者应先检查现有代码是否已变化，再决定应用或重做。至少验证相同种子、撤销后重放、冷重开、小候选表、无反转候选、重复率及叙事收益。不得将“补丁可应用”记作“导演问题解决”。

## 2. 已有暗线被接取后的降级：精确待查边界

这是一条已从源码定位的**待复现假设**，本次未运行新反例，也未实现修复。

| 位置 | 当前事实 | 下一验证重点 |
| --- | --- | --- |
| `systems/lore.py`，`quest_opened` | 新建公开任务时设置 `accepted_by_player=True` | 这条路径已有保护，不应推翻 |
| `systems/lore.py`，`quest_surfaced` | 已有暗线变明，更新 surfaced turn/day；没有标记玩家接受 | “玩家发现”与“玩家接受”需要区分 |
| `systems/lore.py`，`quest_advanced` | 更新公开摘要与 last_advanced_day；没有设置接受标记 | 叙事上答应追查是否有持久承诺事件 |
| `loop/turn.py`，`_run_demote_on_leave` | `accepted_by_player` 为真才豁免离开地区/长期空闲降级 | 不能通过全面关闭 demote 解决 |
| `loop/strategy.py`，任务协议 | 只有 open/surface/advance/resolve；open 要求全新 id | 已有线的接受表达缺少明确领域操作 |
| `systems/lore.py`，任务验证 | `open` 拒绝已有 id；错误提示允许通过 surface 接取暗线 | 需要核对协议用词与持久语义是否一致 |

优先最小场景：预置暗线 → 玩家获知/浮出 → 玩家明确接受 → 离镇/跨多个游戏日 → 返回。负对照是只获知、没有接受的背景线；它仍可依据原世界规则降级/重排。检查真实事件、投影、公开账本、撤销、重开及同 id 的既有 stages/clues，不只读正文。

已有相关测试：

- `tests/loop/test_world_continuity.py::test_an_explicitly_accepted_investigation_does_not_vanish_while_elsewhere`：只直接追加新的 `quest_opened`，没有覆盖“已有暗线 → surface → 接受”。
- `tests/loop/test_quest_transitions.py`：离镇降级、原镇不降级、idle 按游戏日、当回合 surface 防抖及下回合降级。
- `tests/loop/test_quest_lifecycle.py`：暗 → 明 → 推进 → 降暗 → 世界推进的完整既有生命周期。
- `tests/systems/test_quest_state.py`、`tests/systems/test_lore_active.py`、`tests/loop/test_quest_disclosure.py`：状态/公开范围相关回归。

[RCV-127](../verification/RCV-127-world-continuity.md) 的既有修复仍有效。新的疑点是未覆盖的“接受既有线”路径，不应写成 RCV-127 所有保护失效。

## 3. 最容易误导接手者的历史信息

1. `docs/progress/CODEX_CLOUD_HANDOFF.md` 的早期段落仍写旧 B1 HOLD、旧型号 5.6 Terra/Luna、禁止真实模型、禁止 push/并行等历史限制。入口必须说明以当前用户指令及现行环境规则为准；旧 HOLD 已解除，指定 DM 是 6.1 Terra。迁移交接本轮停止实现与模型调用，不意味着永久取消后续开发。
2. `docs/HANDOFF.md` 写最新交付在 `main`，当前不可按其直接恢复。新分支是 `harness`；旧生产分支 `handoff/codex-cloud-20261025` 截至交接开始为 `91646fb`。不可覆盖旧分支来消除差异。
3. RCV-139 的 **100 回合**是确定性替身跑状态/调度，不能算用户要求的连续 100 回合 6.1 Terra 真跑。6.1 Terra 当前证据是 11 个已提交剧情行动、35 个真实模型响应，含 1 次撤销及 2 次冷重开；其中第 5 步初跑失败，修后续跑。不能写成无失败完整首跑。
4. DSH 第一轮 **178 passed / 2 skipped** 是选定上游组件测试，不是所有上游测试或完整插件安装。**7 组消融测试通过**表示反例和干预被检测到，不表示所有候选机制被准入。第三方自主 NPC 全流程、DSH 安装/交付均未验收。
5. `NativeProvider.model = gpt-6.1-terra` 和 manifest 字段是声明，不能独自证明下一次运行选中了型号。接手者必须核对实际模型选择/传输，不可由其他模型代写文件却沿用此标签；mailbox 等待时长不是 HTTP 推理延迟，UTF-8 字节不是 tokens。
6. `experiments/terra-live/run.py` 是 11 剧情行动加 1 控制步骤的短场景，硬上限 70 请求。它不是通用 100 回合 runner。不得仅提高数字后把固定短场景称为百回合验收。
7. 原始 manifest、provenance 及旧报告含 `/workspace/...`、`/tmp/...` 等当时环境路径，应保留为历史证据。新执行输出、临时副本中的 resume_from、Python 路径须按新环境重定位，不能依赖旧目录存在，也不要改写原始哈希证据。
8. 未完成导演补丁不应自动进入运行时代码。原测试数（2276 等）不是应用补丁后的门禁数。

## 4. 存档与隐私边界

交接存档来自这次人工合成的 Terra 测试世界。归档应只保留该合成场景的 SQLite 完整备份，包含 receipts、原输入、撤回记录及派生索引；不能以仅 `continued/events.json` 代替完整存档。

本次没有理由复制环境变量、凭据文件、GitHub 登录状态、用户私人游戏存档、无关个人记忆或原始整段个人历史。游戏里的“秘密标记”用于认知隔离实验，不是服务凭据。公开仓库里的研究材料应保持这个边界。

建议交接发布前由主执行者最后核对：新增文件白名单、常见凭据格式、完整性 manifest、独立干净 clone 中的补丁检查与合成存档还原。对新副本做可恢复验证即可，不继续原任务的功能开发。
