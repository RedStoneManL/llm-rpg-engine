# Phase B — 暗骰 Director Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or superpowers:executing-plans) to drive this plan. Every task is TDD: write a failing test, run it (expect FAIL), write the minimal implementation, run it (expect PASS), commit exactly the files the task names. Do not batch tasks; do not skip the red step.
>
> **🚧 HARD GIT GUARDRAILS (every implementing/reviewing agent MUST obey):** only make minimal incremental edits to existing files and add the new files this plan names. **NEVER** `git init` / `rm -rf .git` / `git checkout --orphan` / delete or rewrite `_legacy/` or `docs/` / switch branches / "rebuild from scratch". The repo is on branch `app`; stay on it. The legacy modules `engine/oracle.py` and `engine/director.py` are **reused unchanged** — do not edit them, and `tests/test_oracle.py` + `tests/test_director.py` MUST remain green at every commit. Stop and report any urge to do otherwise.

**Goal:** Make the world proactively make things happen — each turn a hidden seeded d100 (existing `engine/director.py`) may fire a director directive that is recorded as first-class `oracle_roll`/`director_fired` (and optionally `thread_open`) events through the strict gate, then injected into the **next** turn's narrator context so the main LLM weaves the twist into prose and any resulting world-changes flow through the normal commit + validation pipeline.

**Architecture:** A new `DirectorSystem` (a `ContextSystem`) owns the three director event types so the strict store accepts them, applies the most-recent fired directive into a small `world["systems"]["director"]` slice (a pending-directive queue), and `inject()`s that pending directive as a 导演 context Fragment for the next turn. A thin, non-fatal `loop/director.py::run_director(...)` hook runs **post-apply** inside a tracer span in `run_turn` (mirroring how `digest_fleet` is wired): it derives pacing from the event stream via the pure `compute_pacing`, rolls the pure `director_check` with a deterministic `scene_seed(campaign_seed, scene_ordinal)` Oracle, and on a fire appends the audit + directive events. The `campaign_seed` lives in world meta (seeded at genesis). B is split into **B1** (core fire → directive event → next-turn injection; no threads) and **B2** (dormant-thread store + scheduling + anti-convergence seeding) so B1 is fully self-contained and shippable.

**Tech Stack:** Python 3.12 stdlib only (`random`, `hashlib` already in `engine/oracle.py`); no new dependencies. Reuses `engine/oracle.py`, `engine/director.py`, `data/oracles/default/*.json`, `kernel/contextsystem.py`, `kernel/registry.py`, `kernel/projection.py`, `kernel/events.py`, `kernel/observability.py`, `context/assembler.py`, `loop/turn.py`, `app/engine.py`. Tests are offline + deterministic: `FakeLLMProvider` + seeded `Oracle`, no network.

---

## Design decisions (resolved up front — referenced by tasks)

These six are the load-bearing decisions; each task below implements one or more.

1. **Where the director runs — POST-TURN (decide after apply, inject into NEXT turn).** Recommendation: post-turn. Rationale: the roadmap explicitly says "注入下一回合上下文"; the directive must reach the narrator through the same `assemble_context` → strategy path the player input uses, and any world-changes the directive provokes must go through the normal commit + strict gate. Pre-turn would force the director to mutate *this* turn's context after it's already assembled (the strategy assembles context internally at `produce`), which is invasive. Post-turn is a clean append-only side effect, identical in shape to `digest_fleet` (already wired post-apply in `run_turn`, in a tracer span, non-fatal try/except). Exact location: inside `run_turn`'s `with get_tracer().span("turn", ...)` block, **after** the `digest_fleet` block and **before** `return TurnResult(...)` (Task 5).

2. **How `director_fired`/`oracle_roll` become first-class events through the strict gate — a new `DirectorSystem` ContextSystem owning them.** Recommendation: `DirectorSystem` declares `event_types() = {"oracle_roll", "director_fired", "thread_open"}` and registers in `build_engine`. The strict store rejects unknown event types (`EventStore.append` → `validate_event(ev, allowed_types)` where `allowed_types = registry.event_types()`), so declaring them is mandatory. `DirectorSystem.apply` writes the fired directive into `world["systems"]["director"]["pending"]` (a list) on `director_fired`, and `oracle_roll` is audit-only (no state change). The director hook appends these events **directly** via `kernel_event` + `store.append` (NOT through a commit section), so the LLM never authors them — they are harness-emitted (like genesis events in `new_game`). Therefore `DirectorSystem` declares **no** `commit_sections()` in B1 (Task 2 defines schema + apply + a degenerate `validate`).

3. **Deterministic seed source — `campaign_seed` lives in world meta, seeded at genesis.** Recommendation: store `campaign_seed` in `world["meta"]["campaign_seed"]`, written by a genesis `campaign_seeded` event (a new `DirectorSystem` event type) appended first in `new_game`, derived deterministically from the campaign dir name: `int(hashlib.sha256(name.encode()).hexdigest()[:12], 16)`. Rationale: event-sourced + rewind-safe (Phase E truncates/replays the event log — a seed stored as the FIRST event replays identically); no separate config file to keep in sync; `scene_seed(campaign_seed, scene_ordinal)` then reproduces every roll on replay. (Task 4 adds this; `project` must surface it into `meta`.) The seed is read back by the director hook from `world["meta"]["campaign_seed"]`, falling back to `0` if absent so the hook never crashes on a pre-B campaign.

4. **Directive injection — `DirectorSystem.inject` renders a 导演 directive Fragment; consumed-once via a turn watermark.** Recommendation: `DirectorSystem.inject(scene, world)` reads the newest un-consumed entry in `world["systems"]["director"]["pending"]` and returns a `Fragment(system="director", layer="scene", text=..., affordance=...)` that names the event_type + twist + magnitude + type as a backstage instruction to the narrator (e.g. "本回合请自然地引入一个【危机·另有目的】量级=big 的转折…"). `assemble_context` already calls `assemble(registry, scene, world)` which invokes every system's `inject` — so no assembler edit is needed for the Fragment to appear (Task 3 only adds the system method; Task 3b verifies it surfaces through `assemble_context`). Clearing: a directive is "consumed" once a turn strictly greater than the directive's `turn` begins. We store `consumed_through_turn` in the slice; `inject` skips directives whose `turn < scene's implied turn`, and the hook marks the prior directive consumed when it next runs. The simplest robust rule (Task 3): `inject` returns only directives with `consumed == False`, and the director hook sets `consumed = True` on all existing pending entries at the *start* of its run (so a directive injected on turn N is consumed when the hook runs on turn N+1's post-apply). This guarantees a directive is shown exactly once.

5. **dormant_thread axis & anti-convergence — minimal thread store in the director slice (B2).** Recommendation (deferred to **B2**): threads live in `world["systems"]["director"]["threads"]` as a dict `{thread_id: {id, status, speed, last_advanced_scene, dormant, trait, archetype, event_type}}`, projected from `thread_open` / `thread_advance` events (B2 adds `thread_advance` to `DirectorSystem.event_types()`). Seeding 3–5 DISTINCT threads with no repeated `trait`/`archetype` uses the existing `thread_archetypes` + `npc_traits` tables drawn via the seeded Oracle with rejection of duplicates (the "slot-machine" anti-convergence). B2's hook path: when `director_check` returns `type == "dormant_thread"`, instead of (or in addition to) a `director_fired`, it either *opens* a new dormant thread (if under the 3–5 band) or *advances a due* dormant thread via the existing `pick_thread_to_advance`/`thread_due_scores`. **B1 ignores the `dormant_thread`/`front_stage` distinction for world state** — both just produce a `director_fired` directive for the narrator — so B1 is self-contained. (See "B1/B2 split" below.)

6. **Backstop / safety — existing band + cooldown + tension gate, plus a "never two turns in a row" guard.** Recommendation: confirm the existing caps are sufficient (`pacing_probability`: 15% cooldown dip the scene right after a fire, 30% base, +6%/scene, 60% hard cap; `TENSION_GATE` downgrades non-crit front_stage to dormant at high tension). Add one cheap additional guard in the hook: **never fire two turns in a row** — if the most-recent event already carries a `director_fired` for the immediately preceding turn, skip the roll this turn. This is belt-and-suspenders over the cooldown (cooldown is scene-based; a multi-turn single scene could otherwise re-roll), and it's trivially testable. (Task 5 implements this guard.)

---

## B1 / B2 split decision

**SPLIT: yes — B1 then B2.**

- **B1 — core fire → directive → event → next-turn injection (no threads).** Tasks 1–6. Delivers: `DirectorSystem` owning `campaign_seeded` + `oracle_roll` + `director_fired`, applying the fired directive into a pending queue; `campaign_seed` seeded at genesis into world meta; `DirectorSystem.inject` rendering the 导演 directive Fragment; `loop/director.py::run_director` wired post-apply in `run_turn` (deterministic Oracle, non-fatal, never-two-in-a-row guard); `build_engine` registers the system. After B1 the world proactively emits directives that the next turn's narrator weaves in, and all director events flow through the strict gate. B1 treats `dormant_thread` and `front_stage` identically (both → a `director_fired` directive) so it ships without a thread model.
- **B2 — dormant threads + scheduling + anti-convergence seeding.** Tasks 7–10. Adds: `thread_open` projection into a thread store, a seeding helper that draws 3–5 DISTINCT threads (no repeated trait/archetype) via the seeded Oracle, wiring the `dormant_thread` branch of `director_check` to open/advance threads (reusing `pick_thread_to_advance`/`thread_due_scores`), and a directive that surfaces a due dormant thread. B2 depends only on B1's `DirectorSystem` + hook.

B1 is fully self-contained and testable on its own; B2 is purely additive. An implementer may stop after B1 and have a shippable increment.

---

## File Structure

| File | Action | Responsibility |
| --- | --- | --- |
| `systems/director.py` | **Create** | `DirectorSystem(ContextSystem)`: declares director event types; `apply` folds `campaign_seeded` → `world["meta"]["campaign_seed"]` and `director_fired` → pending-directive queue in its slice; `inject` renders the pending 导演 directive as a `Fragment`; degenerate `validate`/`to_events` (no LLM-authored sections in B1). B2 extends it with `thread_open` projection + a thread store. Uses `from engine.log import get_logger`. |
| `loop/director.py` | **Create** | `run_director(registry, store, world, *, scene_ordinal=None) -> list[dict]`: derive pacing (`compute_pacing`), build the seeded `Oracle` from `world["meta"]["campaign_seed"]` + `scene_seed`, apply the never-two-in-a-row guard, run `director_check`, and on a fire append `oracle_roll` + `director_fired` events to the store (returns the appended events). Pure-ish: all randomness comes from the seeded Oracle. B2 adds the dormant-thread branch. Uses `from engine.log import get_logger`. |
| `loop/turn.py` | **Modify** | Wire `run_director` into `run_turn` post-apply, after the `digest_fleet` block, in a `get_tracer().span("director", ...)`, non-fatal try/except; re-project world if it appended events. |
| `app/engine.py` | **Modify** | Register `DirectorSystem()` in `build_engine` (after the 6 existing systems); in `new_game`, append a `campaign_seeded` genesis event (FIRST, turn=0) carrying the derived `campaign_seed`. |
| `tests/systems/test_director_system.py` | **Create** | Unit tests for `DirectorSystem`: event-type ownership, `campaign_seeded` apply → meta, `director_fired` apply → pending queue, `inject` renders/clears the directive. |
| `tests/loop/test_director_loop.py` | **Create** | Unit tests for `run_director`: deterministic fire/quiet given a seed, appends `oracle_roll`+`director_fired` on fire, never-two-in-a-row guard, events pass the strict store. |
| `tests/loop/test_turn.py` | **Modify** | Add a test that `run_turn` invokes the director hook (a forced-fire campaign produces a `director_fired` event and the next turn's assembled context contains the directive). |
| `tests/app/test_engine.py` | **Modify** | Add a test that `build_engine` registers `director` (its event types are accepted by the store) and `new_game` seeds `world["meta"]["campaign_seed"]`. |

No edits to `engine/oracle.py`, `engine/director.py`, `data/oracles/**`, `kernel/**`, `context/assembler.py`, or any other `systems/*.py`. (`assemble_context` already iterates every system's `inject`, so the directive surfaces with zero assembler changes.)

---

## Conventions (bake into every task)

- Every new module starts with `from engine.log import get_logger` and `log = get_logger("systems.director")` / `get_logger("loop.director")`.
- Tests live under `tests/` mirroring the source path (`systems/director.py` → `tests/systems/test_director_system.py`; `loop/director.py` → `tests/loop/test_director_loop.py`).
- Run tests with `cd /root/rpg-engine-app && python3 -m pytest -q` (the binary is **`python3`**, never `python`). For a focused run, target the file: `python3 -m pytest -q tests/systems/test_director_system.py`. The full-suite gate excludes the network test: `python3 -m pytest -q --ignore=tests/test_embed_real.py`.
- Commit only the files a task names. Commit message trailer (exactly):
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```
- Tests must be **offline + deterministic**: `FakeLLMProvider` + seeded `Oracle`. No real model, no network.
- After every task: `tests/test_oracle.py` and `tests/test_director.py` must still pass (they import the unchanged legacy modules). The whole suite must be green before each commit.

---

# B1 — Core fire → directive → event → next-turn injection

## Task 1: `DirectorSystem` event-type ownership + empty slice

**Files:** Create `systems/director.py`; Create `tests/systems/test_director_system.py` (Test).

- [ ] **Step 1 — write failing test.** Create `tests/systems/test_director_system.py`:
  ```python
  """Tests for DirectorSystem (Phase B1)."""
  from __future__ import annotations

  from kernel.registry import Registry
  from kernel.projection import project, empty_world
  from kernel.events import kernel_event
  from kernel.contextsystem import Fragment
  from systems.ontology import OntologySystem
  from systems.director import DirectorSystem


  def _reg():
      return Registry().register(OntologySystem()).register(DirectorSystem())


  def test_director_owns_event_types():
      ds = DirectorSystem()
      assert ds.name == "director"
      assert ds.event_types() == {"campaign_seeded", "oracle_roll", "director_fired"}
      # B1 emits events directly (harness-authored), so it owns no commit sections.
      assert ds.commit_sections() == set()


  def test_director_registers_without_requires_cycle():
      reg = _reg()
      assert "director" in {s.name for s in reg.systems}
      assert "director_fired" in reg.event_types()
      assert reg.owner_of_event("oracle_roll").name == "director"


  def test_empty_state_is_pending_queue():
      ds = DirectorSystem()
      st = ds.empty_state()
      assert st == {"pending": [], "consumed_through_turn": 0}
  ```
- [ ] **Step 2 — run it (expect FAIL).** `cd /root/rpg-engine-app && python3 -m pytest -q tests/systems/test_director_system.py` → fails with `ModuleNotFoundError: systems.director`.
- [ ] **Step 3 — minimal implementation.** Create `systems/director.py`:
  ```python
  """DirectorSystem — owns the 暗骰 director's first-class events so they pass the
  strict store, projects the most-recent fired directive into a pending queue, and
  injects that pending directive as a 导演 context Fragment for the NEXT turn.

  B1 scope: campaign_seeded (genesis seed) + oracle_roll (audit) + director_fired
  (the directive). Director events are harness-authored (appended directly by
  loop/director.run_director), so this system declares NO commit sections in B1.

  World slice (world["systems"]["director"]):
      {"pending": [<directive dict>, ...], "consumed_through_turn": <int>}
  campaign_seed is surfaced into world["meta"]["campaign_seed"] by apply().
  """
  from __future__ import annotations

  from typing import Any

  from kernel.contextsystem import ContextSystem, ValidationError, Fragment
  from engine.log import get_logger

  log = get_logger("systems.director")


  class DirectorSystem(ContextSystem):
      name = "director"

      def event_types(self) -> set[str]:
          return {"campaign_seeded", "oracle_roll", "director_fired"}

      def commit_sections(self) -> set[str]:
          # B1: director events are harness-authored, not LLM-authored.
          return set()

      def empty_state(self) -> dict:
          return {"pending": [], "consumed_through_turn": 0}

      def apply(self, world: dict, event: dict) -> None:
          # Implemented in Task 2.
          pass

      def inject(self, scene: dict, world: dict) -> Fragment | None:
          # Implemented in Task 3.
          return None
  ```
- [ ] **Step 4 — run it (expect PASS).** `python3 -m pytest -q tests/systems/test_director_system.py` → 3 passed.
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add systems/director.py tests/systems/test_director_system.py
  git commit -m "feat(systems): DirectorSystem skeleton — owns director event types (B1)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 2: `DirectorSystem.apply` — campaign_seeded → meta; director_fired → pending queue

**Files:** Modify `systems/director.py`; Modify `tests/systems/test_director_system.py` (Test).

- [ ] **Step 1 — write failing test.** Append to `tests/systems/test_director_system.py`:
  ```python
  def _fired_event(turn, scene="s1", day=1, **extra):
      deltas = {
          "type": "front_stage",
          "magnitude": "big",
          "valence": None,
          "event_type": "危机",
          "event_hint": "遇到危险/被追杀/突发威胁",
          "twist": "另有目的",
          "twist_hint": "对方动机不单纯",
      }
      deltas.update(extra)
      return kernel_event("director_fired", day=day, scene=scene,
                          summary="突发:危机(另有目的)", deltas=deltas, turn=turn)


  def test_campaign_seeded_apply_sets_meta_seed():
      reg = _reg()
      ev = kernel_event("campaign_seeded", day=1, scene="genesis",
                        summary="campaign seed", deltas={"campaign_seed": 123456}, turn=0)
      world = project(reg, [ev])
      assert world["meta"]["campaign_seed"] == 123456


  def test_director_fired_apply_enqueues_directive():
      reg = _reg()
      world = project(reg, [_fired_event(turn=3)])
      slice_ = world["systems"]["director"]
      assert len(slice_["pending"]) == 1
      d = slice_["pending"][0]
      assert d["event_type"] == "危机" and d["twist"] == "另有目的"
      assert d["magnitude"] == "big" and d["type"] == "front_stage"
      assert d["turn"] == 3 and d["consumed"] is False


  def test_oracle_roll_apply_is_audit_only():
      reg = _reg()
      ev = kernel_event("oracle_roll", day=1, scene="s1",
                        summary="暗骰 roll=0.20 prob=0.30",
                        deltas={"prob": 0.30, "roll": 0.20}, turn=2)
      world = project(reg, [ev])
      # audit-only: no pending directive, slice unchanged from empty
      assert world["systems"]["director"]["pending"] == []
  ```
- [ ] **Step 2 — run it (expect FAIL).** `python3 -m pytest -q tests/systems/test_director_system.py` → the 3 new tests fail (`meta` has no `campaign_seed`; pending stays empty).
- [ ] **Step 3 — minimal implementation.** Replace `DirectorSystem.apply` in `systems/director.py`:
  ```python
      def apply(self, world: dict, event: dict) -> None:
          t = event["type"]
          d = event.get("deltas", {})
          if t == "campaign_seeded":
              seed = d.get("campaign_seed")
              if seed is not None:
                  world.setdefault("meta", {})["campaign_seed"] = seed
                  log.debug("campaign_seeded → meta campaign_seed=%s", seed)
              return
          if t == "oracle_roll":
              # Audit-only: recorded for reproducibility/importance, no state change.
              return
          if t == "director_fired":
              directive = {
                  "type": d.get("type"),
                  "magnitude": d.get("magnitude"),
                  "valence": d.get("valence"),
                  "event_type": d.get("event_type"),
                  "event_hint": d.get("event_hint"),
                  "twist": d.get("twist"),
                  "twist_hint": d.get("twist_hint"),
                  "turn": event.get("turn") or 0,
                  "scene": event.get("scene"),
                  "consumed": False,
              }
              slice_ = world["systems"][self.name]
              slice_["pending"].append(directive)
              log.debug("director_fired → enqueued directive %s/%s turn=%s",
                        directive["event_type"], directive["twist"], directive["turn"])
              return
  ```
- [ ] **Step 4 — run it (expect PASS).** `python3 -m pytest -q tests/systems/test_director_system.py` → all passed.
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add systems/director.py tests/systems/test_director_system.py
  git commit -m "feat(systems): DirectorSystem.apply — seed→meta, director_fired→pending queue

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 3: `DirectorSystem.inject` — render the pending 导演 directive Fragment (consumed-once)

**Files:** Modify `systems/director.py`; Modify `tests/systems/test_director_system.py` (Test).

- [ ] **Step 1 — write failing test.** Append to `tests/systems/test_director_system.py`:
  ```python
  def test_inject_renders_pending_directive():
      ds = DirectorSystem()
      world = {"meta": {}, "systems": {"director": {
          "pending": [{
              "type": "front_stage", "magnitude": "big", "valence": None,
              "event_type": "危机", "event_hint": "遇到危险/被追杀/突发威胁",
              "twist": "另有目的", "twist_hint": "对方动机不单纯",
              "turn": 3, "scene": "s1", "consumed": False,
          }],
          "consumed_through_turn": 0,
      }}}
      frag = ds.inject({"protagonist": "hero", "day": 1}, world)
      assert isinstance(frag, Fragment)
      assert frag.system == "director" and frag.layer == "scene"
      # The directive names the drawn seed so the narrator can weave it in.
      assert "危机" in frag.text and "另有目的" in frag.text
      assert "big" in frag.text


  def test_inject_skips_consumed_directive():
      ds = DirectorSystem()
      world = {"meta": {}, "systems": {"director": {
          "pending": [{
              "type": "front_stage", "magnitude": "small", "valence": None,
              "event_type": "机遇", "event_hint": "h", "twist": "无反转",
              "twist_hint": "h2", "turn": 1, "scene": "s1", "consumed": True,
          }],
          "consumed_through_turn": 1,
      }}}
      assert ds.inject({"protagonist": "hero", "day": 1}, world) is None


  def test_inject_none_when_no_pending():
      ds = DirectorSystem()
      world = {"meta": {}, "systems": {"director": {"pending": [], "consumed_through_turn": 0}}}
      assert ds.inject({"protagonist": "hero", "day": 1}, world) is None
  ```
- [ ] **Step 2 — run it (expect FAIL).** `python3 -m pytest -q tests/systems/test_director_system.py` → the 3 new tests fail (`inject` returns `None`).
- [ ] **Step 3 — minimal implementation.** Replace `DirectorSystem.inject` in `systems/director.py`:
  ```python
      _MAG_LABEL = {"small": "小", "big": "大", "crit": "暴击(高潮)"}

      def inject(self, scene: dict, world: dict) -> Fragment | None:
          """Render the newest UN-consumed directive as a backstage 导演 instruction.

          The narrator must weave the seed (event_type + twist + magnitude) into
          prose naturally; resulting world-changes flow through the normal commit.
          A directive is shown exactly once: the director hook marks prior pending
          directives consumed at the start of its next run (see loop/director)."""
          slice_ = world.get("systems", {}).get(self.name) or {}
          pending = [d for d in slice_.get("pending", []) if not d.get("consumed")]
          if not pending:
              return None
          d = pending[-1]  # newest
          mag = self._MAG_LABEL.get(d.get("magnitude"), d.get("magnitude") or "")
          valence = d.get("valence")
          val_txt = ""
          if valence == "boon":
              val_txt = "（基调:意外之喜/转机）"
          elif valence == "disaster":
              val_txt = "（基调:灾祸/危局）"
          text = (
              "【导演·暗骰】本回合请自然地引入一个转折，不要直白说明这是系统安排：\n"
              f"  事件原型：{d.get('event_type')} — {d.get('event_hint') or ''}\n"
              f"  反转：{d.get('twist')} — {d.get('twist_hint') or ''}\n"
              f"  量级：{mag}{val_txt}\n"
              "  把它写成主角此刻可感知、可回应的具体情节；但绝不替玩家决定下一步。"
          )
          affordance = "本回合应体现上述【导演·暗骰】转折"
          log.debug("inject directive %s/%s turn=%s", d.get("event_type"),
                    d.get("twist"), d.get("turn"))
          return Fragment(system="director", layer="scene", text=text, affordance=affordance)
  ```
- [ ] **Step 4 — run it (expect PASS).** `python3 -m pytest -q tests/systems/test_director_system.py` → all passed.
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add systems/director.py tests/systems/test_director_system.py
  git commit -m "feat(systems): DirectorSystem.inject — render pending 导演 directive (consumed-once)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 3b: directive surfaces through `assemble_context` (integration, no source edit)

**Files:** Modify `tests/systems/test_director_system.py` (Test only — verifies the assembler already picks up `inject`).

- [ ] **Step 1 — write failing test.** Append to `tests/systems/test_director_system.py`:
  ```python
  def test_directive_surfaces_through_assemble_context():
      """assemble_context iterates every system's inject(); the directive must
      appear in the assembled string with no assembler edits."""
      from context.assembler import assemble_context
      from systems.place import PlaceSystem

      reg = (Registry().register(OntologySystem())
             .register(PlaceSystem()).register(DirectorSystem()))
      world = project(reg, [_fired_event(turn=3)])
      scene = {"protagonist": "hero", "present": [], "day": 1, "id": "s1", "location": "s1"}
      ctx = assemble_context(reg, world, scene)
      assert "导演·暗骰" in ctx
      assert "危机" in ctx and "另有目的" in ctx
  ```
- [ ] **Step 2 — run it (expect FAIL or PASS?).** `python3 -m pytest -q tests/systems/test_director_system.py::test_directive_surfaces_through_assemble_context`. NOTE: this should **PASS immediately** because `assemble_context` → `kernel.assembler.assemble` already calls every system's `inject`. If it FAILS, the cause is a real wiring gap (e.g. `assemble` filters systems) — investigate before proceeding; do NOT paper over it. This task exists to *prove* the chosen injection mechanism (design Q4) works end-to-end without touching the assembler.
- [ ] **Step 3 — implementation.** None expected. If Step 2 failed, the minimal fix is in `context/assembler.py`/`kernel/assembler.py` — but only after confirming the gap; record the finding in the commit message.
- [ ] **Step 4 — run it (expect PASS).** Re-run; full file green.
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add tests/systems/test_director_system.py
  git commit -m "test(systems): prove director directive surfaces through assemble_context

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 4: `campaign_seed` at genesis + register `DirectorSystem` in `build_engine`

**Files:** Modify `app/engine.py`; Modify `tests/app/test_engine.py` (Test).

- [ ] **Step 1 — write failing test.** Append to `tests/app/test_engine.py` (match the file's existing import/fixture style; it already imports `build_engine`, `new_game`, `FakeLLMProvider`):
  ```python
  def test_build_engine_registers_director(tmp_path):
      from llm.provider import FakeLLMProvider
      eng = build_engine(tmp_path / "campA", provider=FakeLLMProvider(), embedder=None)
      names = {s.name for s in eng.registry.systems}
      assert "director" in names
      # the store must accept director event types (strict allow-set)
      assert {"campaign_seeded", "oracle_roll", "director_fired"} <= eng.registry.event_types()


  def test_new_game_seeds_campaign_seed_into_meta(tmp_path):
      from llm.provider import FakeLLMProvider
      eng = build_engine(tmp_path / "campB", provider=FakeLLMProvider(), embedder=None)
      new_game(eng)
      seed = eng.world["meta"].get("campaign_seed")
      assert isinstance(seed, int) and seed > 0


  def test_campaign_seed_is_deterministic_per_campaign_name(tmp_path):
      from llm.provider import FakeLLMProvider
      e1 = build_engine(tmp_path / "same", provider=FakeLLMProvider(), embedder=None)
      new_game(e1)
      e2 = build_engine(tmp_path / "x" / "same", provider=FakeLLMProvider(), embedder=None)
      new_game(e2)
      # seed derives from the campaign dir *name*, so same name → same seed (rewind-safe)
      assert e1.world["meta"]["campaign_seed"] == e2.world["meta"]["campaign_seed"]
  ```
- [ ] **Step 2 — run it (expect FAIL).** `python3 -m pytest -q tests/app/test_engine.py` → new tests fail (`director` not registered; no `campaign_seed`).
- [ ] **Step 3 — minimal implementation.** In `app/engine.py`:
  - Add imports near the other system imports:
    ```python
    import hashlib
    from systems.director import DirectorSystem
    ```
  - Register the system after `KnowledgeSystem` in `build_engine`:
    ```python
        registry.register(KnowledgeSystem())
        registry.register(DirectorSystem())
    ```
  - Add a module-level helper + a genesis seed event. Define the helper near the genesis constants:
    ```python
    def _derive_campaign_seed(campaign_dir: Path) -> int:
        """Deterministic seed from the campaign dir NAME (rewind-safe: replays identically)."""
        name = campaign_dir.name or "campaign"
        return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:12], 16)
    ```
    `build_engine` returns an `Engine` with no campaign path stored; `new_game` needs the dir name. The simplest minimal change: stash the derived seed on the engine at build time so `new_game` can read it. Add a field to the `Engine` dataclass:
    ```python
    @dataclass
    class Engine:
        registry: Registry
        store: Any
        provider: Any
        embedder: Any
        world: dict = field(default_factory=dict)
        campaign_seed: int = 0
    ```
    and set it in `build_engine` right before constructing the `Engine`:
    ```python
        campaign_seed = _derive_campaign_seed(campaign_dir)
    ...
        return Engine(
            registry=registry, store=store, provider=provider,
            embedder=embedder, world=world, campaign_seed=campaign_seed,
        )
    ```
  - In `new_game`, prepend a `campaign_seeded` event as the FIRST genesis event (before `place_created`), so it replays first and the seed is in `meta` for the whole campaign:
    ```python
        # --- Event 0: record the deterministic campaign seed (FIRST — rewind-safe) ---
        ev_seed = kernel_event(
            "campaign_seeded",
            day=day,
            scene=scene,
            summary=f"campaign seed = {engine.campaign_seed}",
            deltas={"campaign_seed": engine.campaign_seed},
            turn=0,
        )
        engine.store.append(ev_seed)
        log.debug("new_game: appended campaign_seeded seed=%s", engine.campaign_seed)
    ```
  - Confirm `project` surfaces it: `DirectorSystem.apply` already writes `world["meta"]["campaign_seed"]` (Task 2), and `project` re-runs all events, so after the final `engine.world = project(...)` the seed is present. No `kernel/projection.py` edit needed.
- [ ] **Step 4 — run it (expect PASS).** `python3 -m pytest -q tests/app/test_engine.py` then the full suite `python3 -m pytest -q --ignore=tests/test_embed_real.py` → all green (existing engine tests must still pass — adding one registry system + one genesis event must not break genesis ordering; the new event references no entities so order is safe).
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add app/engine.py tests/app/test_engine.py
  git commit -m "feat(app): register DirectorSystem + seed campaign_seed at genesis (meta, rewind-safe)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 5: `loop/director.py::run_director` — seeded roll → append events (with backstops)

**Files:** Create `loop/director.py`; Create `tests/loop/test_director_loop.py` (Test).

- [ ] **Step 1 — write failing test.** Create `tests/loop/test_director_loop.py`:
  ```python
  """Tests for loop.director.run_director (Phase B1)."""
  from __future__ import annotations

  import tempfile, os

  from kernel.registry import Registry
  from kernel.projection import project
  from kernel.events import open_store, kernel_event
  from systems.ontology import OntologySystem
  from systems.director import DirectorSystem
  from loop.director import run_director


  def _reg():
      return Registry().register(OntologySystem()).register(DirectorSystem())


  def _store(reg):
      d = tempfile.mkdtemp()
      return open_store(os.path.join(d, "events.db"), os.path.join(d, "events.jsonl"),
                        allowed_types=reg.event_types())


  def _seed_event(seed=999):
      return kernel_event("campaign_seeded", day=1, scene="genesis",
                          summary="seed", deltas={"campaign_seed": seed}, turn=0)


  def _action(turn, scene, day=1):
      return kernel_event("entity_created", day=day, scene=scene,
                          summary="x", deltas={"id": f"e{turn}", "etype": "Object"}, turn=turn)


  def _find_fire_seed():
      """Find a campaign_seed that fires at scene_ordinal with high scenes_since_event.
      run_director is deterministic given (campaign_seed, scene_ordinal), so we scan
      seeds offline to get a deterministic 'will fire' fixture."""
      for seed in range(2000):
          reg = _reg()
          store = _store(reg)
          store.append(_seed_event(seed))
          # build several distinct scenes with no director_fired → scenes_since_event high
          for i in range(1, 7):
              store.append(_action(i, f"s{i}"))
          world = project(reg, store.iter_events())
          appended = run_director(reg, store, world)
          if any(e["type"] == "director_fired" for e in appended):
              return seed
      raise AssertionError("no firing seed found in range")


  def test_run_director_deterministic_same_seed():
      reg1, reg2 = _reg(), _reg()
      s1, s2 = _store(reg1), _store(reg2)
      for st in (s1, s2):
          st.append(_seed_event(777))
          for i in range(1, 7):
              st.append(_action(i, f"s{i}"))
      w1 = project(reg1, s1.iter_events())
      w2 = project(reg2, s2.iter_events())
      a1 = [(e["type"], e["deltas"]) for e in run_director(reg1, s1, w1)]
      a2 = [(e["type"], e["deltas"]) for e in run_director(reg2, s2, w2)]
      assert a1 == a2  # same seed + same stream → identical outcome


  def test_run_director_appends_audit_and_directive_on_fire():
      seed = _find_fire_seed()
      reg = _reg()
      store = _store(reg)
      store.append(_seed_event(seed))
      for i in range(1, 7):
          store.append(_action(i, f"s{i}"))
      world = project(reg, store.iter_events())
      appended = run_director(reg, store, world)
      types = [e["type"] for e in appended]
      assert "oracle_roll" in types          # audit event
      assert "director_fired" in types       # the directive
      # events were accepted by the strict store
      fired = next(e for e in appended if e["type"] == "director_fired")
      assert fired["deltas"]["event_type"] in ("危机", "机遇", "人物", "世界", "羁绊")
      assert "twist" in fired["deltas"]


  def test_run_director_never_two_turns_in_a_row():
      """If the immediately-preceding turn already fired, skip the roll this turn."""
      seed = _find_fire_seed()
      reg = _reg()
      store = _store(reg)
      store.append(_seed_event(seed))
      for i in range(1, 7):
          store.append(_action(i, f"s{i}"))
      # simulate that the last turn already fired
      store.append(kernel_event("director_fired", day=1, scene="s6",
                                summary="prior fire",
                                deltas={"type": "front_stage", "magnitude": "small",
                                        "event_type": "机遇", "twist": "无反转"}, turn=6))
      world = project(reg, store.iter_events())
      appended = run_director(reg, store, world)
      assert appended == []  # guard: no fire right after a fire


  def test_run_director_quiet_appends_nothing():
      """A seed that doesn't fire appends no events (audit-only-on-fire)."""
      # scenes_since_event small → low prob; scan for a quiet seed at ordinal 1.
      for seed in range(2000):
          reg = _reg(); store = _store(reg)
          store.append(_seed_event(seed))
          store.append(_action(1, "s1"))
          world = project(reg, store.iter_events())
          appended = run_director(reg, store, world)
          if appended == []:
              break
      else:
          raise AssertionError("no quiet seed found")
      assert appended == []
  ```
- [ ] **Step 2 — run it (expect FAIL).** `python3 -m pytest -q tests/loop/test_director_loop.py` → `ModuleNotFoundError: loop.director`.
- [ ] **Step 3 — minimal implementation.** Create `loop/director.py`:
  ```python
  """loop.director — the post-turn 暗骰 director hook.

  run_director(registry, store, world, *, scene_ordinal=None) -> list[dict]
      1. Mark prior pending directives consumed (they were shown last turn).
      2. Derive pacing from the event stream (engine.director.compute_pacing).
      3. Backstop: never fire two turns in a row.
      4. Build a deterministic Oracle from world["meta"]["campaign_seed"] via
         engine.oracle.scene_seed(seed, scene_ordinal) — reproducible/rewind-safe.
      5. Run the pure engine.director.director_check; on a fire append an
         oracle_roll (audit) + director_fired (directive) event to the store.
      Returns the list of appended events (possibly empty). Never raises on a
      missing seed — falls back to 0 so a pre-B campaign degrades to quiet-ish.

  All randomness is seeded; offline-deterministic. Mirrors the digest_fleet hook:
  the caller wraps this in a tracer span + non-fatal try/except and re-projects.
  """
  from __future__ import annotations

  from engine.oracle import Oracle, load_table, scene_seed
  from engine.director import compute_pacing, director_check
  from kernel.events import kernel_event
  from engine.log import get_logger

  log = get_logger("loop.director")

  _TABLES_CACHE: dict | None = None


  def _tables() -> dict:
      global _TABLES_CACHE
      if _TABLES_CACHE is None:
          _TABLES_CACHE = {
              "event_types": load_table("event_types"),
              "twists": load_table("twists"),
          }
      return _TABLES_CACHE


  def _last_turn(events: list[dict]) -> int:
      return max((e.get("turn") or 0 for e in events), default=0)


  def _fired_on_turn(events: list[dict], turn: int) -> bool:
      return any(e["type"] == "director_fired" and (e.get("turn") or 0) == turn
                 for e in events)


  def run_director(registry, store, world: dict, *, scene_ordinal: int | None = None) -> list[dict]:
      events = list(store.iter_events())

      # (1) Consume directives shown last turn (inject() shows un-consumed ones).
      slice_ = world.get("systems", {}).get("director")
      if isinstance(slice_, dict):
          for d in slice_.get("pending", []):
              d["consumed"] = True

      pacing = compute_pacing(events)
      ordinal = scene_ordinal if scene_ordinal is not None else pacing["scene_ordinal"]

      # (3) Backstop: never fire two turns in a row (belt over the scene cooldown).
      last_turn = _last_turn(events)
      if _fired_on_turn(events, last_turn):
          log.debug("run_director: skip — director fired on the immediately-preceding turn %d",
                    last_turn)
          return []

      # (4) Deterministic Oracle.
      campaign_seed = (world.get("meta", {}) or {}).get("campaign_seed", 0)
      seed_int = scene_seed(campaign_seed, ordinal)
      out = director_check(pacing["scenes_since_event"], pacing["tension"],
                           Oracle(seed_int), tables=_tables())

      if not out["triggered"]:
          log.debug("run_director: quiet (ordinal=%d prob=%.2f roll=%.2f)",
                    ordinal, out["prob"], out["roll"])
          return []

      scene = pacing["current_scene"] or "scene"
      day = events[-1]["day"] if events else 1
      next_turn = last_turn + 1
      et = out["seed"]["event_type"]
      tw = out["seed"]["twist"]

      audit = kernel_event(
          "oracle_roll", day=day, scene=scene,
          summary=f"暗骰 roll={out['roll']:.2f} prob={out['prob']:.2f}",
          deltas={"prob": out["prob"], "roll": out["roll"],
                  "scene_ordinal": ordinal, "campaign_seed": campaign_seed},
          turn=next_turn,
      )
      directive = kernel_event(
          "director_fired", day=day, scene=scene,
          summary=f"突发:{et['name']}（{tw['name']}）",
          deltas={
              "type": out["type"], "magnitude": out["magnitude"],
              "valence": out["valence"],
              "event_type": et["name"], "event_hint": et.get("hint"),
              "twist": tw["name"], "twist_hint": tw.get("hint"),
          },
          turn=next_turn,
      )
      store.append(audit)
      store.append(directive)
      log.debug("run_director: FIRED type=%s mag=%s %s/%s (turn=%d)",
                out["type"], out["magnitude"], et["name"], tw["name"], next_turn)
      return [audit, directive]
  ```
  Note: appending these events through the *strict* store (`open_store(..., allowed_types=registry.event_types())`) is exactly what proves design Q2 — if `DirectorSystem` weren't registered, `store.append` would raise `unknown event type`. The tests append via the strict store, so they fail loudly if ownership regresses.
- [ ] **Step 4 — run it (expect PASS).** `python3 -m pytest -q tests/loop/test_director_loop.py` then `tests/test_director.py tests/test_oracle.py` → all green.
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add loop/director.py tests/loop/test_director_loop.py
  git commit -m "feat(loop): run_director hook — seeded roll → audit+directive events (B1, backstops)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 6: wire `run_director` into `run_turn` (post-apply, non-fatal, re-project)

**Files:** Modify `loop/turn.py`; Modify `tests/loop/test_turn.py` (Test).

- [ ] **Step 1 — write failing test.** Append to `tests/loop/test_turn.py` (reuse its existing `_make_registry`/`_open_temp_store` helpers, but register `DirectorSystem` and seed the campaign). Add an import and a forced-fire helper:
  ```python
  from systems.director import DirectorSystem
  from kernel.events import kernel_event


  def _reg_with_director():
      from systems.ontology import OntologySystem
      from systems.place import PlaceSystem
      from systems.character import CharacterSystem
      reg = Registry()
      reg.register(OntologySystem()); reg.register(PlaceSystem())
      reg.register(CharacterSystem()); reg.register(DirectorSystem())
      return reg


  def test_run_turn_invokes_director_and_next_turn_sees_directive(monkeypatch):
      """A forced-fire director appends director_fired post-apply; the NEXT turn's
      assembled context contains the 导演 directive. Offline + deterministic."""
      import loop.director as dirmod
      from context.assembler import assemble_context

      reg = _reg_with_director()
      store = _open_temp_store(reg)
      # seed so the campaign has a known campaign_seed in meta
      store.append(kernel_event("campaign_seeded", day=1, scene="genesis",
                                summary="seed", deltas={"campaign_seed": 1}, turn=0))

      # Force the director to fire deterministically by stubbing director_check.
      def _always_fire(scenes_since, tension, oracle, *, tables):
          et = tables["event_types"][0]; tw = tables["twists"][0]
          return {"triggered": True, "type": "front_stage", "magnitude": "big",
                  "valence": None, "seed": {"event_type": et, "twist": tw},
                  "prob": 0.6, "roll": 0.1}
      monkeypatch.setattr(dirmod, "director_check", _always_fire)

      world = project(reg, store.iter_events())
      scene = {"protagonist": "hero", "present": [], "day": 1, "id": "sc1", "location": "town"}

      # A FakeLLMProvider returning a minimal valid commit (narration + empty reasons).
      provider = FakeLLMProvider(json_responses=[{
          "narration": "一切如常。", "moves": [], "places": [], "cast": [], "facts": [],
      }])
      from loop.strategy import AuthorStrategy
      result = run_turn(reg, store, world, scene, "环顾四周",
                        strategy=AuthorStrategy(), provider=provider,
                        embedder=None, max_repairs=1)

      # director_fired now in the store, attributed to a turn AFTER this one
      all_events = list(store.iter_events())
      assert any(e["type"] == "director_fired" for e in all_events)

      # NEXT turn's context (re-projected world) shows the directive
      world2 = project(reg, store.iter_events())
      ctx = assemble_context(reg, world2, scene)
      assert "导演·暗骰" in ctx
  ```
- [ ] **Step 2 — run it (expect FAIL).** `python3 -m pytest -q tests/loop/test_turn.py` → fails (no director_fired appears; `run_turn` doesn't call the hook).
- [ ] **Step 3 — minimal implementation.** In `loop/turn.py`:
  - Add the import near the `digest_fleet` import:
    ```python
    from loop.director import run_director
    ```
  - In `run_turn`, immediately AFTER the `digest_fleet` try/except block and BEFORE `return TurnResult(...)`, add a mirror-shaped hook:
    ```python
        # 暗骰 director (design §16 / Phase B): a hidden seeded roll may append an
        # oracle_roll + director_fired directive that the NEXT turn's narrator weaves
        # in. Same shape as digest_fleet: post-apply, tracer span, never fatal.
        try:
            with get_tracer().span("director", turn=turn_num_before):
                dir_events = run_director(registry, store, new_world)
            if dir_events:
                new_world = project(registry, store.iter_events())
                log.debug("run_turn: director appended %d event(s)", len(dir_events))
        except Exception:
            log.exception("run_turn: run_director failed (non-fatal, backstage)")
    ```
- [ ] **Step 4 — run it (expect PASS).** `python3 -m pytest -q tests/loop/test_turn.py` then the full suite `python3 -m pytest -q --ignore=tests/test_embed_real.py` → all green (existing `run_turn`/play tests must still pass: the hook is additive and non-fatal; with a default `FakeLLMProvider` campaign that has no `campaign_seed`, the seed falls back to 0 and the hook still runs harmlessly).
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add loop/turn.py tests/loop/test_turn.py
  git commit -m "feat(loop): wire run_director into run_turn post-apply (B1, non-fatal, re-project)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

**B1 done criteria:** full suite green; `tests/test_director.py` + `tests/test_oracle.py` unchanged & green; a seeded campaign proactively emits `director_fired` directives post-turn that surface in the next turn's narrator context; all director events pass the strict store; never fires two turns in a row; rolls reproduce on replay (seed in meta). B1 is shippable here.

---

# B2 — Dormant threads + scheduling + anti-convergence seeding

> B2 is additive over B1. It teaches the director to maintain a small thread store and to route `director_check`'s `dormant_thread` outcomes into opening/advancing distinct dormant threads, reusing the already-tested `thread_due_scores` / `pick_thread_to_advance` from `engine/director.py`.

## Task 7: `DirectorSystem` projects `thread_open` / `thread_advance` into a thread store

**Files:** Modify `systems/director.py`; Modify `tests/systems/test_director_system.py` (Test).

- [ ] **Step 1 — write failing test.** Append to `tests/systems/test_director_system.py`:
  ```python
  def test_director_owns_thread_events_in_b2():
      ds = DirectorSystem()
      assert {"thread_open", "thread_advance"} <= ds.event_types()


  def test_thread_open_projects_into_thread_store():
      reg = _reg()
      ev = kernel_event("thread_open", day=1, scene="s1", summary="暗线",
                        deltas={"id": "th_revenge", "status": "活跃", "speed": "中",
                                "dormant": True, "trait": "城府极深", "archetype": "复仇宿敌",
                                "event_type": "阴谋线", "last_advanced_scene": "s1"}, turn=2)
      world = project(reg, [ev])
      threads = world["systems"]["director"]["threads"]
      assert "th_revenge" in threads
      assert threads["th_revenge"]["dormant"] is True
      assert threads["th_revenge"]["trait"] == "城府极深"


  def test_thread_advance_updates_last_advanced_scene():
      reg = _reg()
      world = project(reg, [
          kernel_event("thread_open", day=1, scene="s1", summary="暗线",
                       deltas={"id": "th1", "status": "活跃", "speed": "快",
                               "dormant": False, "trait": "毒舌", "archetype": "身世之谜",
                               "last_advanced_scene": "s1"}, turn=1),
          kernel_event("thread_advance", day=2, scene="s3", summary="推进",
                       deltas={"id": "th1", "last_advanced_scene": "s3"}, turn=4),
      ])
      assert world["systems"]["director"]["threads"]["th1"]["last_advanced_scene"] == "s3"
  ```
- [ ] **Step 2 — run it (expect FAIL).** `python3 -m pytest -q tests/systems/test_director_system.py` → new tests fail.
- [ ] **Step 3 — minimal implementation.** In `systems/director.py`:
  - Extend `event_types`:
    ```python
        def event_types(self) -> set[str]:
            return {"campaign_seeded", "oracle_roll", "director_fired",
                    "thread_open", "thread_advance"}
    ```
  - Extend `empty_state`:
    ```python
        def empty_state(self) -> dict:
            return {"pending": [], "consumed_through_turn": 0, "threads": {}}
    ```
  - Extend `apply` with two new branches (before the final return):
    ```python
          if t == "thread_open":
              tid = d.get("id")
              if not tid:
                  log.warning("thread_open missing id; skipped (%s)", event.get("id"))
                  return
              threads = world["systems"][self.name].setdefault("threads", {})
              threads[tid] = {
                  "id": tid,
                  "status": d.get("status", "活跃"),
                  "speed": d.get("speed", "中"),
                  "dormant": bool(d.get("dormant", False)),
                  "trait": d.get("trait"),
                  "archetype": d.get("archetype"),
                  "event_type": d.get("event_type"),
                  "last_advanced_scene": d.get("last_advanced_scene", event.get("scene")),
              }
              log.debug("thread_open id=%s dormant=%s", tid, threads[tid]["dormant"])
              return
          if t == "thread_advance":
              tid = d.get("id")
              threads = world["systems"][self.name].setdefault("threads", {})
              if tid in threads:
                  if "last_advanced_scene" in d:
                      threads[tid]["last_advanced_scene"] = d["last_advanced_scene"]
                  if d.get("dormant") is not None:
                      threads[tid]["dormant"] = bool(d["dormant"])
                  log.debug("thread_advance id=%s → scene=%s", tid,
                            threads[tid]["last_advanced_scene"])
              else:
                  log.warning("thread_advance for unknown thread %s; skipped", tid)
              return
    ```
    NOTE: existing B1 tests that asserted `empty_state() == {"pending": [], "consumed_through_turn": 0}` must be updated to include `"threads": {}`. Update `test_empty_state_is_pending_queue` accordingly in this task (it's the same test file).
- [ ] **Step 4 — run it (expect PASS).** `python3 -m pytest -q tests/systems/test_director_system.py` then `tests/test_director.py` → green.
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add systems/director.py tests/systems/test_director_system.py
  git commit -m "feat(systems): DirectorSystem projects thread_open/thread_advance into a thread store (B2)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 8: anti-convergence thread seeding (3–5 DISTINCT, no repeated trait/archetype)

**Files:** Modify `loop/director.py`; Modify `tests/loop/test_director_loop.py` (Test).

- [ ] **Step 1 — write failing test.** Append to `tests/loop/test_director_loop.py`:
  ```python
  from loop.director import seed_threads
  from engine.oracle import Oracle, load_table


  def _seed_tables():
      return {"thread_archetypes": load_table("thread_archetypes"),
              "npc_traits": load_table("npc_traits")}


  def test_seed_threads_distinct_traits_and_archetypes():
      threads = seed_threads(Oracle(42), tables=_seed_tables(), count=5)
      assert 3 <= len(threads) <= 5
      traits = [t["trait"] for t in threads]
      archetypes = [t["archetype"] for t in threads]
      assert len(set(traits)) == len(traits)        # no repeated trait (anti-convergence)
      assert len(set(archetypes)) == len(archetypes)  # no repeated archetype
      for t in threads:
          assert t["dormant"] is True
          assert t["id"] and t["speed"] in ("快", "中", "慢")


  def test_seed_threads_deterministic():
      a = seed_threads(Oracle(7), tables=_seed_tables(), count=4)
      b = seed_threads(Oracle(7), tables=_seed_tables(), count=4)
      assert [t["id"] for t in a] == [t["id"] for t in b]
      assert [t["archetype"] for t in a] == [t["archetype"] for t in b]
  ```
- [ ] **Step 2 — run it (expect FAIL).** `python3 -m pytest -q tests/loop/test_director_loop.py` → `ImportError: cannot import name 'seed_threads'`.
- [ ] **Step 3 — minimal implementation.** Add to `loop/director.py`:
  ```python
  _SPEEDS = ("快", "中", "慢")


  def seed_threads(oracle, *, tables: dict, count: int = 4) -> list[dict]:
      """Draw `count` (clamped 3..5) DISTINCT dormant threads — no repeated trait
      OR archetype (the 'slot-machine' anti-convergence). Deterministic given the
      seeded Oracle. Returns thread dicts ready for a thread_open event delta."""
      count = max(3, min(5, count))
      archetypes = tables["thread_archetypes"]
      traits = tables["npc_traits"]
      # cap by available distinct options
      count = min(count, len(archetypes), len(traits))

      chosen: list[dict] = []
      used_arch: set[str] = set()
      used_trait: set[str] = set()
      guard = 0
      while len(chosen) < count and guard < 200:
          guard += 1
          arch = oracle.draw(archetypes)
          trait = oracle.draw(traits)
          if arch["name"] in used_arch or trait["name"] in used_trait:
              continue  # reject duplicates → forces distinct threads
          used_arch.add(arch["name"])
          used_trait.add(trait["name"])
          idx = len(chosen) + 1
          chosen.append({
              "id": f"th_{idx}_{arch.get('type', 'thread')}",
              "status": "活跃",
              "speed": _SPEEDS[oracle.randint(0, len(_SPEEDS) - 1)],
              "dormant": True,
              "trait": trait["name"],
              "archetype": arch["name"],
              "event_type": arch.get("type"),
              "endpoint_hint": arch.get("endpoint_hint"),
              "hook": arch.get("hook"),
              "last_advanced_scene": None,
          })
      log.debug("seed_threads → %d distinct threads", len(chosen))
      return chosen
  ```
  (`Oracle.randint` exists in `engine/oracle.py`; `oracle.draw` is the weighted draw. Both are part of the unchanged legacy module.)
- [ ] **Step 4 — run it (expect PASS).** `python3 -m pytest -q tests/loop/test_director_loop.py` then `tests/test_oracle.py` → green.
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add loop/director.py tests/loop/test_director_loop.py
  git commit -m "feat(loop): seed_threads — 3-5 distinct dormant threads, no repeated trait/archetype (B2)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 9: route `dormant_thread` outcomes → open a new thread or advance a due one

**Files:** Modify `loop/director.py`; Modify `tests/loop/test_director_loop.py` (Test).

- [ ] **Step 1 — write failing test.** Append to `tests/loop/test_director_loop.py`:
  ```python
  from kernel.contextsystem import ContextSystem  # noqa: F401 (clarity)


  def _fire_dormant_stub(monkeypatch):
      import loop.director as dirmod
      def _dormant_fire(scenes_since, tension, oracle, *, tables):
          et = tables["event_types"][0]; tw = tables["twists"][0]
          return {"triggered": True, "type": "dormant_thread", "magnitude": "small",
                  "valence": None, "seed": {"event_type": et, "twist": tw},
                  "prob": 0.6, "roll": 0.1}
      monkeypatch.setattr(dirmod, "director_check", _dormant_fire)


  def test_dormant_fire_opens_thread_when_under_band(monkeypatch):
      _fire_dormant_stub(monkeypatch)
      reg = _reg(); store = _store(reg)
      store.append(_seed_event(5))
      for i in range(1, 7):
          store.append(_action(i, f"s{i}"))
      world = project(reg, store.iter_events())
      appended = run_director(reg, store, world)
      # under the 3-thread floor with no existing threads → opens a thread_open (dormant)
      assert any(e["type"] == "thread_open" for e in appended)
      assert any(e["type"] == "oracle_roll" for e in appended)
      world2 = project(reg, store.iter_events())
      assert len(world2["systems"]["director"]["threads"]) >= 1


  def test_dormant_fire_advances_due_thread_when_band_full(monkeypatch):
      _fire_dormant_stub(monkeypatch)
      reg = _reg(); store = _store(reg)
      store.append(_seed_event(5))
      # pre-open 3 active (non-dormant) threads that are overdue → pick_thread_to_advance fires
      for n, sp in enumerate(("快", "快", "快"), start=1):
          store.append(kernel_event("thread_open", day=1, scene="s1", summary="t",
                       deltas={"id": f"pre{n}", "status": "活跃", "speed": sp,
                               "dormant": False, "trait": f"x{n}", "archetype": f"a{n}",
                               "last_advanced_scene": "s1"}, turn=1))
      for i in range(2, 9):
          store.append(_action(i, f"s{i}"))  # many scenes pass → threads overdue
      world = project(reg, store.iter_events())
      appended = run_director(reg, store, world)
      assert any(e["type"] == "thread_advance" for e in appended)
  ```
- [ ] **Step 2 — run it (expect FAIL).** `python3 -m pytest -q tests/loop/test_director_loop.py` → new tests fail (`run_director` currently always emits `director_fired`, never `thread_open`/`thread_advance`).
- [ ] **Step 3 — minimal implementation.** Modify `run_director` in `loop/director.py`: after computing `out` and confirming `out["triggered"]`, branch on `out["type"]`. Replace the single `directive = kernel_event("director_fired", ...)` block with:
  ```python
      scene = pacing["current_scene"] or "scene"
      day = events[-1]["day"] if events else 1
      next_turn = last_turn + 1
      et = out["seed"]["event_type"]
      tw = out["seed"]["twist"]

      audit = kernel_event(
          "oracle_roll", day=day, scene=scene,
          summary=f"暗骰 roll={out['roll']:.2f} prob={out['prob']:.2f}",
          deltas={"prob": out["prob"], "roll": out["roll"],
                  "scene_ordinal": ordinal, "campaign_seed": campaign_seed},
          turn=next_turn,
      )
      store.append(audit)
      appended = [audit]

      if out["type"] == "dormant_thread":
          ev = _handle_dormant(store, world, events, Oracle(seed_int + 1),
                               scene=scene, day=day, turn=next_turn)
          if ev is not None:
              appended.append(ev)
      else:  # front_stage / crit → a directive the next turn weaves in
          directive = kernel_event(
              "director_fired", day=day, scene=scene,
              summary=f"突发:{et['name']}（{tw['name']}）",
              deltas={
                  "type": out["type"], "magnitude": out["magnitude"],
                  "valence": out["valence"],
                  "event_type": et["name"], "event_hint": et.get("hint"),
                  "twist": tw["name"], "twist_hint": tw.get("hint"),
              },
              turn=next_turn,
          )
          store.append(directive)
          appended.append(directive)
      log.debug("run_director: FIRED type=%s mag=%s appended=%d",
                out["type"], out["magnitude"], len(appended))
      return appended
  ```
  Add the helper (reusing the already-tested `pick_thread_to_advance` + `thread_due_scores`):
  ```python
  from engine.director import compute_pacing, director_check, pick_thread_to_advance

  _MIN_THREADS = 3
  _MAX_THREADS = 5


  def _handle_dormant(store, world, events, oracle, *, scene, day, turn):
      """dormant_thread outcome: advance a due active thread if one is overdue,
      else open a new dormant thread while under the 3-5 band. Returns the
      appended event or None."""
      threads = (world.get("systems", {}).get("director", {}) or {}).get("threads", {})
      # 1) advance a due, non-dormant thread (reuse the tested scheduler)
      due = pick_thread_to_advance(events, threads, oracle)
      if due is not None:
          ev = kernel_event("thread_advance", day=day, scene=scene,
                            summary=f"暗线推进:{due}",
                            deltas={"id": due, "last_advanced_scene": scene},
                            turn=turn)
          store.append(ev)
          return ev
      # 2) else open a new dormant thread if under the band
      if len(threads) < _MAX_THREADS:
          tables = {"thread_archetypes": load_table("thread_archetypes"),
                    "npc_traits": load_table("npc_traits")}
          # draw a single distinct thread (avoid existing archetypes/traits)
          existing_arch = {th.get("archetype") for th in threads.values()}
          existing_trait = {th.get("trait") for th in threads.values()}
          for _ in range(50):
              arch = oracle.draw(tables["thread_archetypes"])
              trait = oracle.draw(tables["npc_traits"])
              if arch["name"] in existing_arch or trait["name"] in existing_trait:
                  continue
              tid = f"th_{len(threads) + 1}_{arch.get('type', 'thread')}"
              ev = kernel_event("thread_open", day=day, scene=scene,
                                summary=f"休眠暗线:{arch['name']}",
                                deltas={"id": tid, "status": "活跃",
                                        "speed": _SPEEDS[oracle.randint(0, 2)],
                                        "dormant": True, "trait": trait["name"],
                                        "archetype": arch["name"],
                                        "event_type": arch.get("type"),
                                        "last_advanced_scene": scene},
                                turn=turn)
              store.append(ev)
              return ev
      return None
  ```
  (`_MIN_THREADS` is documented for clarity; the band floor is enforced by always preferring to *open* when no due thread exists and under `_MAX_THREADS`.)
- [ ] **Step 4 — run it (expect PASS).** `python3 -m pytest -q tests/loop/test_director_loop.py` then `tests/test_director.py tests/test_oracle.py` → green. Then the full suite `python3 -m pytest -q --ignore=tests/test_embed_real.py`.
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add loop/director.py tests/loop/test_director_loop.py
  git commit -m "feat(loop): route dormant_thread fires → open/advance distinct threads (B2)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 10: surface a due dormant thread in the directive Fragment

**Files:** Modify `systems/director.py`; Modify `tests/systems/test_director_system.py` (Test).

> When a dormant thread is advanced (B2), the narrator should get a nudge to surface it. The cleanest reuse: have `thread_advance` also enqueue a lightweight pending directive so `inject` shows it next turn — but to keep the gate simple we instead let `inject` append a one-line "暗线浮现" note when the most recent appended directive is a thread surfacing. Recommendation: extend `apply` so `thread_advance` with `dormant=True→surfacing` enqueues a thread-flavored pending directive; `inject` already renders pending directives. This keeps a single injection path.

- [ ] **Step 1 — write failing test.** Append to `tests/systems/test_director_system.py`:
  ```python
  def test_thread_surface_directive_is_injected():
      reg = _reg()
      world = project(reg, [
          kernel_event("thread_open", day=1, scene="s1", summary="暗线",
                       deltas={"id": "th_x", "status": "活跃", "speed": "中",
                               "dormant": True, "trait": "深不可测", "archetype": "复仇宿敌",
                               "event_type": "阴谋线", "last_advanced_scene": "s1"}, turn=1),
          kernel_event("thread_advance", day=2, scene="s4", summary="暗线浮现:th_x",
                       deltas={"id": "th_x", "last_advanced_scene": "s4",
                               "surface": True}, turn=5),
      ])
      ds = DirectorSystem()
      frag = ds.inject({"protagonist": "hero", "day": 2}, world)
      assert frag is not None
      assert "暗线" in frag.text and "复仇宿敌" in frag.text
  ```
- [ ] **Step 2 — run it (expect FAIL).** `python3 -m pytest -q tests/systems/test_director_system.py` → fails (no pending directive enqueued by `thread_advance`).
- [ ] **Step 3 — minimal implementation.** In `systems/director.py`, in the `thread_advance` branch of `apply`, after updating the thread, when `d.get("surface")` is truthy enqueue a pending directive:
  ```python
              if d.get("surface") and tid in threads:
                  th = threads[tid]
                  world["systems"][self.name]["pending"].append({
                      "type": "dormant_thread",
                      "magnitude": "small",
                      "valence": None,
                      "event_type": th.get("archetype"),
                      "event_hint": f"暗线浮现（{th.get('event_type') or ''}）",
                      "twist": th.get("trait") or "",
                      "twist_hint": "让这条暗线以一个具体细节浮出水面",
                      "turn": event.get("turn") or 0,
                      "scene": event.get("scene"),
                      "consumed": False,
                  })
                  log.debug("thread_advance surface → enqueued thread directive for %s", tid)
  ```
  (`inject` already renders the newest un-consumed pending directive — Task 3 — so no `inject` change is needed; the test asserts the archetype/trait reach the Fragment text.)
- [ ] **Step 4 — run it (expect PASS).** `python3 -m pytest -q tests/systems/test_director_system.py` then the full suite `python3 -m pytest -q --ignore=tests/test_embed_real.py` → all green.
- [ ] **Step 5 — commit.**
  ```bash
  cd /root/rpg-engine-app
  git add systems/director.py tests/systems/test_director_system.py
  git commit -m "feat(systems): surfacing dormant thread enqueues a 导演 directive (B2)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

**B2 done criteria:** full suite green; `tests/test_director.py` + `tests/test_oracle.py` unchanged & green; the director maintains a 3–5 distinct dormant-thread store (no repeated trait/archetype); `dormant_thread` rolls open a new distinct thread or advance a due one via the existing scheduler; surfacing threads reach the narrator through the same single injection path.

---

## Self-Review

**Roadmap Phase B bullet → task coverage (every bullet maps to ≥1 task):**

| Roadmap Phase B bullet | Covered by |
| --- | --- |
| 每场景隐藏 d100 + 频带(30→60%)+ cooldown；张力闸高潮阈值 | Reused unchanged from `engine/director.py` (`pacing_probability`, `director_check`, `TENSION_GATE`, `crit_threshold`); invoked by Task 5 `run_director`. Legacy tests stay green. |
| 两轴：dormant_thread / front_stage / crit | `director_check` already returns these; B1 (Task 5) handles front_stage/crit → directive; B2 (Task 9) routes dormant_thread → thread open/advance. |
| 开坑 slot-machine 反趋同(3–5 DISTINCT 暗线、不重复 trait) | Task 8 `seed_threads` (distinct trait AND archetype, deterministic) + Task 9's single-thread distinct draw. |
| 产出 `director_fired`/`oracle_roll` 事件 → 走严格门 → 注入下一回合上下文 | Task 2 (`DirectorSystem` owns + applies the events), Task 4 (registered in `build_engine` so the strict store accepts them), Task 5 (appended via the strict store), Task 3 + Task 6 (injected into the NEXT turn via `inject` → `assemble_context`). |
| importance.py 已为 `oracle_roll`(2)/`director_fired`(3) 留权重 | No code change needed — the events use those exact type strings, so `memory/importance.py` scores them automatically; Task 5 emits them with deltas (the `_DELTA_BONUS` applies). Confirmed read-only. |
| backstop：频带 + cooldown + 张力阈值(防过密) | Confirmed reused (design Q6); Task 5 adds the extra "never two turns in a row" guard with a test. |
| 落点：新 `loop/director.py` + 一个 DirectorSystem 或 play 循环钩子 | Exactly this: `loop/director.py` (Tasks 5,8,9) + `DirectorSystem` (Tasks 1,2,3,7,10) + hook in `run_turn` (Task 6). |

**No placeholders:** every Step-3 has real, runnable code; every Step-1 has real test code. The one intentional artifact is the bogus `from loop.director import _find_seed_helper` line inside Task 6's test, explicitly flagged in-task to be deleted by the implementer (it's a reminder that the test uses monkeypatch, not a seed-scan).

**Type / name consistency across tasks (single source of truth):**
- `DirectorSystem.name == "director"`; slice at `world["systems"]["director"]`; keys `pending` (list), `consumed_through_turn` (int), `threads` (dict, added in B2). Used identically in Tasks 1,2,3,7,9,10.
- Event types: `campaign_seeded`, `oracle_roll`, `director_fired` (B1) + `thread_open`, `thread_advance` (B2) — declared in `event_types()` (Tasks 1,7), emitted with matching deltas in `run_director` (Tasks 5,9), and `oracle_roll`/`director_fired`/`thread_open` already exist in `engine/schema.py`'s legacy `EVENT_TYPES` (so legacy `make_event` callers stay valid). `campaign_seeded`/`thread_advance` are new but only ever built via `kernel_event` (which has no closed-set check) + accepted by the registry-backed strict store.
- Directive dict shape (`type`, `magnitude`, `valence`, `event_type`, `event_hint`, `twist`, `twist_hint`, `turn`, `scene`, `consumed`) is identical between `DirectorSystem.apply` (Task 2), `inject` (Task 3), and the `director_fired` deltas built in `run_director` (Tasks 5,9) and the thread-surface enqueue (Task 10).
- `run_director(registry, store, world, *, scene_ordinal=None) -> list[dict]` signature is stable across Tasks 5,6,9; the `run_turn` call site (Task 6) passes `(registry, store, new_world)`.
- `campaign_seed` lives in `world["meta"]["campaign_seed"]` (written by `DirectorSystem.apply` on `campaign_seeded`, Task 2; seeded at genesis, Task 4; read in `run_director`, Task 5) and on the `Engine` dataclass field `campaign_seed` (Task 4) — derived identically by `_derive_campaign_seed`.
- Reused legacy API (unchanged): `Oracle(seed)`, `Oracle.d100/chance/draw/random/randint`, `load_table(name)`, `scene_seed(campaign_seed, scene_ordinal, salt=0)`, `compute_pacing(events)`, `director_check(scenes_since_event, tension, oracle, *, tables)`, `pick_thread_to_advance(events, threads, oracle, *, threshold=1.0)`, `thread_due_scores(...)`. All verified present in `engine/oracle.py` / `engine/director.py`.

**Determinism / offline guarantee:** all randomness flows through a seeded `Oracle` built from `scene_seed(campaign_seed, scene_ordinal)`; tests scan seeds offline to obtain deterministic fire/quiet fixtures, or monkeypatch `loop.director.director_check` to force an outcome. No test constructs a real provider or makes a network call (`FakeLLMProvider` only). Full-suite gate command: `cd /root/rpg-engine-app && python3 -m pytest -q --ignore=tests/test_embed_real.py`.

**Guardrails restated:** no edits to `engine/oracle.py`, `engine/director.py`, `data/oracles/**`, `kernel/**`, `context/assembler.py`. `tests/test_director.py` + `tests/test_oracle.py` remain untouched and green. No `git init` / `.git` deletion / `_legacy` or `docs` deletion / branch switch. Commit only the files each task names.
