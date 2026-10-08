"""Shared authoring guidance for the existing typed physical sections."""

LINKS_GUIDANCE = 'links：开通/更新双向通路用 {"op":"open","a":地点id,"b":地点id,"travel_cost":非负整天数}（省略 op 仍为 open）；已存在通路实际关闭用 {"op":"close","a":地点id,"b":地点id}，close 不带 travel_cost。端点必须是已有或本回合新建的 Place；关闭必须指向实际存在的连接，不能为关闭编造地点。局部通行显式给 travel_cost:0，省略按1天算。按发生顺序列出；撤跳板/断路等实际发生后关闭原端点对，重开再给 open；船从旧岸开走不能保留旧岸跳板为可走路径。只据本回合实际变化落账，不从人物移动、提及、包含、同地或计划自动推断开关；facts/knowledge 和 moves 不代替 links，通路开关也不替玩家移动。一个端点对代表一条聚合双向通路，不支持单向或多个独立平行通道。'
