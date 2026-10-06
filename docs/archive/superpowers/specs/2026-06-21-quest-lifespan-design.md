# 任务生命周期 / 过期 + 游戏内时间休眠 设计 (Quest Lifespan / Expiry)

> 2026-06-21 · rpg-engine-app `app` 分支。统一事件线/任务系统(`systems/lore.py` QuestSystem)的下一层:**游戏内时间寿命 + 过期清理 + 把休眠/idle 改成 clock 驱动**。
> 前置已就绪:世界钟(`meta.day`+band,`kernel.clock.expired/to_units`)、统一 quest 模型(state 暗/明/了结)、demote-on-leave(明→暗)。
> 缘起:① 生命周期对齐用户早先定的"暗线按游戏内时间定 lifespan、到点清";② quest demo 复跑发现 idle-demote 用 raw turn 号会被舰队膨胀(浮现后约 1 玩家回合就退暗),**必须改成按 meta.day 计**;③ density 量产之前需要过期清理兜底。
> 配套:`2026-06-21-unified-questline-design.md`、`2026-06-20-world-clock-design.md`。

## 目标
一条任务有**游戏内时间寿命**;暗态线无人理会、寿命走完 → **过期了结/清理**;休眠与 idle 判定一律走**游戏钟(meta.day)**,不再用会膨胀的 raw turn 号。

## 设计

### 1. 字段(创建时定,挂 quest line)
- `born_day`: 创建时的 `meta.day`(已可从事件 day 取)。
- `lifespan_days`: 游戏内寿命(天)。**创建时定**:density 生成时由 LLM 按事件性质给(L3 接);手工/backstop 创建给一个按复杂度的默认(simple≈3、medium≈7、complex≈20——起始值,可调)。
- `last_advanced_day`: 最近一次推进(暗骰 advance / 明态 advance / surface)时的 `meta.day`。取代现在的 `last_advanced_turn`。

### 2. 过期(暗态线,clock 驱动)
在 `run_lore`(暗骰那趟)里,对每条 `state=="暗"` 的线:若 `clock.expired(born_units, lifespan_units, now_units)`(用 band-units 或直接按 day 比较)→ emit `quest_expired{id}`;apply 置 `state="了结"` + `resolved={"by":"expiry"}`(无人理会、悄无声息地凉了)。
- **复杂线例外**:complex 暗线寿命到点**不是简单清理**,而是触发**终局**(L4 的 world-rescue 掷骰 + 封顶影响)。本 spec 只为 complex 标记"到期待终结"(`pending_finale=True`)或直接 emit 一个 `quest_finale_due` 信号,真正的终局逻辑留 L4。simple/medium 到期 = 直接了结(expiry)。

### 3. 休眠语义(对齐 ★6,用 clock)
- demote(明→暗,已实现)= 离开锚地/idle。退暗后 lifespan **继续按游戏钟走**(born_day 不变)。
- 暗态期间寿命走完 → 过期(上一节)。**这就是"错过了"**:你没在寿命内回来跟进,它凉了。
- 寿命内回到锚地 → 它还是暗态、还在(玩家可再 surface)。**这就是"回来还在"**。
- (统一:simple/medium/complex 退暗后都按 clock 计寿命;区别只在到期处理——下两档了结,复杂线进终局。)

### 4. idle-demote 改 clock(修复 demo 发现的 bug)
`loop/turn.py` 的 idle-demote 当前用 `turn_num - last_advanced_turn >= IDLE_DEMOTE_TURNS`(turn 号被舰队膨胀,太急)。改为:
- 记 `last_advanced_day`(meta.day)而非 turn(§1)。
- idle 判定:`now_day - last_advanced_day >= IDLE_DEMOTE_DAYS`(默认 2 游戏日)。游戏钟只在叙事推进时间时走,所以"几天没碰才退暗"是对的语义,且不被舰队膨胀。
- left-town 路径不变(仍带 just-surfaced 护栏)。

### 5. 事件
- `quest_expired`{id} — 暗态 simple/medium 寿命到 → 了结(by:expiry)。harness(run_lore)产。
- (complex 到期 → `quest_finale_due`{id} 或 `pending_finale` 标记,L4 消费。)
- `last_advanced_day` 在 quest_surfaced/quest_advanced/lore_advanced 的 apply 里写。

## 复用 / 影响
- `kernel.clock.expired/to_units` 直接用。
- `run_lore` 已遍历暗态线(暗骰),过期检查顺手加在同一趟。
- demote 的 idle 路径改 day(turn→day),left-town 不变。
- born_day/lifespan_days 加进 quest line(create 时);现有手工创建给默认。

## 不在本期
- 复杂线真正的终局(world-rescue 掷骰 + 封顶影响)= L4。本期只标"到期待终结"。
- density 生成(L3)负责创建时让 LLM 定 lifespan_days;本期给默认值,L3 接 LLM。

## 复杂场景验证(实现后)
离线:暗线 born_day=1、lifespan=3;推进游戏钟到 day≥4(无人 surface)→ 过期了结。一条明线 idle ≥ IDLE_DEMOTE_DAYS 游戏日 → demote(按 day,不被 turn 膨胀)。寿命内回锚地 → 还在。complex 到期 → pending_finale(不直接清)。
(可选)真机:让 demo 的 clock 走几天,看明线是否按"几天没碰"而非"1 回合"退暗。

## 自检
- 过期/休眠/idle 全挂 meta.day(游戏钟),不用 turn 号 → 修了 demo 的 idle-eager bug + 对齐"按游戏内时间"。
- simple/medium 到期了结;complex 到期标 finale(留 L4)。
- born_day/lifespan_days/last_advanced_day 全随事件折叠 → rewind 安全。
- density(L3)量产前先有过期兜底,避免任务只增不减。
