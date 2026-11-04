# 引擎进度与交接文档

这里统一保存当前进度、跨环境交接和后续阶段记录，随代码提交到 GitHub。
不需要从聊天或本地手工上传文件；在云端拉取本分支即可获得代码及上下文。

当前接续分支：`handoff/codex-cloud-20261025`，不要误从旧 `main` 恢复。

## 阅读顺序

1. [Codex Cloud 完整交接说明](CODEX_CLOUD_HANDOFF.md)：用户目标、实际能力、限制、测试环境及下一步。
2. [产品执行清单](PRODUCT_EXECUTION.md)：累计进度、已验收事实和开放风险。
3. [Agentic 历史召回与结果先行设计](../designs/AGENTIC_HISTORY_AND_OUTCOME_PLAN.md)：设计方向，不等于已实现。
4. [RCV-116 验证记录](../verification/RCV-116-45c270d.md)：具体版本、命令、结果和局限。
5. [RCV-117 验证记录](../verification/RCV-117-recap-source-chain.md)：云端恢复实测、总概要来源链与旧档兼容。

6. [RCV-118 验证记录](../verification/RCV-118-independent-recap-jobs.md)：独立有界重试、部分失败与 Luna 原生摘要样本。

7. [RCV-119 全量评估](../verification/RCV-119-current-environment-assessment.md)：解除 HOLD 后的环境、性能、检索／正文反例与优先级。

8. [RCV-120 验证记录](../verification/RCV-120-retrieval-scope.md)：混合检索先限定候选再排序，完整 2171 passed。

9. [当前架构与体验优化 HTML](../reports/2026-11-04/architecture-status.html)：模块结构、最新调用路径与体验优先路线；[RCV-126 核验](../verification/RCV-126-delivery-architecture-report.md)。
   [2026-10-26 历史版本](../reports/2026-10-26/architecture-status.html)与 [RCV-121](../verification/RCV-121-architecture-report.md)保留供对照。

10. [RCV-122 结果先行正文](../verification/RCV-122-outcome-narration.md)：Hybrid 改造与默认作者修正后的正文同步。

11. [RCV-123 人物连续性](../verification/RCV-123-character-continuity.md)与[体验优先迭代计划](../designs/PLAY_EXPERIENCE_PLAN.md)：当前状态／关系接回角色卡，后续转向任务与后果。

## 维护规则

- 本目录两份正文是当前唯一维护版本；仓库根目录的同名文件只作兼容入口。
- 后续阶段总结可新增 `YYYY-MM-DD-主题.md`，同时更新本索引及执行清单。
- 设计正文继续放 `docs/designs/`，验证记录继续放 `docs/verification/`，由这里链接，不复制多份。
- 历史报告和 `docs/HANDOFF.md` 保留历史性质，不作为当前完成状态；实际能力以源码和当前版本验证为准。
- 不提交密钥、用户 campaign 数据、临时日志或完整恢复包。

## 最近交付

- 2026-11-04：RCV-126 将 HTML 同步到交付闭环 `f9127cf`，97模块、872符号，完整回归2220通过与Terra样本分层展示。

- 2026-11-04：[RCV-125 交付约定与任务后果](../verification/RCV-125-delivery-outcomes.md)：实际转移结算、
  迟交可补救、明确放弃、最近六条公开结局；Terra 5 回合／10 条原生响应通过，人物丰富度仍需改善。

- 2026-11-04：RCV-124 更新 HTML 为体验优先版，按 RCV-123 生产源码核验；完整引擎 gate 已在 RCV-123 重跑得到 **2191 passed / 1 deselected**。

- 2026-11-04：用户将玩法体验提升为首要队列。RCV-122 已落地结果确定后的最终正文；RCV-123 已补 NPC 状态连续性，后续转向任务／承诺后果。

- 2026-10-26：RCV-121 交付基于 `03cabc4` 的自包含 HTML 架构报告：14 章节、15 系统、96 模块与可切换调用流程。
  本次是文档与源码核对，不改运行行为，不把历史 gate 当作本次重跑。

- 2026-10-25：RCV-120 将评估发现的检索范围错误落实修复；专项 **71 passed**，全量 **2171 passed / 1 deselected**。
  原输入档案、actor/revision 来源句柄及结果先行最终正文仍待做，旧 HOLD 不再阻塞。

- 2026-10-25：RCV-119 重新评估完成，包含 100／1000／5000 条本地 profiling 与检索范围、来源键、Hybrid 正文反例。
  B1/profiling 旧暂停已解除，下一项实际修复为 FTS／语义候选范围一致性。

- 2026-10-25：RCV-118 完成摘要／总概要独立维护，专项 **118 passed**，全量 **2142 passed / 1 deselected**。
  Luna medium 两份真实响应通过精确请求回放；旧 B1/profiling HOLD 已由用户明确解除，开始重新全量评估。

- 2026-10-25：RCV-117 在交接检查点 `d1833ad` 上实现总概要来源链，绑定有序摘要与上一版总概要，
  保留旧档未验证来源标识。新环境基线 **2107 passed / 1 deselected**；实现后全量 **2135 passed / 1 deselected**，
  最终专项 **85 passed**。仅本地提交，没有 push／部署／外部上传；详细范围及限制见上方验证记录。
- 2026-10-25：`e8caf7b470e67c0706fbd8c87f513ada9a2f2b96` 已推送至上述分支，远端 SHA 已核对。
  生产基线为 RCV-116 `45c270d`；全新独立虚拟环境离线门禁为 **2107 passed, 1 deselected in 32.12s**。
- 同日：按用户要求集中进度文档；本次仅文档整理，不改变引擎行为，不将既有测试结果冒充本次重跑。
