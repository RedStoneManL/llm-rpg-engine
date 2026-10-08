"""Shared authoring guidance for the existing typed physical sections."""

MATERIALIZATION_GUIDANCE = '首次追踪此前事实已明确记载、但还没有 Object ID 的场景物件，用 {"op":"materialize","id":"新物品id","initial":{"kind":"scene_component","place":"来源地点id"},"source_ref":"来源目录ref","source_digest":"来源目录digest","source_quote":"来源text中的唯一逐字片段"}；仅支持当前地点的可见历史事实来源。初始化表示物理场景放置，不代表合法所有权、NPC保管或玩家已经取走。随后实际取下/拿走必须另写 transfer，from 为该地点、to 为实际持有者。不要把场景部件猜成 NPC 持有，也不能把已有 Object 再 materialize。普通 create+首次放置仍仅证明终点，不证明某人已交给玩家；材料不够就保留不确定性。materialize 所在 items 按行顺序在本回合 facts/clock/moves 之前处理，用原前态来源，不能引用同回合新事实。'

LINKS_GUIDANCE = 'links：开通/更新双向通路用 {"op":"open","a":地点id,"b":地点id,"travel_cost":非负整天数}（省略 op 仍为 open）；已存在通路实际关闭用 {"op":"close","a":地点id,"b":地点id}，close 不带 travel_cost。端点必须是已有或本回合新建的 Place；关闭必须指向实际存在的连接，不能为关闭编造地点。局部通行显式给 travel_cost:0，省略按1天算。按发生顺序列出；撤跳板/断路等实际发生后关闭原端点对，重开再给 open；船从旧岸开走不能保留旧岸跳板为可走路径。只据本回合实际变化落账，不从人物移动、提及、包含、同地或计划自动推断开关；facts/knowledge 和 moves 不代替 links，通路开关也不替玩家移动。一个端点对代表一条聚合双向通路，不支持单向或多个独立平行通道。'
