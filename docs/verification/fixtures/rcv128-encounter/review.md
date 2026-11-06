# 当面人物与远方传闻验收

最终样本 `final/` 使用普通 PlayerDashboard、AuthorStrategy、SQLite 和逐份 Terra 原生请求，四回合：当面遇见木匠林月、明确选她作为原话收件人询问只听说的画师沈砚、冷重开、去枫桥、返回柳溪。林月由模型自身生成 cast.create 与同 ID 的 moves；保存后选择列表显示“林月（抱着木板的柳溪镇木匠）”，冷重开后仍在，离开后列表为空，返回后同一 ID 再次出现。第二回合成功提交，事件中记录了林月听到的逐字原话，主角也获得有林月来源的转述。远方沈砚未被实体化或放到现场。

保留的早期实验：最初 UI 只显示人物描述，测试脚本按名字匹配将其误判为不在场，实际上 ID 已在列表；这是已更正的测试判定。该次的第二个原生提案进一步尝试对同场临时人物写公开 reported:沈砚 来源事实，普通入口抛出 `TurnRejected: player fact lies outside the local public authority boundary`；最终四回合样本未写这条 facts，而以主角 told 和 heard_player 实现交谈，不能用成功样本宣称前述拒绝已修复。

关闭摘要、导演、级联、追赶、密度和降暗 hook，保留 run_lore；用本会话生成、逐请求本地回放，不是远程 HTTP 调用或多次统计。输入点名角色的姓名和职业，不能推断其他措辞下模型必然创建同场角色。路程时间与互动长期后果不在本验收范围。可用 `.venv/bin/python scripts/verify_encounter_native.py docs/verification/fixtures/rcv128-encounter/final` 重放；空目录需每次响应 pending 请求。
