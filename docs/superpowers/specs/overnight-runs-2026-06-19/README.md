# 夜间实机验证 transcripts(2026-06-19,glm-5.1)

这些是 redesign(P1/P2)夜间自主推进时,在 **glm-5.1(叙事)+ glm-4.7(廉价舰队)** 上跑的真实回合输出。原本写在 `/tmp/`(临时、会丢),拷进仓库存档,方便回看。决策汇总见上一层的 `2026-06-19-overnight-decisions-for-red.md`。

| 文件 | 是什么 | 看点 |
|---|---|---|
| `00-baseline-glm5.1.out` | redesign 前的基线(砍乙后、P1 前),6 回合 | 散文质量、director 自然开火、knowledge 流动 |
| `01-P1-cascade-verify.out` | P1 cascade `world`-段驱动,单回合 | 叙事模型声明全城大火 `areas:[city,market,shrine,slums]` → cascade 逐地渲染**涌现后果**(神殿敞门收容难民、贫民窟泼水拆隔离带) |
| `02-P2-recap-storylines-verify.out` | P2 recap+故事线,5 回合 | recap 分层(summarized 0→1→2);叙事模型自发织**3 条交织暗线**(夺玉像→玉像另有隐情→锦衣卫角力) |
| `03-cascade-bug-diagnosis.out` | cascade 0 事件的诊断探针 | 证明组件全对(area 是真 Place、trigger 命中、glm-4.7 verdict evolve:true)→ 根因是集成 |
| `04-capstone-BEFORE-fix.out` | 全引擎 7 段冒险(cascade 触发被 narration 挤掉前) | `cascade_events=3`、`place_evolved=0` —— bug 现场;但故事线惊艳(T7 自发开"蒙面截货人"回扣 T3 灰袍访客) |
| `05-capstone-AFTER-fix.out` | 同上,修复后重跑 | **`cascade_events=12`** —— cascade 真实波及;A+B+C+D+P1+P2 集成确认 |

## 重新跑(任意一个)
```bash
cd /root/rpg-engine-app
set -a; . ./.env.local; set +a            # 载入 GLM_MODEL=glm-5.1 等
export PYTHONPATH=/root/rpg-engine-app
python3 docs/superpowers/specs/overnight-runs-2026-06-19/capstone.py      # 全引擎 7 段
python3 docs/superpowers/specs/overnight-runs-2026-06-19/verify_p1.py     # P1 cascade
python3 docs/superpowers/specs/overnight-runs-2026-06-19/verify_p2.py     # P2 recap+故事线
```
脚本都用全新临时存档,不碰 `./campaign`。
