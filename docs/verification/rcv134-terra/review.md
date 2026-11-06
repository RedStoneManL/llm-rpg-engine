# RCV134 普通入口正文审核样本

使用普通非 demo 的 `build_dashboard` 默认 `GroundedAuthorStrategy`，合成 SQLite 初始客栈、集市和主角持有的铜灯；不打开用户存档。两个互相独立的新存档都接收相同行动“我只观察，不去集市，不卖灯”。作者／审稿员为本会话 Terra，逐个读取实际生产请求后生成响应，回放到同一实际入口；没有远程 HTTP。关闭摘要、导演、级联、追赶、密度、降暗后台 hook；没有证明默认全后台行为。

自然回合：作者写主角留在客栈看晨雾，铜灯仍在手边，结构没有移动或转移；独立审核回应 `supported:true`。最终持久化正文相同，人物位置为客栈、铜灯持有人为主角。该分支 2 次实际原生请求。

注入对抗分支：向相同默认入口的作者返回一份明确标注为注入的结构化提案，正文为“你抵达集市，把铜灯卖了”，同时 moves 等效果为空。这不是模型自然写出的错误。真正 Terra 审稿员在实际 `narration_review` 请求中返回 `supported:false`，指出抵达集市与最终客栈/空 moves 冲突、卖灯没有物品交接或交易结果。随后的真实重写请求只给最终结果和审稿意见；模型改写为留在客栈看晨雾、灯仍在手，第二次审核 `supported:true`；最终持久化修正正文，位置仍为客栈，持有人仍为主角。此分支 3 次原生请求，未触发兜底。

每条精确请求、原生响应、落库结果保存在 `natural/` 和 `injected/`；后者的 `injected_draft.json` 与 `injected_author_request.json` 明确区分注入。是单次模型生成与自评，不能证明审稿员对其他隐含行为、否定、复杂资源或人物认知持续可靠；没有测审稿失败时的宿主兜底，也不声称模型不会误放行。正常回合从一条调用增加到作者加独立审核两次，本地回放不测真实模型响应时延。

重跑只验证同一生产请求、持久化与既存响应，不重新采样：

```
.venv/bin/python docs/verification/rcv134-terra/probe.py docs/verification/rcv134-terra/natural natural
.venv/bin/python docs/verification/rcv134-terra/probe.py docs/verification/rcv134-terra/injected injected
```
