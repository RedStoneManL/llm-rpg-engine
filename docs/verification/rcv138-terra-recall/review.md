# 修前：原生收据与只读回忆

普通非 demo `build_dashboard`、`GroundedAuthorStrategy`。第 1 天在客栈选择同场阿岚（内部 ID `npc_lan_17`）明确说“紫檀风铃今晚响三次。”，随后选阿禾（`npc_he_24`）说“白瓷茶盏放在西窗。”；两回合原生正文分别记下发言，模型均未声明 knowledge.heard_player，宿主接受并发布。第三回合主角离开客栈至集市。逐份生产作者和审稿请求及对应 Terra 响应保存在 `requests.json`、`responses.json`，发布结果在 `progress.json`（之后又实际发布了一次回客栈的回合；第 5 个请求待处理，保留作为记录）。

在第三回合之后、回客栈之前，冷重开普通 dashboard，通过普通行动框问：第一天我对阿岚说了什么、我是哪天对阿岚说“紫檀风铃今晚响三次。”、我是哪天对阿岚说阿禾收到的原话、以及一条从未说过的原话。四者均返回“在这些条件下找不到可核验的原文”。前两个本应命中阿岚实际发言，故这是自然遗漏反例；后两个无匹配符合预期，但仅在当前无收据条件下不能独立证明收件人过滤正确。没有来源可打开，未完成来源邻文正向验证。所有查询 revision 均 8，事件数量不变，实际模型调用 0。完整请求返回及时间点保存在 `queries.json`，第三回合后的实际 SQLite 合成快照为 `synthetic-events.db`。这是宿主确定性查询，不是模型回忆；也不测 HTTP 运输。

第一次原生提案试图声明 heard_player，但误用 fact_key/value 而未使用宿主接受的 speaker/statement 字段，遭 `TurnRejected`；该请求、原始响应、失败保存在 `first-rejected/`。之后新的原生提案省略 knowledge 并通过；未修改前次响应或伪造成功。该样本还不证明模型无法正确输出 heard_player，仅记录一次真实失败与后续遗漏。

合成世界由 demo 世界种子初始化，但实际游戏使用普通非 demo 入口；关闭 digest_fleet、run_director、run_cascade、run_catchup、run_density、_run_demote_on_leave、run_lore。`probe.py` 是逐请求捕获驱动，`query.py` 为第三回合后的查询驱动；存档位于固定 /tmp 路径，带随机事件 ID 的已有请求不应混作新会话重放。
