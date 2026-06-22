# 世界钟 · 真机验证 (glm-5.1, 2026-06-20)

世界钟 feature 上线后,在**真实 glm-5.1**(非离线 FakeLLM)上跑的烟雾测试。
离线测试证明了机制正确,但答不了"真模型每回合产得出合法 `clock` delta 吗、时间推进得合不合理"——这正是 reasoning-model 集成最容易藏坑的地方。

配套:spec `../2026-06-20-world-clock-design.md`、plan `../../plans/2026-06-20-world-clock.md`。

## 怎么跑
```bash
cd /root/rpg-engine-app
set -a; . ./.env.local; set +a            # 载入 GLM_MODEL=glm-5.1 等
export PYTHONPATH=/root/rpg-engine-app
python3 docs/superpowers/specs/clock-smoke-2026-06-20/clock_smoke.py    # 首轮(发现 band 过推)
python3 docs/superpowers/specs/clock-smoke-2026-06-20/clock_smoke2.py   # 修复后(定点复验)
```
每轮全新临时存档,不碰 `./campaign`。

## 发现 → 修复 → 复验

**结果(全部正向):** clock **从未被丢弃**(5/5 回合都产出合法声明,forcing function 在真模型上站得住);时间真的走(day 1→4,不再冻结);`advance:false` 在连续动作上用对;隔夜→次晨的跨天进位正确;reason 写得有理有据。

**`clock_smoke.py` 暴露的一个真缺陷(T5):** 一个"窜出夺物即逃"的**几秒动作**被推进了 `+1 band`(晨→中午),而且它的 reason 自相矛盾——嘴上说"仍在晨时段内但接近尾声",delta 却跳到了中午。根因:模型按**连续时间**思考(想表达"清晨稍晚"),但我们的 band 是**离散四段**,于是把细碎动作过度推进。这正是 spec 待决 #2 担心的 band 颗粒度问题。

**修复(commit 8bde116):** 给两个 prompt(甲+丙)的 clock 段加一句判定要诀——`bands` 只数**真正跨过的时段**;**先想清动作结束落在哪个时段,再据此给 delta**;同一时段内的细碎动作(冲刺/夺取/几句交谈)给 `advance:false`,切勿为小动作多推一段。

**`clock_smoke2.py` 定点复验(全部命中):**
| 回合 | 动作 | 期望 | 模型产出 |
|---|---|---|---|
| T1 | 井台边几句问路 | advance:false | ✅ false「几分钟,仍在清晨同一时段」 |
| T2 | 清晨疾行到日头西沉 | 推进数段 | ✅ +2 段 晨→下午 |
| T3 | 拔刀几息斩野狗 | advance:false | ✅ false「数个呼吸,仍属下午同一时段」|
| T4 | 和衣睡到东方泛白 | 次日晨 | ✅ +2 段 下午→夜→次日晨,day 2 |

细动作不再过推,粗动作(赶路/隔夜)推进与跨天进位都正确。

## 结论
- 世界钟在真模型上**可用**:每回合产合法 clock、时间推进合理、不冻结。
- **待决 #2(delta vs to_band)就此解决**:delta 模型 + 这句 prompt 澄清已足够,`to_band` 目标暂不需要。
- **待决 #1(本回合即生效)**:真机上表现自然(时间在动作发生的当回合推进),维持本回合即生效。
- 仍建议:更长的真机跑(多日委托、隔夜潜伏链)进一步压测 band 判定的稳定性。
