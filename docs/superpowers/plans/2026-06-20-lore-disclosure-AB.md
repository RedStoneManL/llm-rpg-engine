# Lore Disclosure A/B Comparison — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps. TDD.

**Goal:** Build BOTH progressive-disclosure mechanisms for lore lines so we can A/B-compare them on complex cases with a real LLM:
- **A (PULL / tools):** narrator sees a compact L0 index; calls a `fetch_storyline(id, depth)` tool on demand to expand a line (graded). Uses a provider tool-loop.
- **B (PUSH / station):** engine auto-pushes the current-L3 lines' L1 beat + the area's L0 index into context, no tool call.

**Architecture:** Shared disclosure data on `LoreSystem` (description/trigger/l3_anchor + a pure `fetch_lore(line, depth)` grader). B = an inject variant. A = the P3a tool-loop (borrowed) + a `fetch_storyline` tool + an L0-index inject + 甲 wiring. A disclosure-mode switch (`off|A|B`) threads through `run_turn`/`play`. A comparison harness builds a complex world and runs the same turns under A and B on glm-5.1.

**Tech Stack:** Python 3.12, stdlib only, pytest offline (`FakeLLMProvider`/`ScriptedToolProvider`). Run: `cd /root/rpg-engine-app && PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q`.

**Specs:** `docs/superpowers/specs/2026-06-20-lore-event-line-design.md` (lore design + disclosure) + `docs/superpowers/plans/2026-06-19-P3-narrator-tools.md` (the tool-loop — A reuses its Tasks 1/2/6).

## Global Constraints
- Python 3.12; branch `app`; stdlib only (HTTP via `urllib` as `llm/provider.py` does). `python3` not `python`.
- Offline-deterministic tests; no network. A's loop tested via `ScriptedToolProvider` (P3a's keystone).
- Baseline before this plan: **849 passed, 1 deselected**. Additive; don't break existing tests.
- Levels (design spec `2026-06-17-...-design.md:105`): **L1 国家/region, L2 城镇/town, L3 细节/venue (where the player acts).** Lore SEEDED per L2 town; dormancy-anchored at L2 (simple/medium) or L1 (complex); **trigger-bound at L3 venue**.
- Disclosure grades: **L0** = `description` + `trigger` (compact index, always); **L1** = current-stage beat + latest clue; **L2** = clue history + secret-edge hint + related ids.
- A's loop: reuse `_run_tool_loop` / `complete_with_tools` / `ScriptedToolProvider` per P3a plan Tasks 1, 2, 6 — do NOT reinvent; build those first if absent.
- HARD git guardrails: stay on `app`; NEVER git init/reset/rebase/checkout/branch-switch; never delete `_legacy/` or `docs/`. Commit only files each task names. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- `fetch_storyline` is READ-ONLY (like all P3 tools); all writes still go through the turn-commit gate (incl. lore advancement via the `world`/`storylines` sections + `run_lore` 暗骰).

## Phasing
- **P-prep (Task 1):** shared disclosure data + `fetch_lore` grader. (A & B both need it; independent of A-native.)
- **P-B (Task 2):** station-push inject (B). Small, no tool-loop.
- **P-A (Tasks 3–6):** P3a tool-loop (borrow) → `fetch_storyline` tool → L0-index inject → 甲 wiring behind a mode switch.
- **P-compare (Task 7):** disclosure-mode switch threaded through run_turn/play + a complex-case harness; run on glm-5.1, dump comparison.

---

### Task 1: Shared disclosure data + `fetch_lore` grader

**Files:** Modify `systems/lore.py`, `loop/lore.py`; Create `tests/systems/test_lore_disclosure.py`.

**Interfaces:**
- Skeleton gains `description: str` (L0), `trigger: str` (L0, LLM-judged condition), `l3_anchor: str` (the L3 venue the trigger binds to). `LoreSystem.apply` stores them; `create_lore_line` `_REQUIRED` adds `description`, `trigger`, `l3_anchor`.
- Produces `loop.lore.fetch_lore(line: dict, depth: int) -> dict`: depth 0 → `{id, description, trigger}`; depth 1 → + `{about, stage_idx, beat, latest_clue}`; depth 2 → + `{clues, secret_edge, anchor}`. Pure, no I/O.

- [ ] **Step 1: Write failing tests** — `tests/systems/test_lore_disclosure.py`:

```python
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.lore import LoreSystem
from loop.lore import fetch_lore


def _reg():
    r = Registry(); r.register(OntologySystem()); r.register(LoreSystem()); return r


_SK = {"id": "caravan", "complexity": "medium", "about": "商队失踪",
       "secret": "首领卷款潜逃", "anchor": "qingshi_town", "l3_anchor": "qingshi_market",
       "description": "集市上关于失踪商队的窃窃私语",
       "trigger": "玩家在集市打听商队/货物/失踪的人",
       "stages": [{"hint": "有人在打听商队下落"}, {"hint": "城门记录显示商队从没出城"}],
       "threshold": 60}


def test_skeleton_stores_disclosure_fields():
    w = project(_reg(), [kernel_event("lore_created", day=1, scene="s", summary="x",
                                      deltas=_SK, turn=1)])
    ln = w["systems"]["lore"]["lines"]["caravan"]
    assert ln["description"] == "集市上关于失踪商队的窃窃私语"
    assert ln["trigger"].startswith("玩家在集市")
    assert ln["l3_anchor"] == "qingshi_market"


def test_fetch_lore_depth0_index():
    w = project(_reg(), [kernel_event("lore_created", day=1, scene="s", summary="x",
                                      deltas=_SK, turn=1),
                         kernel_event("lore_advanced", day=1, scene="s", summary="a",
                                      deltas={"id": "caravan", "stage_idx": 0,
                                              "hint": "有人在打听商队下落"}, turn=2)])
    ln = w["systems"]["lore"]["lines"]["caravan"]
    d0 = fetch_lore(ln, 0)
    assert set(d0) == {"id", "description", "trigger"}
    assert d0["id"] == "caravan"


def test_fetch_lore_depth1_current_beat():
    w = project(_reg(), [kernel_event("lore_created", day=1, scene="s", summary="x",
                                      deltas=_SK, turn=1),
                         kernel_event("lore_advanced", day=1, scene="s", summary="a",
                                      deltas={"id": "caravan", "stage_idx": 0,
                                              "hint": "有人在打听商队下落"}, turn=2)])
    ln = w["systems"]["lore"]["lines"]["caravan"]
    d1 = fetch_lore(ln, 1)
    assert d1["stage_idx"] == 0
    assert d1["latest_clue"] == "有人在打听商队下落"
    assert d1["about"] == "商队失踪"


def test_fetch_lore_depth2_history_and_secret_edge():
    w = project(_reg(), [kernel_event("lore_created", day=1, scene="s", summary="x",
                                      deltas=_SK, turn=1),
                         kernel_event("lore_advanced", day=1, scene="s", summary="a",
                                      deltas={"id": "caravan", "stage_idx": 0,
                                              "hint": "有人在打听商队下落"}, turn=2),
                         kernel_event("lore_advanced", day=2, scene="s", summary="a",
                                      deltas={"id": "caravan", "stage_idx": 1,
                                              "hint": "城门记录显示商队从没出城"}, turn=3)])
    ln = w["systems"]["lore"]["lines"]["caravan"]
    d2 = fetch_lore(ln, 2)
    assert d2["clues"] == ["有人在打听商队下落", "城门记录显示商队从没出城"]
    assert "secret_edge" in d2  # a hint toward the secret, not the secret verbatim


def test_create_lore_line_requires_disclosure_fields():
    import pytest, tempfile, os
    from kernel.events import open_store
    from loop.lore import create_lore_line
    r = _reg()
    d = tempfile.mkdtemp()
    store = open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                       allowed_types=r.event_types())
    with pytest.raises(ValueError):
        create_lore_line(store, {"id": "x", "complexity": "simple", "about": "a",
                                 "stages": [], "threshold": 50, "anchor": "t"},
                         day=1, scene="s", turn=1)  # missing description/trigger/l3_anchor
```

- [ ] **Step 2: Run → FAIL** (`cannot import name 'fetch_lore'`; fields absent).

- [ ] **Step 3: Implement.**
  - In `systems/lore.py` `apply` for `lore_created`, add to the stored line dict: `"description": d.get("description"), "trigger": d.get("trigger"), "l3_anchor": d.get("l3_anchor")`.
  - In `loop/lore.py` add `fetch_lore`:
    ```python
    def fetch_lore(line: dict, depth: int) -> dict:
        """Graded disclosure of one lore line. depth 0=index, 1=current beat, 2=history+secret-edge."""
        out = {"id": line.get("id") if "id" in line else None,
               "description": line.get("description"),
               "trigger": line.get("trigger")}
        # line dicts in the slice are keyed by id elsewhere; carry id if present
        if out["id"] is None:
            out.pop("id")
        if depth >= 1:
            stages = line.get("stages", [])
            idx = line.get("stage_idx", -1)
            beat = (stages[idx].get("hint") if 0 <= idx < len(stages)
                    and isinstance(stages[idx], dict) else None)
            clues = line.get("clues_dropped", [])
            out.update({"about": line.get("about"), "stage_idx": idx,
                        "beat": beat, "latest_clue": clues[-1] if clues else None})
        if depth >= 2:
            secret = line.get("secret") or ""
            out.update({"clues": list(line.get("clues_dropped", [])),
                        "anchor": line.get("anchor"),
                        # secret_edge = a deniable nudge toward (not a reveal of) the secret
                        "secret_edge": ("有迹象指向更深的隐情" if secret else None)})
        return out
    ```
    NOTE: the slice stores lines keyed by id WITHOUT an `id` field inside; the disclosure callers pass `{**line, "id": lid}`. So `fetch_lore` reads `line.get("id")` and the caller injects it. Add `"id": lid` when calling. (Tests pass the line dict from the slice; to satisfy `test_fetch_lore_depth0_index` asserting `id`, have callers/tests pass `{**ln, "id": "caravan"}` — update the test fixtures to do so, OR store `id` in the line on create. Simplest: store `"id": lid` in the line dict on `lore_created` apply. Do that — add `"id": lid` to the created line dict.)
  - Add `description`, `trigger`, `l3_anchor` to `_REQUIRED` in `loop/lore.py` `create_lore_line`.
  - Update existing lore test skeletons (`tests/systems/test_lore_system.py` `_SKELETON`, `tests/loop/test_lore_loop.py` `_SK`) to include `description`/`trigger`/`l3_anchor` so they still pass the now-stricter `_REQUIRED`. Name them in your report.

- [ ] **Step 4: Run → PASS** (new file + the updated existing lore tests).
- [ ] **Step 5: Full suite** → 849 + new, green. Fix any lore-skeleton fixtures that the stricter `_REQUIRED` breaks (legitimate — add the 3 fields).
- [ ] **Step 6: Commit** `systems/lore.py loop/lore.py tests/systems/test_lore_disclosure.py` + the updated existing lore test files (named).
```
feat(lore): disclosure data (description/trigger/l3_anchor) + fetch_lore grader

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

### Task 2: B — station-push inject

**Files:** Create `loop/lore_disclosure.py`; Test `tests/loop/test_lore_disclosure_B.py`.

**Interfaces:** `loop.lore_disclosure.station_push_fragment(registry, world, scene) -> str|None` — returns the B-mode context text: for the protagonist's current L3 venue, the L1 `fetch_lore(line,1)` of each active line whose `l3_anchor == current L3`; plus an L0 index (`fetch_lore(line,0)`) of OTHER active lines in the same town (L2 ancestor). Returns None if no active lines in range.

- [ ] **Step 1: Failing tests** — build a world: town `qingshi_town` (L2) containing venues `market`/`tavern` (L3); protagonist at `market`; two lines anchored l3=`market`, one at l3=`tavern`. Assert `station_push_fragment` includes the market lines' L1 beats and the tavern line only as an L0 index line (description), and excludes lines from other towns.

(Full fixture + assertions: mirror `test_lore_disclosure.py` setup + `place_created`/`entity_moved` for the venues; assert beat text for market lines present, tavern line's beat ABSENT but its description present.)

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `station_push_fragment`: resolve protagonist current L3 (graph `located_in`); find its L2 ancestor (walk `contained_by` to a level-2 place); for each active line: if `l3_anchor == current L3` → render `fetch_lore(line,1)` beat; elif its town == current town → render `fetch_lore(line,0)` index; format into a compact text block. (Reuse the location helpers; lines come from `world["systems"]["lore"]["lines"]`.)
- [ ] **Step 4–6: PASS, full suite, commit.**

---

### Task 3: Borrow P3a tool-loop (Tasks 1, 2, 6)

Build **P3a plan Tasks 1, 2, 6** verbatim from `docs/superpowers/plans/2026-06-19-P3-narrator-tools.md`: `ScriptedToolProvider` + `supports_tools()` (T1), `_run_tool_loop` (T2), `ZhipuProvider.complete_with_tools` + `_openai_chat_body(tools=)` + `_openai_parse`/`_openai_append_result` (T6). Skip P3a Tasks 3–5 (the fog tool suite) — A uses only `fetch_storyline` (Task 4 here). Commit per P3a's commit messages. Full suite green after each.

---

### Task 4: `fetch_storyline` tool + L0-index inject (A)

**Files:** Create `llm/lore_tools.py`; Modify `loop/lore_disclosure.py`; Test `tests/llm/test_lore_tools.py`.

**Interfaces:**
- `llm.lore_tools.build_lore_tool(registry, world, scene)` → a `Tool` (from `llm/tools.py` if P3a Task 3 built it, ELSE a minimal local `Tool` dataclass) named `fetch_storyline`, params `{id: str, depth: int}`, fn returns `fetch_lore({**line, "id": id}, depth)` for the line, or `{"error": "..."}` if unknown. READ-ONLY.
- `loop.lore_disclosure.index_fragment(registry, world, scene) -> str|None` — the A-mode L0 index: `fetch_lore(line,0)` for active lines in the current town/region, as a compact list (this is what's always in context for A; the LLM calls `fetch_storyline` to expand).

- [ ] Tests: `fetch_storyline` returns graded content by depth; unknown id → error JSON; never raises. `index_fragment` lists L0 of in-range lines. (Offline.)
- [ ] Implement + PASS + full suite + commit.

---

### Task 5: Wire A into AuthorStrategy behind a mode switch

**Files:** Modify `loop/strategy.py`; Test `tests/loop/test_lore_disclosure_A.py`.

- `AuthorStrategy.produce` gains awareness of a disclosure mode (passed via `scene["disclosure"]` or a strategy attr). When mode==A AND `provider.supports_tools()`: assemble context with the A index_fragment, build the lore tool registry, call `complete_with_tools([...], tools=[fetch_storyline], tool_executor=...)`. When mode==B: include `station_push_fragment` in context, normal `complete_messages`. When off: today's behavior.
- [ ] Test with `ScriptedToolProvider`: a script that calls `fetch_storyline("caravan", 1)` then emits a commit → assert the tool ran + the commit applied. (Offline, deterministic.)
- [ ] Implement + PASS + full suite + commit.

---

### Task 6: disclosure-mode switch through run_turn / play

**Files:** Modify `loop/turn.py`, `app/play.py`; Test append.
- Thread a `disclosure_mode: str = "off"` param from `play_loop` → `run_turn` → strategy. Default "off" (existing behavior; existing tests untouched). 
- [ ] Test that mode flows + B/A paths are selected. Full suite green. Commit.

---

### Task 7: Complex-case comparison harness + glm-5.1 run

**Files:** Create `docs/superpowers/specs/lore-AB-2026-06-20/compare.py` (a script, like the clock smoke) + a short README.
- The script: build a complex world — `qingshi_town` (L2) with venues market/tavern/shrine/docks (L3); seed ~10 lore lines across them (mixed complexity, mixed stage_idx, with description/trigger/l3_anchor); a protagonist. Define a fixed sequence of ~6 player actions that stress the difference (digging into a specific line; ambient wandering; an action matching one line's trigger while 3 others share the venue; revisiting).
- Run the SAME sequence under disclosure_mode="A" and ="B" on glm-5.1 (`make_provider("zhipu", GLM_MODEL)`); for each turn dump: what was in context (A's index vs B's pushed beats), which lines A's LLM chose to `fetch_storyline` (from `tool_invocations`), and the narration. Print an A-vs-B side-by-side summary (context tokens-ish, # lines surfaced, whether the narrator wove the right line, any leak of off-venue lines).
- [ ] Run it; archive the script + transcript + a findings note in `docs/superpowers/specs/lore-AB-2026-06-20/`. This is the deliverable the human reviews to pick A vs B.

---

## Out of Scope
- Full P3 fog tool suite (map/characters/factions POV+DM) — A uses only `fetch_storyline`.
- Density-generation (L3 of the lore roadmap) — the harness hand-authors the complex world.
- Productionizing the winner — after the human picks A or B.

## Self-Review
- Levels corrected (L3 = venue/finest). Disclosure grades L0/L1/L2 consistent across `fetch_lore` (Task 1), B push (Task 2), A tool+index (Task 4). `fetch_storyline` read-only; writes via gate. A's loop reuses P3a (no reinvention). Mode switch defaults off → existing 849 tests untouched.
