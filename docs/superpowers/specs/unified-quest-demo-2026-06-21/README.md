# 统一事件线/任务系统 — 完整生命周期真机 demo (glm-5.1, 2026-06-21)

`demo.py` 在 glm-5.1 上跑青石镇(3 任务 + 邻村)6 回合,走整条状态机。`transcript.out` 是原始输出。

## 成了的(核心模型 + bug 修复在真机上立住了)
- **暗骰自走 + 状态分道**:T1–T3,暗线(失踪商队 stage0→2、假药郎中 0→1)每回合被暗骰推进、漏线索;`station_push_fragment` 准确推当前 venue 的线索(就在此处)+ 全镇风声。
- **明态推进走 quests 段**:T5/T6 叙事模型**正确用了 `quests` 段**(advance + open),码头浮尸的 summary 进了明账。
- **★污染 bug 没了**:**零条 `storyline_advanced unknown id`**(对比 lore-AB A 模式那场灾难)。状态分道的结构性修复在真机上成立。
- **world-push 浮现**:复杂线"码头浮尸"被暗骰推到末 stage → `quest_surfaced(by:world)` 自动炸成明态(T1 就炸了——因为我把它预酿到了 stage1 + threshold 100,种子设置导致偏早,机制本身对)。
- **fleet backstop**:director 埋的 thread 被 backstop 落成了 `quest_created(暗)`(`th_auto_…` 那条),迁移成功。

## 真机暴露的两个真问题
1. **★surface 用不了 → 开出重复任务(主要)**:玩家 T1–T3 一直在查那条**暗线"失踪商队"**,但叙事模型**从没 surface 它**——到 T5 反而 `open` 了一条**新明线"商队失踪"**(同一个故事、两个对象、两个 id)。根因:**ambient 披露只给了线索文本、没给暗线的 id**,所以模型无法 `surface(id)`,只能凭空 `open` 一条。⇒ 修法:**ambient 里带上暗线 id**(像 index_fragment 那样 `[id] 线索`)+ prompt 说明"玩家跟进 ambient 里的某条暗线就 surface 它的 [id],别另开新的"。
2. **demote-on-leave 不稳**:T4 玩家"动身去邻村",但码头浮尸(明、锚青石镇)**没退回暗态**(T4 后仍 state=明)。根因:真机叙事常把人移到**图上不存在的地方**("出城门上路…"),当前 L2 town 解析为 None → 防爆掉的护栏(current_l2 None 就 return)顺带把 demote 也跳过了。⇒ 真实世界的位移是"脏"的,纯靠位置判 demote 太脆。建议:补一条**按空闲回合数 demote**的后备(明线 N 回合没被推进 → 退暗),不依赖位置解析。

## 结论
统一模型 + 那条结构性 bug 修复**在真机上立得住**(模型会用 quests 段、不再污染明账、暗骰/world-push/backstop 都转)。两个转换机制(surface-by-id、demote-on-leave)有真机落地缺口,需补:#1 暗线 id 进 ambient(必修,防重复任务);#2 idle-based demote 后备(建议)。

## 修复后复跑 (transcript2.out) — 两个 gating bug 都修好了,真机确认
- **surface-by-id 成了**:T1 叙事模型现在 `surface` 已存在的暗线 **by id**(`[{op:surface, id:失踪商队}, {op:surface, id:假药郎中}]`),用的正是 ambient 里的 `[id]` —— **不再开重复任务**(上一轮那条凭空 open 的"商队失踪"没了)。之后 advance/明账 正常。
- **ambient 不再泄露明线**:T3/T6 的 ambient 只列暗态线(明态的失踪商队/码头浮尸不在 ambient,只在明账)。state 过滤生效。
- 完整生命周期跑通:surface(by id)→ advance(明账)→ 离开+demote → complex world-push,无重复、无污染、无泄露。
- **新发现(tuning,非阻塞)**:idle-demote 用的是 raw turn 号,而 turn 号会被后台舰队(director/lore fire 各占一个 turn 槽)膨胀 → N=3 turns ≈ 1 个玩家回合,导致明线浮现后约 1 回合就被 demote(假药郎中 T1 浮现、T2 就退暗)。**应改成按游戏内时间(meta.day)计 idle** —— 归到下一步的 lifespan/L2 一起做(那本来就是 clock 驱动的休眠/过期)。
