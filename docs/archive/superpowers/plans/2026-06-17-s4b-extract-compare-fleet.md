# S4b: ExtractStrategy(乙) + compare mode + backstage fleet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. `- [ ]` steps, TDD.

**Goal:** Complete the loop: the second TurnStrategy (乙, prose-then-史官-extract), a compare mode (run 甲+乙 on the same pre-turn snapshot, caller picks the canonical), and the backstage fleet (importance scoring + reflection write-back, post-commit). All in `loop/`, offline with `FakeLLMProvider`.

**Architecture:** `ExtractStrategy(乙)` = two provider calls: (1) narrate prose-only over `assemble_context`; (2) a 史官 extraction call `complete_json` that turns the prose into a `TurnCommit`. To support compare without double-committing, split `run_turn` into `produce_turn` (produce→validate→repair→final TurnCommit, **no store write**) + `apply_turn` (to_events→append→project). `run_compare` calls `produce_turn` for both strategies against the SAME pre-turn world (no apply) and returns both candidates; the caller `apply_turn`s the chosen one. Backstage fleet runs after `apply_turn`: score new events' importance (provider), accumulate per subject, and when `should_reflect` fires, `reflect` (provider) → append a `character_evolved` arc event.

**Tech Stack:** Python 3.12 stdlib; reuses `loop/` (S4a), `kernel.digest`, `memory.importance`/`memory.reflection`, `llm.provider`. Offline tests `python3 -m pytest -q --ignore=tests/test_embed_real.py` with `FakeLLMProvider`/`FakeEmbedder`.

**Conventions/Guardrails:** TDD per task; `get_logger("loop.*")`; commit per task; `python3`. Never `git init`/`rm -rf .git`/`checkout --orphan`; never delete/modify `_legacy/` or `docs/`; edit only `loop/` + its tests (import others read-only). New code stays in `loop/`.

---

## File Structure
- `loop/turn.py` (modify) — split into `produce_turn(...) -> (TurnCommit, attempts, dropped)` + `apply_turn(registry, store, commit, *, day, scene) -> world`; keep `run_turn` as `produce_turn`+`apply_turn` for back-compat (existing S4a tests must stay green).
- `loop/strategy.py` (modify) — add `ExtractStrategy(乙)`.
- `loop/compare.py` (new) — `run_compare`.
- `loop/fleet.py` (new) — backstage `digest_fleet(registry, store, new_events, world, *, provider) -> list[arc_events_appended]`.
- Tests: extend `tests/loop/test_strategy.py`; new `tests/loop/test_compare.py`, `tests/loop/test_fleet.py`.

---

## Task 1: Split `run_turn` into `produce_turn` + `apply_turn` (refactor, keep S4a green)
- [ ] Step1: write tests asserting `produce_turn(registry, world, scene, input, *, strategy, provider, embedder, max_repairs=3)` returns `(commit, attempts, dropped)` WITHOUT writing the store (store empty after), and `apply_turn(registry, store, commit, *, day, scene)` appends events + returns the new world; and that existing `run_turn` still works (= produce then apply). Step2 fail. Step3 refactor `loop/turn.py` accordingly (run_turn delegates). Step4 pass + full suite green (S4a tests unchanged). Step5 commit `refactor(loop): split run_turn into produce_turn + apply_turn`.

## Task 2: `ExtractStrategy(乙)`
- [ ] Step1: `tests/loop/test_strategy.py` — a `FakeLLMProvider` scripted so `complete` returns prose and `complete_json` returns the extracted commit; `ExtractStrategy().produce(...)` returns a `TurnCommit` whose narration == the prose and sections == the extracted ones; `repair=` feedback reaches the 史官 extract call. Step2 fail. Step3 implement `ExtractStrategy` in `loop/strategy.py` (call 1: `provider.complete(system_narrate, user)` → prose; call 2: `provider.complete_json(system_史官_extract, prose, TURNCOMMIT_SCHEMA)` → TurnCommit.from_dict, narration forced to the prose). Step4 pass. Step5 commit `feat(loop): ExtractStrategy (乙) — prose then 史官 extraction`.

## Task 3: compare mode
- [ ] Step1: `tests/loop/test_compare.py` — `run_compare(registry, world, scene, input, *, provider, embedder=None)` returns `{"甲": (commit_a, ...), "乙": (commit_b, ...)}` both produced against the SAME pre-turn world (neither applied — store unchanged); then applying the chosen via `apply_turn` commits only that one. Use two FakeLLMProviders (or one scripted) so 甲/乙 yield different commits. Step2 fail. Step3 implement `loop/compare.py` `run_compare` (produce_turn for AuthorStrategy + ExtractStrategy on the same world; return both candidates). Step4 pass + full suite green. Step5 commit `feat(loop): compare mode — run 甲+乙 on one snapshot, caller picks`.

## Task 4: backstage fleet (importance + reflection)
- [ ] Step1: `tests/loop/test_fleet.py` — `digest_fleet(registry, store, new_events, world, *, provider, threshold=30)`: scores each new event via `memory.importance.score(ev, provider=provider)`, accumulates per primary subject (event actors/deltas subject), and for any subject crossing `threshold` calls `memory.reflection.reflect(subject, recent, provider)` → appends a `character_evolved` event carrying the arc fact-delta (predicate "arc"). Test with a FakeLLMProvider returning high importance + an arc synthesis → assert an arc `character_evolved` event was appended and the subject's `value_at(subject,"arc",day)` is set after re-project. Step2 fail. Step3 implement `loop/fleet.py`. Step4 pass + full suite green. Step5 commit `feat(loop): backstage fleet — importance scoring + reflection write-back`.

---

## Done criteria for S4b
- Full suite green. Both strategies work; compare produces two candidates from one snapshot without double-committing; backstage fleet scores importance + writes reflection arcs. Offline (FakeLLMProvider); no kernel/systems edits.

**Next:** S5 — CLI v1 REPL: `make_provider` from env/config, seed/new-game, turn loop (pick strategy or compare), render player-facing narration, OOC commands, persistence via the event store. Also (polish): cross-section validation fix (logged in memory).
