# Codex 接手入口 · 2026-11-13

**目标：拉取 `harness` 后，直接接续当前 RPG 世界引擎工作。此次只整理迁移，不继续实现功能。**
本目录保存可执行的交接上下文，不是聊天逐字导出，也不包含模型内部推理或凭据。

## 1. 先核对版本

```bash
git clone --branch harness https://github.com/RedStoneManL/llm-rpg-engine.git
cd llm-rpg-engine
git status --short
git branch --show-current
git log -5 --oneline
git merge-base --is-ancestor 59b41178cd2fe6603fea972cab439dc358401c83 HEAD
python3 scripts/handoff/verify.py
```

工作区应干净、分支为 `harness`，祖先检查及交接校验应通过。不要在旧 `main` 上重新实现这些工作。
原远端没有名为 harness 的分支，因此此次从最新研究提交新建它；原 `handoff/codex-cloud-20261025` 保留。

| 检查点 | 含义 |
| --- | --- |
| `91646fb6c3cfe418709ae0f869ba985c282e2003` | RCV-139；原交接分支的最新生产基线，含此前完整 RCV 历史 |
| `9acb6f3f301dbeaab384650b0a1d17aa5f44a81a` | 固定 DSH 上游版本，消融工具、证据、准入规则；无第三方 runtime 接入 |
| `59b41178cd2fe6603fea972cab439dc358401c83` | 6.1 Terra 真实短局、同回合新 NPC 转述权限修复及回归 |
| 本分支的交接提交 | 文档、报告、完整合成存档快照、未完成补丁；运行时保持 `59b4117` |

原工作区 `research/endurance-100-20261113` 的一个未提交改动保存在
[`wip/director-novelty-UNVALIDATED.patch`](wip/director-novelty-UNVALIDATED.patch)。它没有应用到本分支。
原文件保留不动；不要因为文件名含 endurance 就认为百回合实现或测试已完成。

## 2. 必读顺序

1. 本文与根目录 [AGENTS.md](../../AGENTS.md)：当前目标、权限、证据边界与下一步。
2. [用户上下文与决策](USER_CONTEXT_2026-11-13.md)：为什么迁移、为什么不能直接采用第三方声称的能力。
3. [研究台账](RESEARCH_LEDGER_2026-11-13.md)：五个候选的可取之处、已实测反例、准入结果与架构方案。
4. [执行手册](EXECUTION_RUNBOOK_2026-11-13.md)：环境、复现、恢复存档、真实模型 mailbox 和限制。
5. [100 回合接续计划](ENDURANCE_PLAN_2026-11-13.md)：已知缺陷、具体下一动作、阶段验收。
6. [交接审计](AUDIT_NOTES_2026-11-13.md)：容易误判的路径、旧指令与剩余风险。

需要展开时再读原始材料，不必先读全仓库：

| 问题 | 入口 |
| --- | --- |
| 当前全部模块如何连接 | [MODULE_INDEX](../MODULE_INDEX.md)、[体验与架构报告](../reports/2026-11-07/experience-repairs.html) |
| 综合 DSH 方案的完整设计 | [原始综合评估 HTML](artifacts/RPG_DSH_Integrated_Assessment_2026-11-10.html) |
| 第三方机制具体跑了什么 | [第一轮验证报告](../verification/DSH_VALIDATION_ROUND1.md)、[原始结果](../verification/dsh-validation-round1/observations.json) |
| 6.1 Terra 实际怎么玩 | [完整回放 HTML](artifacts/Terra_DM_Real_Run_2026-11-11.html)、[实测报告](../verification/TERRA_DM_LIVE_2026-11-11.md) |
| 11 回合每次请求和响应 | [初跑证据](../verification/terra-live-20261111/manifest.json)、[续跑证据](../verification/terra-live-20261111/continued/manifest.json)及同级 requests/responses |
| 更早修过哪些问题 | [累计执行清单](../progress/PRODUCT_EXECUTION.md)、[RCV-139 验证](../verification/RCV-139-host-speech-and-endurance.md) |
| 怎样决定能否接入 | [外部机制准入规则](../designs/EXTERNAL_MECHANISM_ADMISSION.md) |

## 3. 现在到底完成到哪里

| 事项 | 真实状态 |
| --- | --- |
| 现有世界内核、事实来源、POV、原话、事务、撤销 | 已实现，具体覆盖范围看源码和各 RCV 证据 |
| 相似项目研究 | 对五个项目做源码/文档比较；只对 Agentic Roleplay、LikeTavern 的选定组件做过实验 |
| 第三方组件验证 | 178 passed、2 skipped；7 组独立实验、13 项本引擎检查，0 次真实模型调用 |
| Terra DM 真实短局 | 11 个已提交剧情行动、35 次模型响应、1 次撤销、2 次冷重开；首跑失败保留并在修复后续跑 |
| 新 NPC 当回合登场并转述 | `59b4117` 已修；原失败响应重放通过；记录的全量回归 2276 passed、1 deselected |
| 导演重复“认错人” | 真跑已复现；只有一个未验证设计/实现草稿，尚未验收 |
| 已有暗线被接受后仍可能降级 | 源码定位的待复现问题；没有新红例和修复，不能把旧 RCV-127 当作完整覆盖 |
| 连续 100 回合真实游玩 | **未完成，尚未启动新的 100 回合真实模型 run**；历史 100 回合是固定响应的机械实验 |
| DSH 安装、生命周期、UI 与发布 | **未做完整宿主验收，未发布插件** |

这些通过数来自保存的历史原始结果；交接只做材料、恢复和可迁移性校验，不把它们称为本轮重跑。

## 4. 下一 agent 应从这里继续

1. 阅读上述材料并核对基线；恢复一份合成测试存档，确认原始证据可查。
2. 先做已有暗线“被玩家接受却未标记 accepted_by_player”的最小反例；沿事件语义修复，不以台词关键词硬补。
3. 审核导演草稿；设计事件溯源的近期主题去重，比较零窗口、默认窗口和小表回退。不要直接无条件应用补丁。
4. 设计可断点续跑的 100 回合真实模型驱动。既有 `experiments/terra-live/run.py` 只含 12 项操作（11 剧情+1 撤销）及 70 请求上限，不能用重复运行它冒充连续 100 回合。
5. 核实新环境实际可用的 `gpt-6.1-terra` 与计费来源；按 20 → 30 → 100 回合递进。模型调用入口不能靠 clone 转移；不可用时继续独立代码/离线工作并准确说明缺项。
6. 记录正文、状态、NPC 所见、暗线和回滚；有缺陷就留现场，机制修复后同场景重测，再在冻结版本完成验收。最后再逐个适配第三方候选。

不要仅因交接或测试通过就把下一任务结束在计划；也不要忽略用户本次明确要求**此处先停止功能实现**。

## 5. 随仓库带走的材料

- 两个历史研究提交及原有生产历史；原始请求/响应、失败、消融、JUnit/Vitest JSON 等已经在 Git 中。
- 两份此前仅在 Work 外层目录的完整 HTML 报告，现为自包含文件。
- 本次合成场景的完整 SQLite backup，gzip 压缩，保留事件、撤销标记、action receipts、玩家原输入、结果 metadata 和 FTS。
- 原未完成导演 diff；待办和需要审计的模块；恢复与校验命令。
- `manifest.json` 给关键交接文档、原始实验和补丁做 SHA-256 清单；`scripts/handoff/verify.py` 可在全新 clone 中验证。

快照恢复示例（目标目录必须不存在）：

```bash
python3 scripts/handoff/verify.py --restore-campaign ./campaigns/handoff/terra-live-campaign
```

恢复脚本只处理本仓库附带的合成快照，不自动调用模型或推进剧情。数据库是权威，JSONL 由 `EventStore.sync_jsonl()` 重建。
原 SQL dump 的 FTS 还原在交接检查时失败，因此交付的是已测试的完整 SQLite backup，不交付那份失败的 dump。

不会随 Git 迁移的内容：Work 原生工具/模型选择器、会话权限、密钥、虚拟环境、第三方 node_modules、正在运行的进程。
第三方源码由冻结 SHA 与锁文件重建，不混入本仓库；新环境依赖和模型连接步骤见执行手册。

## 6. 旧文档冲突处理

`docs/HANDOFF.md`、旧 `CODEX_CLOUD_HANDOFF` 中的日期、分支、待办和授权是当时快照。
本目录是 2026-11-13 的接续状态；具体实现以当前源码、原始证据及最新用户指令为准。
旧 B1 HOLD 已解除；不要再次阻塞；旧“不 push”不阻止此次已明确要求的 harness 交接。
`--demo`、录制重放、确定性 100 回合与新的真实长局四种证据必须分别命名。

## 7. 本次交接校验

在独立、无 hardlink 的全新 clone 中完成：155 项归档文件哈希、完整存档解压/FTS/回执/原输入、JSONL 重建、重复恢复拒绝、历史11行动/35响应回放重建、10项回放边界核对，以及原失败候选的修前拒绝/修后提交对照。工作区保持干净，运行时代码与 `59b4117` 相同。

详见 [交接验证原始结果](TRANSFER_VALIDATION_2026-11-13.json)。该结果验证提交 `c065d98`；后继仅补此验证记录与索引。没有新模型调用、没有重跑全量 pytest、没有完成百回合真跑或 DSH 宿主验收。
