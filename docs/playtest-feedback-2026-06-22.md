# Playtest feedback — 2026-06-22 (live, RECORD-ONLY until user says go)

> User is playing a `日式西幻 / 王道异世界冒险谭` campaign (`./isekai`, `--debug`).
> Reporting issues in real time; do NOT change code until they give the go-ahead.
> Each item: what / where / desired behavior / impl note.

## #1 — Bootstrap (开局初始化) has no progress indicator
- **What:** During world generation the user only sees `[新游戏] 正在生成世界，请稍候…` then a long silent wait (the bootstrap is ~7–8 sequential LLM calls). No sense of progress.
- **Desired:** A live progress readout during genesis — **what it's doing now** + **step N / total** (e.g. `[3/8] 正在生成势力…`, `[6/8] 正在生成暗线…`). A simple step counter is enough; a bar is nice-to-have.
- **Where / impl note (for later):** `loop/bootstrap.py::bootstrap_world` runs the steps in sequence — frame → regions → local_map → (protagonist) → factions → npcs → threads → opening. Emit a progress line via the `out`/print channel BEFORE each step (the orchestrator needs an `out`/callback passed in, or use `log`/print). `reroll_all`/`reroll_step` re-run steps too — progress should cover those. `app/__main__.py` already has an `out` callable that could be threaded into `new_game`/`bootstrap_world`. Each gen step has a stable name → easy to label. Total = count the steps (≈8).
- Status: RECORDED, not yet implemented.

## #2 — World summary (世界摘要) too dry → want a full opening INTRO
- **What:** The post-bootstrap summary is too bare (just world_name/tone/conflict/counts + a 1-line narration excerpt). It doesn't situate the player.
- **Desired:** Open with a proper **intro** that writes out:
  1. **主角身世** — who the protagonist is (background / origin story), not a generic stub.
  2. **当前地图 L1 / L2 信息** — the region (L1) they're in + the local area (L2 town + key venues), described.
  3. **过往历史** — relevant world/back-history.
  4. **当前目标 / 简单任务** — "what I'm doing right now": the protagonist's immediate objective + a starting hook/quest.
- **Where / impl note (for later) — part presentation, part NEW generation:**
  - *Already have* (just needs richer presentation in `app/__main__.py`): L1 region (name/terrain/seed from `regions_summary`), L2 town + venues (`local_map`), the opening narration (`gen_opening`), faction/NPC/thread content.
  - *Likely NEW generation needed:*
    - **过往历史** — not currently produced; add a history field (extend `gen_frame` or a small new step) from the world frame's central_conflict.
    - **主角身世** — VERIFY: the bootstrap protagonist is created (Task 9) reusing `app.engine` constants → probably a GENERIC sketch/goal (`一位踏上旅途的冒险者` / `探索这个世界`). If generic, AUTHOR the protagonist's background to fit the generated world (new LLM bit, or fold into `gen_opening`/`gen_frame`).
    - **当前目标** — surface one protagonist-bound 暗线 (gen_threads already makes 1–2 `anchor=protagonist` lines) and/or the protagonist's goal as the explicit "starting objective."
  - Cleanest shape: a `gen_intro`/expanded `gen_opening` that emits a structured intro {protagonist_origin, locale (L1+L2), history, objective} which `bootstrap_world`'s `summary` carries and `__main__` prints as a formatted opening block (before the reroll prompt). Keep it within the strict-prompt+repair harness; numbers/structure stay engine-decided.
- **Reinforced by the user mid-play: "我是谁我在哪我叫什么我要干嘛"** — the opening must answer identity / name / location / objective. This is the acceptance bar for #2.
- Status: RECORDED, not yet implemented.

## #3 — Tool-loop rounds too few + max output too short
- **What:** Saw `tool loop hit max_tool_rounds=3; forcing final`. The narrator runs out of tool rounds after only 3 (it wants to query map/recall/ambient several times before writing) → forced to finalize prematurely. Also the max OUTPUT length is too short (responses risk truncation).
- **Desired:** `max_tool_rounds` ≥ **12**. And a generous max-output-token cap so a turn doesn't truncate.
- **Where / impl note:** `loop/strategy.py::AuthorStrategy.produce` reads `int(os.environ.get("RPG_MAX_TOOL_ROUNDS", "3"))` — bump the default (≥12) AND/OR set `RPG_MAX_TOOL_ROUNDS=12` in `run.sh`. Output cap = `--max-tokens` (run.sh:25 `${GLM_MAX_TOKENS:-32768}`; `app/__main__.py:73`).
- **User clarification (resolves the #8 tension):** keep the output CAP **high** (headroom, prevent accidental truncation / 撑爆) — that's a SAFETY ceiling, NOT a target. The actual prose length is a SEPARATE lever controlled by the narration-style prompt (see #8 / `strategy.py:50` & `:91`). So: high cap + long tool/research loop + a prompt that asks for CONCISE, plot-forward prose. No contradiction.
- Status: RECORDED, not yet implemented.

## #4 — No "engine is working" loading indicator during a turn
- **What:** After the player acts, they wait with NO feedback — can't tell if the engine is still working or hung.
- **Desired:** A loading/heartbeat indicator while the turn is being generated — WITHOUT revealing the actual content (no spoilers of the real actions). Just "DM 正在落笔…" / a spinner / dots so the user knows it's alive.
- **Where / impl note:** `app/play.py::play_loop` calls `run_turn` synchronously (the provider blocks while the model thinks; non-streaming). Options: print a generic working line before `run_turn`; or a background spinner thread that prints dots until the call returns (stop it after). Keep it content-free (no real moves/secrets). Possibly tie a lightweight "still alive" tick to the backstage phases too. The `--debug` trace already records phases but that's separate from the player-facing indicator.
- Status: RECORDED, not yet implemented.

## #5 — Chat UI: distinguish player vs DM (make it readable)
- **What:** The play interface is a flat wall of text; hard to tell which part is the player's input vs the DM's narration.
- **Desired:** Visually distinguish the two — at minimum a clear marker for "你(player)" vs "DM", e.g. prefixes / a separator line / ANSI color. Terminal-friendly (user is in a TUI).
- **Where / impl note:** `app/play.py::play_loop` prints `result.narration` directly and doesn't frame the player's line. Add: echo the player input with a `▶ 你：` prefix (or similar), print DM narration under a `【DM】` header or a separator rule, optionally ANSI color (dim for player, normal for DM) gated on a TTY check. Keep it plain-ASCII-safe + degrade when not a TTY (e.g. piped/tests).
- Status: RECORDED, not yet implemented.

## #6 — Generated content feels empty; player can't learn 我是谁/在哪/要干嘛 (DIAGNOSED via the ./isekai trace)
- **What:** In play, the player can't get details out of the DM; doesn't know identity or objective. ("啥都问不出来，我是谁也不知道，我要干嘛也不知道")
- **Diagnosis (from `./isekai` events + trace — genesis-only trace, no play turns were captured there):**
  1. **Protagonist is a GENERIC placeholder** — `character_created protagonist {sketch:"一位踏上旅途的冒险者", goal:"探索这个世界"}`, no name, no 身世, no real goal. The bootstrap (Task 9 orchestrator) creates the protagonist by reusing `app.engine` constants instead of AUTHORING one to fit the generated world. ROOT CAUSE of "我是谁不知道". (This is the concrete confirmation of #2's suspicion.)
  2. **Self-knowledge is fog-gated (a real fog bug).** `llm/tools.py::_characters_query_fn`: for the protagonist asking about itself, `co_present = (protagonist in scene["present"])` is FALSE (`_build_scene` excludes the protagonist from `present`), and there's no `knows(protagonist, "protagonist.sketch")` grant → it returns `{"id":"protagonist","known":false}`. **So even a rich 身世 would be invisible to the protagonist via POV tools.** The protagonist must ALWAYS know its own identity (treat pov==cid as fully known, OR grant the protagonist self-knowledge at genesis).
  3. **No starting objective surfaced** — the 暗线 (incl. 1–2 `anchor=protagonist` lines from gen_threads) are hidden by design and never surfaced as "your current goal" → "要干嘛不知道".
  4. World HAS content (3 NPCs / 6 lore / 3 factions / 9 places) but it's fog-gated with no intro bridging it to the player → "啥都问不出来".
- **Fixes (for later):**
  - **Author the protagonist** at bootstrap: an LLM-written name + 身世 + goal fitting the world (new gen bit or fold into gen_frame/gen_opening); stop using the generic `app.engine` constants.
  - **Fix self-knowledge fog:** in the POV tools, `pov == cid` (and pov's own place/facts) should be treated as known — the protagonist can never be a stranger to itself. Audit `_characters_query_fn`/`_recall_query_fn`/`_map_query_fn` for the self case.
  - **Surface a starting objective:** promote one protagonist-bound thread (or the protagonist goal) into the opening intro as the explicit current quest (ties to #2's "当前目标").
  - Consider granting the protagonist `knows` on its own sketch/goal + current locale at genesis so POV tools/recall return them.
- **UPDATE after reading the REAL session (`./campaign`, 18 turns, the 日式西幻 world):**
  - The DM content is actually **rich**, not empty — turn 1 ("我是谁我在哪") got a genuinely good **amnesia opening** (灰窑镇 / 圣光王朝 / a scar that "jumps"); turn 2 got a detailed inventory/clue scan. So "啥细节都没有" was an over-statement.
  - BUT the **generic protagonist is confirmed** (`一位踏上旅途的冒险者 / 探索这个世界`) → the DM IMPROVISES identity (amnesia) instead of having a designed 身世/name. The player wants a real identity, not improv. → still must AUTHOR the protagonist.
  - **No explicit objective** still confirmed (DM dropped a clue 第三窑口 but never stated a quest). → surface a starting objective.
  - Part of the "啥都问不出来" feeling was amplified by **#7 below** (garbage input turns 3–5 returned non-answers).
- Status: RECORDED (diagnosed + confirmed on real session), not yet implemented. **High priority.**

## #7 — Terminal control sequences (scroll / tmux / arrows) are eaten as game input  ★ BIGGEST playability blocker
- **What (seen in `./campaign` turns 3–5):** the player typed terminal/tmux things to scroll — `Ctrl+b [` (tmux copy-mode), `tmux set -g mouse on`, and raw arrow-key escapes `[B[A[B[A...` — and **all of it went INTO the game as "player actions."** The DM gamely replied "玩家执行的是终端命令，非游戏内动作," but those turns are wasted/garbled. This is the real source of the "messy, I'm-just-waiting, can't-tell-what's-happening" feeling.
- **Why:** `app/play.py::play_loop` does `for line in inputs:` reading raw stdin lines, so ANSI/escape sequences and stray control input become turns.
- **Desired:** the REPL must not turn scrolling/navigation into game turns. Options (pick when implementing):
  - Use a real line editor (`readline` / `prompt_toolkit`) so arrows = history/cursor (not input), and the user gets line editing + history.
  - Sanitize/ignore lines that are pure ANSI/escape/control sequences (drop them, don't run a turn).
  - Document that scrollback should be done via the terminal/tmux OUTSIDE the prompt, and/or page long DM output.
- Combined with #4 (loading indicator) and #5 (player/DM framing), this is the "make it actually pleasant to play in a terminal" cluster. Likely the highest-leverage UX fix.
- Status: RECORDED, not yet implemented. **★ top playability priority.**

## #8 — Turns are slow + the story advances too little per turn (MEASURED from ./campaign trace)
- **What:** "一个场景一轮好几分钟，只往里深入了一点点" — each turn takes minutes and barely moves the plot.
- **Measured (`python -m app.trace ./campaign/trace.jsonl --stats` + per-turn):**
  - `produce` = ~2263s total (21 gens) — the DOMINANT cost (the main authoring call + tool rounds).
  - `repair` = ~470s (8 repairs) — a big tax; a failed-validation commit re-runs the WHOLE authoring call.
  - Per turn: **76–294 s**; output **2984–13687 tokens/turn** (turn:17 = 294s / 13687 tokens).
  - Backstage during play is cheap (cascade ~20ms, digest ~146ms, density ~0) — the slowness is the main narration call, NOT the hooks. (The big `density` 147s / `gen_*` numbers are GENESIS, not per-turn.)
- **Root cause (latency and pacing are the SAME problem):** the model emits 7k–13k tokens of atmospheric prose per turn → slow to generate (reasoning model, token-by-token) AND verbose-but-static (texture piled on, plot/clock barely advances). Plus the repair re-author tax.
- **Fixes (for later):**
  1. **Prompt: advance more, describe less.** EXACT LOCATION: `loop/strategy.py:50` (`_SYSTEM_PROMPT` 【narration 文风】, 甲) and `loop/strategy.py:91-94` (`_NARRATE_PROMPT` 【文风】, 丙). The culprits: "重环境氛围" + "**篇幅随情境而定，不设字数限制**" + "展示而非告知". Change to: each turn must visibly advance plot/scene/clock; atmosphere 点到为止; a SOFT length budget (not "no limit"). Biggest lever — fixes BOTH latency (fewer tokens) and pacing.
  2. **Repair must be MODULAR, not full-rewrite (user: "不要让她整个重写，太恐怖了").** EXACT LOCATION: `loop/turn.py::produce_turn` — its repair loop calls `strategy.produce(..., repair=text)` which RE-AUTHORS the whole turn (narration + ALL sections) every repair → ~59s each, 8 repairs = 470s. Change so repair re-emits ONLY the failing section(s), keeping the (valid) narration + passing sections. Likely needs `AuthorStrategy.produce` to accept "repair just these sections" and merge, instead of regenerating everything. Also worth: tighten the first-pass commit prompt so fewer repairs are needed.
  3. **Stream the narration** so the player sees it as it's written → perceived latency drops massively even if total time is similar (pairs with #4). NOTE: the provider is currently non-streaming (`_do_post` blocks); streaming is a provider change.
  4. (Async backstage — low impact here since play-time hooks are already cheap.)
  5. **→ See #9: make verbosity a CONFIG DIAL** (the general form of fix #1 — don't hardcode concise, expose a knob).
- **⚠️ TENSION with #3:** #3 asked for MORE tool rounds (≥12) + LONGER max output; #8 wants FASTER + more plot motion. Resolve by separating: longer *tool/research* loop is OK (cheap-ish), but the *output prose* should be more concise + plot-forward (not 13k tokens of atmosphere); streaming hides the rest. Balance these two when implementing.
- Status: RECORDED (measured), not yet implemented. **High priority (the "is it fun to play" issue).**

## #9 — Make narration verbosity/pacing a CONFIG DIAL (concise ↔ verbose), expose the interface now
- **What (user):** "想啰嗦啰嗦，想简洁简洁" — narration style should be a tunable config, not hardcoded. Open the knob now; add a future hook for adaptive / on-the-fly adjustment ("适时调整功能的接口").
- **Design:**
  - A `narration_verbosity` knob — e.g. `concise | medium | rich` (or a 1–5 scale / a soft per-turn length budget). Maybe a parallel `pacing` knob (how much to advance per turn). Default to a sensible MEDIUM (fixes #8's default-too-verbose without locking it).
  - **Read point:** `loop/strategy.py` builds `_SYSTEM_PROMPT` / `_NARRATE_PROMPT` (lines 50 / 91). Replace the hardcoded "重环境氛围…不设字数限制" with a fragment SELECTED by the config value (a small map: concise→"精炼,氛围一两笔,优先推进剧情/时钟"; rich→"浓墨铺陈"). So the style line becomes config-driven.
  - **Expose now (3 levels):**
    1. **env** `RPG_NARRATION_VERBOSITY=concise|medium|rich` (+ run.sh line).
    2. **CLI** `--verbosity`.
    3. **runtime OOC command** `/verbosity <level>` (this IS the "适时调整接口" — lets the player change it mid-session; the play loop already dispatches OOC commands in `app/play.py`).
  - **Future adaptive hook (open the seam, wire later):** the config value should be a SETTABLE runtime value (the same setter `/verbosity` uses), so a later adaptive layer (e.g. director/scene: combat→terse+fast, exploration→richer) can set it per-situation. Open the setter interface now; the adaptive policy is future work.
  - **Where config lives:** there's no central config object today (settings come from env/CLI ad-hoc). Likely add a small `Config`/settings carried on the engine (or a module-level settings read by strategy), sourced env→CLI→default, runtime-mutable. This becomes the home for future knobs too (pacing, tool rounds, etc.).
- **Supersedes** #8 fix #1 (instead of "make it concise," make it a dial defaulting to medium).
- Status: RECORDED, not yet implemented.

## #10 — Internal place IDs (town_0/venue_0) leak into player-facing text  (found by live smoke)
- **What:** the intro/objective/opening narration show raw IDs: "前往**town_0**的**venue_0**", "场所：venue_0、venue_1", "小镇**town_0**入口处". Should be the place NAMES.
- **Root:** (a) `gen_local_map` summary carries venue IDs but not their NAMES, so the #2 intro prints IDs; (b) `gen_protagonist` (objective) and `gen_opening` (narration) prompts are fed the IDs and the LLM echoes them.
- **Fix:** gen_local_map summary carries an id→name map for venues (and the start town name is already known); gen_protagonist/gen_opening prompts reference NAMES + explicitly forbid emitting internal ids like `town_0`/`venue_0` in player-facing text; the __main__ intro prints names.
- Status: FIXING (autonomous).

## #11 — Reroll loop swallows the player's first real action  (found by live smoke)
- **What:** after bootstrap, the reroll loop treats any non-`reroll`/non-`开始` line as "unknown → break into game" but DISCARDS the line. So a player who types their first ACTION (e.g. "我是谁？") loses it — it never runs as turn 1.
- **Fix (app/__main__.py reroll loop):** a non-reroll, non-break line should break into the game AND be used as the FIRST turn (prepend it to the play_loop input stream), instead of being discarded.
- Status: FIXING (autonomous).
