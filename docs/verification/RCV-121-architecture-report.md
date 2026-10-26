# RCV-121 — 当前架构 HTML 报告

日期：2026-10-26（Asia/Shanghai）。审读源码基线：
`03cabc4b0cb8f8589339bcf0da8cd2f15af93d56`，分支 `handoff/codex-cloud-20261025`。
本阶段只新增报告／生成器和进度记录，没有修改引擎运行行为。

## 交付

[architecture-status.html](../reports/2026-10-26/architecture-status.html) 为单文件自包含中文报告，
14 章节、约 325 KB，无外部 JavaScript、CSS、字体、图片或 CDN 请求。

覆盖整体架构、核心对象、普通回合／duplicate／失败取消／撤回重开四条流程、后台钩子顺序、15 个领域系统
（14 默认 + 1 opt-in）的状态／事件／提交段／方法、创世、策略与上下文、两条不同 recall 路径、存储／权限／
来源边界、既有性能证据、优化路线和完整源码索引。另说明内容 pack、旧 hook、脚本、测试与历史资料目录。
默认注册事件类型实际为 50 个；源码索引包含九个运行时目录的 96 个非 `__init__.py` 模块、25,514 行
（含注释／空行）、861 个类／函数／方法符号。静态导入图不被说成完整动态调用图。

主调用顺序按 `loop/turn.py` 实际函数体审读；系统契约来自临时空 campaign 的实际 Registry 对象，显式
Fake provider、embedder=None，不调用生成、不读取用户存档。语义解释人工核对，未直接采纳过时模块 docstring。
例如 app.gui 已有普通已保存 campaign 模式，cascade 已有横向传播，不能只看早期模块说明误报。

## 构建

```bash
.venv/bin/python scripts/build_architecture_report.py
```

生成器使用标准库和现有项目模块，不新增引擎运行依赖。源码 HEAD、函数位置、系统注册契约与模块索引自动读取；
架构语义、优先级和历史测量说明为人工整理，未来代码改变后必须重新审读，不可仅重新运行脚本就宣称文案仍正确。

## 实际检查

- HTML 解析与内部定位：14 sections、143 唯一 ID、453 个链接／锚点全部可定位；相对验证报告路径存在。
- AST 复核：全部 96 个模块的 861 个符号与所列行号一致；15 个系统卡片齐全；无远程资源引用。
- `node --check` 检查内联脚本语法成功。
- Node VM + 最小 DOM adapter 实际执行交互：四个流程 tab、键盘切换、模块搜索、旧路径筛选（7 项）、
  零结果、展开／折叠、跨链接重置过滤并打开目标，全部通过。此检查不等同浏览器 DOM／布局验证。
- 尝试 MuPDF Story 内部打印 smoke：汉字与正文可解析，但其 CSS 支持不完整，报告了现代选择器兼容问题；
  没有把它作为浏览器截图或响应式视觉验收，也没有交付该打印中间件。
- 当前运行时 Playwright 模块可用，Chromium executable 仍不存在；本阶段未进行真实 Chromium 渲染验收。
- `git diff --cached --check` 在提交前通过。

本阶段没有重跑引擎全量测试。报告中 **2171 passed, 1 deselected** 明确引用 RCV-120 的 2026-10-25 gate；
性能表引用 RCV-119 的 e69029a 基线实测；Luna 两请求引用 RCV-118。没有把历史记录改写为本次实测。

## 决策与边界

建议下一项为成功回合原 input 同事务档案，再接 campaign/actor/revision 绑定 source session，之后实现
locate/open/邻文展开与引用核验。结果裁定后的最终正文是并列 P0，复用已有 resources resolution，实际改造
Hybrid frozen-prose。随后根据测量优化上下文预算与投影成本，再扩大 NPC 自主闭环。

旧 B1/profiling HOLD 继续视为已解除；私有解释默认关闭、不改凭据、不外传存档、不自动推送／PR／部署／
Library／Drive／邮件的边界不变。本阶段无子 agent、真实模型、embedding 权重下载或外部上传。
