# Flavor Pack System — design

> Status: design approved 2026-07-07, awaiting spec review before writing-plans.
> Branch: `app`.

## Goal

Turn the "world aesthetic" — the genesis seed tables **and** the narration voice —
into a swappable **flavor pack**. `FLAVOR=isekai` loads a whole light-novel/异世界
feel; the current grounded western-fantasy/wuxia tables become the `classic` pack
(the default). Adding a new aesthetic = drop in a new pack directory, no code
change. First two packs ship: `classic` (byte-identical to today) and `isekai`.

## Motivation

Two concrete problems, both surfaced in play:

1. **Aesthetic lock-in.** The protagonist seed pools (`protagonist_origins/hooks/
   quirks`), `tone_axes`, and the world-flavor tables are all one aesthetic —
   grounded 西幻/武侠 (没落世家/边境猎户/退伍老兵). There is no way to get a
   日式轻小说 / isekai protagonist (社畜穿越 / 高中生穿越 / 魂穿). The pools are the
   only source of variety, so every world is the same flavor.
2. **The narration voice is a hardcoded literary lean.** `loop/strategy.py`'s
   `_NARRATE_PROMPT_TEMPLATE` (:221) bakes `【文风】融合细腻描写与戏剧张力：重环境氛围…`
   straight into the infrastructure prompt. Even when the player injects
   `STYLE=日式轻小说`, this hardcoded line drags the prose back to literary — the
   root of the "文笔繁复" complaint. Voice is a flavor choice masquerading as infra.

A flavor pack fixes both: it bundles the content pools **and** the voice, and the
voice is injected at prompt-assembly time from the pack rather than hardcoded.

## Architecture

A **flavor pack** is a directory under `data/oracles/` holding the oracle tables
that pack overrides, plus a `pack.json` manifest describing the non-table
aesthetic (voice, tone behaviour, auto-select hints).

```
data/oracles/
  classic/            # the default pack = today's genesis tables, renamed
    pack.json
    protagonist_origins.json  protagonist_hooks.json  protagonist_quirks.json
    tone_axes.json
    world_magic.json  world_power.json  world_tension.json
    npc_roles.json    npc_traits.json
    terrains.json     place_kinds.json  thread_types.json
  isekai/             # first alternative pack
    pack.json
    protagonist_origins.json  protagonist_hooks.json  protagonist_quirks.json
    tone_axes.json
    # (only the tables it changes; the rest fall back to classic)
```

**Table resolution:** a pack-aware load tries `<flavor>/<name>.json`, falling back
to `classic/<name>.json`. So `isekai/` need only contain the tables it changes
(protagonist_*, tone_axes); everything else (npc/terrains/world_*) falls back to
`classic`. `classic` is both the default pack and the fallback base — it must be
complete.

> Reuses the existing `load_table(name, genre)` seam (`engine/oracle.py`), which
> resolves `data/oracles/<genre>/<name>.json` then `default/`. Change: add an
> optional `base` parameter — `load_table(name, genre, base="default")` — so the
> chain is `[genre, base]`. Genesis loads pass `base="classic"`; every other caller
> is untouched (its `base` stays `"default"`). Today all genesis calls pass the
> literal `"genesis"`; this design makes that argument the **active flavor**
> (`load_table(name, flavor, base="classic")`), and `data/oracles/genesis/` is
> renamed to `classic/`.

## `pack.json` manifest

```json
{
  "name": "日式轻小说·异世界",
  "desc": "穿越来的现代人视角，轻快明亮的异世界冒险。",
  "voice": "日式轻小说笔法：短句为主、对白与主角内心吐槽驱动、环境点到即止、口语化、节奏轻快明亮、适度玩梗、少堆华丽长定语。",
  "tone": {
    "bright": ["冒险", "热血", "日常", "治愈", "欢乐"],
    "dark":   ["悬疑", "权谋"],
    "hints_bright": ["轻松","明亮","王道","日常","治愈","热血","搞笑","欢乐","校园","甜"],
    "hints_dark":   ["黑暗","残酷","绝望","压抑","悬疑","阴谋"]
  },
  "select_hints": ["异世界","穿越","转生","召唤","轻小说","社畜","勇者","魂穿","isekai"]
}
```

Fields:
- `name` / `desc` — display strings (shown at genesis: `[开局] 风味：<name>`).
- `voice` — the narration voice directive, injected where the hardcoded literary
  line used to be (see Voice extraction). Optional; empty → neutral base only.
- `tone` — the tone universe + mood-bias config for this pack. `bright`/`dark`
  classify the pack's `tone_axes` entries; `hints_bright`/`hints_dark` are the
  pitch keywords that bias the tone roll. **Migrated out of the hardcoded
  `_BRIGHT_TONES`/`_DARK_TONES`/`_*_PITCH_HINTS` in `loop/genesis/world.py`.**
- `select_hints` — pitch substrings that auto-select this pack (see Selection).

Missing/blank fields degrade gracefully: no `voice` → neutral; no `tone` → full
uniform tone draw; no `select_hints` → never auto-selected (explicit `--flavor`
only).

## Selection & precedence

The **active flavor** resolves at new-game time:

1. Explicit `--flavor <name>` (run.sh `FLAVOR=`) wins.
2. Else scan the pitch against every pack's `select_hints`; first match wins.
   (Deterministic order: `classic` first, then others alphabetically, so a
   pitch with no isekai words stays `classic`.)
3. Else default `classic`.

Unknown `--flavor` → error listing available packs (fail fast, don't silently
fall back).

## Persistence & resume (important)

Genesis content pools are consumed once at new-game. But the **voice** applies on
every turn, including resumed sessions. So the resolved flavor must persist:

- At genesis, store the active flavor in the `campaign_seeded` event deltas
  (`{"flavor": "<name>"}`), so it lands in the event log.
- On every launch (new **or** resume), resolve voice as: `--flavor`/`STYLE=`
  override → else the stored campaign flavor's `pack.voice` → else neutral. This
  keeps a resumed isekai campaign reading as isekai without re-passing `FLAVOR`.
- A projection surfaces the stored flavor (e.g. `world["flavor"]`), read at engine
  wiring time.

## Voice extraction (the style refactor)

Today (`loop/strategy.py`):
- `_SYSTEM_PROMPT_TEMPLATE` (:169) and `_NARRATE_PROMPT_TEMPLATE` (:221) each begin
  with `__STYLE__` (injected via `_style_fragment`) **followed by a hardcoded voice
  directive** — the 甲 line is mild; the 丙 line (`融合细腻描写与戏剧张力：重环境氛围…`)
  is the strong literary lean.

Change:
- **Delete the hardcoded voice directives** from both templates. The base template
  keeps only voice-agnostic mechanics (output format, 必填段, show-don't-tell,
  don't-decide-for-the-player, protect-secrets, JSON rules).
- The `classic` pack's `voice` = the current 丙 literary directive verbatim, so
  `classic` prose is unchanged.
- The injected voice (`__STYLE__` seam, unchanged mechanism) is sourced with this
  precedence: explicit `STYLE=`/`--style`/`/style` runtime override → active pack's
  `voice` → neutral. `_settings` gains a "pack voice default" the resolver sets at
  launch; an explicit user style still overrides it.
- `verbosity` (length) stays fully orthogonal and unchanged.

Net effect: `classic` = byte-identical prose; `isekai` = genuinely light prose (no
hidden "重环境氛围" fighting it); any pack defines its own voice.

## Tone-bias migration

`loop/genesis/world.py` currently hardcodes `_BRIGHT_TONES`, `_DARK_TONES`,
`_BRIGHT_PITCH_HINTS`, `_DARK_PITCH_HINTS` and `_tone_for_pitch`. Move the data
into `pack.tone`; `_tone_for_pitch` reads the active pack's `tone` config instead
of the module constants. Behaviour for `classic` is preserved (its `pack.json`
carries the same lists). Signature becomes `_tone_for_pitch(oracle, pitch, table,
tone_cfg)`.

## The two launch packs

### `classic` (default) — migration only, no behaviour change
- Move `data/oracles/genesis/*` → `data/oracles/classic/*`.
- Add `classic/pack.json` with: `voice` = the current hardcoded 丙 directive;
  `tone` = the current `_BRIGHT_TONES`/`_DARK_TONES`/hints; `select_hints` =
  `["武侠","西幻","克苏鲁","权谋","硬核"]` (so a grounded pitch resolves classic
  explicitly; and because classic is also the default, an empty or unmatched pitch
  lands on classic too). `name` = "经典·硬核西幻/武侠".
- Regression gate: with no `FLAVOR` and no isekai pitch, genesis + narration are
  byte-identical to pre-change `main`.

### `isekai` — new content
`isekai/protagonist_origins.json` (现代人穿越类型):
```
w2 加班到累垮的社畜，一觉醒来已在异世界
w2 放学路上出意外的普通高中生
w2 延毕摆烂、整天打游戏的大学生
w2 家里蹲多年的资深宅
w2 过劳猝死在工位的程序员
w1 便利店上夜班的打工人
w1 带着前世记忆转生、如今长成少年的婴儿（魂穿）
w1 沉迷某网游、结果穿进游戏世界的老玩家
w1 被卷进召唤仪式的现代路人
w1 追番追漫、满脑子设定的死宅少女
w1 刚毕业还没着落的应届生
```
`isekai/protagonist_hooks.json` (怎么被卷进来):
```
w2 睁眼就多了个只有自己看得见的「状态面板」
w2 被卡车创飞，再睁眼已躺在异世界的草地上
w2 女神半哄半骗把你送来，附赠一个「外挂」技能当报酬
w2 稀里糊涂被当成「传说中的勇者」接进了王城
w1 醒来时身上绑定了一个莫名其妙的称号/技能
w1 发现这个世界跟自己通关过的游戏一模一样
w1 被贵族的召唤术召来，成了「召唤失败的赠品」
w1 睡前还在刷手机，醒来手机没了、人在异世界
```
`isekai/protagonist_quirks.json` (现代人违和感):
```
w1 遇事先下意识摸口袋找手机
w1 见到魔物先想「这在游戏里是几级怪、掉什么装备」
w1 满嘴网络梗和游戏黑话，当地人一脸茫然
w1 老拿现代常识吐槽异世界的不合理
w1 嘴上总挂着「这不科学」
w1 习惯给一切打分、写点评
w1 一紧张就碎碎念前世的琐事
w1 对异世界食物念念不忘，总想复刻家乡菜
w1 走哪都下意识想找个「存档点」
w1 见人先想「这人有没有支线任务」
```
`isekai/tone_axes.json` (brighter universe):
```
w2 冒险   w2 热血   w2 日常   w1 治愈   w1 欢乐   w1 悬疑   w1 学园
```
`isekai/pack.json`: `voice` = 日式轻小说 directive (above); `tone` classifies the
above (bright: 冒险/热血/日常/治愈/欢乐/学园; dark: 悬疑); `select_hints` as in the
manifest example.

`isekai` does NOT ship its own world_magic/power/tension/npc/terrains — those fall
back to `classic`, so an isekai run is still a coherent 异世界 (a modern-person lens
on a classic-flavored world). Enriching those for isekai is future work.

## Out of scope (explicitly cut)

- **No isekai "cheat/system/金手指" mechanic.** Decided: pure narrative flavor, no
  starting ability/系统/称号 element, no `CHEAT` knob. (Keeps it "软核" and avoids a
  new genesis element coupling into power/lore systems.)
- More packs (武侠 / 克苏鲁 / 校园日常) — the system is built to accept them, but only
  `classic` + `isekai` ship now.
- Per-pack overrides of npc/terrain/world tables for isekai — deferred; falls back
  to classic.

## Testing

- **Pack load:** a manifest loads; missing manifest → neutral defaults; malformed
  manifest → clear error.
- **Table fallback:** `load(name, "isekai")` returns the isekai table when present,
  else the classic one.
- **Selection precedence:** explicit `--flavor` > pitch `select_hints` > `classic`;
  unknown `--flavor` errors.
- **Pitch auto-select:** an isekai-worded pitch resolves `isekai`; a grounded/empty
  pitch resolves `classic`.
- **Voice injection:** the active pack's `voice` reaches the narration prompt; an
  explicit `STYLE=` overrides it; empty pack voice → neutral base.
- **Tone from manifest:** `_tone_for_pitch` uses the active pack's `tone` config
  (bright pitch on isekai draws an isekai bright tone).
- **Persistence/resume:** flavor stored at genesis; a resumed campaign recovers its
  voice without re-passing `FLAVOR`.
- **Regression (the big one):** no `FLAVOR`, non-isekai pitch → genesis rolls +
  narration prompt are byte-identical to pre-change (classic == today).

## Files / modules touched

- `data/oracles/classic/*` (moved from `genesis/`) + `classic/pack.json` (new).
- `data/oracles/isekai/*` (new) + `isekai/pack.json` (new).
- `engine/oracle.py` — pack-aware table resolution (fallback `<flavor>` → `classic`)
  + a `load_pack_manifest(flavor)` loader.
- `loop/genesis/world.py` — `_tone_for_pitch` reads `tone_cfg` from the manifest;
  remove the hardcoded tone constants.
- `loop/genesis/*` — genesis `load_table(name, "genesis")` calls take the active
  flavor (threaded from bootstrap).
- `loop/strategy.py` — delete hardcoded voice directives; voice comes from
  `_settings` (pack default, user-overridable).
- `app/engine.py` — resolve active flavor (explicit → pitch → classic); load its
  manifest; set the pack voice default; expose `world["flavor"]`.
- `app/__main__.py` — `--flavor` flag; store flavor at genesis; recover on resume.
- `run.sh` — `FLAVOR=""` knob (empty = auto-by-pitch).

## Success criteria

- `FLAVOR=isekai ./run.sh` (or a pitch like "异世界穿越轻小说") yields a 社畜/高中生/
  魂穿-type protagonist, a bright tone, and genuinely light-novel prose.
- `FLAVOR=classic` (or default) is byte-identical to today.
- A third pack could be added as data only (a directory + `pack.json`), no code.
