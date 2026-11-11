# DSH 候选机制验证实验

这里是独立研究代码，不被游戏运行时导入。按 plan.json 固定上游提交，直接运行上游实现及测试，再用 probes.test.mjs 的中文场景做消融。test_own_boundary.py 把同一信息隔离原则应用到我们的实际上下文与工具边界。

本轮真实模型调用为 0；使用真实 SQLite、文件系统和实际提示词组装。上游测试中的宿主／模型替身依照原测试执行。不能据此宣称已经验证完整 DSH 插件、NPC 对话质量、模型费用或 Windows 安装。

## 复现

需要 Node 24、npm、Git，以及安装了本项目依赖的 Python。上游源码留在仓库外；命令不会修改生产运行时，也不提交第三方源码。

~~~bash
python experiments/dsh-validation/run.py \
  --upstream-root /tmp/rpg-dsh-validation-upstream \
  --python /path/to/rpg-venv/bin/python \
  --setup
~~~

已有环境可省略 --setup。--groups probes,own 只重跑独立消融和本引擎对照；--verify-only 只核对源码版本并输出哈希。默认覆盖两个项目本轮选定的上游测试、独立消融和本引擎回归，并保存每组的日志、退出码和结构化报告。

setup 使用附带的 package-lock.json 通过 npm ci 安装测试依赖；生命周期脚本关闭。Agentic Roleplay 使用自身冻结的 pnpm 锁文件，LikeTavern 的 state 测试使用最小 Vitest 配置，不加载其 host/UI 兼容垫片。依赖补齐只影响隔离目录，不替换被测实现。已有 checkout 的提交不符或 tracked 文件改动时拒绝运行，不自动 reset。

## 怎么读结果

- probes-tests.json 绿色：测量和负对照按预期执行。
- observations.json 中每个 gate：候选机制实际是否可进入下一阶段。绿色测试不等于准入。
- truth_noninterference 的 blocked 表示已经找到反例：同一角色可见资料下，幕后文本能影响召回。
- no_summary_expansion 用 spy 只关闭实际 WAL 调用的摘要展开函数；其余文件、事务和回滚代码照常运行，并断言该干预确实被调用。
- memory_policy 的各组共享 recall=4、associate=0；default 组沿用默认遗忘参数，no_decay 只关闭 flaky。未提供 embedding 或 reranker。
- belief_evidence 的消融只绕过 proposalProblems；保留真实写入校验，因此可以看到防御层之间的冗余。
- Counterfactual 测试比较数据边界，不把硬编码回答算作真实模型体验。

开发场景的变化必须更新 plan.json 并保留解释；不能把修到通过的场景再次称作未见过的保留集。报告中的上游 holdout 是作者自己的命名，不是我们独立设计的盲测。

结果报告：../../docs/verification/DSH_VALIDATION_ROUND1.md
准入规则：../../docs/designs/EXTERNAL_MECHANISM_ADMISSION.md
