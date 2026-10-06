# 仓库阶段性 Health Check — 2026-06-22

6 路并行审计(死代码×2、测试健康、架构、正确性/事件溯源、约定/技术债)综合。总体:**核心事件溯源管线扎实、可重放、纪律好;问题集中在几个真 bug、一摊从没接线的死功能(Mode A)、几处架构接缝、文档/命名陈旧。**

| 维度 | 评级 |
|---|---|
| 正确性 / 事件溯源 | B+ (2 个真·replay bug) |
| 架构 / 结构 | B- (几处接缝,无运行时环) |
| 测试 | Adequate (1186 passed;有缺口) |
| 死代码 | ~600 LOC 生产死码可删 + Mode A 整摊 |
| 文档/命名 | 多处陈旧;rename 有 replay 风险 |

---

## A. 必修 bug(正确性 / replay)

- **A1 [Critical] director `consumed` 标志没事件溯源** (`systems/director.py:63,97` + `loop/director.py:140`)。每次 `project()` 从事件重建,`consumed` 复位 False;已触发的 directive 在任何"project 介于 mutate 与 inject 之间"的路径(rewind/replay/测试)里会**重复注入**。"只显示一次"不变量破。修:把 consumed 事件溯源(发 `directive_consumed` 事件 或 折 `consumed_through_turn` 水位)。
- **A2 [Important] `place.py:to_events` 的 `int(m.get("arrive_day", day))` 会崩** (`systems/place.py:348`)。`arrive_day` 是可选项、`validate` 不查类型;模型给 `"下周"` → `int()` ValueError,而 `apply_turn` **不在 backstage try/except 内** → **整回合崩**。修:`validate(moves)` 加 arrive_day 类型校验。
- **A3 [Important] `lore_advanced` 的 `clues_dropped.append` 在 apply 里无去重守卫** (`systems/lore.py:169`)。幂等性全靠 loop 层的 `already` 集合,不在 apply 自身;一旦该守卫被绕过(retract-replay 等)线索翻倍。修:apply 里 `if hint and hint not in ln["clues_dropped"]`。
- A4 [Minor] cascade 每条 queue entry 的 `consumed` 也是 transient(水位 `consumed_through_turn` 已兜底,降级 Minor)。
- A5 [Minor] `kernel/validation.py:83` 宽 except 把 validator 自身 bug 变成 repair 提示,开发期掩盖真错(建议 debug 模式 re-raise)。
- A6 [Minor] backstage 钩子吞掉 `project()` 的 ValueError(部分 append + 崩 的边角脆弱)。

## B. 死代码(可删生产码 + 其死测试;**不动 `docs/`、`_legacy/`**)

- **B1 Mode A 披露整摊**(specced+tested 却从没接线;B 站推赢了):`loop/lore_disclosure.py::index_fragment`(~52)、`llm/lore_tools.py`+`llm/tools.py`(~134)、`llm/provider.py` 的 `ScriptedToolProvider`+`_run_tool_loop`+`complete_with_tools`(OpenAI/Zhipu)(~126)。连带死测试:test_lore_disclosure_A / test_lore_tools / test_tool_loop。AuthorStrategy 永远走 B(`station_push_fragment`)。一次扫干净。
- **B2 `systems/story.py`(StorySystem,235 LOC)** — 统一时退役、`build_engine` 不注册、孤儿。删。(注:`tests/systems/test_story_system.py` 已被改成测 LoreSystem,**别删,改名即可**。)
- **B3 `loop/director.py::seed_threads`(37 LOC)** — 仅测试调用,`run_director` 不用。删 + 其 2 个孤立测试。
- B4 `AnthropicProvider`(~75)— 真 provider、当前无调用,**谨慎留**(合理的未来路径)。
- B5 `stages[].impact` — 代码里从不存在(只在 spec),无可删。
- B6 死脚本(在 `docs/` 下,**按约束不删**,仅标记陈旧):`lore-AB/compare.py`(monkeypatch 不存在的属性、已崩)、`overnight-runs/{capstone,verify_p2}.py`(读已废 `story` 切片,静默返回空)。

## C. 架构接缝

- C1 `loop/cascade.py` 硬索引 `world["systems"]["cascade"]`(:778)/`["ontology"]` — 若该系统未注册则 KeyError 被 backstage 吞掉。改 `.get()` 或 run_turn 加 registry 守卫。
- C2 `loop.endgame ↔ loop.lore ↔ loop.density` 环 — 靠 `run_lore` 里函数内 import 遮着(`loop/lore.py:74`)。建议抽公共 `loop/lore_math.py`(region_scope/build_catastrophe_events 等无状态helper)解环。
- C3 层耦合:`loop/fleet.py` + `context/assembler.py` 直接 `import systems.narrative` 读其模块常量(RECAP_*);`systems/knowledge.py:172` 函数内 import `systems.faction.members_of`;`context/viewpoint.py` import `systems.knowledge.knows`。应走 registry/世界切片。
- C4 helper 三胞胎:`_l2_ancestor`(lore_disclosure)/`_ancestor_of_level`(density)/turn 里 import 私有 `_l2_ancestor` — 合并到一个 `graph_utils`。
- C5 巨文件:`cascade.py`(895)、`density.py`(675)、`turn.py`(517)、`lore.py`(498)— 可拆但不紧急。

## D. 测试缺口

- D1 [Important] `run_turn` 的 backstage 钩子里只有 `run_density` 有故障注入测试;`run_lore`/`run_catchup`/`_run_demote_on_leave` 的 except 路径**没测**。补 3 个 monkeypatch fault 测试(照 density 那个)。
- D2 [Important] 没有**多系统 replay 幂等**测试(现有只用 FakeNoteSystem)。补:全引擎事件流 project 两次断言世界相同 → 会抓 A1/A3 这类 apply 副作用。
- D3 [Minor] 空断言:`test_assembler`(只 isinstance/len)、`test_endgame` summary(只 isinstance)。换成内容断言。
- D4 [已知] FakeLLMProvider 规范 shape 掩盖真机 gap(本仓反复踩;structured-repair 已部分对冲)。1 个 deselected 测试是 `test_embed_real`(slow,有意)。63s 跑时偏长(CLI 子进程测试占大头)。

## E. 文档 / 命名 / 技术债

- E1 lore→quest 改名:**Python 符号安全**(类/文件/run_lore/内部 slice 引用),但 **事件类型字符串 `lore_created`/`lore_advanced`/`lore_seeded`/`density_refreshed` 写进事件日志 → 改名破坏旧日志重放**;`name="lore"`/slice key 也进快照。**建议:事件串永久保留(它们是协议);内部符号改名收益低、可不做。**
- E2 陈旧 docstring:sprint 标签(T1–T5 / L1–L4)、"A/B mode"、"story"、"option-a";尤其 `systems/lore.py` docstring 谎称"L2/L3/L4 是分开未做的阶段"(其实都做了)。
- E3 陈旧文档:`docs/2026-06-19-architecture-world-model.md` 的 Story 行、`docs/codegraph/INDEX.md` 测试数(476,实 1186)。
- E4 误名:`backstop_storylines`(发的是 quest_created);`endgame demo.py` 打印 `ln.get('status')`(已删字段,恒 None,应 `state`)。
- E5 `app/play.py:105` `//steer` 占位(诚实告知未实现,留 v1)。

---

## 建议顺序
1. **修 A1/A2/A3**(真 bug,replay/崩)+ 补 D1/D2 测试(正好覆盖这些)。
2. **死代码扫除 B1/B2/B3**(~600 生产 LOC + 死测试;零生产影响)。
3. **架构 C1/C3/C4**(便宜的硬化:cascade .get、解层耦合、合并 helper)。
4. **文档/误名 E2/E3/E4**(便宜、高可读性收益)。
5. 选做:C2 解环、C5 拆巨文件、E1 内部改名(低收益)、B6 死脚本标注。
