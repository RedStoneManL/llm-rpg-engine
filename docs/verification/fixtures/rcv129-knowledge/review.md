# 不同人物认知与通信边界验收

基线 5d8eefe 的 `baseline/projection.json`：世界公开值为“公告说渡船停航”，阿岚保存的认知为“旧告示说渡船全天通航”，白禾保存的是“听说渡船仅在上午通航”。但当面阿岚和条件抵达白禾的 `speakable_evidence` 都被公共值替代；普通作者交谈材料也消费这一投影。修后 `after_projection/projection.json` 分别保留两人的不同值，白禾材料仍明确以合法抵达枫桥为条件。注：修复过程中基线目录曾被当前代码覆盖一次，随后用 5d8eefe 独立 git archive 与相同脚本重建，确认基线再现；并非宣称保留了最初字节级文件。

`after_native/` 为修后普通 PlayerDashboard + AuthorStrategy 的五个连续 Terra 响应：玩家明确只对阿岚说纸鹤原话并在心里想铃铛；询问阿岚渡船；冷重开；前往白禾所在镇；询问白禾；返回。阿岚答旧告示全天并说不能保证现在仍如此，白禾答听说只上午且未核实；两人没有引用另一人的说法或认定公共停航事实。阿岚把“旧告示说”扩展成“我看过的旧告示”，这是模型对消息来源的细微具体化，原种子不单独证明她亲眼看过。

五个提交后的世界事实一直是停航，两份 NPC 知识保持不同原值；冷重开前后投影一致。逐字纸鹤原话只出现在阿岚对应的 host-authorized `knowledge_set`；白禾没有该记录。两次自由文本询问没有被转换为 NPC 逐字听话收据。未说出口的铃铛心念没有写进两名 NPC 的知识，也没有被 NPC 说出；该文本确实作为玩家行动进入模型请求，不等于模型没有读到。幕后秘密没有出现在任一生产请求或正文。

到枫桥时，阿岚的纸鹤原话仍可在作者材料中出现，但被标为“仅抵达柳溪后”的条件材料；白禾的正文没有引用。实际原始请求、模型回答、每回合状态与知识事件、重开前后分别在 `requests.json`、`responses.json`、`result.json` 和 `reopen.json`；`result.json` 的布尔检查负责机械边界，不替代对话文本评审。

这是本会话 Terra 对逐份生产请求生成的一条路径，普通 GUI + SQLite、AuthorStrategy、真实 run_lore；关闭摘要、导演、级联、追赶、密度和降暗 hooks，未使用远程 HTTP。非独立重复采样，也未覆盖 NPC 获得新消息后更改认知、矛盾信念的细致推理或缺少公开事实的私人信念；不能证明所有自然语言都会遵守边界。问题本身只是自由文本而非额外 host 收据，因此不能据此说两名 NPC 有持久化的询问原话。

128 的旧临时人物 `reported:` 提案在新版本已重新通过普通入口，证据位于 `rcv128-encounter/fact_retest/`；它复用旧生成响应，使用新版本消息和校验，是回放回归而不是新原生样本。

重跑修后：

```
.venv/bin/python scripts/verify_npc_knowledge_native.py projection /tmp/rcv129-projection
.venv/bin/python scripts/verify_npc_knowledge_native.py native docs/verification/fixtures/rcv129-knowledge/after_native
```

原生空目录在每次请求时暂停；每次读取 pending.json 并追加真实响应后继续。使用已有文件重放只核对消息、持久化和保存，不会重新采样模型。
