# 世界钟 + 时间引擎 设计 (World Clock + Time Engine)

> 2026-06-20 · rpg-engine-app `app` 分支。
> 这是从"事件线 / lore 系统"大设计里抽出的**地基子项**,只管一件事:**游戏内时间如何被可靠地推进、表示、计算**。
> lore 生命周期 / 休眠 / 复杂线世界掷骰 / 密度刷新等都是本钟的**消费方**,不在本 spec;但本 spec 暴露它们要用的时间原语(`expired()` 等)。
> 配套:架构图 `docs/2026-06-19-architecture-world-model.md`。

## 目标

让游戏内时间成为一等基础设施:一个 `(day, band)` 世界钟,由叙事模型**每回合显式推动**(推进与否都要给理由),引擎做**全部**时间算术(推进 / 时间差 / 判断大小 / 到期)。根除当前"时间冻住"的 bug。

## 背景:为什么现在时间是死的

- `kernel/projection.py:23` —— 每条事件 `world["meta"]["day"] = ev["day"]`,meta.day 跟事件的 day 走。
- 但 `app/play.py:45-75 _build_scene` 里 `day = meta.get("day") or 1`,事件又用 scene 的 day 盖章 → **循环,day 恒为 1**。
- 没有 band(时段)概念,只有 day。
- `loop/time.py` 的 `detect_jump`(阈值 2 天)定义了却**没人调用**;`run_catchup` 靠 `stale_entering_scope` 触发,但 day 不动 → catch-up 实际空转。

→ 没有任何机制真正推进时间。

## 设计

### 1. 钟的表示

- `meta["day"]: int`(沿用,已接好各处)+ 新增 `meta["band"]: int ∈ 0..3`。
- band 显示名:`["晨","中午","下午","夜晚"]`(4 段,用户定)。
- 合起来 `(day, band)` = 世界钟。campaign 起点 `day=1, band=0`(晨)。
- 选择保留 `meta["day"]` 而非换成 `meta["clock"]={}`:不破坏 `current_day()`、`projection.py:23`、`detect_jump` 等既有读点;band 是纯增量。

### 2. 时间引擎 —— 纯模块 `kernel/clock.py`

无 I/O、纯函数、完全可离线单测。这是用户要的"算时间的引擎"。核心:**把钟压成单一整数刻度**(band-units),所有时间判断退化成整数运算。

```python
BANDS = ("晨", "中午", "下午", "夜晚")

def to_units(day, band)  -> int:   return day * 4 + band
def from_units(u)        -> tuple: return (u // 4, u % 4)          # 自动进位:夜→次日晨
def advance(day, band, ddays, dbands) -> tuple:
    return from_units(to_units(day, band) + ddays * 4 + dbands)
def elapsed(a_units, b_units) -> int:  return b_units - a_units    # 时间差(band 刻度)
def compare(a_units, b_units) -> int:  # 判断大小: -1 / 0 / 1
def expired(born_units, lifespan_units, now_units) -> bool:        # 给 lore 用
    return now_units - born_units >= lifespan_units
def band_name(band) -> str: return BANDS[band]
```

lore 的"生命周期到没到" = 一次 `expired(born, lifespan, now)`。"谁先谁后" = `compare`。这套原语是 lore spec 的全部时间依赖。

### 3. commit 里的 `clock` 段(增量,不是绝对值)

叙事模型**每回合必填**。因校验闸 `_section_shape_errors`(`kernel/validation.py:11-34`)强制所有段为对象数组,`clock` 也是 `[{...}]`,validator 再强制**恰好 1 个元素**:

```json
"clock": [{"advance": true,  "days": 0, "bands": 2, "reason": "蹲守到入夜，约半日"}]
"clock": [{"advance": false, "days": 0, "bands": 0, "reason": "紧接上一刻，无流逝"}]
```

- **增量制**:模型报"过了多久"(`days` 整天 + `bands` 段),不报绝对钟——绝对钟它必记错。绝对钟引擎持有。`bands` 可 >3,引擎归位(6 段 = 1 天 2 段)。
- **`reason` 必填**:推进、不推进都要写为什么。这是治"时间冻住"的**核心 forcing function**——模型每回合被迫显式想一次时间。
- 把 `"clock"` 加进 `required_sections`(`validate_commit(..., required_sections=...)`,`validation.py:99-107`):漏写 → `empty_no_reason` 报错 → 经修复闸逼出。

### 4. 归属:扩展现有 `TimeSystem`(不新建系统)

`systems/time.py` 的 `TimeSystem` 已 own `time_advanced`(catch-up 载体),本就是"时间"。让它再 own `clock` 段 + 新事件 `clock_advanced`(docstring 的"D1 无 commit 段"演进为 D2):

- `commit_sections() -> {"clock"}`(原为 `set()`)。
- `event_types() -> {"time_advanced", "clock_advanced"}`。
- `validate("clock", decl, world)`:恰好 1 元素;`advance` 是 bool;`reason` 非空;`days`/`bands` 为 `>=0` 整数;`advance=true` 时 `(days,bands)` 不全 0;`advance=false` 时须全 0。错误码沿用 `missing` / `bad_enum` / `bad_range` / `bad_shape` 风格。
- `to_events("clock", decl, ...)`:产出一条 `clock_advanced`,其 `day` = **引擎算出的新 day**,`deltas = {new_band, days, bands, reason}`。
- `apply(world, clock_advanced)`:设 `meta["band"] = deltas["new_band"]`(`meta["day"]` 仍由 `projection.py:23` 从 `ev["day"]` 设)。

### 5. 回合循环集成(本回合即生效,非滞后)

**关键决策**:时间是在**本回合内**流逝的("蹲守到入夜" = 本回合横跨午→夜),故本回合产出的事件按**推进后**的钟盖章。

在 `loop/turn.py`(validate 通过之后、to_events 之前):

1. 读 commit 的 `clock` 段 → `clock.advance(meta.day, meta.band, days, bands)` 得新 `(day, band)`。
2. 用**新 day** 作为本回合所有事件的盖章 day(替换 scene 里读出的旧 day,`run_turn` 现于 :228-230 从 scene 取 day)。
3. `clock_advanced` 事件落新 `(day, band)`;projection 后 meta 到新钟。

(若改要"本回合叙事 + 下回合才到点"的滞后模型,只动这一处——见末尾待决 #1。)

### 6. context 注入

- `TimeSystem.inject(scene, world)` 返回 scene 层 `Fragment`:`【此刻】第 N 天 · 中午`;`affordance` 写 `clock` 段用法 + 点明当前 band(让模型算 delta 有参照,如"现在中午,到入夜 = +2 段")。
- travel_cost 作参考:扩 `PlaceSystem` 已有的"当前位置 + 出口"注入,每个出口附标称脚程:`出口:苍狼岭(约 2 天)`。

### 7. travel_cost:参考 + 软诊断(不硬卡)

- travel_cost 已存在于边上(`systems/place.py:153-177`,`place_linked` 默认 1;`navigate()` Dijkstra 已用)。统一成钟的刻度(band-units / `{days,bands}`)。
- 由模型**建图 / 连边时写**(`links` 段),仅作**标称脚程**。真实耗时模型用 `clock` 自报(路上被劫、迷路、抄近道都会偏离标称)。
- 因 `clock` 已强制每回合带 reason,"时间冻住"已被根治 → travel_cost **不做硬校验**,降级为**非阻塞 log 诊断**:跨标称 ≥1 天的边却 `advance:false`/0 时记一条 warning(供调参),不进修复闸。

### 8. catch-up 顺带盘活

day 真动起来后,`loop/time.py` 的 `current_day` / `detect_jump` / `stale_entering_scope` 才有判据。本 spec **不改** catch-up 逻辑,仅加一个集成测试确认 `days>=1` 跳跃能让 catch-up 正常触发。

## 不在本 spec(交给后续)

- **lore 系统消费方**:生命周期 / 休眠(冻结=停更新但 life span 照走)/ 复杂线终局世界掷骰 / 密度刷新——都消费本钟与 `expired()`,但在 lore spec 实现。
- **scene-progression**(推进 `meta.scene`):独立后续件;day 跳跃 / 换地点是它的天然边界信号,届时消费本钟。

## 测试(全部离线、确定性)

- `kernel/clock.py`:纯函数,穷举 band 进位、多天推进、时间差、`expired` 边界、`compare`。
- `TimeSystem` clock 段:`validate` 各错误码;`to_events` 算出的新 day 正确;`apply` 设 band。
- 集成:一回合 `advance 2 bands` → meta 钟前进且本回合事件盖新钟;`advance 1 day` → catch-up 触发。
- 校验闸:漏 `clock` → `empty_no_reason`;`clock` 双元素 / 缺 reason / `advance` 与 `(days,bands)` 矛盾 → 各自报错并可经修复闸改好。

## 待决(请拍板)

1. **本回合即生效 vs 滞后一回合**(§5):我选前者——时间在本回合流逝,事件按推进后的钟盖章。
2. **band 表达**:模型只报 delta(`days`+`bands`),不报目标时段;靠注入"现在中午"让它自己算"入夜 = +2"。若实测模型常算错,再加可选 `to_band` 目标、由引擎算最小正向 delta。默认前者。
