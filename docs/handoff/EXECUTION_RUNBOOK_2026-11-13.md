# Codex 接手运行手册（2026-11-13）

本手册只交接已有执行方法与缺口；本次迁移没有继续实现功能、启动真实模型或完成百回合测试。仓库根目录是下列命令的工作目录。分支入口、当前提交及 WIP 保存方式以同目录主交接文件为准。

## 1. 当前能恢复什么

| 对象 | 当前证据 | 接手时的边界 |
| --- | --- | --- |
| 生产基线 | `91646fb`，RCV-139 | 有 100 回合**确定性替身**耐久结果，不是 100 回合真实 DM |
| DSH 组件消融 | `9acb6f3`；`experiments/dsh-validation/`、`docs/verification/dsh-validation-round1/` | 无模型调用；未启动完整 DSH 宿主 |
| Terra 真跑及修复 | `59b4117`；`experiments/terra-live/`、`docs/verification/terra-live-20261111/` | 11 个已提交剧情行动、35 份模型响应，含初跑失败、修复续跑、撤销；不是一次无故障连续通过 |
| 真实 100 回合 | 迁移前只有设计方向和导演去重 WIP | 尚无完成的 20 / 30 / 100 回合真实生成证据，不得引用 RCV-139 代替 |

根目录 `CODEX_CLOUD_HANDOFF.md` 已改为本轮入口；旧 `docs/progress/CODEX_CLOUD_HANDOFF.md` 含很多历史基线与已被后续用户指令替代的排序/暂停规则。必须先读本轮最新入口，再把旧文件当作背景材料。

## 2. 新环境准备与离线基线

先确认分支、工作树和必要祖先，保留新环境原有未提交改动，不执行破坏性 reset：

```bash
git status --short
git branch --show-current
git log -8 --oneline
git merge-base --is-ancestor 91646fb HEAD
git merge-base --is-ancestor 9acb6f3 HEAD
git merge-base --is-ancestor 59b4117 HEAD
```

源码不是已经完备的 Python 发行包：`pyproject.toml` 当前只有 pytest 配置；`requirements.txt` 只列 Langfuse，NumPy 实际被运行时导入却只列在 `requirements-dev.txt`。暂用仓库根目录直接运行，不把 `pip install -e .` 当作已验证安装入口。

已用过的核心版本是 Python 3.12.14、pytest 8.4.2；新环境仍需自行执行验收。可创建独立环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pytest==8.4.2 numpy pyyaml
.venv/bin/python -m pytest -o "addopts=-m 'not slow'" -q
```

这些是待执行命令，不是本轮重新安装或重新全量通过的声明。2026-11-13 抽查系统默认 Python 为 3.12.14，默认解释器没有 pytest / Langfuse；不要因 `python3` 存在而假定它就是以前的测试 venv。NumPy、PyYAML 版本不是永久锁定依赖。

完整 `requirements-dev.txt` 还含 FastEmbed、代码图工具，不是核心离线 gate 的前提。默认 embedding 为 `none`，不要为恢复上下文先下载权重。SQLite 需支持 FTS5 / trigram 和 JSON 功能；缺少时应报告环境差异，不能删去覆盖测试。历史 `full-regression.xml` 是 2276 passed、1 deselected，后续另有 11 项 speaker 边界专项；不可把 XML 的历史成绩称为新环境通过。

可单独复核有代表性的旧证据，不会调用真实模型：

```bash
.venv/bin/python -m pytest -q tests/app/test_same_turn_speaker.py
.venv/bin/python -m pytest -o addopts= -q experiments/dsh-validation/test_own_boundary.py tests/app/test_history_sources.py tests/app/test_explicit_speech.py
.venv/bin/python docs/verification/rcv139/world_endurance.py
```

最后一条使用临时新存档和固定作者/后台响应，仅检验机制。`--demo`、`FakeLLMProvider`、录制响应重放、固定背景回复都不能算真实 DM 游玩验收。

## 3. DSH 组件实验复现

第三方仓库不作为游戏运行时依赖，也不随本仓库打包复制。`experiments/dsh-validation/plan.json` 保存完整上游 commit；`runner-dependencies/package-lock.json` 固定隔离测试依赖。

历史环境为 Node v24.22.1、Python 3.12.14 / Linux。需要 Git、npm；setup 会使用指定的 pnpm 12.3.4，网络需能访问对应 GitHub 仓库及包注册表。选择**新的结果目录**，不要覆盖第一轮原始证据：

```bash
.venv/bin/python experiments/dsh-validation/run.py \
  --upstream-root ../rpg-dsh-validation-upstream \
  --results ../rpg-handoff-runs/dsh-rerun-01 \
  --python .venv/bin/python \
  --setup
```

注意：该 runner 在不同 cwd 启动 Python，`--python .venv/bin/python` 仅在本引擎任务 cwd 为仓库根目录时成立；其当前 Python 子任务均如此。若修改 runner 的 cwd，请传入实际解释器绝对路径，而不是沿用旧机器的路径。已有隔离环境可以移除 `--setup`；加 `--groups probes,own` 只跑独立消融和本引擎对照。`--verify-only` 只检查源码版本并写 provenance。

setup 禁用生命周期脚本，使用冻结锁文件；不自动 reset dirty / 错版本上游。若网络受限，只能说明未重跑，不能把缓存 JSON 当作重新执行。上游测试 pass 表示其断言通过；真正的机制准入看 `observations.json` 的 gate 和 `docs/designs/EXTERNAL_MECHANISM_ADMISSION.md`。

## 4. 6.1 Terra 真跑的真实调用方式

既有实验**不是一个 `--model` 参数就能自动联网推理的脚本**。`experiments/terra-live/run.py` 用 `NativeProvider` 将正式引擎请求投递到本地文件；当前平台中明确选择 `gpt-6.1-terra` 的 agent 读取请求并写回响应。它随后经正式工具循环、校验、SQLite 和后台流程执行。

迁移前须区分三件事：

1. 字段 `NativeProvider.model = 'gpt-6.1-terra'` 只是记录标签，不自行选择或认证模型。
2. request / response SHA-256 证明请求内容对应，不证明响应来自某一型号。新环境必须保留宿主可验证的模型选择记录。
3. 新 Codex 环境可能没有同样的可选模型 agent 能力。若没有可验证的 6.1 Terra 调用入口，应明确报告这个具体缺口，继续离线工作；不能用主代理、其他模型、固定回应冒充，也不能擅自切换付费 API 或把模型名猜成可用 API ID。现有桥接脚本不需要 API 密钥，不等同于“新环境真实调用一定免费/不耗任何额度”。

### 新的短场景

在**不存在**的 campaign 目录与全新 evidence 目录运行；先确认选择了正确模型并有 agent 负责邮箱响应，否则脚本只会等待：

```bash
.venv/bin/python experiments/terra-live/run.py \
  --out ../rpg-handoff-runs/terra-live-01/evidence \
  --campaign ../rpg-handoff-runs/terra-live-01/terra-live-campaign
```

引擎每次产生 `requests/NNN.json` 与 `pending.json`，其中包含原始 messages、tools、kwargs、调用位置、行动号与哈希。DM agent **只读取当前投递的请求**，不可额外读取 GM 存档、未来脚本行动、预期答案、其他角色私密状态或评审结论。模型需要工具时返回原始 `tool_calls`，交给引擎真正执行，不能自行杜撰工具结果。

agent 将对应响应原子写到 `responses/NNN.json`：

```json
{
  "request_sha256": "从当前请求逐字复制的哈希",
  "message": {"role": "assistant", "content": "模型原始响应"}
}
```

工具请求使用 OpenAI 兼容 `message.tool_calls` 结构。写临时文件再 rename，避免引擎读到半份 JSON。不得手工润色模型正文或把协议错误修成成功；原样留证后走引擎自己的修复路径。

当前脚本每请求最多等 1500 秒，每次运行最多 70 次请求。行动列表是 12 项（11 个剧情行动、1 个 `undo_and_reopen` 控制项）；第 7 项前做冷重开。这**不是 100 回合驱动器**。`done.json` 仅说明进程结束，须看 `failed`、`failure.json`、`result.json`，不能把有 done 当通过。

### 失败后续跑

当前参数只支持保留失败现场后，从**同一正确检查点存档**继续：

```bash
.venv/bin/python experiments/terra-live/run.py \
  --out ../rpg-handoff-runs/terra-live-01/continued \
  --campaign ../rpg-handoff-runs/terra-live-01/terra-live-campaign \
  --resume-from ../rpg-handoff-runs/terra-live-01/evidence \
  --start-action 5
```

这里的 `5` 只对应历史那次第 5 项失败；新失败必须根据实际已提交事件和行动收据计算。脚本只检查 `resume-from/failure.json` 存在，没有验证 campaign 与该失败现场一致，操作者必须核验。不要把已经完成续跑的最终存档直接配给初始失败目录再次从第 5 项执行。

当前每次运行请求号从 000 重新开始，因此必须使用全新输出目录。`action_id` 固定为 `terra-live-N`，不能把这套 ID 原样拿来给同一存档新增另一轮测试；续跑同一行动时则应保留稳定 ID，避免重复提交。

## 5. 历史存档和原始证据的可迁移性

源码与请求/响应已在 Git 内；迁移前实际 campaign 位于仓库外的 `../terra-live-campaign/`。2026-11-13 只读盘点：`events.db` 204800 字节，`events.jsonl` 42059 字节；SQLite integrity 为 `ok`，96 条事件、revision 15、有效最大 turn 10。这里的 turn 10 不与“11 次提交且撤销一次”矛盾。

原始 DB 除 events 还有 `action_receipts`、`player_inputs`、`speech_names`、`action_result_metadata`、FTS 等表。`continued/events.json` 和 JSONL 只有事件，**不能当作完整 SQLite 存档的等价备份**。本轮已保存 SQLite backup 后 gzip 压缩的完整快照：

- `docs/handoff/artifacts/terra-live-campaign-2026-11-11.sqlite3.gz`
- `docs/handoff/artifacts/terra-live-campaign-snapshot.json`（SHA-256、表行数、恢复核验元数据）

恢复到新目录后工作，不能改写归档快照。`.gitignore` 默认忽略 `*.db`、`events.jsonl`，因此归档使用明确列入交接清单的 `.sqlite3.gz`，不能以任意本地文件存在推断已经推送。也可直接用 `python3 scripts/handoff/verify.py --restore-campaign ./campaigns/handoff/terra-live-campaign` 一次完成清单校验、恢复和 JSONL 重建。下列为等价的底层恢复步骤：

```bash
.venv/bin/python - <<'PY'
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

root = Path('docs/handoff/artifacts')
archive = (root / 'terra-live-campaign-2026-11-11.sqlite3.gz').read_bytes()
meta = json.loads((root / 'terra-live-campaign-snapshot.json').read_text())
assert hashlib.sha256(archive).hexdigest() == meta['archive_sha256']
payload = gzip.decompress(archive)
assert hashlib.sha256(payload).hexdigest() == meta['sqlite_sha256']
destination = Path('../rpg-handoff-runs/restored/terra-live-campaign')
destination.mkdir(parents=True, exist_ok=False)
with (destination / 'events.db').open('xb') as stream:
    stream.write(payload)
with sqlite3.connect(f'file:{destination / "events.db"}?mode=ro', uri=True) as db:
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 96
    assert db.execute('SELECT count(*) FROM action_receipts').fetchone()[0] == 11
    assert db.execute('SELECT count(*) FROM player_inputs').fetchone()[0] == 11
print(destination.resolve())
PY
```

SQLite 是权威。缺少的 JSONL 镜像可由 `EventStore.sync_jsonl()` 在恢复副本上生成，不需重造或重复追加事件。快照来自最终续跑完成状态；它不直接等于初跑第 5 项失败前的 checkpoint。

原生调用使用路径名派生 campaign seed（若没有显式 `campaign_seeded` 事件）；更换 campaign 最后一级目录名可能改变之后的导演抽样。复现历史行为时保留目录 basename `terra-live-campaign`，并核验实际 seed。新百回合试验应显式固定 seed，并把它作为实验元数据，而非依赖临时目录名。

### 生成历史回放 HTML 的路径陷阱

`review.py` 从 `continued/manifest.json` 的 `resume_from` 读取初跑位置。该 manifest 保存了旧工作区绝对路径。新 clone 直接执行旧 README 的 review 命令，可能只统计续跑的 7 个行动 / 23 次响应，漏掉初跑。**不要为修复展示而修改已归档原始 manifest。** 可在临时副本中重新定位：

```bash
.venv/bin/python - <<'PY'
import json
import shutil
import subprocess
import sys
from pathlib import Path

source = Path('docs/verification/terra-live-20261111').resolve()
target = Path('../rpg-handoff-runs/terra-review-copy').resolve()
if target.exists():
    raise SystemExit('Choose a new target; preserve prior review output')
shutil.copytree(source, target)
manifest_path = target / 'continued/manifest.json'
manifest = json.loads(manifest_path.read_text())
manifest['resume_from'] = str(target)
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
subprocess.run([sys.executable, 'experiments/terra-live/review.py', str(target / 'continued')], check=True)
print(target / 'continued/Terra_DM_Live_Replay.html')
PY
```

这只是临时报告重建，不能把修改后的 manifest 冒充原始证据。正确汇总应含 11 个已提交剧情行动、35 次响应。作者、正文审查和后台均用同一 Terra，因此不是独立模型盲评；没有 provider token usage，邮箱等待不能当 API latency。

### 原失败候选的机械重放

`replay_failure.py` 读取初跑 `responses/011.json`，通过 Git 中 `9acb6f3:context/capabilities.py` 对比修前校验，再在临时克隆 DB 里 rewind 到第 5 回合，使用原响应落库。它有意使用 `FakeLLMProvider` 装载录制响应，不是新增真跑。

把第一参数指向上述证据副本，第二参数指向主交接恢复出的 campaign 副本：

```bash
.venv/bin/python experiments/terra-live/replay_failure.py \
  ../rpg-handoff-runs/terra-review-copy \
  ../rpg-handoff-runs/restored/terra-live-campaign
```

脚本内部再用 SQLite backup 生成临时工作副本，不改源 DB，但会写第一参数目录的 `exact-failure-replay.json`，所以不能把原始证据目录作为输出。新环境 clone 需含 `9acb6f3` 对象；单提交浅 clone 不够。

## 6. 配置名与不允许的隐式降级

仅列配置名，不携带任何旧环境 secret 值：

| 用途 | 名称 |
| --- | --- |
| 一般运行时 provider | `RPG_PROVIDER`、`RPG_MODEL`、`RPG_BASE_URL` |
| 单独后台模型 | `RPG_CASCADE_MODEL`、历史别名 `GLM_CASCADE_MODEL` |
| 外部 provider 密钥 | `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`ANTHROPIC_API_KEY` |
| 文本与工具配置 | `RPG_CONVERSATION_MODE`、`RPG_NARRATION_VERBOSITY`、`RPG_NARRATION_STYLE`、`RPG_MAX_TOOL_ROUNDS` |
| 本地向量与调试 | `RPG_EMBEDDER`、`RPG_DEBUG`、`RPG_LOG_LEVEL`、`RPG_DEBUG_TRACE` |
| DSH 实验目录（runner 注入） | `DSH_VALIDATION_UPSTREAM`、`DSH_VALIDATION_RESULTS` |

真实测试必须明确 provider。低层 `build_engine()` 在没有传入 provider 时可能按环境默认 `fake`；不能因正常产生正文就认为调用了模型。NativeProvider 通过显式 provider 注入避开这个默认，但新的后台模型环境覆盖也必须核对，保证没有偷偷换模型。不得上传 `.env`、令牌、真实私人存档。

## 7. 下一 agent 恢复后的工作顺序

用户目前指令是停止在本 Work 环境继续实现并完成交接。到下一 Codex 任务确认继续后：

1. 核验代码、交接 WIP、合成存档和旧原始证据，跑当前分支离线 gate；不要重复研究已经有确凿证据的结论。
2. 先接续导演近期主题去重的未完成工作，并验证撤销/冷重开一致性；“接取既有暗线后仍可能降暗”只是本轮待复核假设，应先做最小反例，不能写成已确认或已修复。
3. 把短样本脚本扩展为分段可恢复、行动 ID 稳定、预算有界的连续长局驱动器；保存每阶段 checkpoint、请求/响应、事件、状态、耗时和失败现场。不要只把 70 请求上限调高然后称为完整方案。
4. 先到 20 回合，再到 30，最终连续 100 回合；段间审读人物连续性、来源、暗线实际后果和节奏，外加机械不变量检查。任何修复保留原失败证据，并说明哪些属于重放、重跑、旧分支与当前有效历史。
5. 真实模型需确认为 6.1 Terra，保留默认后台流程；扩大前核对资源预算。测试成功仅证明这组种子/场景经过验证，不能承诺任意百回合绝不会出问题。
6. 第三方机制坚持单项最小适配、消融、回归后小步合入。完整 DSH 安装、生命周期、取消/重启、模型桥接和 UI 仍是之后独立的验收任务。

当前手册没有把未完成 runner、DSH 插件或独立 NPC 决策描述为现有可运行能力。
