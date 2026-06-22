# World Clock + Time Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make in-game time a reliable, first-class clock `(day, band)` that the narrator advances every turn (with a mandatory reason), with all time arithmetic in a pure engine module.

**Architecture:** A pure `kernel/clock.py` collapses the clock to one integer scale (band-units) for all math. The existing `TimeSystem` gains ownership of a new `clock` commit section + `clock_advanced` event. `run_turn` computes the post-advance day from `world.meta` + the turn's `clock` declaration and stamps the turn at that day; `meta.band` is folded by the clock event's `apply`. Adding `clock` to `REQUIRED_SECTIONS` is the forcing function that kills the "frozen time" bug.

**Tech Stack:** Python 3.12, pytest (offline/deterministic — `FakeLLMProvider`, no network). Run tests with `cd /root/rpg-engine-app && PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q`.

**Spec:** `docs/superpowers/specs/2026-06-20-world-clock-design.md`

## Global Constraints

- Python 3.12; every module uses `from engine.log import get_logger`.
- Tests are offline only — no network. Use `FakeLLMProvider(json_responses=[...])`. Langfuse stays no-op.
- Run command: `cd /root/rpg-engine-app && PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q` (note: `python3`, not `python`).
- Baseline before this plan: **793 passed, 1 deselected**. No task may reduce the passing count except by the planned edits to `tests/systems/test_time_system.py` (Task 2) which UPDATE assertions, not delete coverage.
- **HARD git guardrails:** stay on branch `app`; never `git init` / `git reset --hard` / `git rebase` / `git checkout --orphan` / switch branches; never delete `_legacy/` or `docs/`. Commit only the files each task names. End every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- **NEVER** print or commit the contents of `.env.local` (it holds a secret API key).
- Band index → name mapping is fixed: `0=晨, 1=中午, 2=下午, 3=夜晚` (4 bands per day).
- Clock declaration is a **delta**, never absolute: `{"advance": bool, "days": int>=0, "bands": int>=0, "reason": str}`. The narrator never reports the absolute day/band.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `kernel/clock.py` (new) | Pure time arithmetic: band-units, advance, elapsed, compare, expired, band names. No I/O. | 1 |
| `tests/kernel/test_clock.py` (new) | Exhaustive unit tests for `kernel/clock.py`. | 1 |
| `systems/time.py` (modify) | `TimeSystem` also owns the `clock` section + `clock_advanced` event; validates/emits/applies it; injects the current clock. | 2 |
| `tests/systems/test_time_system.py` (modify) | Update the `commit_sections()`/event-type assertion; add clock validate/to_events/apply/inject tests. | 2 |
| `loop/turn.py` (modify) | Compute post-advance day from `world.meta` + `clock` decl; stamp turn at it. Add `clock` to `REQUIRED_SECTIONS`. | 3 |
| `tests/loop/test_clock_loop.py` (new) | Integration: a clock-advancing turn moves `meta.day`/`meta.band`; no-advance keeps them. | 3 |
| `loop/strategy.py` (modify) | Document the `clock` section in `_SYSTEM_PROMPT` (甲) and `_SYSTEM_PROMPT_HYBRID` (丙). | 4 |
| `tests/loop/test_clock_required.py` (new) | Forcing function: a commit lacking `clock` is bounced by the gate and repaired; prompts mention `clock`. | 4 |

---

### Task 1: `kernel/clock.py` — pure time engine

**Files:**
- Create: `kernel/clock.py`
- Test: `tests/kernel/test_clock.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces:
  - `BANDS: tuple[str, str, str, str]` = `("晨", "中午", "下午", "夜晚")`
  - `to_units(day: int, band: int) -> int`
  - `from_units(units: int) -> tuple[int, int]`  # (day, band)
  - `advance(day: int, band: int, ddays: int, dbands: int) -> tuple[int, int]`  # (new_day, new_band)
  - `elapsed(from_units: int, to_units: int) -> int`  # band-unit delta
  - `compare(a_units: int, b_units: int) -> int`  # -1 / 0 / 1
  - `expired(born_units: int, lifespan_units: int, now_units: int) -> bool`
  - `band_name(band: int) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/kernel/test_clock.py`:

```python
from kernel import clock


def test_to_units_and_from_units_roundtrip():
    assert clock.to_units(1, 0) == 4
    assert clock.to_units(3, 2) == 14
    assert clock.from_units(4) == (1, 0)
    assert clock.from_units(14) == (3, 2)


def test_advance_within_day():
    # 晨(0) + 2 bands -> 下午(2), same day
    assert clock.advance(1, 0, 0, 2) == (1, 2)


def test_advance_band_carries_into_next_day():
    # 夜晚(3) + 1 band -> next day 晨(0)
    assert clock.advance(1, 3, 0, 1) == (2, 0)


def test_advance_full_days_keeps_band():
    assert clock.advance(2, 1, 3, 0) == (5, 1)


def test_advance_overflow_bands_carry_days():
    # 晨(0) + 6 bands -> +1 day, 下午(2)
    assert clock.advance(1, 0, 0, 6) == (2, 2)


def test_advance_zero_is_identity():
    assert clock.advance(4, 2, 0, 0) == (4, 2)


def test_elapsed_is_unit_difference():
    a = clock.to_units(1, 0)
    b = clock.to_units(3, 2)
    assert clock.elapsed(a, b) == 10


def test_compare_orders_clocks():
    a = clock.to_units(1, 3)
    b = clock.to_units(2, 0)
    assert clock.compare(a, b) == -1
    assert clock.compare(b, a) == 1
    assert clock.compare(a, a) == 0


def test_expired_boundary():
    born = clock.to_units(1, 0)        # 4
    now_at = clock.to_units(2, 0)      # 8  -> elapsed 4
    assert clock.expired(born, 4, now_at) is True       # exactly at lifespan
    assert clock.expired(born, 5, now_at) is False      # one unit short
    assert clock.expired(born, 3, now_at) is True       # past lifespan


def test_band_name():
    assert clock.band_name(0) == "晨"
    assert clock.band_name(3) == "夜晚"
    assert clock.band_name(4) == "晨"   # wraps defensively
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/kernel/test_clock.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'kernel.clock'`

- [ ] **Step 3: Write the implementation**

Create `kernel/clock.py`:

```python
"""kernel.clock — pure in-game-time arithmetic.

The world clock is (day:int, band:int 0..3). Bands per day:
    0=晨  1=中午  2=下午  3=夜晚

Everything collapses to a single integer scale (band-units = day*4 + band)
so advance / elapsed / compare / expiry are plain integer ops. No I/O — fully
deterministic and offline-testable. This is the "time engine" all time-based
systems (lifespans, dormancy, catch-up) build on.
"""
from __future__ import annotations

BANDS: tuple[str, str, str, str] = ("晨", "中午", "下午", "夜晚")


def to_units(day: int, band: int) -> int:
    """Collapse (day, band) to a single comparable integer scale."""
    return day * 4 + band


def from_units(units: int) -> tuple[int, int]:
    """Inverse of to_units: (day, band), band in 0..3 (auto carry)."""
    return (units // 4, units % 4)


def advance(day: int, band: int, ddays: int, dbands: int) -> tuple[int, int]:
    """Advance the clock by ddays whole days + dbands bands; normalize carry."""
    return from_units(to_units(day, band) + ddays * 4 + dbands)


def elapsed(from_units_val: int, to_units_val: int) -> int:
    """Band-unit delta between two clock instants."""
    return to_units_val - from_units_val


def compare(a_units: int, b_units: int) -> int:
    """-1 if a<b, 0 if equal, 1 if a>b."""
    return (a_units > b_units) - (a_units < b_units)


def expired(born_units: int, lifespan_units: int, now_units: int) -> bool:
    """True once at least `lifespan_units` have elapsed since birth."""
    return now_units - born_units >= lifespan_units


def band_name(band: int) -> str:
    """Display name for a band index (wraps defensively)."""
    return BANDS[band % 4]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/kernel/test_clock.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add kernel/clock.py tests/kernel/test_clock.py
git commit -m "feat(clock): pure in-game-time engine (band-units, advance/elapsed/expired)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Extend `TimeSystem` to own the `clock` section

**Files:**
- Modify: `systems/time.py`
- Test: `tests/systems/test_time_system.py`

**Interfaces:**
- Consumes: `kernel.clock.band_name` (Task 1); `kernel.events.kernel_event`; `kernel.contextsystem.{ContextSystem, ValidationError, Fragment}`.
- Produces (on `TimeSystem`):
  - `commit_sections() -> {"clock"}`
  - `event_types() -> {"time_advanced", "clock_advanced"}`
  - `validate("clock", decl, world) -> list[ValidationError]`
  - `to_events("clock", decl, *, turn, day, scene) -> [clock_advanced event]` (event's `deltas` = `{"advance","days","bands","reason"}`)
  - `apply(world, clock_advanced)` sets `world["meta"]["band"] = (old_band + bands) % 4`
  - `inject(scene, world) -> Fragment("time", "scene", text, affordance)`

**Context — the current `systems/time.py` (full file, 60 lines) ends with:**
```python
    def commit_sections(self) -> set[str]:
        return set()

    def empty_state(self) -> dict:
        return {}

    def apply(self, world: dict, event: dict) -> None:
        g = world["systems"]["ontology"]
        d = event.get("deltas", {})
        pid = d.get("id")
        if pid:
            entity = g.get_entity(pid)
            if entity is None:
                log.warning("time_advanced dangling id=%s; last_update not stamped", pid)
            else:
                entity.attrs["last_update"] = event["day"]
                log.debug("time_advanced stamped last_update=%d for id=%s", event["day"], pid)
        # If no id: pure elapse carrier — projection sets meta.day via kernel
```

- [ ] **Step 1: Write the failing tests**

Edit `tests/systems/test_time_system.py`. First UPDATE the existing registration test to expect the new ownership (it currently asserts `commit_sections()` is empty). Find:

```python
def test_timesystem_registers_and_owns_event():
```
and replace its body's assertions so it reads:

```python
def test_timesystem_registers_and_owns_event():
    reg = _reg()
    ts = TimeSystem()
    assert "time_advanced" in ts.event_types()
    assert "clock_advanced" in ts.event_types()
    assert ts.commit_sections() == {"clock"}
```

Then APPEND these new tests to the same file:

```python
from kernel.contextsystem import Fragment


def test_clock_validate_accepts_well_formed_advance():
    ts = TimeSystem()
    decl = [{"advance": True, "days": 0, "bands": 2, "reason": "蹲守到入夜"}]
    assert ts.validate("clock", decl, {}) == []


def test_clock_validate_accepts_well_formed_non_advance():
    ts = TimeSystem()
    decl = [{"advance": False, "days": 0, "bands": 0, "reason": "紧接上一刻"}]
    assert ts.validate("clock", decl, {}) == []


def test_clock_validate_rejects_wrong_element_count():
    ts = TimeSystem()
    errs = ts.validate("clock", [], {})
    assert any(e.code == "bad_count" for e in errs)
    errs2 = ts.validate("clock", [{"advance": False, "days": 0, "bands": 0, "reason": "a"},
                                   {"advance": False, "days": 0, "bands": 0, "reason": "b"}], {})
    assert any(e.code == "bad_count" for e in errs2)


def test_clock_validate_requires_reason():
    ts = TimeSystem()
    errs = ts.validate("clock", [{"advance": True, "days": 1, "bands": 0, "reason": "  "}], {})
    assert any(e.field == "[0].reason" for e in errs)


def test_clock_validate_requires_bool_advance():
    ts = TimeSystem()
    errs = ts.validate("clock", [{"days": 0, "bands": 0, "reason": "x"}], {})
    assert any(e.field == "[0].advance" for e in errs)


def test_clock_validate_rejects_negative_amounts():
    ts = TimeSystem()
    errs = ts.validate("clock", [{"advance": True, "days": -1, "bands": 0, "reason": "x"}], {})
    assert any(e.field == "[0].days" for e in errs)


def test_clock_validate_advance_true_needs_nonzero_amount():
    ts = TimeSystem()
    errs = ts.validate("clock", [{"advance": True, "days": 0, "bands": 0, "reason": "x"}], {})
    assert any(e.code == "bad_advance" for e in errs)


def test_clock_validate_advance_false_needs_zero_amount():
    ts = TimeSystem()
    errs = ts.validate("clock", [{"advance": False, "days": 1, "bands": 0, "reason": "x"}], {})
    assert any(e.code == "bad_advance" for e in errs)


def test_clock_to_events_emits_clock_advanced():
    ts = TimeSystem()
    decl = [{"advance": True, "days": 0, "bands": 2, "reason": "蹲守到入夜"}]
    evs = ts.to_events("clock", decl, turn=1, day=3, scene="s1")
    assert len(evs) == 1
    ev = evs[0]
    assert ev["type"] == "clock_advanced"
    assert ev["day"] == 3
    assert ev["deltas"] == {"advance": True, "days": 0, "bands": 2, "reason": "蹲守到入夜"}


def test_clock_apply_folds_band_only():
    ts = TimeSystem()
    world = {"meta": {"day": 1, "band": 0}, "systems": {}}
    ev = ts.to_events("clock", [{"advance": True, "days": 5, "bands": 2, "reason": "x"}],
                      turn=1, day=6, scene="s")[0]
    ts.apply(world, ev)
    # band only depends on dbands: 晨(0)+2 -> 下午(2). days do not move band.
    assert world["meta"]["band"] == 2


def test_clock_apply_band_wraps():
    ts = TimeSystem()
    world = {"meta": {"day": 1, "band": 3}, "systems": {}}
    ev = ts.to_events("clock", [{"advance": True, "days": 0, "bands": 1, "reason": "x"}],
                      turn=1, day=2, scene="s")[0]
    ts.apply(world, ev)
    assert world["meta"]["band"] == 0   # 夜晚(3)+1 -> 晨(0)


def test_clock_inject_shows_current_clock():
    ts = TimeSystem()
    world = {"meta": {"day": 4, "band": 1}, "systems": {}}
    frag = ts.inject({}, world)
    assert isinstance(frag, Fragment)
    assert frag.layer == "scene"
    assert "第 4 天" in frag.text
    assert "中午" in frag.text
    assert "clock" in frag.affordance
```

Note: `_reg()` and `TimeSystem` are already imported at the top of this test file (it imports `TimeSystem` and builds `_reg()`). If `_reg()` does not register `TimeSystem`, the registration test still works because it instantiates `TimeSystem()` directly — no change to `_reg()` needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/systems/test_time_system.py -q`
Expected: FAIL — `commit_sections() == {"clock"}` assertion fails (currently empty); new `validate`/`to_events`/`inject` tests fail (methods inert / `clock_advanced` not owned).

- [ ] **Step 3: Write the implementation**

Edit `systems/time.py`. Update the imports block near the top (after `from kernel.contextsystem import ContextSystem`):

```python
from kernel.contextsystem import ContextSystem, ValidationError, Fragment
from kernel.events import kernel_event
from kernel.clock import band_name
```

Change `event_types` and `commit_sections`:

```python
    def event_types(self) -> set[str]:
        return {"time_advanced", "clock_advanced"}

    def commit_sections(self) -> set[str]:
        return {"clock"}
```

Replace the `apply` method with a type-branching version (clock first, then the existing time_advanced logic unchanged):

```python
    def apply(self, world: dict, event: dict) -> None:
        if event["type"] == "clock_advanced":
            # Band depends only on dbands (whole days never move the band).
            # meta.day is set by projection from event["day"]; we fold band here.
            d = event.get("deltas", {})
            old_band = world["meta"].get("band") or 0
            world["meta"]["band"] = (old_band + int(d.get("bands", 0) or 0)) % 4
            log.debug("clock_advanced -> day=%s band=%d", event["day"], world["meta"]["band"])
            return

        g = world["systems"]["ontology"]
        d = event.get("deltas", {})
        pid = d.get("id")
        if pid:
            entity = g.get_entity(pid)
            if entity is None:
                log.warning("time_advanced dangling id=%s; last_update not stamped", pid)
            else:
                entity.attrs["last_update"] = event["day"]
                log.debug("time_advanced stamped last_update=%d for id=%s", event["day"], pid)
        # If no id: pure elapse carrier — projection sets meta.day via kernel
```

Append `validate`, `to_events`, and `inject` to the class:

```python
    def validate(self, section: str, decl, world: dict) -> list[ValidationError]:
        if section != "clock":
            return []
        decl = decl or []
        if len(decl) != 1:
            return [ValidationError(
                "clock", "", "bad_count",
                f"clock 段必须恰好 1 个元素（本回合的时间推进），当前 {len(decl)} 个")]
        item = decl[0]
        errs: list[ValidationError] = []

        adv = item.get("advance")
        if not isinstance(adv, bool):
            errs.append(ValidationError(
                "clock", "[0].advance", "missing",
                "clock 必须含布尔 'advance'（本回合时间是否推进）"))

        reason = item.get("reason")
        if not (isinstance(reason, str) and reason.strip()):
            errs.append(ValidationError(
                "clock", "[0].reason", "missing",
                "clock 必须含非空 'reason'（推进多少的依据，或为何不推进）"))

        days = item.get("days", 0)
        bands = item.get("bands", 0)
        days_ok = isinstance(days, int) and not isinstance(days, bool) and days >= 0
        bands_ok = isinstance(bands, int) and not isinstance(bands, bool) and bands >= 0
        if not days_ok:
            errs.append(ValidationError(
                "clock", "[0].days", "bad_range", f"days 必须为 >=0 整数，当前 {days!r}"))
        if not bands_ok:
            errs.append(ValidationError(
                "clock", "[0].bands", "bad_range", f"bands 必须为 >=0 整数，当前 {bands!r}"))

        if isinstance(adv, bool) and days_ok and bands_ok:
            if adv and days == 0 and bands == 0:
                errs.append(ValidationError(
                    "clock", "[0]", "bad_advance",
                    "advance=true 但 days/bands 全为 0；给出推进量，或改 advance=false"))
            if not adv and (days != 0 or bands != 0):
                errs.append(ValidationError(
                    "clock", "[0]", "bad_advance",
                    "advance=false 但 days/bands 非 0；不推进时两者须为 0"))
        return errs

    def to_events(self, section: str, decl, *, turn: int, day: int, scene: str) -> list[dict]:
        if section != "clock":
            return []
        out: list[dict] = []
        for item in (decl or [])[:1]:
            adv = bool(item.get("advance"))
            days = int(item.get("days", 0) or 0)
            bands = int(item.get("bands", 0) or 0)
            reason = str(item.get("reason", ""))
            summary = (f"时间 +{days}天{bands}段：{reason}" if adv
                       else f"时间未推进：{reason}")
            out.append(kernel_event(
                "clock_advanced", day=day, scene=scene, summary=summary,
                deltas={"advance": adv, "days": days, "bands": bands, "reason": reason},
                turn=turn,
            ))
        return out

    def inject(self, scene: dict, world: dict) -> Fragment | None:
        meta = world.get("meta", {})
        day = meta.get("day") or 1
        band = meta.get("band") or 0
        text = f"【此刻】第 {day} 天 · {band_name(band)}"
        affordance = (
            'clock（每回合必填，恰好 1 个元素）：'
            '[{"advance":true/false,"days":整天数,"bands":时段数,"reason":"理由"}]。'
            f'当前 {band_name(band)}（晨→中午→下午→夜晚）；bands 是推进的时段数，'
            '可大于 3，引擎自动进位。即使时间不动（连续场景）也要 advance:false 且给 reason。'
        )
        return Fragment("time", "scene", text, affordance)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/systems/test_time_system.py -q`
Expected: PASS (the updated registration test + all new clock tests).

- [ ] **Step 5: Commit**

```bash
git add systems/time.py tests/systems/test_time_system.py
git commit -m "feat(clock): TimeSystem owns the clock commit section + clock_advanced event

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire clock advance into `run_turn` + make `clock` required

**Files:**
- Modify: `loop/turn.py`
- Test: `tests/loop/test_clock_loop.py`

**Interfaces:**
- Consumes: `kernel.clock.advance` (Task 1); `TimeSystem` owning `clock` (Task 2).
- Produces: `loop.turn._advanced_day(world, commit) -> int`; `REQUIRED_SECTIONS` now includes `"clock"`; `run_turn` stamps the turn at the advanced day.

- [ ] **Step 1: Write the failing tests**

Create `tests/loop/test_clock_loop.py`:

```python
import os
import tempfile

from kernel.registry import Registry
from kernel.projection import empty_world
from kernel.events import open_store
from loop.turn import run_turn, REQUIRED_SECTIONS
from loop.strategy import AuthorStrategy
from llm.provider import FakeLLMProvider
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.time import TimeSystem


def _registry():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(TimeSystem())
    return r


def _store(registry):
    d = tempfile.mkdtemp()
    return open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                      allowed_types=registry.event_types())


def _scene():
    return {"protagonist": "hero", "present": [], "day": 1, "id": "town", "location": "town"}


def test_clock_in_required_sections():
    assert "clock" in REQUIRED_SECTIONS


def test_clock_advance_moves_band_within_day():
    r = _registry()
    world = empty_world(r)
    canned = {"narration": "日头偏西。",
              "clock": [{"advance": True, "days": 0, "bands": 2, "reason": "蹲守到入夜"}]}
    store = _store(r)
    try:
        result = run_turn(r, store, world, _scene(), "等",
                          strategy=AuthorStrategy(),
                          provider=FakeLLMProvider(json_responses=[canned]))
    finally:
        store.close()
    assert result.world["meta"]["day"] == 1     # no whole days
    assert result.world["meta"]["band"] == 2     # 晨(0)+2 -> 下午(2)


def test_clock_advance_moves_multiple_days():
    r = _registry()
    world = empty_world(r)
    canned = {"narration": "三日兼程。",
              "clock": [{"advance": True, "days": 3, "bands": 1, "reason": "翻山三日，至次日中午"}]}
    store = _store(r)
    try:
        result = run_turn(r, store, world, _scene(), "赶路",
                          strategy=AuthorStrategy(),
                          provider=FakeLLMProvider(json_responses=[canned]))
    finally:
        store.close()
    assert result.world["meta"]["day"] == 4      # 1 + 3
    assert result.world["meta"]["band"] == 1      # 晨(0)+1 -> 中午(1)


def test_clock_no_advance_keeps_clock():
    r = _registry()
    world = empty_world(r)
    canned = {"narration": "紧接着。",
              "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "紧接上一刻"}]}
    store = _store(r)
    try:
        result = run_turn(r, store, world, _scene(), "继续",
                          strategy=AuthorStrategy(),
                          provider=FakeLLMProvider(json_responses=[canned]))
    finally:
        store.close()
    assert result.world["meta"]["day"] == 1
    assert (result.world["meta"].get("band") or 0) == 0


def test_clock_advance_persists_across_two_turns():
    r = _registry()
    world = empty_world(r)
    t1 = {"narration": "入夜。", "clock": [{"advance": True, "days": 0, "bands": 3, "reason": "黄昏到深夜"}]}
    t2 = {"narration": "翌日。", "clock": [{"advance": True, "days": 0, "bands": 1, "reason": "熬到天亮"}]}
    store = _store(r)
    try:
        provider = FakeLLMProvider(json_responses=[t1, t2])
        w1 = run_turn(r, store, world, _scene(), "守夜",
                      strategy=AuthorStrategy(), provider=provider).world
        # 晨(0)+3 -> 夜晚(3), still day 1
        assert (w1["meta"]["day"], w1["meta"]["band"]) == (1, 3)
        w2 = run_turn(r, store, w1, _scene(), "再守",
                      strategy=AuthorStrategy(), provider=provider).world
        # 夜晚(3)+1 -> 次日 晨(0)
        assert (w2["meta"]["day"], w2["meta"]["band"]) == (2, 0)
    finally:
        store.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/loop/test_clock_loop.py -q`
Expected: FAIL — `test_clock_in_required_sections` fails (clock not yet in the set); the advance tests fail (`meta.day`/`band` stay 1/0 because `run_turn` still stamps `scene["day"]`).

- [ ] **Step 3: Write the implementation**

Edit `loop/turn.py`.

(a) Add `"clock"` to `REQUIRED_SECTIONS` (currently line 43):

```python
REQUIRED_SECTIONS = frozenset({"moves", "places", "cast", "facts", "clock"})
```

(b) Add a helper just below `_next_turn` (after its `return max_turn + 1`):

```python
def _advanced_day(world: dict, commit) -> int:
    """Post-advance day for this turn = current clock + the turn's clock delta.

    The narrator's `clock` section is a delta {advance, days, bands}. We fold it
    onto the current (day, band) from world.meta and return the new day; the new
    band is folded separately by TimeSystem.apply on the clock_advanced event.
    Absent/none clock => no advance (back-compat with callers that omit it).
    """
    from kernel import clock as _clock
    meta = world.get("meta", {})
    cur_day = meta.get("day") or 1
    cur_band = meta.get("band") or 0
    decl = commit.sections.get("clock") or []
    if (isinstance(decl, list) and decl and isinstance(decl[0], dict)
            and decl[0].get("advance")):
        ddays = int(decl[0].get("days", 0) or 0)
        dbands = int(decl[0].get("bands", 0) or 0)
    else:
        ddays = dbands = 0
    new_day, _new_band = _clock.advance(cur_day, cur_band, ddays, dbands)
    return new_day
```

(c) In `run_turn`, replace the day computation (currently lines 228-231):

```python
        day = scene.get("day", 1)
        scene_id = scene.get("id") or scene.get("location") or "scene"

        new_world = apply_turn(registry, store, commit, day=day, scene=scene_id)
```

with:

```python
        scene_id = scene.get("id") or scene.get("location") or "scene"
        day = _advanced_day(world, commit)   # clock delta -> this turn stamps at post-advance day

        new_world = apply_turn(registry, store, commit, day=day, scene=scene_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/loop/test_clock_loop.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the FULL suite and fix any required-`clock` fallout**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q`

Expected new failures are ONLY in tests that drive a turn through the required-sections gate (i.e. call `play_loop`, or pass `required_sections=REQUIRED_SECTIONS`) with a fake whose output lacks `clock`. For each such failure, add a no-advance clock to that test's canned output:

```python
"clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}],
```

(Tests that call `run_turn`/`produce_turn` with the DEFAULT `required_sections=frozenset()` do NOT require clock and must still pass unchanged — if one of those now fails, it's a real regression, not a fixture gap; investigate instead of patching the fixture.)

Re-run `PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q` until green. The passing count must be >= the baseline 793 (it will exceed it by the new tests).

- [ ] **Step 6: Commit**

```bash
git add loop/turn.py tests/loop/test_clock_loop.py
# plus any test files you had to patch with a no-advance clock in Step 5:
# git add tests/app/test_play.py  (etc. — only the ones you actually changed)
git commit -m "feat(clock): advance the world clock each turn; clock is now required

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Document the `clock` section in the narrator prompts + forcing-function test

**Files:**
- Modify: `loop/strategy.py`
- Test: `tests/loop/test_clock_required.py`

**Interfaces:**
- Consumes: Task 2 (`clock` validation) + Task 3 (`clock` in `REQUIRED_SECTIONS`).
- Produces: `_SYSTEM_PROMPT` and `_SYSTEM_PROMPT_HYBRID` both describe the `clock` section.

- [ ] **Step 1: Write the failing test**

Create `tests/loop/test_clock_required.py`:

```python
import os
import tempfile

from kernel.registry import Registry
from kernel.projection import empty_world
from kernel.events import open_store
from loop.turn import run_turn, REQUIRED_SECTIONS
from loop.strategy import AuthorStrategy, _SYSTEM_PROMPT, _SYSTEM_PROMPT_HYBRID
from llm.provider import FakeLLMProvider
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.time import TimeSystem


def test_both_prompts_document_clock():
    assert "clock" in _SYSTEM_PROMPT
    assert "clock" in _SYSTEM_PROMPT_HYBRID


def _registry():
    r = Registry()
    for s in (OntologySystem(), PlaceSystem(), CharacterSystem(), TimeSystem()):
        r.register(s)
    return r


def _store(registry):
    d = tempfile.mkdtemp()
    return open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                      allowed_types=registry.event_types())


def test_missing_clock_is_repaired_via_gate():
    """A commit lacking `clock` is bounced by the required-sections gate; the
    repair attempt supplies it and the turn proceeds (forcing function)."""
    r = _registry()
    world = empty_world(r)
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "town", "location": "town"}

    # First attempt: every OTHER required section explained via reasons, but NO clock.
    no_clock = {"narration": "原地。",
                "reasons": {"moves": "未移动", "places": "无新地点",
                            "cast": "无人物变化", "facts": "无"}}
    # Repair attempt: now includes a no-advance clock.
    with_clock = {**no_clock,
                  "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "紧接上一刻"}]}

    provider = FakeLLMProvider(json_responses=[no_clock, with_clock])
    store = _store(r)
    try:
        result = run_turn(r, store, world, scene, "观察",
                          strategy=AuthorStrategy(), provider=provider,
                          required_sections=REQUIRED_SECTIONS)
    finally:
        store.close()
    assert result.repair_attempts >= 1
    assert "clock" not in result.dropped_sections
    assert result.world["meta"]["day"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/loop/test_clock_required.py -q`
Expected: FAIL — `test_both_prompts_document_clock` fails (prompts don't mention `clock` yet).

- [ ] **Step 3: Write the implementation**

Edit `loop/strategy.py`.

(a) In `_SYSTEM_PROMPT`, inside the `【结构】` list, add a `clock` bullet immediately after the `storylines:` line (after line 58):

```python
- clock: [{"advance":true/false, "days":整天数, "bands":时段数, "reason":"为什么"}]（**每回合必给，恰好一个元素**）——本回合游戏内时间推进多少：advance 是否推进；一天分四段（晨/中午/下午/夜晚），days=过了几整天、bands=过了几段（可>3，引擎自动进位）；reason 必填，写清推进这么多的依据，或【为何本回合不推进】。即使时间没动（连续动作、紧接上一刻）也要给 {"advance":false,"days":0,"bands":0,"reason":"..."}。
```

(b) In `_SYSTEM_PROMPT`, extend the `【必填·防遗漏】` line (line 60) — append this sentence to the end of that paragraph:

```
另外 clock 段每回合必给（恰好一个元素，描述本回合时间推进），不可省略、不可为空。
```

(c) In `_SYSTEM_PROMPT_HYBRID`, add the same `clock` bullet to its section list (after the `storylines:` bullet, around line 103):

```python
   - clock: [{"advance":true/false, "days":整天数, "bands":时段数, "reason":"为什么"}]（**每回合必给，恰好一个元素**）——本回合游戏内时间推进多少（一天四段：晨/中午/下午/夜晚；bands 可>3，引擎自动进位）；reason 必填。散文里时间明显流逝（入夜、次日、三日后）就按量给出，连续紧接也要 advance:false 且写 reason。
```

(d) In `_SYSTEM_PROMPT_HYBRID`, extend its `【必填·防遗漏】` line (line 104) — append:

```
clock 段每回合必给（恰好一个元素），不可省略。
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/loop/test_clock_required.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q`
Expected: all green; passing count > 793 baseline.

- [ ] **Step 6: Commit**

```bash
git add loop/strategy.py tests/loop/test_clock_required.py
git commit -m "feat(clock): document clock section in narrator prompts (甲+丙)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Out of Scope / Follow-ups

- **travel_cost reference injection** — already exists: `PlaceSystem.inject` (`systems/place.py:404-435`) renders exits as `dst(N日)`. No work needed.
- **travel_cost soft diagnostic** (spec §7) — a non-blocking log warning when a `moves` crosses a costly edge but the clock didn't advance — DEFERRED. The mandatory `clock` section already prevents frozen time; this is observability-only.
- **catch-up wiring** (spec §8) — `loop/time.py` catch-up already keys off `meta.day` (`current_day`); now that the clock advances `meta.day`, catch-up becomes live with no change. The clock-loop integration tests (Task 3) prove `meta.day` advances; no new catch-up code is in scope.
- **scene-progression** (advancing `meta.scene`) — separate follow-on piece; a day jump / location change is its natural boundary signal, to be designed later.
- **lore / event-line system** — the big consumer (lifespans, dormancy, complex-line world-roll, density refresh) builds on `kernel.clock.expired()` and `meta.clock`; separate spec.

## Self-Review

- **Spec coverage:** §1 钟表示 → Task 2 (meta.band) + Task 3. §2 时间引擎 → Task 1. §3 clock 段(增量) → Task 2 (schema/validate) + Task 4 (prompt). §4 归属 TimeSystem → Task 2. §5 本回合即生效 → Task 3 `_advanced_day` + stamp. §6 inject 当前钟 → Task 2 `inject`; travel_cost ref → pre-exists. §7 → deferred (documented). §8 catch-up → documented (no code). Required-section forcing function → Task 3 (`REQUIRED_SECTIONS`) + Task 4 (test). All covered.
- **Type consistency:** `advance(day, band, ddays, dbands) -> (day, band)` used identically in Task 1 def, Task 2 (band fold uses `(old_band+bands)%4`, consistent with `advance`'s band component), Task 3 `_advanced_day`. `clock` decl shape `{advance, days, bands, reason}` identical across Tasks 2/3/4. Event `clock_advanced` deltas shape identical in Task 2 to_events + apply + Task 3.
- **No placeholders:** every step has full code + exact run commands + expected output.
