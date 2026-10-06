# 继续开发与跨环境运行

本次交付包含完整应用源码、风格包、回归测试，以及 2026-10-03 的架构对照与真实模型实验。公开仓库沿用 [RedStoneManL/llm-rpg-engine](https://github.com/RedStoneManL/llm-rpg-engine) 的历史，最新交付在 `main`。

## 在新环境启动

需要 Python 3.10+、Git、Bash；核心使用 SQLite 与标准库 HTTP 客户端。

```bash
git clone https://github.com/RedStoneManL/llm-rpg-engine.git
cd llm-rpg-engine
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine"
cp deepseek.env.example "${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine/deepseek.env"
chmod 600 "${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine/deepseek.env"
# 编辑上面的配置文件，填写自己的 DEEPSEEK_API_KEY。
./run-deepseek.sh --campaign ./campaigns/my-adventure --flavor isekai
```

也可以直接提供环境变量 `DEEPSEEK_API_KEY`，或用 `RPG_DEEPSEEK_ENV` 指向自己的配置文件。配置文件存在时会加载其中的变量。`--help` 不需要密钥或网络连接。默认模型为本次实测的 `deepseek-flash`，可通过 `DEEPSEEK_MODEL` 覆盖。

`--campaign` 指向空目录会创世，指向已有目录会读档。需要延续本机旧冒险时，关闭旧进程后自行复制完整存档目录；个人存档不在 Git 仓库里。公开 `run.sh` 保留智谱 Coding Plan 入口，配置示例见 `.env.local.example`。

## 先读哪些文件

1. [图文报告](reports/2026-10-03/report.html)：下载仓库后用浏览器打开；包含前后架构、Claude 建议复核、真实游玩失败与改进结果。
2. [实现契约](action-integrity.md)：事务、撤销、版本、记忆、资源与风格包的具体边界。
3. [模块地图](MODULE_INDEX.md)：原有各领域如何连接；2026-10-03 新增的资源裁定、视角过滤、随机提示以实现契约为补充。
4. [真实游玩复核](reports/2026-10-03/evidence/implemented-live-review.json)：下一步工作的直接依据。

报告与 evidence 是当时实验的记录，部分路径指向原机器，原始散列对应 2026-10-03 的代码。2026-10-06 打包仅另作跨环境启动和文档整理；报告中指向当前源码的链接已迁移到仓库内。`changes.patch` 为当时相对内部开发基线的补丁，**不要再应用到当前仓库**。

## 已完成与已验证

- 一个玩家行动、叙事和立即发生的后台事件先暂存，校验后统一提交；失败不推进对话。SQLite 是事实来源，JSONL 是可重建导出。
- 回合撤销覆盖整次行动；写入版本与回执用于冲突检测。回执只对同一事件批次幂等，不是请求重试队列。
- POV 读取视图、事实索引、每轮状态刷新、分块摘要和近期对话缓存。
- DeepSeek 原生结构输出、完整工具消息组、真实创世失败门控。
- 按场景包定义的可复现随机提示，以及可选资源支出/不足拒绝、明确等待终点裁定。

2026-10-03：1712 项测试通过、1 项 slow 测试按默认配置排除；120 次确定性行动包括 12 次写入故障、10 次完整撤销和 8 次重开。7 份旧档副本事实历史一致，原存档未修改。

最后一轮 DeepSeek 实验：两种题材、16 次行动、75 次 API 调用、264797 tokens。16/16 存盘回放、8/8 资源期望值、2/2 明确等待终点、2/2 重开与撤销通过。完整同步行动中位数 8.05 秒，不是首字延迟。API 输出、前三轮失败及人工复核均保留在报告目录。

2026-10-06 发布副本再次通过 1712 项测试（1 项默认排除）、120 回合故障恢复及跨环境启动检查；详见 [发布验证](reports/2026-10-06/release-verification.json)。本次打包未重新调用付费模型。

## 下一步的明确问题

1. **叙事与效果一致性**：不足交易虽然正确拒绝并保持余额，正文仍可能写出退款/找零。扩展结构化效果与对应叙事校验，验收时同时读账本和正文。
2. **物品归属与承诺**：模型可能编造出借人、混淆蓝伞持有者与归还日期。建立物品转移及带绝对日期/对象的承诺模型，测试重开、摘要后和隔多回合的精确召回。
3. **资源系统完整性**：目前只覆盖明确支出/消耗与等待。奖励、收入、偷窃、完整交易、战斗伤害需要各自的效果裁定；已注册余额不能被自由改写。
4. **真正长局与后台演化**：现在的后台随玩家行动同步运行。异步任务需要先保证版本冲突、过期结果与恢复语义，再增加任务队列；不能先展示尚未提交的结果。
5. **多题材质量**：继续保留场景包、风格和通用约束的边界。需要更长局与盲评；当前 16 回合和单个秘密探针不足以证明所有语义与秘密都可靠。

随机提示保证可复现抽签，不保证模型正文完全相同或一定更好看。事实账本正确、可重放也不代表整篇故事已经自洽。

## 运行验证

```bash
python -m pip install pytest numpy
python -m pytest
python -m pytest tests/test_action_integrity.py tests/test_world_continuity.py tests/test_resources.py
python -m app --help
./run-deepseek.sh --help
```

完整开发工具依赖见 `requirements-dev.txt`；图表工具可能还需系统 Graphviz。默认测试不调用付费模型。可复现的 120 回合故障恢复实验见 `scripts/verify_world_continuity.py`，结果写到自己指定的输出目录：

```bash
python scripts/verify_world_continuity.py --output-dir ./campaigns/endurance-check
```

禁止用原存档作写入实验；禁止用重新生成的模型正文充当历史重放结果。新功能必须保留失败样本，并区分结构正确、状态正确与叙事正确。
