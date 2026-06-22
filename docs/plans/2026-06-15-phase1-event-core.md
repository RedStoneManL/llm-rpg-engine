# RPG Engine — Phase 1: 事件核心 (as-built)

> **注**:原始的逐步 TDD 实现计划在一次子 agent 事故中连同 git 历史丢失(见 `docs/INCIDENT-2026-06-16-git-reset.md`)。Phase 1 的**实现已完成并通过全部测试**;本文件是 as-built 记录。代码本身(`engine/`、`tests/`)即权威记录。

**Goal:** rpg-engine 的事件溯源地基——append-only 事件 store(SQLite + JSONL 镜像)、事件 schema、确定性投影(events → 活体角色/暗线/承诺/反派/数值/时间线/节奏)、CLI 骨架 `rpg new|log-event|project|status`。

**Status:** ✅ 完成,29 passed。已过 spec-compliance review(✅ 合规)+ code-quality review(✅ approved,3 个 Important 已修)。

## 交付物

| 文件 | 职责 |
|---|---|
| `engine/schema.py` | 20 类封闭事件枚举 `EVENT_TYPES`、`make_event()`、`validate_event()`(append 时强制校验) |
| `engine/store.py` | `EventStore`:append-only SQLite(`seq` 自增定序)+ JSONL 可读镜像(含 seq、retract 后重写保持一致)+ `retract_from_seq()` + `close()`/上下文管理器 |
| `engine/projection.py` | `project(events)` 纯函数 fold + `apply()` 分发 + `write_projections()`。**活体角色**:`relationship_change` 更新 trust 并追加 evolution 日志;`character_reveal/development` 改写人设字段 |
| `engine/cli.py` | `cmd_new/cmd_log_event/cmd_project/cmd_status`,`RPG_HOME` 作用域,store 用 `with` 管理 |
| `bin/rpg` | argparse 入口,捕获异常输出干净 `error:`(非 traceback) |
| `tests/` | 29 测试:schema 6 + store(含 JSONL seq/retract 一致性、上下文管理器)+ projection 8 + cli(含坏 JSON 干净报错、`--rebuild` 清旧)+ determinism 2 |

## 关键保证(对应 spec §15)

- **活体角色**(§15 #3):艾拉经 `character_reveal`(trait 入档)+ 两次 `relationship_change` → trust `高→极高`,evolution 留 3 条痕。端到端冒烟已验证。
- **抗漂移**(§15 #2):`project()` 幂等;`retract_from_seq()` 后重投影,状态**自动回滚**(纯函数,无需手撤)。`test_determinism.py` 锁定。
- **唯一真相源**:SQLite 权威,JSONL 为派生镜像(append 含 seq、retract 后重写),二者不发散。

## 承接 Phase 2

`EventStore.iter_events()`、事件 `chunk_ids` 字段、`storage/campaigns/<id>/archive/` 已就位,供 P2 的逐字捕获、语义切片、bge-m3 向量与 `rpg recall` 挂接。`retract_from_seq()` 供 P3 倒带。`director_fired`/`oracle_roll` 事件类型 + `pacing` 投影供 P4 导演。悬空引用检测留给 P5 `rpg check`。

## 遗留改进(已记录,非阻塞)

- `--rebuild` 已实现为清空 projections 目录重建(原为 no-op,已修)。
- 悬空引用(`thread_advance` 指向未开线 / `promise_kept` 指向未知承诺)目前静默 no-op;留给 P5 `rpg check` 标记(spec §8 已加该 linter)。
