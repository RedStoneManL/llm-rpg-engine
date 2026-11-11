# 6.1 Terra DM 原生实测

用户于 2026-11-11 指定 `gpt-6.1-terra` 担任 DM。实验用指定型号的原生 agent 逐份处理正式引擎请求，通过本地文件交换响应；不是远程 HTTP API 压测，也没有使用预写故事替代生成。

`run.py` 使用正式 `PlayerDashboard`、默认 `GroundedAuthorStrategy`、默认修复次数、真实 SQLite 与游戏工具循环。没有 patch 或关闭后台 hook。第三方 DSH 代码没有接入这次运行，embedding 沿用本环境默认的 none。

每份请求保存在 `requests/NNN.json`，包含原始 messages、tools、调用位置与哈希。选定 DM 只能读取当前请求，写入对应 `responses/NNN.json`；工具调用由引擎执行。响应不会由主代理手工改写。作者、正文审查、摘要、后台模型调用都使用同一 Terra，因此正文审查不是独立模型盲评。

## 运行方式

```bash
python experiments/terra-live/run.py --out /path/to/fresh-evidence --campaign /path/to/fresh-campaign
```

调用指定的模型处理 `pending.json`，按其中消息和工具定义生成 `message`，原子写入：

```json
{"request_sha256":"从请求复制的哈希","message":{"role":"assistant","content":"模型原始回答"}}
```

工具调用使用 OpenAI 兼容的 message.tool_calls 结构；由实际 `_run_tool_loop` 执行。每份请求等待上限 1500 秒，单次运行最多 70 个请求。出现 `done.json` 后停止。该脚本不自行调用其他模型，不静默降级为 fake。

报告生成：

```bash
python experiments/terra-live/review.py docs/verification/terra-live-20261111/continued
```

## 本轮证据

- 初跑：4 个行动成功，第 5 个行动拒绝；12 个真实模型响应。原始脚本保存在 `driver-initial.py`，可核对 manifest 的哈希。
- 修后从同一失败前存档续跑：7 个行动成功，另执行一次撤销；23 个真实模型响应。
- 合计：11 个实际提交的剧情行动，35 份真实模型请求／响应，1 次模型主动游戏工具调用。撤销后的活跃历史不包含被撤销的约定。
- `exact-failure-replay.json`：原失败响应保持原样，在克隆存档重放；修前拒绝，修后落库。这里使用已录制响应，不能计为又一次模型生成。
- `full-regression.xml`：2276 passed、1 deselected。随后补充新公开地点与已有抵达人物两种边界，`speaker-extra-regression.xml` 的 11 项专项全部通过；运行时代码未再改变。
- 失败记录和成功续跑分别保留，没有用新样本覆盖失败样本。

后续生成的 HTML 是用户阅读报告，不提交生成文件；原始证据、评审数据及生成器保留在仓库。
