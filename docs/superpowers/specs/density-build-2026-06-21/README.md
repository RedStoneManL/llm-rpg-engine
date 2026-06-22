# 密度生成 — 真机 demo + 真机暴露的 bug + 修法 (glm-5.1, 2026-06-21)

`demo.py` 在 glm-5.1 上跑一个 region-less 世界(幽港镇⊃4 venues,无预种暗线),主角在镇里活动 6 回合,看引擎**自动长出暗线**。`transcript.out` 是最终(修复后)输出。`probe.py` 是诊断脚本。

## 真机暴露的 bug(离线测试盖不住)
第一次 demo:**播种触发但 0 条线生成**。probe 直打 glm-5.1 发现:模型返回**合法 JSON、内容极好,但用它自己的 key** —— `summary`/`title`(非 `about`/`description`),stage 按叙事角色命名(`{title,summary}` 或 `{hook}`/`{resolution}`/`{clue_a}`,非 `{hint}`),回显 `complexity`/`stage_count`,漏 `trigger`/`secret`。严格解析全 drop。**离线 FakeLLMProvider 喂规范 shape 的 canned dict,把这个真机 gap 完全盖住了**(reasoning-model 集成的典型坑)。

## 修法:严格逐字段声明 prompt + harness 校验-修正循环(按用户指示)
不用"容错解析"妥协(撤掉了 synonym 映射),而是**让 harness 强制契约**(照搬引擎给叙事 commit 用的 validation-repair 模式):
- **逐字段显式 prompt**:每个 key 写明名字+类型+必填 + 完整示例,明说引擎按程序解析、任何偏差都拒。
- **校验-修正循环**(`generate_lore_batch`,max_repairs=2):每轮 `complete_messages` → `_validate_gen_lines` 逐行查必填(`about`/`description`/`trigger`/`secret`/`l3_anchor` + `stages[{hint}]`,l3∈venues)→ 有问题 `_build_gen_repair` **回喂点名错误**(「Line N: 缺 `"about"`; stage 2 的 key 必须是 `"hint"`; `"l3_anchor"` 必须是 venues 之一」)→ 模型对着自己输出+报错改 → 仍不合规的行**丢弃,不 coerce**。多余 key 容忍但忽略,引擎 spec 复杂度永远赢。

## 修复后真机结果(transcript.out)
- **T1 首次播种 3 条合规暗线**(repair 循环逼出来的),内容质量高、各锚一个 venue、stage 渐进:
  - `渔港码头`(simple):渔网捞上泡烂布偶,剖开内填碎骨+刻名铜牌
  - `渡口茶摊`(simple):角落永摆一副碗筷,老掌柜给空位斟茶 → 柱缝欠条债主是盐商会馆旧号
  - `镇守庙`(medium):地砖刻满人名,暴雨夜渗出血色名字 → 撬开暗室藏镇海祭文+孩童缚石索具
- **暗骰酝酿**:T2–T6 各线 stage -1→0→1、clues 累积(生成的线接进了现有 run_lore 管道)。
- **ambient 披露**:生成线带 `[id]` 进环境线索,「就在此处」(当前 venue)vs「本镇其余风声」(全镇)正确分流。
- **暗→明**:T4 玩家去镇守庙找庙祝,叙事模型把该线 surface 成明态。**自动生成 → 酝酿 → 披露 → 浮现,整条链真机跑通。**
- refresh 本轮未触发(demo 的钟没推过 refresh 间隔;refresh 由 e2e 测试确定性覆盖)。

## 结论
密度生成 = "活世界"核心,真机立住了:引擎按密度自动生成、LLM 只写故事内容、harness 用严格契约+修正循环把真机模型的"自我发挥"逼回规范。autonomy within harness。
