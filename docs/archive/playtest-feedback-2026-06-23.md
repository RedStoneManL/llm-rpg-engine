# Playtest feedback — 2026-06-23

## #R1 续档无回顾(resumed game has no recap) — UX gap

**Observed:** Loading an existing campaign (non-empty store) prints
`[载入存档] 已读取 N 条事件。` + `[transcript] …` and then drops STRAIGHT into
`play_loop`, which silently blocks on stdin. No intro, no recap, no prompt
character — so it looks frozen, but it is actually waiting for the player's next
action.

**Mechanism (current, `app/__main__.py`):**
- empty store → `new_game` (genesis + opening narration + `_print_intro` 世界摘要) → play.
- non-empty store → `[载入存档]` → `play_loop` directly. No intro/recap is printed;
  the world is re-projected from the events but the player sees nothing.

**Why it matters:** This is the "我是谁/我在哪/上次到哪了" problem again, but for
*resumed* games. After a break the player has zero context and the terminal looks
hung.

**Proposed fix (NOT built yet — recorded for later):** on load, print a compact
"continue" recap before entering play_loop, e.g.:
- protagonist name + current location (region > town > venue, by name)
- current objective (the public 目标 fact)
- last DM narration (or a 1-line "上次:" summary from the last narration_recorded)
- turn number / day
Reuse the POV/ambient query path so it respects fog. Keep it short; gate the
"last narration" echo so we don't dump a wall of text.

**Also note:** `play_loop` prints no input prompt on a fresh wait, so even a new
game looks idle until the player types. Consider a minimal prompt (e.g. `> `) —
TTY-gated — so it's clear the engine is waiting for input, not working.

**DECISION (2026-06-23): the resume recap uses option A** — build the "旅程至此"
section by REUSING the engine's existing per-scene summaries
(`world["systems"]["narrative"]` scene_summarized chain) + recent raw narration +
structured facts (name / location / objective / day). No new LLM call;
deterministic; rewind-safe. Recap block sits at the BOTTOM (right above the input
cursor). NOT BUILT YET — recorded for later.

---

## #R2 开场「世界摘要」剧透核心谜底 (intro spoils the hidden truth) — design bug

**Observed (live, 艾瑟加德 playthrough):** the `[世界摘要]` intro shows, on the very
first screen, a `核心冲突` line like:
> 以勇者与圣女携手拯救世界的王道冒险**为伪装**,掩盖着三大王国与至高教廷暗中争夺圣剑血脉与世界霸权的残酷权力游戏

i.e. it hands the player THE central mystery (the heroic-adventure framing is a
facade over a power game) in line one. Player reaction: "这世界摘要感觉都给我剧透完了".

**Root cause:** `gen_frame` generates ONE `central_conflict` string and stores it
as a `secrecy="public"` fact; `_print_intro` (`app/__main__.py`) prints it to the
player. But the LLM (a strong model) writes the *full hidden truth* into
`central_conflict` — the surface premise and the secret-underneath conflated into
one public string. This is the SAME surface-vs-deep principle we applied to
`objective`: the deep conspiracy is the DM's secret, revealed through play — it
must not be shown up front.

**Fix direction (NOT built — recorded):**
- Split the world framing into a **public surface premise** (the 王道冒险 framing —
  safe to show, sets tone) and a **secret true conflict** (`secrecy="secret"`,
  DM-only; seeds the hidden threads). `gen_frame` should author both; emit the
  surface one as the public `central_conflict`/premise fact and the truth as a
  secret fact (or fold it into the campaign threads, which already carry secrets).
- `_print_intro` shows ONLY the surface premise, never the hidden truth.
- Likely also trim the protagonist `长期目标` if it over-reveals the endgame
  (e.g. "夺取圣剑血脉的控制权") — keep the player-facing goal at the surface layer.

## #R3 intro block possibly printed/duplicated multiple times — verify

In the same paste the `[世界摘要]` block (and the 核心冲突 / 当前目标 lines) appeared
repeated several times + the whole block twice.

**RESOLVED — NOT a code bug.** Controlled single run (fake provider, fresh temp
campaign) prints `[世界摘要]` exactly ONCE; `_print_intro` has 3 call sites but only
one fires per launch (first-run uses `[世界摘要]`; reroll uses `[新世界]/[新xxx]`).
The repeats were MULTIPLE `bash run.sh` launches during the run.sh path-fix
debugging — tmux scrollback accumulated several intros. No fix needed.

## #R4 开场叙事被截断 + 完整开场从未展示 (opening truncated to 120 chars) — real bug

**Observed (live):** the 【开场】 line in the intro cuts off mid-sentence, e.g.
"…火光摇摇晃晃地把每个人的影子拉成不同的形状，像是堂中" 〔截断〕 → straight to the counts footer.

**Root cause:** `bootstrap_world` sets `summary["narration_excerpt"] =
narration[:120]`; `_print_intro` prints that 120-char *excerpt* as 【开场】. The
FULL opening prose (authored by `gen_opening`, stored as a `narration_recorded`
event) is **never shown to the player** — after the intro, `play_loop` starts and
blocks on input without replaying the opening. So the player only ever sees the
truncated first 120 chars of their opening scene.

**Fix direction (NOT built — recorded):** render the FULL opening as a proper DM
narration block (reuse `_print_dm_narration`) right after the summary header, so
the player reads the whole opening scene. Keep the 120-char `narration_excerpt`
only if something else needs a short form; it must NOT be the player-facing
opening. (Pairs naturally with #R2: the [世界摘要] summary block should be the
meta header — 主角/位置/目标/counts — and the opening prose its own full block.)

## #R5 原始结构化提交泄漏给玩家 (raw world-change commit leaks into narration) — SEVERE

**Observed (live):** mid-play, the player saw the raw structured commit dumped as
narration — `facts`/`relations`/`knowledge`/`clock` arrays, INCLUDING
`secrecy:"secret"` / `"restricted"` facts (护身符发热, 皮箱被撬). Both an
immersion break AND a fog/secret leak.

**Root cause:** `loop/strategy.py` (AuthorStrategy.produce, ~lines 306 & 314):
```python
data = _parse_json_object(raw) or {"narration": raw}
```
When `_parse_json_object(raw)` returns None (the model emitted JSON that won't
parse), the fallback shoves the ENTIRE raw model output into `narration`, which is
printed verbatim to the player. `_parse_json_object` already tolerates ```json
fences + surrounding prose + outermost `{...}`, but still fails on: invalid JSON
(unescaped newline/quote inside a string, trailing comma) or a TRUNCATED response
(max-tokens cut the JSON mid-object — ties to #R4 / the 32768 cap on long turns).

**Fix direction (NOT built — recorded; HIGHEST priority):**
1. **Never leak raw into narration.** Replace `or {"narration": raw}` with a safe
   failure: treat unparseable output as a failed commit → run the existing
   repair loop (ask the model to re-emit valid JSON). If it STILL fails after
   repairs, emit a NEUTRAL fallback narration (e.g. "（周遭一时没有明显变化。）"),
   NEVER the raw blob. The raw text may be logged (debug trace) but never shown.
2. Harden `_parse_json_object` to salvage common reasoning-model breakage
   (trailing commas; if truncated, trim to the last balanced object) — secondary
   to #1.
3. Detect/mitigate truncation: the strategy could notice a finish_reason=="length"
   / unbalanced braces and force a repair or raise the per-call max-tokens.

This is the most severe of the playtest issues — it leaks secrets the fog system
is specifically designed to protect. Recommend fixing first.

## #R6 智谱 429 速率限制 (HTTP 429 from per-turn call burst) — robustness

**Observed (live):** `rpg.llm: _do_post HTTP 429 (Too Many Requests); retry 1/4
after 1s` repeatedly. NOT caused by Claude review subagents (those hit Anthropic,
not zhipu) — confirmed only the player's own game process was running.

**Cause:** the zhipu account RPM/concurrency limit (coding endpoint) is hit by the
engine's per-turn burst: produce (1) + up to `--max-repairs`(6) repair calls + up
to `RPG_MAX_TOOL_ROUNDS`(12) tool-calling rounds + backstage hooks
(digest_fleet/director/cascade/density) — ~10-20 requests within seconds.

**Already handled:** `_do_post` retries 429/5xx/timeout with exponential backoff
`min(2**attempt, 30)` → 1/2/4/8s, 4 retries, so most 429 recover; the log is noisy
but the turn usually completes.

**Fix direction (NOT built — recorded):**
1. Honor the `Retry-After` response header on 429 (use it as the wait when present)
   instead of only the 2**attempt schedule.
2. Optionally throttle the per-turn burst: serialize/space the backstage hook
   calls, or a small global min-interval between API calls under rate pressure.
3. Expose `--max-tool-rounds` as a CLI flag (currently only the `RPG_MAX_TOOL_ROUNDS`
   env) so call volume is easy to dial.

**User-side knobs (no code change) to reduce 429 now:** lower `--max-repairs`
(6→2) and set `RPG_MAX_TOOL_ROUNDS=4-6` — fewer calls per turn. Trade-off: fewer
repairs = more dropped sections; fewer tool rounds = less POV querying.

## #R7 `moves`/`places` 段几乎每回合验证失败被丢弃 + 丢弃提示不透明 (recurring section drops)

**Observed (live + trace `/root/games/play2/trace.jsonl`):** the play loop prints
`[提示：2个段落因验证失败已丢弃]` with no names. The trace shows the repair prompts
naming the failing sections EVERY turn: `[moves、places]` (sometimes `relations`).
So the dropped world-changes were the protagonist/NPC **movement** and **place**
declarations — narration kept, but those world-state updates were discarded.

**Two problems:**
1. **Opaque message.** `app/play.py` prints only `len(dropped_sections)`. Should
   name them, e.g. `[提示：moves、places 段验证失败，本回合这些世界变更未生效]`.
2. **`moves`/`places` fail validation almost every turn** (not a one-off). The
   model keeps emitting a `moves`/`places` decl the validator rejects — most likely
   it references a place by name / invents a place not in the declared set, or uses
   the wrong id; `place`/`character` validate bounces it; repairs (cut short by
   `--max-repairs` + the 429s of #R6) can't fix it → dropped → the world silently
   loses movement/new-location updates and can drift from the narration.

**Fix direction (NOT built — recorded):**
- Name dropped sections in the player notice (quick).
- Investigate the `moves`/`places` validator-vs-model mismatch: tighten the prompt
  (how to declare a place before moving / use the venue ids actually present), OR
  make the validator auto-create a referenced-but-undeclared place (lenient on new
  locations), OR improve the repair prompt for these two sections specifically.
  Read the actual validate errors in the trace to pick the cure.

## #R8 文风可调:想要「日式轻小说」voice(narration STYLE dial, not just length)

**Observed (live):** narration is good but reads as heavy literary Chinese; the
player wants a 日式轻小说 voice (short sentences, strong imagery, internal
monologue/tsukkomi, dialogue-driven, light/brisk pacing).

**Gap:** the only narration knob today is `verbosity` (concise/medium/rich) — that
controls LENGTH, not VOICE/STYLE. The pitch sets world genre, not prose style, so
the DM defaults to one literary voice regardless.

**Fix direction (NOT built — recorded):** add a narration **style/voice** dial,
orthogonal to verbosity — a configurable style directive injected into the
narration prompt (`loop/strategy.py` `_VERBOSITY_FRAGMENT` sibling), settable via
`--style` flag / `RPG_NARRATION_STYLE` env / `/style` OOC command, default neutral.
e.g. style="日式轻小说" → inject "文风:日式轻小说——短句、强画面感、第二人称内心独白与吐槽、
对白驱动、节奏轻快，少用堆砌的长定语". Verbosity (length) × style (voice) compose.
**Immediate (no code):** `--verbosity concise` cuts the wordiness partway, but
won't give the light-novel voice — that needs the style dial.
