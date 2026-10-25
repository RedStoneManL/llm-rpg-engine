# RCV-119 — 解除 HOLD 后的当前环境全量评估

日期：2026-10-25 UTC。源码基线：`e69029a`（RCV-118）；接续分支
`handoff/codex-cloud-20261025`。这是覆盖主要模块的重新评估，不是宣称整个产品或所有历史设想均已验收。

## 结论与授权

用户已明确要求“不用之前的 hold，重新全量评估”。B1 detail retrieval 与 profiling 不再是授权阻塞；
可以开展本地实现、测试与指定的 Luna medium 受控真实生成。换环境本身不证明旧接口已经具备访问控制。
不改凭据、不导出用户存档或密钥、不部署、不 push/main/PR、不上传 Library/Drive/email，私有解释默认关闭。

当前能继续推进，不需要用户再次解除旧 HOLD。仍有两项局部环境缺口：远程 provider API 的凭据未配置；
本机未安装可执行 Chromium。它们限制对应验收，不阻止本地来源协议、FTS、向量范围过滤和结果先行开发。

## 环境与执行证据

- Python **3.12.14**，独立 venv，pytest **8.4.2**；NumPy、PyYAML 可导入，`pip check` 无冲突。
- SQLite **3.53.1**，实际建立 `fts5(... tokenize='trigram')` 成功；不需要外部索引服务。
- Python fastembed 未安装，未下载权重；现有唯一 slow real-embedding 测试继续不运行。
- Node 和运行时 Playwright 模块可用，但其 Chromium 可执行路径不存在；PATH 也无 chromium/chrome。
  Python Playwright 未安装。没有把旧 Chromium 报告说成本环境新跑过。
- 仅检查标准 provider 环境变量是否存在（不读取或输出值）：OpenAI、DeepSeek、Zhipu、Anthropic 均未配置。
  可用原生模型为 `gpt-6-luna` medium；两份真实生成及生产请求回放见 RCV-118，不能替代 HTTP API 验收。
- 本环境恢复基线 **2107 passed / 1 deselected**；当前 RCV-118 完整 gate **2142 passed / 1 deselected**，
  **90.51s**。RCV-119 只增加评估脚本与记录，未更改生产代码；没有无理由重复同一全量 gate。
- 本轮未调用浏览器插件、真实外部 API、外部存储或 embedding 下载。性能与反例仅使用新建临时合成 campaign。

## 主要模块复核

| 模块 | 已实现／本轮证据 | 真正缺口与判定 |
| --- | --- | --- |
| EventStore／回合事务 | 当前完整回归覆盖 SQLite publish、revision、receipt、取消、后台 savepoint、撤回和冷恢复 | 底座可复用；不是多用户生产服务／无限规模承诺 |
| 事实与知识 | POV 投影、普通作者写权限、明确交流 receipt、resource arithmetic 均已有实现和回归 | 不能把旁白、摘要或玩家意图直接当 NPC 已知／世界事实 |
| 旁白与摘要来源 | RCV-115 保留未摘要原文；116/117 校验分段／递归来源；118 独立有界维护 | 来源 hash 不证明语义；旧未绑定来源不补造；无常驻重试调度 |
| 玩家原始输入 | 指纹存 hash；明确交流的 statement 有独立 receipt；可选 transcript 在提交后另写 | 没有完整、与成功回合同事务的原输入档案；历史缺失不可反推 |
| B1／逐步查证 | 现有工具 registry 与调用循环可复用；当前六个工具没有 locate/open/neighbours | `NarrativeSystem` 不实现历史 recall；模型／玩家现有 recall 不能查回任意旧旁白原文；引用在展示层被丢弃 |
| SQLite Archive／FTS | 实际建库与关键词检索可用 | scene+turn 键会重用；短查询 `%` 当 LIKE 通配符；不是可靠不可变来源句柄 |
| 向量检索 | NumPy cosine 与 SQLite 存储可运行 | search 无候选范围；语义分支绕过 day/entity 过滤；没有 actor/revision/活动分支 ACL，不能直接接成授权记忆 |
| 历史可见性 | 当前 player recall 使用 POV redaction；私有解释默认关闭 | `public_event_history` 仅排除私有解释，不是通用 actor ACL；canonical secret 可在宿主 DTO 中存在，不能直接对外暴露 |
| 结果先行正文 | 登记资源已有先解析意图／确定性裁定／余额约束，不能重复重写扣款 | 全部行为的结果→最终正文链未完成；Hybrid 冻结 prose，结构修复不保证正文同步 |
| 长期世界与 NPC | director/cascade/catch-up/任务与撤回有本地实现 | 后台依附前台回合同步运行；不是离线常驻世界，也没有完整 NPC 自主意图→裁定→行为闭环 |
| GUI／运维 | CLI、WSGI／dashboard 已随完整 Python suite 回归 | 未重跑真实浏览器；无远程站点／账户／多人／部署；这些不是当前首项 |
| 真实模型质量 | Luna 两请求真实生成，原响应精确回放并保存来源、冷重开验证 | 样本小，存在日期语境合并；未验证连续长期剧情、agentic tool 选择或远程 provider transport |

## 实测反例（不只读旧报告）

复现脚本：[assess_local_engine.py](../../scripts/assess_local_engine.py)。原始结构化结果：
[rcv119-assessment.json](rcv119-assessment.json)。运行：

```bash
.venv/bin/python scripts/assess_local_engine.py docs/verification/rcv119-assessment.json
```

主 profiling 后增加了语义过滤与宿主 DTO 反例，单独重跑 `gaps()` 更新 gap_probes，未修改既有实测时间。

1. Archive 同 scene+turn 写旧分支再写新分支：ID 相同；用旧 ID 读到了 `NEW_BRANCH_QUOTE`。
   这是旧存储的替换语义，不是新引入回归；不能作为未来可引用原文身份。
2. `fts_search('%')` 在没有字面百分号的历史也返回 1 条。短词 LIKE 未转义，需统一字面查询契约。
3. `recall(... day=1, entity='阿岚', k=1)`，FTS 无命中，语义最高分属于另一天／另一个人，实际返回
   `day=2, entities=['船主']`。可立即修复的共享边界：语义排序前与 FTS 使用相同候选范围。
4. 在带秘密事实的临时世界，宿主 `Engine.public_history()` 包含秘密值；当前 `player_recall()` 无命中。
   这证明宿主 helper 不能直接充当未来源材料 ACL，不声称现有 GUI/API 已泄漏秘密。
5. Hybrid 策略原 prose 为“你付清船费，已经乘船抵达对岸。”，结构修复后 moves 从有移动改为空，
   prose 原样保留。这是策略层确定性反例，未将它说成真实模型或已发布回合的证据。
6. 100／1000／5000 条旁白都包含 `原文定位词0`，当前 player recall 对该词均 0 命中。
   因此非常快的 player recall 时间不是历史搜索性能合格的证明，它根本没有搜索该叙事历史。

## Profiling 实测

每规模新建合成 campaign，3 个计时样本取中位数，随后第 4 个回合运行 cProfile。原始样本全部保留。
旁白每条约 300 字，故意不预先摘要以模拟长期服务故障；真实 `run_turn` 采用固定前台策略与本地摘要替身。
不是普通作者模型延迟、全混合图谱负载、token 数、语义召回率或服务性能承诺。

| 初始旁白数 | snapshot + project | 冷重开 | 本地策略 run_turn | 未摘要 context 字符 | FTS 查询 | 64 维人工向量查询 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 1.850 ms | 2.732 ms | 19.629 ms | 32,232 | 0.489 ms | 0.329 ms |
| 1,000 | 18.553 ms | 21.416 ms | 131.715 ms | 321,132 | 1.886 ms | 4.608 ms |
| 5,000 | 149.389 ms | 133.210 ms | 863.808 ms | 1,613,132 | 8.698 ms | 23.846 ms |

cProfile 各规模的单次完整回合有 **12 次 project**，热点包括重复历史扫描／反序列化、deepcopy 和 publish
前校验。cProfile 本身会增加时间，不能把其累计耗时与未插桩 wall-clock 相加。FTS/向量计时不包含建库与
逐项插入成本，向量为确定性数字数组，没有加载任何 embedding 模型。

短期更显著的问题是未摘要原文无界增长而非索引查询耗时；先建可信原文读取与上下文预算协议，不能直接截掉旧原文
来美化测量。之后可评估 revision-scoped 投影复用，必须保留撤回、跨实例、失败回滚和最终 preflight 正确性。

## 重新排序后的执行项

1. **马上执行：检索范围一致性。** 修复 FTS／语义 day/entity 候选分歧，向量排序前限定候选；覆盖空集合、
   高分越界、低分合规、冷重开和 FTS 降级。它是 B1 可复用底座，不能冒充 actor ACL 或完整 agentic recall。
2. **P0：原输入来源与安全阅读集合。** 成功回合同事务保存原 input，明确 actor/证据类型；旁白、receipt、
   fact、私有解释分别建授权集合。复用 EventStore snapshot；引用绑定 campaign/actor/revision/活动事件＋内容。
   原始历史只交宿主；模型只拿经过授权的目录与不透明 source handle。旧档缺失返回不可查证。
3. **P0：结果先行。** 复用已有 resources resolution；将移动／交接／交流的结构裁定与最终 prose 分离，
   prose 修复不得重新扣款或产生新效果。现有 Hybrid frozen-prose 路径需实质修改与反例回归。
4. **P1：B1 agentic loop。** 对接现有 tool registry，locate→open→前后文→重新检索，并验证引用确实读过。
   先关键词／时间浏览，向量只是候选；真实 Luna 验证缺失原话时不补造、摘要漏词时能回源。
5. **P1：长期运行。** 安全读协议建立后再做预算与积压策略、投影缓存；全局定时世界与 NPC 闭环另切片。

没有继续沿用历史 HOLD，也没有因为解除暂停就开放未经实现的 raw-store 工具。环境限制只影响对应验证，
工程缺口可以继续本地完成。以上第 1 项作为下一实际修复，其余保留为明确未完成的设计／实施项。
