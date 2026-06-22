# P2 — 剧情连续性 / Narrative Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or superpowers:executing-plans for a separate session). Every task is TDD: write the REAL failing test first, run it, see it FAIL, write the minimal REAL implementation, run it, see it PASS, run the full-suite gate, then commit exactly the files the task names. No placeholders, no stubbed-out bodies, no "fill this in later". If a step's behavior is unclear, re-read the cited source file before writing code. Tests are OFFLINE + deterministic (`FakeLLMProvider` / a small keyed fake); NO network, NO real model.

**Goal:** Make "where is the story" a FORCE-PUSHED first-class citizen of every turn's context, instead of an implicit thing the DM reconstructs from relevance-`recall`. Two always-injected components: (1) a **storyline ledger** — structured plot-thread明账 (`{id, status, summary, last_advanced_scene}`) the narrator opens/advances/resolves, with a digest backstop for漏报; (2) a **recency-tiered recap** — recent N scenes verbatim + older summarized + older-still summary-of-summaries, maintained by the backstage digest fleet. Both are rendered into context EVERY turn by `assemble_context`, NOT behind the `query`/recall gate (spec §1 PUSH vs PULL; §2.2 recap; §2.3 storyline ledger).

**Architecture:** Two new harness-and-narrator systems following the existing ContextSystem pattern:

- **`StorySystem(ContextSystem)`** (`systems/story.py`) — owns a `storylines` **commit section** (LLM-authored, goes through the strict gate, mirroring `KnowledgeSystem`'s `knowledge` section) plus three event types (`storyline_opened`/`storyline_advanced`/`storyline_resolved`). Projects into its own world slice `world["systems"]["story"]` (a dict of thread records — rewind-safe because it folds from events). `inject()` force-renders the active+dormant ledger into the scene layer every turn. A **digest backstop** in the fleet independently scans the turn and flags clearly-active-but-undeclared threads (harness-authored, lightweight-validated).

- **`NarrativeSystem(ContextSystem)`** (`systems/narrative.py`) — owns the recap. Harness-authored only (no commit section): three event types (`narration_recorded` = each turn's verbatim prose; `scene_summarized` = a cheap-model summary of an aged-out scene; `recap_recompressed` = summary-of-summaries when summaries grow). Projects into `world["systems"]["narrative"]` (a recap slice: per-scene raw narration + rolling summaries + the recursive super-summary). `inject()` force-renders "最近 N 场原文 + 更远摘要" into context every turn. Maintained post-apply by an **extended digest fleet** (`loop/fleet.py`): records this turn's narration, and when a scene ages out of the recent window, summarizes it with a CHEAP provider (only then — not every turn).

Wiring: `run_turn` already runs `digest_fleet` post-apply with a tracer span; we extend `digest_fleet`'s signature to take the new components' inputs (narration text + a cheap `recap_provider`) and thread them from `run_turn` (which already plumbs `cascade_provider` — reuse it as the cheap recap provider by default). `assemble_context` gains a mandatory recap+storyline composition step that does NOT depend on `query`.

**Tech Stack:** Python 3.12 stdlib only (no new deps). Reuses the S0 kernel (`Registry`/`project`/`kernel_event`/`EventStore`), the S1 `facts/FactGraph` only where a system needs the shared graph (the story/narrative slices are plain dicts in `world["systems"][name]`, like `DirectorSystem`/`CascadeSystem` slices — they do NOT require ontology), the `KnowledgeSystem` commit-section pattern (`commit_sections`/`validate`/`to_events`), the `DirectorSystem.inject` force-injection pattern, `loop/fleet.py::digest_fleet` post-apply hook shape, and `llm/provider.py` (`complete_json` for the cheap summarizer; `FakeLLMProvider` / a tiny keyed fake offline). Constants are module-level so tests reference `mod.CONST` and the human can tune them.

---

## Design decisions (load-bearing — referenced by tasks)

These resolve every specifics question the brief asked. Each task cites the decision number it implements.

### D1 — Storyline ledger system ownership + event shapes — **new `StorySystem`, narrator-declared via a `storylines` commit section, plus a digest backstop**

A NEW system `StorySystem(ContextSystem)` (`systems/story.py`), NOT a reuse of `DirectorSystem`. Rationale: the director's `thread_open`/`thread_advance` are HIDDEN pacing seeds (暗骰 节奏种子, spec §2.3 explicitly: "跟暗骰 director 的 threads 不是一回事"); the storyline ledger is the PLAYER-facing明账. Conflating them would force one slice to carry two semantics and one inject to render both. Keep them separate systems, separate slices, separate event names.

- `name = "story"`; `requires() == set()` (the ledger is self-contained — it does NOT write the shared FactGraph, so it needs no ontology dependency; this also keeps the unit tests tiny); `commit_sections() == {"storylines"}`; `event_types() == {"storyline_opened", "storyline_advanced", "storyline_resolved"}`; `empty_state() == {"threads": {}}` (an ordered dict-by-id of ledger records).

- **State shape** (one record per thread, in `world["systems"]["story"]["threads"][id]`):
  ```python
  {"id": str, "status": "活跃"|"休眠"|"已结", "summary": str,    # 一句话
   "last_advanced_scene": str | None, "opened_scene": str | None}
  ```

- **Commit section `storylines`** (LLM-authored, mirrors `knowledge`): a list of items, each
  ```
  {"op": "open"|"advance"|"resolve", "id": str, "summary": str}
  ```
  `summary` required for `open`/`advance` (the一句话 line), optional for `resolve`. Mapped by `to_events`:
  | op | event | apply effect on the slice |
  |---|---|---|
  | `open` | `storyline_opened` | create record `{id, status:"活跃", summary, opened_scene:scene, last_advanced_scene:scene}`. If id already exists → treat as advance (log.debug, update summary + last_advanced_scene) so a re-open is idempotent, never crashes. |
  | `advance` | `storyline_advanced` | if id exists: set `summary` (if given), `last_advanced_scene=scene`, and `status="活跃"` (advancing a休眠 thread re-activates it). If id missing → create it活跃 (defensive, log.warning) so a dangling advance still lands an明账 line rather than vanishing. |
  | `resolve` | `storyline_resolved` | if id exists: set `status="已结"`, `last_advanced_scene=scene`, update summary if given. Missing id → log.warning + skip (nothing to resolve). |

- **`validate(section="storylines", decl, world)`** (mirror `KnowledgeSystem.validate`, referential where it can be): for each item — `op` must be one of the three (`bad_enum` else); `id` must be a non-empty str (`missing` else); `summary` required (`missing`) for `open`/`advance`. NO dangling-ref check on `id` (a storyline id is a free narrator-coined label, not an entity id — unlike `knowledge.knower`). This keeps the gate from bouncing legitimate new threads.

- **Digest backstop (§12 漏报并进 / spec §3 方案A+方案B):** `loop/fleet.py` gains `backstop_storylines(world, new_events, story_threads) -> list[suggestion]`. It scans this turn's player events for a clearly-active thread that no current ledger record covers, and **emits a harness-authored `storyline_advanced` with `status:"休眠"` marker** (NOT a full auto-open) — i.e. a FLAG, not a confident new明线. **DECISION-FOR-HUMAN below:** flag-as-休眠 vs auto-open-活跃. This plan implements the conservative FLAG: the backstop never invents a活跃明线 (that's the narrator's job via the gate); it only surfaces a休眠 "possible thread" record so the next turn's narrator sees it in the ledger and can promote it via `advance`. The backstop is **minimal**: it only fires when there are ZERO active threads AND the turn had a substantive player event (heuristic_floor ≥ 2) — so an established campaign with the narrator declaring threads never triggers it. Lightweight referential validation only (the id it coins must be unique vs existing threads), drop-on-fail, NO repair.

- **`inject()`** force-renders the ledger EVERY turn into the **scene** layer (continuity is scene-relevant, and a thread list is small so re-rendering it per turn is cheap): active threads first, then休眠, 已结 omitted (resolved threads leave the live ledger). Compact one-line-per-thread format. Returns `None` only when the ledger is empty.

### D2 — Recap storage: **event-sourced** (NOT a derived artifact)

The recap is stored event-sourced (folds from events into `world["systems"]["narrative"]`), NOT recomputed each turn from the transcript. Justification:
- **Rewind-safety (spec §5 invariant: "投影永不崩; rewind-safe").** `/rewind N` retracts events ≥ turn N and re-projects (`app/engine.py::rewind`). If the recap were a derived artifact recomputed from `TurnResult`s, it would NOT survive a rewind — the summaries of retracted scenes would linger. Event-sourcing makes the recap fold from the surviving events automatically, exactly like every other slice.
- **No transcript dependency at read time.** The transcript JSONL (`app/play.py::_write_transcript`) is a review/eval side-channel, written best-effort in a `try/except` and absent in many test/headless paths. The assembler must NOT depend on it. Event-sourcing puts the raw narration in the authoritative store.
- **Consistency with the rest of the engine.** Every other piece of world state is event-sourced; a derived recap would be the lone exception and a rewind footgun.

Cost note honored: the summarization LLM call is gated — it runs ONLY when a scene ages out of the recent-N window (detected in the digest fleet by comparing the current scene to the recent-window scenes in the slice), on the CHEAP provider. Recording raw narration (`narration_recorded`) is a zero-LLM event append every turn; summarizing is the only LLM cost and it is rare.

### D3 — Where raw recent narration is persisted: **a new `narration_recorded` event, owned by `NarrativeSystem`**

Narration is currently NOT persisted anywhere event-sourced (confirmed: it lives only in `TurnResult.narration` and the best-effort transcript). So the recap's "recent-N verbatim" tier has no source today. We add it:
- After a turn's commit applies, the digest fleet appends ONE `narration_recorded` event carrying `{scene, text}` (the verbatim prose). `NarrativeSystem.apply` stores it into the recap slice keyed by scene.
- The assembler pulls the recent-N scenes' raw text straight from `world["systems"]["narrative"]` — no transcript, no recompute.
- This is the minimal, event-sourced, rewind-safe persistence the brief asked us to "decide + justify". The transcript stays as-is (untouched) for human review.

**Recap slice shape** (`world["systems"]["narrative"]`):
```python
{
  "scenes": [                       # append-order list of scene buckets
    {"scene": str, "raw": [str, ...],   # one entry per turn's narration in that scene
     "summary": str | None},            # filled when the scene ages out (cheap-model)
  ],
  "super_summary": str | None,      # recursive summary-of-summaries (recompressed)
  "summarized_through_index": int,  # how many scene buckets have been summarized
}
```
Recent-N = the last `RECAP_RAW_SCENES` buckets keep their `raw` verbatim; older buckets carry only `summary`; when the number of summarized buckets exceeds `RECAP_SUMMARY_FANOUT`, they fold into `super_summary` (recursive tier).

**Constants** (module-level in `systems/narrative.py`, referenced as `nmod.CONST`):
- `RECAP_RAW_SCENES = 2` (spec §2.2 default: 最近 2 个 scene 原文). **Flag: tunable** (DECISION-FOR-HUMAN).
- `RECAP_SUMMARY_FANOUT = 6` (when >6 aged summaries accumulate, recompress into super_summary).

### D4 — Assembler layering — **dedicated high-priority continuity placement, cache-aware split**

`assemble_context` force-includes recap + storylines every turn, independent of `query`. Placement, exploiting the existing `stable → scene → volatile` cache order (`kernel/assembler.py::LAYER_ORDER`):

- **`super_summary` (recursive, rarely-changing) + 已-aged scene summaries → `stable` layer.** They change only when a scene ages out (infrequent), so keeping them in the cache-stable prefix maximizes prompt-cache reuse.
- **Storyline ledger + recent-N raw narration → `scene` layer.** The ledger changes most turns (advance/open) and the recent raw window slides every scene; both are scene-current continuity, so they belong with the other scene-layer content (POV facts, scene state). The recent-raw window naturally rolls.
- **Mechanism:** the two systems' `inject()` return `Fragment(layer="stable"|"scene", ...)`. Because `kernel/assembler.py::assemble` already gathers + layer-sorts every system's inject fragment, force-push is achieved simply by the systems returning a fragment (no `query` gate touches inject). BUT the recap needs BOTH a stable fragment (summaries) AND a scene fragment (recent raw) from ONE system, and `inject()` returns at most one `Fragment`. Resolve this in the assembler: `assemble_context` does a dedicated **recap composition step** that reads `world["systems"]["narrative"]` directly and emits the stable-summary block + the scene-raw block into the right layer sections of the output string (alongside the existing viewpoint/recall composition). `NarrativeSystem.inject()` returns the **scene-layer recent-raw** fragment (so the generic `assemble()` path also surfaces it for any caller that only renders fragments), and the assembler additionally splices the **stable-layer summary** block. `StorySystem.inject()` returns the single scene-layer ledger fragment (one layer suffices for it).
  - To avoid double-rendering the recent-raw (once via `assemble()` fragment, once via the dedicated step), the dedicated recap step in `assemble_context` renders ONLY the stable summary block; the scene recent-raw comes through the normal `assemble()`/`render()` fragment path. (Task 9 asserts no duplication.)

### D5 — Determinism for tests — **`FakeLLMProvider` for the summarizer; assert on slice shape + rendered string, never on call order**

- The recap summarizer is the only LLM call in P2; tests drive it with `FakeLLMProvider(json_responses=[{"summary": "..."}])` or `responses=[...]`. The summary call uses `complete_json` (small fixed schema `{"summary": str}`) so a `json_responses` list is the clean fake.
- Backstop and ledger projection are pure (no LLM) → deterministic.
- Tests assert on the slice dict shape and on the assembled-context STRING (substring presence/ordering), never on a provider call counter for correctness.

### Coordination with P1 (cascade `world` section) — **READ THIS at implementation time**

P1 (a separate, concurrently-written plan) ALSO adds a NEW commit section (`world`) to the SAME `_SYSTEM_PROMPT` (甲) and `_SYSTEM_PROMPT_HYBRID` (丙) strings in `loop/strategy.py`, owned analogously to `knowledge`. **This plan's Task 6 edits those same two prompt strings** to expose the `storylines` section. The two edits touch the same lines.
- Do NOT assume P1's code exists. Reference the **`knowledge` section** (already in both prompts) as the shared precedent for the exact insertion style.
- The human's agent serializes the two implementations. Whichever lands second must REBASE its prompt edit onto the first's (re-read the prompt strings before editing; insert the `storylines` block adjacent to the `knowledge` / `world` blocks; keep both sections present). The same caution applies to any shared touch of `assemble_context` (P1 may add a `world`-driven block; P2 adds recap/storyline blocks — both must coexist).
- Task 6 below includes an explicit "if P1 already added a `world` block, insert `storylines` alongside it, do not remove `world`" instruction.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `systems/story.py` | **Create** | `StorySystem(ContextSystem)` — owns `storylines` commit section + `storyline_opened`/`storyline_advanced`/`storyline_resolved` events; `apply` folds the ledger slice; `validate`/`to_events` for the section; `inject` force-renders the ledger (scene layer). |
| `systems/narrative.py` | **Create** | `NarrativeSystem(ContextSystem)` — owns `narration_recorded`/`scene_summarized`/`recap_recompressed` (harness-authored, no commit section); `apply` folds the recap slice; `inject` force-renders recent-N raw (scene layer); module constants `RECAP_RAW_SCENES`/`RECAP_SUMMARY_FANOUT`; helper `aged_out_scene(slice_, current_scene)`. |
| `loop/fleet.py` | **Modify** | Extend `digest_fleet` signature with `narration_text`/`scene`/`recap_provider`; (1) append `narration_recorded`; (2) when a scene aged out, cheap-model `scene_summarized` (+ recursive `recap_recompressed`); (3) call `backstop_storylines` and append the conservative休眠 flag. New module fns `summarize_scene(...)`, `backstop_storylines(...)`. All non-fatal, harness-authored, drop-on-fail. |
| `loop/turn.py` | **Modify** | Pass `narration_text=commit.narration`, `scene=scene_id`, `recap_provider=cascade_provider` into the existing `digest_fleet` call. No new hook block (recap/storyline maintenance rides the existing digest_fleet span). |
| `context/assembler.py` | **Modify** | Add the mandatory recap composition step (stable-layer summary block) to `assemble_context`, query-independent. (Storyline ledger + recent-raw arrive via the existing `assemble()` fragment path — no extra code beyond the systems' `inject`.) |
| `app/engine.py` | **Modify** | Register `StorySystem()` + `NarrativeSystem()` in `build_engine` (after `CascadeSystem`, before/after `TimeSystem` — order only matters for cache-prefix; place them last). Import lines. |
| `loop/strategy.py` | **Modify** | Expose the `storylines` section in `_SYSTEM_PROMPT` (甲) and `_SYSTEM_PROMPT_HYBRID` (丙), mirroring the `knowledge` block. **Reconcile with P1's `world` block (see Coordination above).** |
| `tests/systems/test_story_system.py` | **Create** | `StorySystem` unit: ownership/registration; `validate` (bad op/missing id/missing summary); `to_events` per op; `apply` folds the ledger (open/advance/resolve, re-open idempotent, dangling advance/resolve defensive); `inject` renders active+休眠, omits已结, empty→None. |
| `tests/systems/test_narrative_system.py` | **Create** | `NarrativeSystem` unit: ownership/registration; `apply` folds raw/summary/super_summary; `aged_out_scene` helper; constants; `inject` renders recent-N raw, empty→None; rewind-safety (project over a retracted subset yields the right slice). |
| `tests/loop/test_fleet.py` | **Modify** | Extend: `digest_fleet` appends `narration_recorded`; summarizes an aged-out scene via cheap provider (and recursive recompress when fanout exceeded); `backstop_storylines` flags a休眠 thread only when zero active + substantive turn; backstop drop-on-dup; non-fatal. (Mirror existing fleet tests' style.) |
| `tests/context/test_assembler.py` | **Modify** | Add: recap stable-summary block + recent-raw scene block + storyline ledger appear in `assemble_context` output **with `query=None`** (force-push proof); correct layer ordering (stable summary before scene raw/ledger before volatile recall); no double-render of recent-raw. |
| `tests/loop/test_turn.py` | **Modify** | One added test: a full `run_turn` records narration into the recap slice (re-projected world has the turn's narration in `world["systems"]["narrative"]`), proving the wiring. |
| `tests/app/test_engine.py` | **Modify** | One added test: `build_engine` registers `story` + `narrative` (owner_of_section/owner_of_event + slices present in `engine.world["systems"]`). |

No other files are touched. `engine/`, `_legacy/`, `data/`, and `docs/` (except THIS plan) are off-limits. `engine/cli.py::cmd_recap` (an unrelated CLI timeline-dump command) is NOT touched and NOT related to this recap component — keep the names distinct.

---

## HARD git guardrails (read once, obey every task)

- **Do NOT** run `git init`, `git reset`, `git rebase`, `git checkout <branch>`, `git switch`, or any branch-changing command. Stay on `app`.
- **Do NOT** edit anything under `engine/`, `_legacy/`, `data/`, or `docs/` (except this one plan file, which is already written — do not modify it during implementation either).
- Commit ONLY the files each task names, with the exact message given.
- After EVERY task's implementation: full-suite gate `cd /root/rpg-engine-app && python3 -m pytest -q --ignore=tests/test_embed_real.py` must be green (the **738-test** baseline + the new tests this plan adds). The legacy suite must also stay green if your worktree runs it.
- Conventions: `from engine.log import get_logger` at top of every new module; `log = get_logger("<dotted.module>")`. Tests mirror source paths under `tests/`. Always invoke pytest as `python3 -m pytest`.

---

# PART A — Storyline ledger (`StorySystem`)

## Task 1: `StorySystem` — ownership, section, empty slice

**Files:** Create `systems/story.py`; Create `tests/systems/test_story_system.py`.

- [ ] **Step 1 — failing test** in `tests/systems/test_story_system.py`:
  ```python
  """Tests for StorySystem (P2 — storyline ledger)."""
  from __future__ import annotations

  from kernel.registry import Registry
  from kernel.projection import project
  from kernel.events import kernel_event
  from systems.story import StorySystem


  def _reg():
      return Registry().register(StorySystem())


  def test_story_owns_section_and_events():
      s = StorySystem()
      assert s.name == "story"
      assert s.commit_sections() == {"storylines"}
      assert s.event_types() == {
          "storyline_opened", "storyline_advanced", "storyline_resolved",
      }
      # self-contained ledger: no shared-graph dependency
      assert s.requires() == set()


  def test_story_registers_and_routes():
      reg = _reg()
      assert reg.owner_of_section("storylines").name == "story"
      assert reg.owner_of_event("storyline_opened").name == "story"


  def test_empty_state_shape():
      assert StorySystem().empty_state() == {"threads": {}}
  ```
- [ ] **Step 2 — run, expect FAIL:** `cd /root/rpg-engine-app && python3 -m pytest -q tests/systems/test_story_system.py` → ImportError (no module).
- [ ] **Step 3 — implement `systems/story.py`** (minimal): module docstring (describe ledger semantics + that it is PLAYER-facing明账, distinct from director's hidden threads); `from kernel.contextsystem import ContextSystem, ValidationError, Fragment`; `from kernel.events import kernel_event`; `from engine.log import get_logger`; `log = get_logger("systems.story")`; `class StorySystem(ContextSystem)` with `name = "story"`, `requires(self)->set()`, `event_types(self)->{"storyline_opened","storyline_advanced","storyline_resolved"}`, `commit_sections(self)->{"storylines"}`, `empty_state(self)->{"threads": {}}`. (Leave `apply`/`validate`/`to_events`/`inject` inherited for now — later tasks add them.)
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add systems/story.py tests/systems/test_story_system.py && git commit -m "feat(systems): StorySystem ownership + storylines section + empty slice (P2)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 2: `StorySystem.apply` — fold the ledger slice (D1)

**Files:** Modify `systems/story.py`; Modify `tests/systems/test_story_system.py`.

- [ ] **Step 1 — failing tests** appended to `tests/systems/test_story_system.py`:
  ```python
  def _opened(tid, summary, scene="s1", day=1, turn=1):
      return kernel_event("storyline_opened", day=day, scene=scene,
                          summary=f"开启 {tid}",
                          deltas={"id": tid, "summary": summary}, turn=turn)

  def _advanced(tid, summary=None, scene="s2", day=1, turn=2):
      d = {"id": tid}
      if summary is not None:
          d["summary"] = summary
      return kernel_event("storyline_advanced", day=day, scene=scene,
                          summary=f"推进 {tid}", deltas=d, turn=turn)

  def _resolved(tid, scene="s3", day=2, turn=3):
      return kernel_event("storyline_resolved", day=day, scene=scene,
                          summary=f"收束 {tid}", deltas={"id": tid}, turn=turn)


  def test_open_creates_active_record():
      world = project(_reg(), [_opened("th_bridge", "查明断桥真相")])
      rec = world["systems"]["story"]["threads"]["th_bridge"]
      assert rec["status"] == "活跃"
      assert rec["summary"] == "查明断桥真相"
      assert rec["opened_scene"] == "s1"
      assert rec["last_advanced_scene"] == "s1"


  def test_advance_updates_summary_and_scene_and_reactivates():
      world = project(_reg(), [
          _opened("th_bridge", "查明断桥真相"),
          _advanced("th_bridge", "发现桥是人为破坏", scene="s2"),
      ])
      rec = world["systems"]["story"]["threads"]["th_bridge"]
      assert rec["summary"] == "发现桥是人为破坏"
      assert rec["last_advanced_scene"] == "s2"
      assert rec["status"] == "活跃"


  def test_resolve_marks_done():
      world = project(_reg(), [
          _opened("th_bridge", "查明断桥真相"),
          _resolved("th_bridge", scene="s3"),
      ])
      rec = world["systems"]["story"]["threads"]["th_bridge"]
      assert rec["status"] == "已结"
      assert rec["last_advanced_scene"] == "s3"


  def test_advance_on_missing_id_is_defensive():
      # dangling advance still lands a 明账 line (created 活跃), never crashes
      world = project(_reg(), [_advanced("th_ghost", "凭空推进")])
      assert world["systems"]["story"]["threads"]["th_ghost"]["status"] == "活跃"


  def test_resolve_on_missing_id_skips():
      world = project(_reg(), [_resolved("th_ghost")])
      assert "th_ghost" not in world["systems"]["story"]["threads"]

  def test_open_honors_explicit_status_for_backstop_dormant_flag():
      # the digest backstop (Task 8) emits storyline_opened with status:"休眠";
      # apply must honor an explicit status rather than always forcing 活跃.
      ev = kernel_event("storyline_opened", day=1, scene="s1", summary="auto",
                        deltas={"id": "th_auto", "summary": "疑似新线",
                                "status": "休眠"}, turn=1)
      world = project(_reg(), [ev])
      assert world["systems"]["story"]["threads"]["th_auto"]["status"] == "休眠"
  ```
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement `StorySystem.apply(self, world, event)`** per D1's table. `slice_ = world["systems"][self.name]`; `threads = slice_.setdefault("threads", {})`; `d = event.get("deltas", {})`; `tid = d.get("id")`; `scene = event.get("scene")`. Guard `tid` falsy → `log.warning(...)` + return.
  - `storyline_opened`: if `tid` in threads → update `summary` (if given) + `last_advanced_scene=scene` (idempotent re-open, log.debug); else create `{"id":tid,"status":d.get("status") or "活跃","summary":d.get("summary",""),"opened_scene":scene,"last_advanced_scene":scene}`. (Honoring an explicit `status` lets the digest backstop in Task 8 land a `休眠` flag via `storyline_opened`; normal narrator opens omit `status` → default `活跃`.)
  - `storyline_advanced`: if `tid` in threads → if `"summary"` in d set it; `last_advanced_scene=scene`; `status="活跃"`. Else create活跃 record (defensive, `log.warning("storyline_advanced unknown id=%s; created", tid)`).
  - `storyline_resolved`: if `tid` in threads → `status="已结"`, `last_advanced_scene=scene`, summary update if given. Else `log.warning(...)` + return.
  - `log.debug(...)` per branch.
- [ ] **Step 4 — run, expect PASS;** full-suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add systems/story.py tests/systems/test_story_system.py && git commit -m "feat(systems): StorySystem.apply folds storyline ledger (open/advance/resolve)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 3: `StorySystem.validate` + `to_events` — the LLM-authored section (D1)

**Files:** Modify `systems/story.py`; Modify `tests/systems/test_story_system.py`.

- [ ] **Step 1 — failing tests** appended (mirror `KnowledgeSystem` validate/to_events tests):
  ```python
  def test_validate_rejects_bad_op():
      errs = StorySystem().validate(
          "storylines", [{"op": "nuke", "id": "x", "summary": "y"}], {})
      assert any(e.code == "bad_enum" and e.field == "[0].op" for e in errs)

  def test_validate_requires_id_and_summary_for_open():
      errs = StorySystem().validate(
          "storylines", [{"op": "open"}], {})
      codes = {(e.field, e.code) for e in errs}
      assert ("[0].id", "missing") in codes
      assert ("[0].summary", "missing") in codes

  def test_validate_resolve_allows_missing_summary():
      errs = StorySystem().validate(
          "storylines", [{"op": "resolve", "id": "th_x"}], {})
      assert errs == []

  def test_validate_ignores_other_sections():
      assert StorySystem().validate("knowledge", [{"whatever": 1}], {}) == []

  def test_to_events_maps_ops():
      evs = StorySystem().to_events(
          "storylines",
          [{"op": "open", "id": "th_a", "summary": "S"},
           {"op": "advance", "id": "th_a", "summary": "S2"},
           {"op": "resolve", "id": "th_a"}],
          turn=4, day=1, scene="s4")
      assert [e["type"] for e in evs] == [
          "storyline_opened", "storyline_advanced", "storyline_resolved"]
      assert evs[0]["deltas"]["id"] == "th_a"
      assert evs[0]["turn"] == 4 and evs[0]["scene"] == "s4"

  def test_to_events_skips_malformed_item():
      evs = StorySystem().to_events(
          "storylines", [{"op": "open", "id": "", "summary": "x"}],
          turn=1, day=1, scene="s1")
      assert evs == []
  ```
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** `validate(self, section, decl, world)` and `to_events(self, section, decl, *, turn, day, scene)`:
  - `validate`: `if section != "storylines": return []`. For each item i: `op = item.get("op")`; if `op not in {"open","advance","resolve"}` → `ValidationError(section, f"[{i}].op", "bad_enum", "op 必须是 open/advance/resolve 之一")`. `id` non-empty str else `missing` (hint "storylines 每项必须提供 id（故事线标识，可自拟）"). For `op in {"open","advance"}`: `summary` non-empty str else `missing` (hint "open/advance 必须提供 summary（一句话剧情线摘要）"). No dangling-ref check (id is a free label).
  - `to_events`: `if section != "storylines": return []`. Map each well-formed item to the matching event via `kernel_event(<type>, day=day, scene=scene, summary=..., deltas={"id":id,"summary":item.get("summary")}, turn=turn)`. Skip (log.warning) items with bad op or empty id. `_OP_EVENT = {"open":"storyline_opened","advance":"storyline_advanced","resolve":"storyline_resolved"}`.
- [ ] **Step 4 — run, expect PASS;** full-suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add systems/story.py tests/systems/test_story_system.py && git commit -m "feat(systems): StorySystem validate + to_events for storylines section

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 4: `StorySystem.inject` — force-render the ledger every turn (D1, D4)

**Files:** Modify `systems/story.py`; Modify `tests/systems/test_story_system.py`.

- [ ] **Step 1 — failing tests** appended:
  ```python
  def test_inject_renders_active_and_dormant_omits_resolved():
      world = project(_reg(), [
          _opened("th_a", "线A：查案", scene="s1"),
          _opened("th_b", "线B：寻人", scene="s1"),
          _resolved("th_b", scene="s2"),                 # 已结 → omitted
      ])
      # th_c manually dormant via advance marking status 休眠 is not an op; emulate
      frag = StorySystem().inject({"id": "s2"}, world)
      assert frag is not None
      assert frag.layer == "scene"
      assert "线A：查案" in frag.text
      assert "线B：寻人" not in frag.text          # resolved omitted
      assert "故事线" in frag.text                  # a 明账 header label

  def test_inject_empty_ledger_returns_none():
      world = project(_reg(), [])
      assert StorySystem().inject({"id": "s1"}, world) is None
  ```
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement `inject(self, scene, world) -> Fragment | None`** (mirror `DirectorSystem.inject` rendering style): read `threads = (world.get("systems",{}).get(self.name) or {}).get("threads", {})`. Partition into active (`status=="活跃"`) and dormant (`status=="休眠"`); drop `已结`. If none → return None. Build a compact block:
  ```
  【故事线·明账】（延续性，每回合必看）
    [活跃] th_a：线A：查案 （上次推进：s1）
    [休眠] th_d：线D：旧怨 （上次推进：s0）
  ```
  one line per thread (`[状态] {id}：{summary} （上次推进：{last_advanced_scene}）`). Return `Fragment(system="story", layer="scene", text=text, affordance="本回合若开启/推进/收束了某条故事线，用 storylines 段声明")`.
- [ ] **Step 4 — run, expect PASS;** full-suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add systems/story.py tests/systems/test_story_system.py && git commit -m "feat(systems): StorySystem.inject force-renders the storyline ledger (scene layer)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

# PART B — Recap (`NarrativeSystem`)

## Task 5: `NarrativeSystem` — ownership, slice, constants, `apply` (D2, D3)

**Files:** Create `systems/narrative.py`; Create `tests/systems/test_narrative_system.py`.

- [ ] **Step 1 — failing tests** in `tests/systems/test_narrative_system.py`:
  ```python
  """Tests for NarrativeSystem (P2 — recency-tiered recap)."""
  from __future__ import annotations

  from kernel.registry import Registry
  from kernel.projection import project
  from kernel.events import kernel_event
  import systems.narrative as nmod
  from systems.narrative import NarrativeSystem


  def _reg():
      return Registry().register(NarrativeSystem())


  def _narr(scene, text, day=1, turn=1):
      return kernel_event("narration_recorded", day=day, scene=scene,
                          summary="narration", deltas={"scene": scene, "text": text},
                          turn=turn)

  def _summ(scene, summary, day=1, turn=2):
      return kernel_event("scene_summarized", day=day, scene=scene,
                          summary="scene summary",
                          deltas={"scene": scene, "summary": summary}, turn=turn)


  def test_owns_events_no_section():
      s = NarrativeSystem()
      assert s.name == "narrative"
      assert s.event_types() == {
          "narration_recorded", "scene_summarized", "recap_recompressed"}
      assert s.commit_sections() == set()          # harness-authored
      assert s.requires() == set()


  def test_constants_present():
      assert nmod.RECAP_RAW_SCENES == 2
      assert nmod.RECAP_SUMMARY_FANOUT == 6


  def test_empty_state_shape():
      assert NarrativeSystem().empty_state() == {
          "scenes": [], "super_summary": None, "summarized_through_index": 0}


  def test_narration_recorded_buckets_by_scene():
      world = project(_reg(), [
          _narr("s1", "第一段。", turn=1),
          _narr("s1", "第一段续。", turn=2),
          _narr("s2", "第二场。", turn=3),
      ])
      scenes = world["systems"]["narrative"]["scenes"]
      assert [b["scene"] for b in scenes] == ["s1", "s2"]
      assert scenes[0]["raw"] == ["第一段。", "第一段续。"]
      assert scenes[1]["raw"] == ["第二场。"]


  def test_scene_summarized_fills_summary():
      world = project(_reg(), [
          _narr("s1", "原文", turn=1),
          _summ("s1", "s1 摘要", turn=2),
      ])
      b = world["systems"]["narrative"]["scenes"][0]
      assert b["summary"] == "s1 摘要"


  def test_recompress_folds_into_super_summary():
      world = project(_reg(), [
          kernel_event("recap_recompressed", day=2, scene="s9", summary="super",
                       deltas={"super_summary": "远古往事总览",
                               "summarized_through_index": 6}, turn=9),
      ])
      ns = world["systems"]["narrative"]
      assert ns["super_summary"] == "远古往事总览"
      assert ns["summarized_through_index"] == 6
  ```
- [ ] **Step 2 — run, expect FAIL** (no module).
- [ ] **Step 3 — implement `systems/narrative.py`:** docstring (event-sourced recency-tiered recap; rewind-safe; raw narration persisted as `narration_recorded`); imports + `log`; module constants `RECAP_RAW_SCENES = 2`, `RECAP_SUMMARY_FANOUT = 6`; `class NarrativeSystem(ContextSystem)` with the ownership methods from the test and `empty_state(self)->{"scenes": [], "super_summary": None, "summarized_through_index": 0}`. `apply(self, world, event)`:
  - `ns = world["systems"][self.name]`; `d = event.get("deltas", {})`; `t = event["type"]`.
  - `narration_recorded`: `scene = d.get("scene")`; `text = d.get("text")`; if not text → log.warning + return. Find the LAST bucket in `ns["scenes"]`; if it exists and its `scene == scene` → append `text` to its `raw`; else append a new bucket `{"scene": scene, "raw": [text], "summary": None}`. (Buckets are append-order; a scene revisited after another scene starts a NEW bucket — acceptable, the recent-N window still works.)
  - `scene_summarized`: `scene = d.get("scene")`; find the FIRST bucket with that scene whose `summary is None` and set `summary = d.get("summary")`; if none found, log.debug + skip.
  - `recap_recompressed`: set `ns["super_summary"] = d.get("super_summary")` (if present) and `ns["summarized_through_index"] = d.get("summarized_through_index", ns.get("summarized_through_index", 0))`.
  - `log.debug(...)` per branch.
- [ ] **Step 4 — run, expect PASS;** full-suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add systems/narrative.py tests/systems/test_narrative_system.py && git commit -m "feat(systems): NarrativeSystem ownership + recap slice + apply (P2 event-sourced recap)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 6: `NarrativeSystem.inject` (recent-N raw) + `aged_out_scene` helper (D3, D4)

**Files:** Modify `systems/narrative.py`; Modify `tests/systems/test_narrative_system.py`.

- [ ] **Step 1 — failing tests** appended:
  ```python
  def test_inject_renders_recent_raw_only():
      # RECAP_RAW_SCENES=2 → only the last 2 buckets' raw appear verbatim
      world = project(_reg(), [
          _narr("s1", "最老原文", turn=1),
          _summ("s1", "s1摘要", turn=2),
          _narr("s2", "中间原文", turn=3),
          _narr("s3", "最近原文", turn=4),
      ])
      frag = NarrativeSystem().inject({"id": "s3"}, world)
      assert frag is not None and frag.layer == "scene"
      assert "最近原文" in frag.text and "中间原文" in frag.text
      assert "最老原文" not in frag.text          # aged out of the raw window

  def test_inject_empty_returns_none():
      assert NarrativeSystem().inject({"id": "s1"}, project(_reg(), [])) is None

  def test_aged_out_scene_detects_window_overflow():
      world = project(_reg(), [
          _narr("s1", "a", turn=1),
          _narr("s2", "b", turn=2),
          _narr("s3", "c", turn=3),     # now 3 buckets, window=2 → s1 aged out
      ])
      ns = world["systems"]["narrative"]
      assert nmod.aged_out_scene(ns) == "s1"     # oldest unsummarized beyond window

  def test_aged_out_scene_none_when_within_window():
      world = project(_reg(), [_narr("s1", "a", turn=1), _narr("s2", "b", turn=2)])
      assert nmod.aged_out_scene(world["systems"]["narrative"]) is None
  ```
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:**
  - Module fn `aged_out_scene(ns: dict) -> str | None`: buckets = `ns.get("scenes", [])`; the recent window = last `RECAP_RAW_SCENES` buckets. A bucket is "aged out" if its index < `len(buckets) - RECAP_RAW_SCENES` AND its `summary is None`. Return the `scene` of the FIRST such bucket (oldest unsummarized beyond the window), else None. (This is what the digest fleet calls to decide whether to summarize this turn.)
  - `inject(self, scene, world) -> Fragment | None`: `ns = (world.get("systems",{}).get(self.name) or {})`; `buckets = ns.get("scenes", [])`; if not buckets → None. `recent = buckets[-RECAP_RAW_SCENES:]`. Build a scene-layer block:
    ```
    【最近剧情·原文】（延续性，每回合必看）
    〔s2〕中间原文
    〔s3〕最近原文
    ```
    join each recent bucket's `raw` entries. Return `Fragment(system="narrative", layer="scene", text=text, affordance="")`. (The stable-layer summary block is spliced by the assembler — Task 9 — NOT here, to keep one fragment per system.)
- [ ] **Step 4 — run, expect PASS;** full-suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add systems/narrative.py tests/systems/test_narrative_system.py && git commit -m "feat(systems): NarrativeSystem.inject recent-N raw + aged_out_scene helper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 7: Register both systems in `build_engine` (D1, D3)

**Files:** Modify `app/engine.py`; Modify `tests/app/test_engine.py`.

- [ ] **Step 1 — failing test** appended to `tests/app/test_engine.py` (mirror the existing `build_engine` registration tests there): build an engine in a `tmp_path` campaign dir with `provider=FakeLLMProvider()`, assert:
  ```python
  assert engine.registry.owner_of_section("storylines").name == "story"
  assert engine.registry.owner_of_event("narration_recorded").name == "narrative"
  assert "story" in engine.world["systems"]
  assert "narrative" in engine.world["systems"]
  ```
- [ ] **Step 2 — run, expect FAIL** (not registered).
- [ ] **Step 3 — implement:** in `app/engine.py` add `from systems.story import StorySystem` + `from systems.narrative import NarrativeSystem`; in `build_engine` register both after `registry.register(CascadeSystem())` (e.g. before `TimeSystem` or after — they have no `requires`, so order only affects cache-prefix; append them last after `TimeSystem` for clarity). Two `registry.register(...)` lines.
- [ ] **Step 4 — run, expect PASS;** full-suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add app/engine.py tests/app/test_engine.py && git commit -m "feat(engine): register StorySystem + NarrativeSystem in build_engine

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

# PART C — Digest fleet maintenance + push wiring

## Task 8: Extend `digest_fleet` — record narration + summarize aged scene + backstop (D2, D3, D1)

**Files:** Modify `loop/fleet.py`; Modify `tests/loop/test_fleet.py`.

Design notes the implementation must honor:
- `digest_fleet` keeps its current arc-reflection behavior unchanged; we ADD recap/storyline maintenance AFTER it (or before — order independent), all guarded so a failure in one does not skip the others (each wrapped so the fleet stays non-fatal overall; the caller already wraps the whole call in try/except).
- New signature (keyword-only, all optional with safe defaults so existing direct callers/tests keep working):
  `digest_fleet(registry, store, new_events, world, *, provider, threshold=30, importance_provider=None, narration_text=None, scene=None, recap_provider=None)`.
- **Record narration** (D3): if `narration_text` (non-empty) and `scene` given, append ONE `narration_recorded` event `deltas={"scene": scene, "text": narration_text}`, `turn = <next turn>` (max store turn + 1, like the cascade/director slot), `day` = max day in new_events or world meta. Re-projection by the caller folds it.
- **Summarize aged scene** (D2): after recording, re-derive the recap slice (project, or read `world` — simplest: read `world["systems"]["narrative"]` BUT that is pre-narration; cleanest is to compute aged_out from a fresh projection. To avoid a double projection in the fleet, compute the post-record slice by calling `project(registry, store.iter_events())` ONCE here and using its `narrative` slice). Call `nmod.aged_out_scene(ns)`. If it returns a scene id AND `recap_provider` is not None: gather that bucket's `raw` text, call `summarize_scene(recap_provider, scene_id, raw_texts)` → a `scene_summarized` event; append it. Then if the count of summarized buckets now exceeds `RECAP_SUMMARY_FANOUT`, build a `recap_recompressed` event (super-summary of the oldest summaries via the cheap provider) and append it. **Gate:** the summarize LLM call fires ONLY when a scene aged out (not every turn) — assert this in tests via `provider.calls` count.
- **Backstop storylines** (D1): call `backstop_storylines(world_after, new_events)` → 0-or-1 harness `storyline_advanced` event with `status` marker休眠; append it. Minimal trigger: only when the post-projection story slice has ZERO `活跃` threads AND `new_events` contains a player event with `heuristic_floor(ev) >= 2`. The coined id must not collide with an existing thread (drop-on-dup). This is the conservative FLAG (DECISION-FOR-HUMAN: flag-vs-auto-open).
- Return value: extend to also include the appended recap/story events (append them to the returned list, OR return the same arc list — tests check the STORE, so returning arc-only is acceptable; document the choice. **Recommendation:** return ALL appended events from the fleet for observability; update the docstring + the one existing assertion in `test_turn.py` if it asserts an exact length — re-check before editing).

- [ ] **Step 1 — failing tests** appended to `tests/loop/test_fleet.py` (mirror the existing fleet test setup — a registry incl. ontology+story+narrative, an open store, a FakeLLMProvider). Add a cheap fake for the summarizer:
  ```python
  import systems.narrative as nmod
  from systems.story import StorySystem
  from systems.narrative import NarrativeSystem

  def _reg_full():
      # ontology required by existing systems used in fleet tests; story/narrative added
      from systems.ontology import OntologySystem
      return (Registry().register(OntologySystem())
              .register(StorySystem()).register(NarrativeSystem()))

  def test_digest_records_narration():
      reg = _reg_full(); store = _store(reg)        # _store helper as in existing tests
      world = project(reg, store.iter_events())
      evs = [kernel_event("action", day=1, scene="s1", summary="walk",
                          actors=["hero"], deltas={}, turn=1)]
      for e in evs: store.append(e)
      digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                   provider=FakeLLMProvider(), narration_text="你走进村庄。", scene="s1")
      w = project(reg, store.iter_events())
      raw = w["systems"]["narrative"]["scenes"][-1]["raw"]
      assert "你走进村庄。" in raw

  def test_digest_summarizes_only_when_scene_ages_out():
      reg = _reg_full(); store = _store(reg)
      # pre-seed 2 scenes of narration (within window → no summary yet)
      for i, sc in enumerate(["s1", "s2"], start=1):
          store.append(kernel_event("narration_recorded", day=1, scene=sc,
                       summary="n", deltas={"scene": sc, "text": f"原文{sc}"}, turn=i))
      world = project(reg, store.iter_events())
      cheap = FakeLLMProvider(json_responses=[{"summary": "s1 的摘要"}])
      # this turn's narration starts s3 → s1 ages out → ONE summarize call
      evs = [kernel_event("action", day=1, scene="s3", summary="x",
                          actors=["hero"], deltas={}, turn=3)]
      for e in evs: store.append(e)
      digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                   provider=FakeLLMProvider(), narration_text="原文s3", scene="s3",
                   recap_provider=cheap)
      w = project(reg, store.iter_events())
      s1b = next(b for b in w["systems"]["narrative"]["scenes"] if b["scene"] == "s1")
      assert s1b["summary"] == "s1 的摘要"
      assert len(cheap.calls) == 1                    # gated: exactly one cheap call

  def test_digest_no_summarize_within_window():
      reg = _reg_full(); store = _store(reg)
      cheap = FakeLLMProvider(json_responses=[{"summary": "X"}])
      evs = [kernel_event("action", day=1, scene="s1", summary="x",
                          actors=["hero"], deltas={}, turn=1)]
      for e in evs: store.append(e)
      digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                   provider=FakeLLMProvider(), narration_text="原文", scene="s1",
                   recap_provider=cheap)
      assert len(cheap.calls) == 0                    # nothing aged out → no LLM cost

  def test_backstop_flags_dormant_when_no_active_thread():
      reg = _reg_full(); store = _store(reg)
      # substantive player event, empty ledger → backstop flags one 休眠 thread
      evs = [kernel_event("world_change", day=1, scene="s1", summary="断桥崩塌",
                          deltas={"place": "bridge", "level": 1}, turn=1)]
      for e in evs: store.append(e)
      digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                   provider=FakeLLMProvider(), narration_text="桥塌了。", scene="s1")
      threads = project(reg, store.iter_events())["systems"]["story"]["threads"]
      assert any(t["status"] == "休眠" for t in threads.values())

  def test_backstop_silent_when_active_thread_exists():
      reg = _reg_full(); store = _store(reg)
      store.append(kernel_event("storyline_opened", day=1, scene="s1", summary="o",
                   deltas={"id": "th_x", "summary": "现有活跃线"}, turn=1))
      evs = [kernel_event("world_change", day=1, scene="s1", summary="大事",
                          deltas={"place": "bridge", "level": 1}, turn=2)]
      for e in evs: store.append(e)
      before = len(list(store.iter_events()))
      digest_fleet(reg, store, evs, project(reg, store.iter_events()),
                   provider=FakeLLMProvider(), narration_text="x", scene="s1")
      # no new storyline_* beyond what we appended (backstop stayed silent)
      sl = [e for e in store.iter_events() if e["type"].startswith("storyline_")]
      assert len(sl) == 1                              # only the pre-seeded open
  ```
  (Re-check the existing `tests/loop/test_fleet.py` for its actual `_store`/registry helpers and reuse them; the snippets above name `_store` — align to whatever the file already defines.)
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** in `loop/fleet.py`:
  - `import systems.narrative as nmod`; `from kernel.projection import project`; `from memory.importance import heuristic_floor`.
  - `summarize_scene(provider, scene_id, raw_texts) -> dict` → builds a cheap-model prompt ("把下面这一场的原文压成一句话中文摘要，只输出 JSON {\"summary\": ...}"), `provider.complete_json(system, user, {"type":"object","properties":{"summary":{"type":"string"}},"required":["summary"]})`, returns a `scene_summarized` `kernel_event` (deltas `{"scene":scene_id,"summary":<summary or "">}`). Defensive: on any exception or empty summary, return None (caller skips).
  - `backstop_storylines(world, new_events) -> dict | None`: `threads = (world["systems"].get("story") or {}).get("threads", {})`; if any `t["status"]=="活跃"` → return None. Find the most-significant new player event (max `heuristic_floor`); if its floor < 2 → None. Coin a stable `tid = f"th_auto_{<short hex hash of ev summary>}"`; if `tid` in threads → None (drop-on-dup). Return `kernel_event("storyline_opened", day=<max day>, scene=<scene>, summary=..., deltas={"id":tid,"summary":ev.get("summary",""),"status":"休眠"}, turn=<next slot>)`. The `status:"休眠"` is honored by `StorySystem.apply`'s open branch (added in Task 2) → the record lands `休眠`, i.e. a FLAG the narrator promotes next turn, not a confident活跃明线. This Task only EMITS the event; the `status`-honor lives in Task 2 (authoritative `StorySystem` behavior + its test).
  - In `digest_fleet`, after the existing arc logic: compute `post = project(registry, store.iter_events())` ONCE; append narration_recorded (if text+scene); re-`post = project(...)`; compute `aged = nmod.aged_out_scene(post["systems"]["narrative"])`; if `aged` and `recap_provider`: find the bucket, `summ_ev = summarize_scene(recap_provider, aged, bucket_raw)`; if summ_ev: append; (recompress check: if summarized count > `nmod.RECAP_SUMMARY_FANOUT`, build recap_recompressed via cheap provider over the oldest summaries; append). Then `backstop = backstop_storylines(post, new_events)`; if backstop: append. Collect all appended into the return list. Each sub-step in its own `try/except` logging non-fatally.
- [ ] **Step 4 — run, expect PASS;** full-suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add loop/fleet.py tests/loop/test_fleet.py && git commit -m "feat(fleet): digest maintains recap (record+summarize aged) + storyline backstop

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

  (`systems/story.py`'s `status`-honor is owned by Task 2 — do NOT re-touch it here; this Task only emits the `storyline_opened` event from the fleet.)

---

## Task 9: `assemble_context` — mandatory recap composition (PUSH, query-independent) (D4)

**Files:** Modify `context/assembler.py`; Modify `tests/context/test_assembler.py`.

Design notes:
- The storyline ledger fragment and the recent-raw narration fragment already arrive via the generic `assemble()` fragment path (both systems' `inject` return scene-layer fragments) — so once Task 4 + Task 6 land, they are ALREADY force-pushed by `assemble()` regardless of `query`. Task 9 only adds the **stable-layer recap summary block** (super_summary + aged scene summaries), which has no system `inject` (one fragment per system; `NarrativeSystem.inject` already returns the scene-raw fragment).
- Add the stable-summary block to the FRONT of the composed string (stable layer, before `base`), reading `world["systems"]["narrative"]` directly. Only render when there is a `super_summary` OR at least one bucket with a non-None `summary`. Format:
  ```
  ## [stable]
  【往昔概要】（更早剧情的压缩记忆）
  «总览» 远古往事总览
  «s1» s1 的摘要
  ```
- Ensure no double-render: the scene recent-raw stays in the `assemble()`-rendered `base` (scene layer); the assembler does NOT also re-emit it.

- [ ] **Step 1 — failing tests** appended to `tests/context/test_assembler.py`. Build a world with a `narrative` slice (a summarized old scene + recent raw) and a `story` ledger, registering `StorySystem` + `NarrativeSystem` in the test `_reg()` (or a local `_reg_continuity()`), and call `assemble_context(reg, world, scene, query=None)`:
  ```python
  def test_recap_and_storylines_force_pushed_without_query():
      # build via events so slices are real
      from systems.story import StorySystem
      from systems.narrative import NarrativeSystem
      reg = (Registry().register(OntologySystem())
             .register(StorySystem()).register(NarrativeSystem()))
      evs = [
          _ev("storyline_opened", scene="s1", id="th_a", summary="线A：查案"),
          _ev("narration_recorded", scene="s1", scene_d="s1", text="最老原文"),
          _ev("scene_summarized", scene="s1", scene_d="s1", summary="s1摘要"),
          _ev("narration_recorded", scene="s2", scene_d="s2", text="中间原文"),
          _ev("narration_recorded", scene="s3", scene_d="s3", text="最近原文"),
      ]
      # NB: narration_recorded/scene_summarized take deltas {"scene","text"/"summary"};
      # build these with kernel_event directly rather than the _ev helper if _ev's
      # deltas mapping doesn't fit — keep the test honest to the event shapes.
      world = project(reg, evs)
      scene = {"protagonist": None, "present": [], "day": 1, "id": "s3"}
      out = assemble_context(reg, world, scene, query=None)   # NO query → still present
      assert "线A：查案" in out            # storyline ledger force-pushed
      assert "最近原文" in out             # recent raw force-pushed
      assert "s1摘要" in out               # aged summary force-pushed (stable block)
      assert "最老原文" not in out         # aged out of raw window
      # ordering: stable summary block precedes scene raw/ledger
      assert out.index("s1摘要") < out.index("最近原文")

  def test_recap_summary_absent_when_no_summaries():
      from systems.narrative import NarrativeSystem
      from systems.story import StorySystem
      reg = (Registry().register(OntologySystem())
             .register(StorySystem()).register(NarrativeSystem()))
      world = project(reg, [])
      out = assemble_context(reg, world, {"protagonist": None, "present": [],
                                          "day": 1, "id": "s1"}, query=None)
      assert "往昔概要" not in out          # nothing summarized → no stable block
  ```
  (Adjust the `_ev` helper or use `kernel_event` directly so `narration_recorded`/`scene_summarized` carry the right `deltas` keys — see Task 5/6 shapes.)
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** in `context/assembler.py`: after computing `base = render(frags)` and BEFORE assembling `parts`, build a `recap_summary_lines` list from `world.get("systems",{}).get("narrative")`: if `super_summary` or any bucket has a `summary`, emit `## [stable]` + `【往昔概要】（更早剧情的压缩记忆）` + `«总览» {super_summary}` (if set) + `«{scene}» {summary}` for each bucket whose `summary` is not None and whose index is beyond the recent window (i.e. the aged ones). Prepend these lines to `parts` (so they lead the string, in the stable layer, maximizing cache reuse). Leave the existing viewpoint/recall composition unchanged. Add a short docstring note that recap+storyline are force-pushed (PUSH, spec §1).
- [ ] **Step 4 — run, expect PASS;** full-suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add context/assembler.py tests/context/test_assembler.py && git commit -m "feat(assembler): force-push recap summary block (PUSH, query-independent) [P2 §1]

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 10: Wire narration + cheap provider into `digest_fleet` from `run_turn` (D3)

**Files:** Modify `loop/turn.py`; Modify `tests/loop/test_turn.py`.

- [ ] **Step 1 — failing test** appended to `tests/loop/test_turn.py` (mirror the existing `run_turn` integration tests; build a registry incl. ontology+place+character+story+narrative; a `FakeLLMProvider` whose narration JSON is a minimal valid commit). Assert that after a turn, the re-projected `result.world["systems"]["narrative"]` contains the turn's narration verbatim:
  ```python
  def test_run_turn_records_narration_into_recap():
      # ... build reg with story+narrative, seed a protagonist+place ...
      provider = FakeLLMProvider(json_responses=[{
          "narration": "你踏入静谧的村庄。", "moves": [], "places": [],
          "cast": [], "facts": [],
      }])
      result = run_turn(reg, store, world, scene, "四处看看",
                        strategy=AuthorStrategy(), provider=provider,
                        cascade_provider=provider)   # cheap recap provider = same fake here
      buckets = result.world["systems"]["narrative"]["scenes"]
      assert any("你踏入静谧的村庄。" in t for b in buckets for t in b["raw"])
  ```
- [ ] **Step 2 — run, expect FAIL** (narration not recorded — `digest_fleet` called without `narration_text`).
- [ ] **Step 3 — implement:** in `loop/turn.py`, change the existing `digest_fleet(...)` call to pass `narration_text=commit.narration, scene=scene_id, recap_provider=cascade_provider`. (No new hook block; recap/story maintenance rides the existing `digest_fleet` tracer span. `cascade_provider` is already a `run_turn` param and is the cheap provider — reuse it as the recap summarizer per the cost note.)
- [ ] **Step 4 — run, expect PASS;** full-suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add loop/turn.py tests/loop/test_turn.py && git commit -m "feat(turn): feed narration + cheap provider into digest_fleet for recap maintenance

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 11: Expose the `storylines` section in 甲/丙 prompts (D1) — RECONCILE WITH P1

**Files:** Modify `loop/strategy.py`; Modify (or add) a prompt-assertion test in `tests/loop/` (mirror however the `knowledge` block is currently asserted, if at all; if no prompt test exists, add a minimal one).

> **COORDINATION (read Coordination section above):** P1 adds a `world` section to these SAME two prompt strings. BEFORE editing, RE-READ `_SYSTEM_PROMPT` and `_SYSTEM_PROMPT_HYBRID` in `loop/strategy.py`. If P1's `world` block is already present, insert the `storylines` block ALONGSIDE it (do NOT remove `world`). If neither is present, mirror the existing `knowledge` block's insertion style. The `knowledge` block is the shared precedent.

- [ ] **Step 1 — failing test** in `tests/loop/test_strategy_prompts.py` (create if absent; otherwise append to the existing strategy test file):
  ```python
  from loop.strategy import _SYSTEM_PROMPT, _SYSTEM_PROMPT_HYBRID

  def test_storylines_section_documented_in_both_prompts():
      for p in (_SYSTEM_PROMPT, _SYSTEM_PROMPT_HYBRID):
          assert "storylines" in p
          assert "open" in p and "advance" in p and "resolve" in p
          # still mentions knowledge (we add alongside, not replace)
          assert "knowledge" in p
  ```
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:** add a `storylines` bullet to the `【结构】` list in `_SYSTEM_PROMPT` and to the rule list in `_SYSTEM_PROMPT_HYBRID`, mirroring the `knowledge` bullet. Example bullet text (keep concise, Chinese, like the others):
  ```
  - storylines: 记录"剧情线的开启/推进/收束"（可选）——本回合若开启新主线、推进已有线、或收束某条线，声明 [{"op":"open"|"advance"|"resolve","id":故事线标识(可自拟稳定字符串),"summary":"一句话剧情线摘要"}]；id 在同一条线的多回合间保持一致，与上文【故事线·明账】中已列的 id 复用。
  ```
  Add a one-line pointer in the `【信息视野】`-adjacent area if helpful. Do NOT alter the `knowledge` block. **If P1's `world` block exists, place `storylines` next to it.**
- [ ] **Step 4 — run, expect PASS;** full-suite gate green.
- [ ] **Step 5 — commit:** `cd /root/rpg-engine-app && git add loop/strategy.py tests/loop/test_strategy_prompts.py && git commit -m "feat(strategy): expose storylines commit section in 甲/丙 prompts (mirror knowledge)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Task 12: Full-suite + legacy gate, final sweep

**Files:** none (verification only).

- [ ] **Step 1 — full suite:** `cd /root/rpg-engine-app && python3 -m pytest -q --ignore=tests/test_embed_real.py` → all green (738 baseline + the new P2 tests; expect ~30+ added).
- [ ] **Step 2 — legacy gate** (if your worktree runs it): confirm the legacy suite is still green (no `engine/` / `_legacy/` files were touched).
- [ ] **Step 3 — placeholder scan:** `cd /root/rpg-engine-app && grep -rn "TODO\|FIXME\|placeholder\|pass  #\|NotImplementedError\|\.\.\.$" systems/story.py systems/narrative.py loop/fleet.py context/assembler.py` → no stub bodies in the new/edited code.
- [ ] **Step 4 — name-consistency scan:** `cd /root/rpg-engine-app && grep -rn "storyline\|narration_recorded\|scene_summarized\|recap_recompressed\|RECAP_RAW_SCENES" systems/ loop/ context/ app/` → event-type strings + constants match this plan exactly; no drift between `to_events` type strings and `event_types()` declarations.
- [ ] **Step 5 — no commit** (verification task). If anything is red, fix forward in the owning task's files only.

---

## Self-Review — spec bullet → task coverage

| Spec bullet (P2 scope) | Where covered |
|---|---|
| §1 PUSH vs PULL — recap+storylines force-pushed, NOT recall-gated | Task 4 (ledger inject), Task 6 (raw inject), **Task 9** (assembler force-push, `query=None` test), D4 |
| §2.2 recap — recent N原文 + 更老摘要 + 摘要的摘要 (recursive) | Task 5 (slice + apply for all 3 tiers), Task 6 (recent-N inject), Task 8 (summarize aged + recompress), Task 9 (stable summary block) |
| §2.2 recap default unit=scene, N=2, constant+tunable | Task 5 (`RECAP_RAW_SCENES=2` constant), DECISION-FOR-HUMAN #2 |
| §2.2 recap maintained by digest fleet, cheap-model, gated to age-out | Task 8 (`summarize_scene` via `recap_provider`, gated on `aged_out_scene`; tests prove 0 calls within window, 1 call on age-out) |
| §2.2 recap rewind-safe storage (event-sourced) | D2, Task 5 (event-sourced slice), Task 5 test `test_recompress...` + narrative project-over-subset |
| §2.2 raw narration persistence (where?) | D3, Task 5 (`narration_recorded`), Task 8 (fleet appends it), Task 10 (run_turn feeds it) |
| §2.3 storyline ledger — `{id,status,summary,last_advanced_scene}`, always injected | Task 1–4 (`StorySystem`), D1; record shape Task 2; always-inject Task 4 |
| §2.3 narrator declares via `storylines` section → events; exposed in 甲/丙 | Task 3 (validate/to_events), Task 11 (prompts) |
| §2.3 ledger in a world slice projected from events (rewind-safe) | Task 2 (`world["systems"]["story"]` folded from events) |
| §2.3 distinct from director's hidden threads | D1 rationale (separate system/slice/event names) |
| §3 方案A+方案B / §12 digest backstop独立穷举漏报 | Task 8 (`backstop_storylines`), D1 (conservative休眠 flag), DECISION-FOR-HUMAN #1 |
| §5 force-push costs more tokens (deliberate) + bounded by tiering | D4 + cost note honored: recap tiered, summarize gated to age-out, cheap provider |
| §5 harness events走轻量 referential 校验 | Task 8 (backstop drop-on-dup, drop-on-fail, no repair); narration/summary apply defensive |
| Coordination with P1 (shared prompt + assembler) | Coordination section + Task 11 explicit reconcile note + D4 note that P1's `world` block must coexist |

**Placeholder scan:** Task 12 Step 3 (no TODO/stub bodies). **Name consistency:** Task 12 Step 4 (event-type strings vs `event_types()` vs `to_events` vs apply branches vs constants). Every event type used in a `kernel_event(...)` call is declared in the owning system's `event_types()` (so `EventStore.append`'s allow-set check passes) — re-verify in Task 12.

---

## DECISIONS FOR HUMAN

1. **Digest backstop: conservative休眠 flag (implemented) vs auto-open活跃.** This plan implements the CONSERVATIVE choice (§3 方案B as a backstop, not a co-author): the backstop only fires when the ledger has ZERO active threads AND the turn was substantive, and it lands a **休眠** "possible thread" record (a flag the narrator promotes next turn via `advance`), never a confident活跃明线. Rationale: auto-opening活跃 threads would let the cheap heuristic pollute the player-facing明账 and double-count with narrator declarations. If you want the backstop more aggressive (auto-open活跃 whenever the narrator forgot, even with other active threads present), say so — it is a one-line trigger-condition change in `backstop_storylines` (Task 8).

2. **Recap window `RECAP_RAW_SCENES = 2` and `RECAP_SUMMARY_FANOUT = 6`.** Spec §2.2 default is N=2 full scenes; I made it a module constant. With chapter-length narration (glm-5.1 writes long), 2 full scenes of原文 + summaries could still be a meaningful token load each turn (force-pushed every turn per §1). If turns blow the budget, lower `RECAP_RAW_SCENES` to 1 or switch the recap unit from scene→turn (the slice would bucket per turn instead). Flagging because §5 explicitly accepts the cost but the exact N is a budget dial only you can set against real model token limits.

3. **Token-budget concern — force-push every turn.** Recap (stable summaries + 2 raw scenes) + storyline ledger are now injected EVERY turn unconditionally (the whole point of §1 PUSH). Combined with P1's `world`-driven cascade context and the existing viewpoint/recall blocks, the per-turn prompt grows. The tiering bounds it (older scenes compress; resolved threads leave the ledger), but you may want a hard `assemble_context` size budget / truncation guard as a follow-up if real runs show prompt bloat — out of scope for P2, noting it as a watch-item.
