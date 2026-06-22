# Debug Mode — 结构化轨迹记录 + agent-friendly 索引 (2026-06-22)

> 一个 langgraph 式的全链路轨迹记录,主要供 **agent(调试者)** 使用:把每次 LLM 调用(system+user prompt / raw output / tool calls / usage)、玩家输入、所有 backstage hook(digest/director/cascade/catchup/lore/density/bootstrap 步)、commit/repair 循环、发出的事件,都以**结构化、可索引**的方式记录;轨迹会很长,所以导航必须 **token 省**(先紧凑索引、再按 seq 钻取)。

**Goal:** 开 `--debug` 跑游戏 → 全部执行轨迹落成一个结构化 JSONL + 一个 agent-friendly viewer,使调试者能廉价地定位到任意一部分(某回合某 phase 的 LLM prompt/output),无需把超长 trace 灌进上下文。

**Architecture:** 复用现有 `kernel.observability.get_tracer()` 接缝(`span/generation/event` 已被 provider 与 run_turn 调用)。新增一个**进程级单例 `DebugTracer`**,把 span 树 + generation(LLM I/O)+ event 写成 JSONL。`get_tracer()` 在 debug 开关下返回该单例(单例是必需的——span 栈要跨多次 `get_tracer()` 调用持续才能正确嵌套)。一个 `rpg-trace` viewer 提供紧凑索引 + 按 seq 钻取。

**Tech Stack:** 纯 stdlib(json/os/time/argparse);复用 `kernel.observability`、`llm.provider` 的 generation 调用点、`loop.turn`/`loop.bootstrap` 的 span 点。无新依赖。

## Global Constraints
- **零开销原则**:不开 debug → `get_tracer()` 仍返回 `NoopTracer`,现有 1331 套件**字节级不变**。debug 仅由 `RPG_DEBUG_TRACE`(路径)或 `--debug` 触发。
- **优先级**:`RPG_DEBUG_TRACE` 设置 → DebugTracer;否则 `LANGFUSE_PUBLIC_KEY` → LangfuseTracer;否则 Noop。
- **agent-friendly 第一**:viewer 默认输出是**紧凑索引**(一行一节点,截断摘要);全文只在 `--show <seq>` 给。文档明确告诉 agent:**先看索引/切片,绝不 `cat` 原始 JSONL 进上下文**。
- 单例 DebugTracer 单线程(引擎回合串行);span 栈是实例级、无需线程安全。
- 记录写入永不让游戏崩(写失败吞掉 + log.debug,同现有 tracer 的容错纪律)。
- Python3,`PYTHONPATH=/root/rpg-engine-app`;提交在 `app`,per-feature,结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

## § 1 DebugTracer(`kernel/observability.py`)

实现与 Noop/Langfuse 同接口的 `span/generation/event`,额外维护状态:
- `_stack: list[tuple[seq, name, attrs]]` —— 当前 span 栈;`span()` 进入时 push + 写 `span_start`,退出时 pop + 写 `span_end`(带 `dur_ms`)。
- `_seq` —— 单调递增计数;每条记录一个 `seq`。
- `path` = 栈中各 span 的 `name`(+ 关键 attr,如 `turn`/`tool_name`)用 `▸` 拼接。
- `generation(name, **attrs)` —— 写一条 `gen`(进入记 input=attrs.get("input")/model;yield 的 handle 的 `finish(output,usage)` 回填 output/usage/dur_ms),path = 当前栈。
- `event(name, **attrs)` —— 写一条 `event`,path = 当前栈。
- 构造时 `open(path, "a")`;`get_tracer()` 缓存单例(模块级 `_DEBUG_TRACER`)。

`get_tracer()` 改:若 `os.environ.get("RPG_DEBUG_TRACE")` → 返回(惰性构造并缓存的)`DebugTracer(path)`;否则维持现有 Langfuse/Noop 逻辑。

## § 2 记录 schema(JSONL,一行一事件)

```json
{"run": "<run-id>", "seq": 42, "ts": 1750000000.0, "dur_ms": 8100,
 "type": "gen", "name": "llm", "path": "turn:3▸cascade▸llm",
 "parent_seq": 39, "input": [{"role":"system",...},{"role":"user",...}],
 "output": "...raw completion...", "usage": {"input":1234,"output":567},
 "attrs": {"model":"glm-5.1","max_tokens":32768}}
```
- `type ∈ {span_start, span_end, gen, event}`。`span_start` 仅 name/path/attrs;`span_end` 带 `dur_ms`;`gen` 带 input/output/usage/dur_ms;`event` 带 attrs(如 player_input 的 text)。
- `run` = 启动时一次性生成的 run-id(传入,不用 `Date.now`/random——由调用方/env 给;v1 用 campaign 名 + store 事件数派生,确定可复现)。

## § 3 捕获覆盖

**已自动(无需改)**:provider `_do_post` 的 `generation("llm")`(每次 LLM 调用)、`span("tool")`/`span("tool_loop")`、run_turn 的 `span("turn"/"digest_fleet"/"director"/"cascade"/"catchup"/"lore")`。

**本设计补足**:
1. **`_do_post` 的 generation 补 I/O**:`generation("llm", model=..., max_tokens=..., input=body.get("messages"))`,并在拿到响应后 `gen.finish(output=<提取的补全文本>, usage=...)`(现在只 `_record_usage` 记了 usage,没记 prompt 输入与 output 文本)。
2. **bootstrap 流水线 span**:`bootstrap_world` 包 `span("genesis")`,每步包 `span("gen_frame"/"gen_regions"/.../"gen_opening")` —— 开局轨迹可索引。
3. **produce/repair span**:`produce_turn` 给首次 produce 包 `span("produce")`、每次修复包 `span("repair", attempt=N)` —— commit/repair 循环可索引。
4. **player_input event**:`app/play.py::play_loop` 每个玩家回合开头 `get_tracer().event("player_input", text=line, turn=turn_no)`。

(这些 span 在 Noop 下零开销,不影响现有套件。)

## § 4 Viewer(`python -m app.trace` / `rpg-trace`)—— agent-friendly

默认(无过滤)输出**紧凑索引**,一行一节点,供 agent 廉价定位:
```
seq   path                          type  dur     tok    summary
42    turn:3▸cascade▸llm            gen   8.1s    1801   {"world":[{"areas":["断桥"...
39    turn:3▸cascade                span  9.0s    -      cascade
12    turn:3                        span  41s     -      turn (input=我走向断桥)
```
命令:
- (无参) / `--turn N` / `--phase cascade` / `--type gen|event|span` —— 过滤后的紧凑索引。
- `--show <seq>` —— **单条全文**:path + 完整 input(system+user)+ 完整 output + usage + dur。这是 agent 钻取单个节点 I/O 的方式(不灌全文件)。
- `--grep <regex>` —— 索引中 input/output 命中正则的行(定位"这句话从哪来")。
- `--tree` —— 缩进 span 树(每节点带 seq),看整体结构后挑 seq。
- `--stats` —— 各 phase 聚合(调用数 / 总+均耗时 / 总 token),查耗时/token 去向。
- `--json` —— 过滤结果以 JSON 行输出(程序化消费)。
- 摘要截断到 ~80 字符;全文只走 `--show`。**这是核心 agent-friendly 原则:索引廉价、全文按需。**

## § 5 启用

`app/__main__.py` 加 `--debug`:置 `RPG_DEBUG_TRACE = <campaign>/trace.jsonl`(若用户没显式给 env)。`--debug` 同时打印一行提示:trace 写到哪、`rpg-trace` 怎么看。`RPG_DEBUG_TRACE` 也可直接经 env 设。不给 → 现状(Noop/Langfuse)。

## § 6 使用文档(交付物)`docs/debug-mode.md`

详细文档(给 agent 也给人),含:
- **何时用 / 它记什么**。
- **启用**:`--debug` / `RPG_DEBUG_TRACE`。
- **schema 字段参考**(§2)。
- **viewer 命令参考**:每个 flag + 真实示例输出。
- **agent 调试配方(recipes)**:
  - 叙事不对 → `rpg-trace <f> --turn N --phase produce --show <seq>` 看那回合的确切 prompt+output。
  - 世界漂移/地点乱 → `--phase cascade` / `--phase density` / `genesis`。
  - "这句话哪来的" → `--grep "<片段>"`。
  - token/耗时爆 → `--stats`。
  - 开局问题 → `--phase genesis --tree`。
- **agent 使用协议(铁律)**:先 `--tree` 或 `--turn/--phase` 看索引定位,再 `--show <seq>` 取单节点全文;**永不 `cat` 原始 trace.jsonl 进上下文**(它很长)。

## § 7 测试(离线)
- DebugTracer:span 嵌套 → 正确 path;`gen` 记到 input+output+usage;`span_end` 有 dur_ms;`event` 记到 attrs;写入容错(坏路径不崩)。
- get_tracer:`RPG_DEBUG_TRACE` 设 → DebugTracer 单例(同一实例跨调用);未设 → Noop(**断言不破坏零开销**)。
- provider generation:`_do_post`(或其可注入的薄封装)在 DebugTracer 下记下了 messages 输入与 output 文本(用 fake response,不联网)。
- viewer:`--turn/--phase/--type` 过滤、`--show` 全文、`--grep`、`--tree`、`--stats`、`--json` 各自正确(喂一个小 fixture trace.jsonl)。
- 回归:不开 debug 时全套件 1331 仍绿。

## 已定(经 2026-06-22 brainstorming)
- 存储 = **JSONL + viewer**(用户选)。架构 = 复用 tracer 接缝 + 单例 DebugTracer。
- **agent-friendly**:紧凑索引 + 按 seq 钻取 + token 省 + 铁律"勿灌全文件"。
- **详细使用文档** `docs/debug-mode.md` 为交付物之一。
- 补 4 处捕获:provider I/O、bootstrap span、produce/repair span、player_input event。

## Out of scope (YAGNI)
- 实时/流式 trace UI(离线 JSONL + CLI 足够)。
- 跨 run 聚合/对比(单 run 一文件;多 run 用多文件)。
- 把 trace 接回 Langfuse(Langfuse 路径保留但与本地 sink 二选一)。
- 改 provider 的重试/超时逻辑(只在其 generation 上补 I/O)。
- 自动 trace 轮转/压缩(debug 产物,用户自行清理)。
