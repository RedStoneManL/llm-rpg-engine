# llm-rpg-engine

[English](README.md) | **中文**

一个由 LLM 叙事、由 harness 维护事实与记忆的 RPG 引擎。玩家负责探索和行动；引擎在后台管理人物、地点、暗线、世界时钟和事件历史。

2026 年 10 月改进了原子行动提交、整回合撤销、视角可见性、长期记忆与事实索引，加入 DeepSeek 原生适配、可复现的风格包随机提示，以及可选资源裁定。原有创世、阵营、后台演化和多题材场景包继续保留。

**继续开发先读：[交接说明](docs/HANDOFF.md)、[实现契约](docs/action-integrity.md)、[图文报告](docs/reports/2026-10-03/report.html)。** 报告在克隆后用浏览器打开，含前后架构图、Claude 建议复核、四轮实测及失败样本。

## 在另一台机器运行

需要 Python 3.10+、Bash 与 Git。

```bash
git clone https://github.com/RedStoneManL/llm-rpg-engine.git
cd llm-rpg-engine
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine"
cp deepseek.env.example "${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine/deepseek.env"
chmod 600 "${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine/deepseek.env"
# 编辑上述文件，填入自己的 DEEPSEEK_API_KEY。
./run-deepseek.sh --campaign ./campaigns/my-adventure --flavor isekai
```

也可以直接提供环境变量 `DEEPSEEK_API_KEY`，或使用 `RPG_DEEPSEEK_ENV` 指定配置文件。默认模型为 `deepseek-flash`；`--help` 无需密钥或联网。旧智谱 Coding Plan 入口仍为 `./run.sh`，配置参考 `.env.local.example`。

空存档目录触发创世，已有目录继续冒险。用 `--pitch` 描述世界，`--flavor classic|isekai` 选择场景包，`--verbosity` 与 `--style` 调整文风。自定义创世、角色卡和世界书见 [创世指南](docs/genesis-blueprint.md)。游玩内用 `/help` 查看命令。

密钥和个人存档不进入 Git；要继续既有故事，需在旧进程关闭后另行复制完整存档目录。

## 这一版改变了什么

一次行动的资源裁定、正文、事实和立即发生的后台事件，先暂存、校验，再一次性写入 SQLite。写入失败不会留下半套世界，也不会推进对话。撤销覆盖整次行动。JSONL 是可重建导出，摘要和聊天记录是可重建记忆。

叙事与工具读取共同的 POV 视图；每轮刷新当前事实。近期聊天、分段摘要与原始事件分层保存。随机提示按存档种子、行动和地点抽取，保持撤销后的可复现性；题材、文风、提示表和资源定义仍来自场景包。

资源模块把自然语言意图交给模型识别，把余额计算与不足拒绝交给 Python。明确等待转换为绝对日期和时段再校验。后台目前随玩家行动同步运行，尚无独立的离线世界服务器。

## 实测与剩余问题

2026-10-03 验证：**1712 项测试通过**；120 回合故障恢复；7 份旧档兼容；最终两种题材、16 回合真实 DeepSeek 游玩。16/16 存盘回放、8/8 资源检查、2/2 明确等待终点通过。

**叙事语义还没有全面通过。** 模型仍可能编造既往细节、混淆物品归属和日期，甚至在交易已拒绝时写出退款。现在的资源规则覆盖明确支出与消耗；奖励、收入、偷窃、完整交易、物品转移还需要效果裁定。随机提示不能保证文学质量，账本正确也不等于故事完全自洽。

```bash
python -m pip install pytest numpy
python -m pytest
python scripts/verify_world_continuity.py --output-dir ./campaigns/endurance-check
```

默认测试不调用付费模型。目录与领域职责见 [模块地图](docs/MODULE_INDEX.md)，接下来的任务和验收条件见 [交接说明](docs/HANDOFF.md)。

## 许可

[MIT](LICENSE) © 2026 Xingyu Liu
