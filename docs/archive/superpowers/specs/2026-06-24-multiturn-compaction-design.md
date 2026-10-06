# Multi-turn conversation + 70% compaction — Design

**Date:** 2026-06-24
**Branch:** app
**Status:** design approved (pending spec review)

## Goal

Stop re-assembling and re-sending the full fog-filtered context to the DM LLM on
every turn. Instead keep **one running conversation** across turns (KV-cache
friendly: stable prefix → cache hit), append only a small per-turn delta, and
**compact** the conversation when it reaches **70% of the 200K context window** —
rebuilding it as a tiered block (stable index + story summary + recent verbatim).

Motivation (from live play): `produce` dominates wall-clock (~146s/call, some
>500s). A large slice of every call is the re-sent full context. Multi-turn reuse
removes that re-send; compaction keeps a long game from ever overflowing.

## Background — what already exists (reuse, don't rebuild)

- **`AuthorStrategy.produce`** (`loop/strategy.py:337`): on every *fresh* turn it
  **discards the thread and rebuilds** `self._messages = [system, user(full_ctx +
  player)]` (line 348/364). Repairs already *continue* the thread
  (`repair_sections`, line 399). The assistant message stored is the **raw JSON
  commit** (line 387/395). The strategy instance persists across turns
  (`app/play.py:397` makes one `AuthorStrategy()`); only `produce` resets it.
- **`assemble_context`** (`context/assembler.py`) already composes the user's
  desired three tiers in one fog-filtered block:
  - *index tier* — place / character / faction / lore injects (map graph, present
    & known entities, factions, quest明账);
  - *recent-verbatim tier* — `NarrativeSystem.inject` force-renders the recent-N
    raw narration blocks (`systems/narrative.py:144`);
  - *summary tier* — the assembler's recap step renders aged scene summaries +
    `super_summary` (`systems/narrative.py` slice: `scenes[].summary`,
    `super_summary`, `summarized_through_index`).
  → **The "compaction template" is essentially `assemble_context` itself.**
- **`station_push_fragment(registry, world, scene)`** (`loop/strategy.py:356`):
  the per-turn 暗线 ambient disclosure. Already the right shape for the delta.
- **Provider usage**: `_record_usage` (`llm/provider.py:24`) already normalizes
  `usage.prompt_tokens → input`, but only pushes it to the debug-trace generation
  handle. It is **not** exposed to the caller yet — the 70% trigger needs that.

## Design

### D1. Setting: `conversation_mode`

`engine/settings.py` gains `conversation_mode ∈ {"multiturn","stateless"}` with
`get_conversation_mode()` / `set_conversation_mode()` and env override
`RPG_CONVERSATION_MODE`. Default **`multiturn`**.

- **`stateless`** = today's exact behavior (rebuild `[system, full_ctx, player]`
  every fresh turn; no thread; no finalize). This path MUST stay byte-for-byte
  identical to the current suite — it is the fallback and the A/B baseline.
- **`multiturn`** = the new running conversation below.

### D2. Persistent thread vs. transient working messages

The current single `self._messages` conflates two things. Split them:

- **`self._thread: list | None`** — the *persistent, clean* conversation that
  survives across turns: `[system, turn1_user, turn1_narration, turn2_user,
  turn2_narration, …]`. Assistant entries are **narration prose**, not raw JSON
  (decision below). Repair exchanges and tool-call/tool-result messages are NEVER
  in here. `None` until the first turn.
- **`self._working: list`** — the *transient, per-turn* message list actually sent
  to the provider: `self._thread + [new_user_delta]`, then repairs and raw-JSON
  assistant outputs and tool exchanges append to it during the turn. Discarded
  (rebuilt from `self._thread`) at the start of the next turn.
- **`self._pending_user: str | None`** — the user message authored for the current
  turn (delta or full ctx), held so it can be committed to `self._thread` on
  success.
- **`self._compaction_due: bool`** — set when a call's prompt-token usage crosses
  the threshold; consumed at the next fresh turn (see D6).

`stateless` mode leaves `self._thread = None` forever and uses only `self._working`
built fresh each turn — i.e. the existing code path.

### D3. Per-turn flow (multiturn)

`produce(..., repair=None)`:

1. **Repair turn** (`repair is not None`): unchanged in spirit — append the repair
   instruction to `self._working`, call, append raw output to `self._working`,
   return. (Repairs touch only `_working`, never `_thread`.)
2. **Continuing turn** (`self._thread is not None` and not `self._compaction_due`):
   - `delta = _build_delta(registry, world, scene, player_input)` (D4).
   - `self._pending_user = delta`; `self._working = list(self._thread) + [{role:
     "user", content: delta}]`.
3. **First turn / compaction / stateless** (`self._thread is None` or
   `self._compaction_due`):
   - `ctx = assemble_context(...) (+ station_push_fragment)` — the existing full
     build (this IS the compacted tiered block).
   - `full_user = ctx + "\n\n[player] " + player_input`.
   - `self._pending_user = full_user`; `self._working = [{system}, {user:
     full_user}]`.
   - if multiturn: `self._thread = [{system}]` (reset to a bare system base — the
     turn's `full_user` + narration are appended by `commit_to_thread` on success,
     so they are NOT pre-added here, else `full_user` would be duplicated) and clear
     `self._compaction_due`.
   - in stateless: leave `self._thread = None` (working-only path, as today).

Then the existing call logic runs **on `self._working`** (tool loop on fresh turns
when `provider.supports_tools()`, else `complete_messages`; repairs always plain).
After each provider call, check usage and maybe set `self._compaction_due` (D6).

### D4. The per-turn delta (`_build_delta`)

A continuing turn does NOT re-send the full context. The delta is small and reuses
existing renderers:

```
[场况] <current location · time-of-day · present NPCs>   # from scene slice
<station_push_fragment, if any>                          # backstage 暗线 disclosure
[player] <player_input>
```

The model already holds the world in-thread; the delta carries (a) the player's
action, (b) the current scene framing, (c) any backstage change surfaced as a push.
Anything else the model needs it can pull via the read-only POV tools (still
available every fresh turn) — that is also the drift safety-net.

`_build_delta` composes the scene framing from the same scene dict the assembler
uses; it must be cheap (no embedder recall — that is a compaction-time cost).

### D5. Committing a turn to the thread (`commit_to_thread`)

The thread must only gain *successful, clean* turns — so the commit happens after
the turn pipeline validates, not inside `produce` (which runs mid-repair-loop).

- New method `AuthorStrategy.commit_to_thread(narration: str) -> None`: if multiturn
  and `self._pending_user is not None`, append `{role:"user", content:
  self._pending_user}` and `{role:"assistant", content: narration}` to
  `self._thread`, then clear `self._pending_user`. No-op in stateless mode.
- `loop/turn.py` calls `strategy.commit_to_thread(commit.narration)` exactly once,
  **only when the turn finally succeeds** (after `validate_commit` passes, before
  `to_events`). On a turn that ultimately fails/falls back, it is not called, so the
  failed exchange never pollutes the thread.
- `commit_to_thread` is defined on the `TurnStrategy` ABC as a no-op default, so
  `HybridStrategy` and any other strategy are unaffected.

### D6. Compaction trigger

- `llm/provider.py` exposes **`self.last_usage: dict | None`** (keys `input` /
  `output` / `total`), set on every completion (in the same place `_record_usage`
  fires; survives the tool loop — reflects the final call's prompt size). `None`
  before any call or when the provider returns no usage.
- After a fresh-turn call in `produce`, read `tok = provider.last_usage and
  provider.last_usage.get("input")`. If `tok and tok > CONTEXT_WINDOW *
  COMPACTION_RATIO` → `self._compaction_due = True`.
- `CONTEXT_WINDOW = 200_000`, `COMPACTION_RATIO = 0.70` (module constants in
  strategy.py; window overridable via settings/env later if needed).
- Effect: the **next** fresh turn takes the D3-step-3 full-rebuild path, which
  re-runs `assemble_context` (now containing the up-to-date index + recent-N
  verbatim + recompressed summary) and resets the thread to it. Compaction = one
  full re-assembly + thread reset. No bespoke compaction renderer needed for v1.
- Fallback when `last_usage` is `None` (provider gave no usage): never compact on
  that signal; rely on the eventual provider that does report usage. (A char-count
  proxy is a possible future addition, out of scope for v1.)

### D7. Thread stores narration, not raw JSON

Assistant entries committed to `self._thread` are the **narration prose**
(`commit.narration`), not the raw JSON commit. Rationale: the thread becomes a
clean story transcript (natural for the model to continue, far fewer tokens), and
the structured truth is owned by the engine — re-injected via the index/recent
tiers on the next compaction (the world is the source of truth, the model need not
re-read its own JSON). Raw JSON still lives in `self._working` during the turn so
the in-turn repair loop works exactly as today.

## Decisions (locked)

| # | Decision | Value |
|---|----------|-------|
| 1 | Context window | **200,000** tokens |
| 2 | Compaction threshold | **70%** (140K) |
| 3 | Recent-verbatim count K | **3** turns (maps to the narrative system's recent-N rendering used at compaction; align that parameter to 3) |
| 4 | Thread stores | **narration prose** (not raw JSON) |
| 5 | `conversation_mode` default | **multiturn** (stateless = byte-identical fallback) |
| 6 | Trigger signal | provider-reported `prompt_tokens` (no tokenizer dep) |

## Drift & safety

- **Drift** (thread view vs. projected world): bounded by three nets — the per-turn
  delta carries backstage changes (push fragment) + current scene framing; the POV
  tools let the model re-query live state every turn; and compaction fully
  re-syncs by re-rendering the index from the current world.
- **Fallback**: `conversation_mode=stateless` reverts to today's behavior with one
  env var. Build keeps that path provably identical (a determinism test).
- **Secrets**: unchanged. The thread holds only narration (already
  player-facing/fog-safe) + the same assembled context the stateless path sends.
  No new disclosure surface.

## Phasing

- **P1 — multi-turn thread** (no compaction): D1–D5, D7. Thread grows unbounded
  (fine for short/medium sessions). Delivers the KV-cache/no-re-send win.
- **P2 — compaction**: D6 (provider.last_usage + trigger + rebuild path). Caps long
  games. Small once P1 exists.

## Testing

P1:
- continuing turn appends `[delta, narration]` to `_thread` (not full ctx, not raw
  JSON); thread length grows by exactly 2 per successful turn.
- repair turns do NOT grow `_thread`; tool exchanges do NOT enter `_thread`.
- `commit_to_thread` only fires on success (failed turn → thread unchanged).
- `conversation_mode=stateless` → produce path byte-identical to current (assert
  via the existing fake-provider turn tests run under stateless).
- delta contains scene framing + player input + push (when lore in range), and is
  materially shorter than the full assembled context.

P2:
- `provider.last_usage` is populated after a completion (fake provider returns
  usage); `None` when absent.
- usage over 140K sets `_compaction_due`; next fresh turn rebuilds full ctx + resets
  thread; `_compaction_due` cleared.
- usage under threshold → continuing turn (no rebuild).

## Out of scope (deferred)

- A bespoke ultra-compact index renderer (v1 reuses `assemble_context`; revisit if
  the index tier is too verbose in practice).
- Streaming output (separate roadmap item for latency/truncation).
- Char-count proxy when the provider reports no usage.
- Persisting the thread across process restarts (resume rebuilds via stateless full
  ctx on first turn — acceptable; the recap recovers continuity).
