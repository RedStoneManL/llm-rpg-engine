# llm-rpg-engine

[English](README.md) | **中文**

**一个事件溯源、由大模型驱动的桌面跑团(TRPG)引擎 —— 一个由模型叙事、由框架(harness)守住底线的活世界。**

核心思路:把**完整的叙事自主权**交给大模型,再用一套**确定性的框架**给它画好边界 —— 一份只追加的事件日志、按种子掷出的暗骰、模型在物理上无法越过的战争迷雾、以及一道严格的提交/修复闸门。**模型负责写故事,引擎负责保证世界始终自洽、可复现、可回溯。**

> 状态:**v0.2** —— 一条完整可玩的闭环(`定义或掷出一个世界 → 逐回合游玩 → 世界自行反应 → 终局 → 回溯`),并在「模型全包」的默认之上叠加了**玩家可定义开局**(蓝图文件 / 交互式 session-zero / 酒馆导入)。约 1540 条离线测试,已用真实推理模型(GLM / zai `glm-5.1`)实机验证。

---

## 它有何不同

- **事件即真相。** 每一次改动都是一条只追加的事件;世界是事件日志的纯函数投影。重放逐字节一致,所以你可以 `rewind` 任意多个回合,整个世界(事实、关系、NPC 信任、任务)都会确定性地回卷。
- **是活世界,不是剧本。** 每回合的隐藏暗骰(按种子)推进后台暗线,把区域级事件沿嵌套地点向下波状传播(波状传播),让幕后阴谋在世界时钟上自行酝酿 —— 哪怕玩家没在看。
- **框架强制的战争迷雾。** 叙事者只能通过**只读的 POV 工具**查询世界,这些工具**在物理上无法返回**视角角色不知道的东西。秘密不会泄露,不是因为提示词求模型嘴严,而是因为那个工具根本不会把它捞出来。
- **模型写故事,引擎掌结构。** 神谕(oracle)掷骰决定*数量、复杂度、结构*(确定性、可回溯);大模型只负责写*文字和内容*。这是对抗「模式坍缩」的解法 —— 用骰子给出的、具体而互异的种子,让模型在上面即兴发挥。
- **严格提交闸门。** 每回合模型返回叙述 + 一份结构化提交;校验器逐字段把不合格的输出打回去(指名缺了什么),直到合规,再炸开成事件。

---

## 一段实录

一个由 pitch `东方武侠悬疑` 引导出来的世界 —— 神谕掷出骨架(区域图、势力、带秘密的 NPC、隐藏暗线),模型写出了这个世界和这段开场:

> **沉疴渡·茶棚**
> 泥腥味先于一切涌进你的鼻腔。你踩上沉疴渡码头的最后一块跳板时,脚下的腐木发出一声闷响……碧落十三泽的水是浑浊的铁锈色,岸边浮着一层油膜般的光泽。正前方支着一间茶棚,四根柱子歪了三根。棚下坐着稀稀落落几人 —— 靠东角一个灰衣老者正低头拨弄一只粗陶药罐,他的左手只剩三根指头,断口处的疤痕已经发白。

NPC 出场就预埋了隐藏秘密(以 `secrecy="secret"` 的事实存储,路人层永远捞不到),例如:*「他是蚀骨城前城主的遗孤,隐姓埋名只为查明当年满门被屠的幕后真凶。」*

---

## 架构

```
玩家输入 ─► AuthorStrategy ──► 大模型(带 POV 迷雾工具) ──► 叙述 + 结构化提交
                 │                                              │
                 ▼                                       校验 / 修复闸门
            装配上下文                                          │
           (迷雾过滤后)                                         ▼
                                              to_events ─► 只追加 EventStore
                                                            │  (SQLite + JSONL 镜像)
                                                            ▼
                                                  project()  ──► World
                                              (事件流的纯折叠投影)
                                                            │
      ┌──────────────────────────────────────────────────┤  回合后钩子
      ▼          ▼           ▼          ▼          ▼        ▼  (隐藏 / 按种子 / 非致命)
   摘要/弧光  暗骰 director  §10 波状传播  补演    暗线暗骰   密度生成
                                                          世界时钟推进
```

- **微内核 + ContextSystem 注册表。** 内核本身对「跑团」一无所知。每个子系统是一个 `ContextSystem`,自行声明:它拥有哪些事件类型、如何把事件折叠进自己那一片世界、如何校验一段提交、如何注入/召回上下文。已注册的系统:`ontology`(双时态事实图)、`place`、`character`、`object`、`faction`、`knowledge`、`director`、`cascade`、`time`(世界时钟)、`narrative`、`scene`、`lore`(统一的暗线/任务线)。
- **双时态事实图(bitemporal fact graph)。** 事实同时带有效时间(游戏天数)× 事务时间(事件顺序),所以「第 12 天那时是真的吗」和「Alice *相信*什么」都是一等公民。知识就是知者自己的一条事实(`knows:{key}`)—— 这正是「源头侧迷雾」得以成立的原因。
- **统一暗线/任务线。** 一套任务模型,状态 `state ∈ {暗 hidden, 明 surfaced, 了结 resolved}`。暗线由引擎暗骰推进;明线由玩家 + 叙事者推进。线有游戏时间寿命、玩家离开时的休眠、以及给大线准备的有界「救世 vs 灾变」终局。
- **确定性回溯。** 所有骰子都是 `Oracle(scene_seed(campaign_seed, key, salt))`;重放路径里没有任何挂钟时间或 RNG。回溯 = 撤回事件 + 重新投影。

---

## 一回合如何运转

1. **装配上下文** —— 经迷雾过滤:叙事者看得到当前场景、主角所知、以及公共氛围知识;硬秘密在源头就被拦下。
2. **创作** —— 模型写叙述,并(通过原生 function-calling)可在提交前调用只读 POV 工具(`map_query`、`recall_query`、`characters_query`、`factions_query`、`ambient_query`)。
3. **校验 / 修复** —— 结构化提交按段校验;不合格就打回模型,直到合规。
4. **应用** —— 各段炸开成事件,追加进 store,世界重新投影。
5. **世界自行反应**(隐藏、按种子、各自非致命):后台摘要、暗骰 director、区域→子地点波状传播、离屏补演、暗线暗骰推进、密度驱动的新暗线,世界时钟推进。

给定战役种子 + 事件日志,一切都是确定性的,所以 `/rewind` 会逐字节重投影出更早的世界。

---

## 开局(世界生成)

`new_game` 跑一段半交互的**老虎机式**开局:神谕从一组小而可扩展的维度表(`data/oracles/genesis/`)里**去重抽取**来定下*结构* —— 一张宏观区域邻接图(钉死,以防之后地理漂移)、一个带场所的起始小镇、势力、开场 NPC、3–5 条战役暗线(+1–2 条绑主角)—— 模型则填上*内容*。进游戏前你可以整体重掷,或单独重掷某个叶子步骤。

### 玩家可定义开局

每个开局部分都是**可定义的** —— 你没定义的,模型来填。只有一份权威的 `GenesisSpec` 被 `new_game` 消费;各来源汇入它(优先级:交互 > 文件 > 导入 > pitch):

- **蓝图文件**(`--genesis world.yaml|json`)—— 指定任意子集的任意部分(`world_premise / regions / local_map / protagonist / factions / npcs / threads / opening`)。标量覆盖;列表**增量**(你给的条目保留,模型补到掷定的数量)。见 [`genesis.example.yaml`](genesis.example.yaml) 与 [`docs/genesis-blueprint.md`](docs/genesis-blueprint.md)。
- **交互式 session-zero** —— 引擎只追问最小必填项(`world_premise.genre` + `protagonist.name`,即「什么世界 / 你是谁」),问到填上或你输入 `/auto` 委托模型为止。「你要干嘛」**不是必填输入**:引擎总会在开场屏生成一个具体目标,而更深的弧光活在隐藏暗线里。
- **酒馆(SillyTavern)导入**(`--import-card card.json` / `--import-world-book wb.json`,`--card-as protagonist|npc`)—— 一个 **LLM 转换层**,把酒馆角色卡 / 世界书*翻译进*我们的原生 spec。引擎不跑酒馆那套关键词注入语义;它只在开局把自由文本读一次,产出结构化的部分。

```bash
# 在文件里定义一个世界,其余交给模型补
./run.sh   # 或: python -m app --campaign ./mygame --provider zhipu --model glm-5.1 \
           #            --base-url <url> --genesis genesis.example.yaml
```

---

## 快速开始

要求:Python 3.10+。核心无重依赖(标准库 + 一个轻量的 OpenAI 兼容 HTTP 客户端);见 `requirements.txt`。

```bash
# 1. 安装(建议用 venv)
pip install -r requirements.txt

# 2. 设置你的大模型 key(GLM / zai,OpenAI 兼容)。.env.local 已被 gitignore。
cp .env.local.example .env.local
$EDITOR .env.local          # 填 ZHIPU_API_KEY=...

# 3. 运行 —— 首次启动会生成一个全新世界,然后进入游戏
./run.sh
# 或直接:
PYTHONPATH=. python3 -m app --campaign ./campaign --provider zhipu \
    --model glm-5.1 --base-url https://open.bigmodel.cn/api/coding/paas/v4
```

游戏内 OOC 指令:`/recall <q>`(搜记忆)、`/rewind <N>` / `/undo`(回卷)、`/verbosity concise|medium|rich`(调叙述详略)、`/compare on|off`(双策略 A/B)、`/help`、`/quit`。

想完全离线(不用 key)摸清机制?测试套用确定性的假 provider —— `PYTHONPATH=. python3 -m pytest -q`。

---

## 确定性、迷雾、可观测性

- **确定性 / 回溯** —— 所有骰子都是 `Oracle(scene_seed(...))`;重放路径无挂钟、无 RNG。回溯 = 撤回事件 + 重投影。
- **知识三层** —— 同一张图上的三个视角:**POV**(某个具体角色的 `knows`)、**公共/氛围**(一个路人能转述的,仅 `secrecy=="public"`,结构上被秘密挡在门外)、**DM**(完整真相,仅作者用)。
- **可观测性** —— 加 `--debug`(或设 `RPG_DEBUG_TRACE=/path/trace.jsonl`),把一条 langgraph 式的结构化轨迹(每次模型调用的 prompt/输出/用量、每个钩子 span、每条事件)记到 JSONL。用对 agent 友好的 `python -m app.trace <file>` 查看器检视:默认紧凑索引、`--show SEQ` 看单个节点完整 prompt+输出,外加 `--turn/--phase/--grep/--tree/--stats`。见 [`docs/debug-mode.md`](docs/debug-mode.md)。
- **可调叙述** —— `--verbosity concise|medium|rich`(或游戏中 `/verbosity`)调 DM 是惜字还是铺陈。开场会生成完整 intro:一个有血肉的主角(名字 / 身世 / 目标)、当前所在(区域 + 小镇 + 场所)、世界背景、以及一个具体的起始目标。

---

## 项目结构

```
app/        CLI 入口 (python -m app)、引擎装配、游玩循环、世界生成、酒馆导入
kernel/     微内核:事件存储、投影、注册表、校验、召回、可观测性
systems/    各 ContextSystem(ontology/place/character/faction/knowledge/director/
            cascade/time/narrative/scene/lore)
facts/      双时态事实图
loop/       每回合流水线:turn、strategy、各后台钩子、bootstrap、genesis_spec、import_sillytavern
llm/        provider(OpenAI/zhipu/anthropic/fake)、POV 工具、结构化输出框架
memory/     召回 / 重要度 / 反思
context/    上下文装配
engine/     神谕、嵌入、日志、运行期设置
data/       神谕表(默认 + 开局维度表)
docs/       设计 spec & 实现计划(逐决策记录架构)
tests/      约 1540 条离线测试(确定性) + 实机大模型探针
```

`docs/superpowers/specs/` 与 `docs/superpowers/plans/` 逐子系统记录了设计与每个决策背后的取舍 —— 想搞懂*为什么*这样建,从那里看起。

---

## 测试

```bash
PYTHONPATH=. python3 -m pytest -q          # 约 1540 条离线测试,确定性,不联网
```

离线测试用假/脚本化 provider,所以整个引擎无需 key 即可被完整跑通。实机行为(真实推理模型会不会调工具、守住秘密、生成自洽世界)由 `docs/` 下的探针脚本验证。

---

## 状态与路线图

**v0.2** —— 完整核心闭环 + 结构化 debug 追踪 + 可调叙述 + **玩家可定义开局**(蓝图文件 / 交互式 session-zero / 酒馆世界书 & 角色卡导入;你没定义的模型来补),离线(约 1540 测试)+ 实机验证。接下来:可选流式输出;给 director 的世界影响「推送」浮现;通过真实游玩调校活世界的各项数值。

## 许可证

[MIT](LICENSE) © 2026 Xingyu Liu
