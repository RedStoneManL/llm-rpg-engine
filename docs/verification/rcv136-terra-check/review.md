# RCV136 明示检定原生样本

普通非 demo `build_dashboard`，默认 `GroundedAuthorStrategy`；输入“检定 潜行 困难 绕过守卫”。合成存档指定 seed 2，host 固定 d100=58、成功线 35、受挫，耗时两个时段，从第 1 天晨到下午。作者的实际生产请求含上述完整结果和时间，作者原生正文写潜行被守卫察觉、退回屋内；独立 narration_review 的实际请求含冻结结果与该正文，原生审稿判定 supported=true。最终正文如实落库。

SQLite `action_checked` 一条，其字段与固定结果一致；玩家公开 `latest_check` 显示相同掷骰、成功线、结果与耗时；最终主角仍在客栈，旧铜灯仍由主角持有。冷重开后面板一致，使用相同行动 ID 重复请求返回 duplicate，检定事件仍仅一条。`result.json` 保存事件、面板、最终投影、原文和重复请求结果；`requests.json` / `responses.json` 按顺序保留实际作者及审稿完整原始消息。

运行 `.venv/bin/python docs/verification/rcv136-terra-check/probe.py`。它使用全新临时合成 SQLite，检查既有响应逐字对应每一条重组生产请求，绝不连接远端 HTTP。作者和审稿回答由本次 Terra 按各自实际请求依次生成；这是一个样本，不证明所有检定或远程模型运输。为聚焦检定关闭 digest_fleet、run_director、run_cascade、run_catchup、run_density、_run_demote_on_leave、run_lore；初始化通过测试用 demo 世界种子，但最终运行入口为普通非 demo dashboard。`expected.json` 中事件 ID 是独立预计算产生，和实际落库事件 ID 可以不同；比较的是结算字段。
