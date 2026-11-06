# RCV139 显式发言宿主结算：原生样本

普通非 demo `build_dashboard`、默认 `GroundedAuthorStrategy`。合成客栈里两名公开同场人物：阿岚 `npc_lan_17`、阿禾 `npc_he_24`。玩家分别选人并提交原话“青竹铃铛明晨响四次。”与“赤陶水壶放在东窗。”，随后实际离开客栈到集市，再冷重开。

两个逐请求生成的 Terra 作者提案均自然省略 knowledge；宿主实际补入各自的 heard_player。因结果变化，两回合均触发额外正文重写及独立审稿，完整实际生产请求和原生响应在 `requests.json`、`responses.json`，发布结果在 `progress.json`。SQLite 记录恰好两条对应的 `knowledge_set`，不同 knower 和逐字 value；下一行动作者收到按人分列的安全交谈材料。不得把角色记录当成世界事实，也未据此断定任何第三人知情。

冷重开后通过普通行动框查询：第 1 天对阿岚和阿禾分别说什么，各只返回该人一条正确收据；问“我是哪天对阿岚说‘青竹铃铛明晨响四次。’”返回第 1 天；把阿禾的话按阿岚查询、查询从未说过的话均无匹配。两条来源引用实际调用 `open_source`，获得原话及前后玩家输入、公开旁白。查询前后版本均 8、事件数不变、调用模型次数 0。完整结果在 `queries.json`，实际合成 SQLite 备份 `synthetic-events.db`；`query.py` 可对同一固定测试存档复查。

限制：这是单次本地原生样本及确定性只读查询，不是远端 HTTP 测试；没有覆盖同句跨日再次发给同一人。来源邻文是玩家可见的公开叙述与输入，不代表其他 NPC 自动知悉其中内容。合成种子使用 demo 世界初始化，但实际入口是普通非 demo；关闭 digest_fleet、run_director、run_cascade、run_catchup、run_density、_run_demote_on_leave、run_lore。驱动依赖固定持久测试路径和随机事件 ID，不把旧响应当新会话的逐字重放；RCV138 修前证据与旧存档未修改或回填。
