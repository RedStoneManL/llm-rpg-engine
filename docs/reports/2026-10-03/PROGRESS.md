# LLM RPG Engine 独立架构审核 · 2026-10-03

## 当前状态：实施与交付完成（2026-10-03）

现有引擎已直接改进，最终结果以本文件末尾的 Final implementation handoff、`implementation-progress.json` 和新版 `report.html` 为准。1712 项回归测试通过；120 回合故障/恢复验证、7 份旧档兼容检查和两种题材共 16 回合真实 DeepSeek 游玩完成。账本、明确等待、提交与恢复机制通过；整体叙事语义尚未全面通过，人工发现及后续验收条件已明确列出。下方保留早期审核、受限环境与迭代失败记录，历史阻塞状态已被后续验证取代。

## 任务与边界

用户目标：找到最新引擎与 Claude 审核上下文，独立评价架构，实际测试后给出系统性改进方案和图文 HTML 报告。允许真实 DeepSeek V4.1 Flash 调用与隔离实验，要求持久跟踪。产品目标：能参与、探索一个鲜活且自洽的异世界。

工作目录 `/root/rpg-engine-app`，HEAD `fb7ebd8`；旧公开副本 `/root/llm-rpg-engine`，HEAD `fb91fd4`。开始时只有 `run.sh` 存在用户未提交改动，保留。原存档 `/root/games/play2` 至 `play8` 只读；实验使用独立目录与复制数据。不重写历史、不发布代码、不自动采用 Claude 的建议。

Claude 会话：`/root/.claude/projects/-root-rpg-engine-app/3f2736aa-1b06-454d-8ad8-0f987c6b0a9f.jsonl`，实质审核发生于 2026-09-07，文件修改时间不等于会话内容时间。两份 HTML 已恢复到 `recovered/`，出处见 `evidence/provenance.json`。

## 初始审核阶段（历史记录）

- [x] 定位新旧目录、git 基线、Claude 会话和原始报告。
- [x] 复核 Claude 的顶层论断与关键故障，提取 78 条原始发现；并非 78 条均逐条重跑。
- [x] 安全保存密钥、配置 DeepSeek 启动入口，核对官方模型名。连通性失败，真实模型验收仍阻塞。
- [x] 运行现有测试基线，分析实际回合、状态、知识与存储边界：1687 passed, 1 deselected, 109.84 秒。
- [x] 编写隔离探针，实测故障和最小原型；17 个主探针 + 知识视图扩展 + 索引对照。
- [ ] 真实模型对照实验：正确性、叙事、延迟、调用/令牌开销。
- [x] 整理通过 / 否决 / 待验证结论、优先级、实施与验收方案。
- [x] 生成自包含中文 HTML 报告；四张图独立渲染并目视检查；校验本地链接、唯一 ID 与 JavaScript 语法。

## 证据规则

每项推荐必须标明证据：代码位置、确定性故障实验、真实 API 实验。局部原型通过不等于整套上线方案通过；假说不写成结论。性能须记录环境、样本数、实际调用与输出，不把首次字节当作完整回合。模型 ID 以官方文档及 API 响应核对。密钥禁止进入报告、测试日志与 git。

## 已知疑点（等待实测）

Claude 认为核心骨架可保留，但事务、回合身份、迷雾与同步后台存在问题；主张叙事先流式输出、抽取记账独立、13 个系统合并到 6 个、context 与 memory 合并。其「126 秒变 3 秒」尚未有同模型对照证据，必须独立检验叙事与提交一致性、后台竞争和恢复语义。

## 日志

- 初始化：完成定位和报告恢复，开始阅读实际写入路径与模型协议。
- 本地证据：`evidence/probes.json`；play8 副本复现 `/undo` 仅撤 2 条 lore 事件（7 玩家回合、最大事件回合号 28）。故障创世静默写入 48 个事件。提交网关验证了预演拒绝毒事件、批量回滚、幂等回执及版本冲突。
- 追加发现：同场 NPC 的未知 goal 经 recall 返回，而 characters_query 会隐藏；默认 multiturn delta 未携带后台已提交的人物状态改变。可见性必须同时约束实体、字段与信念，聊天历史只能当派生缓存。
- 性能：直接读取 play8 trace 得到 7 回合总耗时中位数 75.572 秒、produce 中位数 75.303 秒。首回合 125.993 秒中 density 71.938 秒；不能外推“移后台即 3 秒”。
- 索引原型：真实 kernel.project 上 1K/3K/6K/12K 合成事实事件各测 3 次；12K 事件 2575.054 → 109.880 ms（中位数，23.44 倍）。4320 次历史查询等价；这是本地投影速度，非真实 LLM 回合加速。完整原测试正在隔离代码副本中运行。
- DeepSeek：密钥 `/root/.config/llm-rpg-engine/deepseek.env`，0600；启动入口 `/root/rpg-engine-app/run-deepseek.sh`。官方模型 `deepseek-flash` 对应 V4.1 Flash。当前旧 provider 丢 reasoning_content，启动配置暂设工具轮数 0。真实测试脚本 `experiments/live_validation.py` 已准备，网络阻塞时直接写 blocked 结果，不生成假成绩、不触碰原存档。
- 阻塞来源：终端执行环境 network=restricted，urllib 请求官方 API 遇 DNS 解析失败；已向用户异步说明。未申请不允许的 sandbox 提权，未发送密钥到第三方服务。
- 索引回归最终结果：隔离副本 1687 passed, 1 deselected, 107.17 秒；原仓核心代码未修改。
- 持续验证最终结果：120 次行动、12 次 undo + 重开、17 次重复请求、8 次写入失败、12 次过期结果拒绝；两个独立 SQLite 连接并发提交得到 1 成功 / 1 冲突；全部断言通过。
- 报告：`report.html`（内嵌 SVG 与证据摘要）；图表和架构图在 `figures/`。完整 Chromium 检查受沙箱限制，在启动时因 `shutdown: Operation not permitted` 退出；仅将静态 DOM/脚本检查与图形渲染记为完成，不宣称浏览器页面截图通过。

## 初始交接状态与继续方式（历史记录，已被最终实施结果取代）

**本地审核与报告完成；用户要求的真实 DeepSeek 实测尚未完成，不能宣布整体验收通过。** `evidence/live-validation.json` 保留 blocked 状态，真实模型调用 0。无需再次定位会话或重跑已通过的同一套测试。

下一步只依赖终端能访问 `https://api.deepseek.com`：运行 `python3 experiments/live_validation.py --max-calls 32`，读取实际输出再决定下一组实验。该脚本只做小场景 Author/Hybrid 对照，不能替代工具模式、流式和长局体验验收。不要将目前受限的零工具启动配置误当成完整原生适配。

已采纳的只是有本地反例与原型支持的机制：集中事务提交、幂等与版本、倒带同步缓存、可见性/信念读取视图、创世失败门控、事实索引。尚未采用 13→6、删除会话历史、直接异步化整套后台或“3 秒”性能承诺。所有原存档及开始时未提交的 `run.sh` 改动保持不变。

## 2026-10-03：按用户提供的官方文档复核连接故障

- 官方首次调用文档与模型列表确认：OpenAI 格式 base_url 为 `https://api.deepseek.com`，模型 ID `deepseek-flash` 对应 V4.1 Flash；接口为 `POST /chat/completions`，使用 Bearer 鉴权。当前配置一致。来源：https://api-docs.deepseek.com/zh-cn/ 与 https://api-docs.deepseek.com/zh-cn/api/list-models/ 。
- 分层诊断明确了先前 DNS 报错的更底层原因：当前执行环境调用 TCP、UDP 的 `socket.socket` 均在创建阶段报 EPERM（Operation not permitted）。API、文档域名和 example.com 均无法解析；localhost 从本机解析正常。详见 `evidence/network-diagnosis.json`。
- 这不是 DeepSeek 服务不可用或密钥无效的证据；请求尚未到达服务端。网页检索工具能取得官方文档，也不能证明运行引擎的终端有网络权限。没有修改网络设置或尝试绕过执行环境限制。
- HTML 与交付包已同步补充诊断，真实模型验收仍保持 blocked / 0 completed。下一步条件是获得可访问官方 API 的终端执行环境。

## 2026-10-03：用户开放执行权限后恢复真实测试

- 这轮权限切换后，TCP 与 DNS 正常，官方 `/models` 返回 HTTP 200，包含 deepseek-flash。使用引擎原 OpenAIProvider 构造的真实 Chat Completions 请求也返回 HTTP 200、内容 OK、finish_reason=stop，耗时约 0.42 秒，19 tokens。此前网络阻塞已解除；该短请求不代表实际引擎回合延迟。
- 按用户要求，后续权限排查细节不再加入 HTML 报告。现在运行既有上限 32 次调用的 Author / Hybrid 实测脚本，结果保存到 evidence/live-validation.json；独立测试存档，不改原存档。

## 实施阶段（用户明确授权）

用户要求直接完善现有系统与潜在缺陷，保留多题材场景包和风格扩展，增加可复现的随机多样性；真实游玩验证后更新改前/改后 HTML。实施基线已保存到 baseline-implementation，具体阶段见 implementation-progress.json。现有 run.sh 用户改动保留。第一批真实模型基线：Author 6/6 接受、Hybrid 3/6 接受；工具协议旧适配器在本次真实样本中返回 200，不能把文档预期的 400 写作已复现故障。

## Implementation + live acceptance, 2026-10-03

Implemented atomic action batches, replay preflight, revisions/receipts, action-scoped hooks/undo, deferred conversation commit, POV read views, fact indexes, genesis fail-stop, DeepSeek native profile, seeded pack-based variation, chunked recap, optional scenario fact rules. Full regression passed once after updating fixtures to explicit required sections and fail-stop semantics.

Live v1: 62 DeepSeek calls; two generated campaigns and 16 accepted/replay-equivalent actions. **Not accepted as correctness evidence:** both purchase scenes omitted deductions; malformed JSON was preserved as narration. Evidence retained in implemented-live-play-v1-failures.json. Fixes: native JSON mode for structured calls; preserve only bare prose on rewrap; reject internal JSON in narration; date-stamped canonical fact anchors and refresh self-observable facts over stale belief copies. Live v2 now running; do not claim success before reviewing accounting and prose.

## Final implementation handoff

1712 regression tests pass (1 environment-dependent test deselected by project defaults). Current gateway endurance: 120 actions, 12 injected write failures, 10 complete-action rewinds/salt replays, 8 reopens; final fuel 880. All seven old-save copies retain identical entity/fact/relation history; original DB hashes unchanged. User run.sh bytes preserved.

Live V2: first payments correct, subsequent insufficient-funds actions double-debited; native JSON and prompting did not suffice. Added opt-in scenario resource resolver (intent parsing + Python arithmetic). Live V3: state/accounting pass, but repeated prose and explicit-wait errors found. Added recent-prose repeat guard and absolute wait target validation.

Final V4: 75 real API calls, 16/16 actions persisted and replayed; 8/8 resource expectations, 2/2 explicit wait targets, 2/2 reopens, 2/2 undos. No empty/JSON-envelope narration, no repeated long prefix under the defined rule, no planted hidden-place canary in 16 narrations. 264797 tokens; median complete synchronous action 8.05 s. **Whole-story semantic quality is NOT fully accepted**: manual review still finds invented prior details, ambiguous umbrella possession/date, and prose refund activity inconsistent with a refused transaction. Remaining effect/ownership/commitment work is documented, not represented as finished.

Report replaced with implementation-centered self-contained HTML; original report content recovered from Claude remains linked. Browser passes desktop/mobile overflow, JavaScript and decision filters. Secret excluded from artifacts. Main source edits left reviewable in the shared working tree; no destructive git operations.
