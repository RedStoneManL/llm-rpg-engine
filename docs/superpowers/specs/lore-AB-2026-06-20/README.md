# Lore 披露 A/B 真机对比 (glm-5.1, 2026-06-20)

`compare.py` 把同一个青石镇(8 条事件线、4 个 L3 venue、同种子同世界、同 6 个玩家动作)分别用 **A(PULL/工具)** 和 **B(PUSH/站点直推)** 在 glm-5.1 上跑,看模型怎么处理。`transcript.out` 是原始输出。

跑:`cd /root/rpg-engine-app && set -a; . ./.env.local; set +a && PYTHONPATH=$PWD python3 docs/superpowers/specs/lore-AB-2026-06-20/compare.py`

## 结论:B 完胜

| 维度 | B(站点直推) | A(工具拉取) |
|---|---|---|
| 浮现准确度 | ✅ 每回合精确推当前 venue 的线(就在此处)+ 全镇 L0;无跨 venue 泄露 | ⚠️ T1 贪婪 fetch 了 8 条里的 5 条(没选择性);T2 起索引变(无),靠记住的 id 拉 |
| 工具行为 | 不需要 | ❌ T1 就撞 max_tool_rounds=3 上限、强制收尾;每回合多 1+ 往返 |
| 系统纯净 | ✅ 模型把推来的线当**环境氛围织入**(正是想要的"微妙伏笔") | ❌ 工具的"故事线"措辞诱导模型把 lore 当 storyline,每回合往 StorySystem 狂塞 storyline_advanced(unknown id→created),被修复闸放大到 ~20 条/回合 |
| 叙事质量 | ✅ 6 回合都是浓密的沉浸式散文,lore 自然融入 | ⚠️ 多数回合不错,但 T4 退化成第三人称摘要 |
| 成本/可靠 | ✅ 0 额外 LLM 调用;修复少(仅 T3=4) | ❌ 每回合工具往返 + 修复偏多 + 上限 throttle |

**为什么 B 够用还更好**:你那个"trigger 细到 L3"的洞见让站点直推**天然便宜**——站在集市只推集市那 2-3 条 beat,根本不淹,不需要工具机制。A 的 PULL 想省 token,但 glm-5.1 **贪婪 fetch**(一次拉一半),省不下来;反而引入了上限、索引脆弱、storyline 污染、叙事退化一堆失败模式。

## 对比顺带挖出的真 bug(与 A/B 无关,但 A 触发得最凶)
模型会把"事件线"当成 **storylines** 段去 advance → 一堆 `storyline_advanced unknown id` 涌进 StorySystem(明线账)。lore 和 storyline 是两个系统;**lore 的推进必须走自己的通道(暗骰 / 一个 lore 专用 commit 段),绝不能走 storylines 段**——否则污染明线账。修复闸还把它放大(每修一轮重发一次)。这条要在 lore 正式落地时修。(B 没诱发它,因为 B 把 lore 呈现为"可织入的环境氛围"而非"待推进的故事线"。)

## 建议
1. **lore 披露用 B**(站点直推),把之前评审记的 B polish 收一收(L1 带 about、跳过未起步的空线)即可产品化。
2. **A 的 tool-loop 基础设施留着**——它是你早规划的 P3 PULL 只读工具(map/characters/recall)的地基,只是 **lore 披露不需要它**(L3 稀释 + push 就够)。
3. **修 lore 推进通道**:别让模型把 lore 当 storyline 推;给 lore 自己的推进入口。
