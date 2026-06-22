# SOT Fix Report — 2026-06-22

## BUG 1: Remove vestigial `status` field from lore/quest lines

### Source files changed

**`systems/lore.py`**

| Site | What was removed |
|------|-----------------|
| docstring line 10 | `status ("active"|"resolved"|"expired")` removed from slice schema |
| `lore_created` branch (~line 85) | `"status": "active"` removed from dict literal |
| `quest_created` branch (~line 117) | `"status": "active"` removed from dict literal |
| `quest_opened` branch (~line 149) | `"status": "active"` removed from dict literal |
| `quest_world_resolved` branch (~line 299) | `ln["status"] = "resolved"` deleted |
| `quest_catastrophe` branch (~line 320) | `ln["status"] = "resolved"` deleted |

**`loop/lore.py`**

| Site | Change |
|------|--------|
| loop/lore.py ~line 103 | `if ln.get("status") != "active": continue` → `if ln.get("state") == "了结": continue` |

**`loop/density.py`**

| Site | Change |
|------|--------|
| `count_tier` docstring (~line 116) | Updated from "Only lines with status==active AND state in ..." to "Only lines with state in (暗,明) are counted (了结 lines free their cap slot)." |
| `count_tier` loop (~line 125) | Deleted `if ln.get("status") != "active": continue` block — redundant, next check `if ln.get("state") not in ("暗", "明"): continue` already excludes 了结 lines |

**`loop/lore_disclosure.py`**

| Site | Change |
|------|--------|
| `station_push_fragment` loop (~line 84) | `if line.get("status") != "active": continue` → `if line.get("state") == "了结": continue` |
| `index_fragment` loop (~line 166) | `if line.get("status") != "active": continue` → `if line.get("state") == "了结": continue` |

### Post-edit grep verification

`grep -n "status" systems/lore.py loop/lore.py loop/density.py loop/lore_disclosure.py` — zero matches.

---

## BUG 2: `run_lore` day unified to `meta.day`

**`loop/lore.py` — `run_lore`**

Old code had two separate variables:
- `day = events[-1]["day"] if events else 1` (store tail)
- `_md = world.get("meta", {}).get("day"); now_day = _md if _md is not None else day` (world clock)

`lore_advanced` and `quest_surfaced` used store-tail `day`; lifespan/finale/expiry events used `now_day`.

New code: single `day` variable computed world-clock-first:
```python
_md = (world.get("meta", {}) or {}).get("day")
day = _md if _md is not None else (events[-1]["day"] if events else 1)
```

`now_day` variable eliminated; all `kernel_event(... day=...)` calls in `run_lore` use this single `day`. `scene` sourcing unchanged. Stale comment removed.

Consequence: `lore_advanced.day == meta.day`, so `systems/lore.py:173` `ln["last_advanced_day"] = event.get("day")` now records the world-clock day, consistent with idle-demote's `meta.day` read in `loop/turn.py`.

---

## Tests updated

| File | Test / line | Entity | Reason for change |
|------|-------------|--------|-------------------|
| `tests/systems/test_lore_endgame.py` | `TestQuestWorldResolved::test_status_becomes_resolved` | lore line | Was asserting vestigial `status=="resolved"`; renamed to `test_no_status_field_on_liujie_line`, now asserts `"status" not in ln` |
| `tests/systems/test_lore_endgame.py` | `TestQuestCatastrophe::test_status_becomes_resolved` | lore line | Same as above for catastrophe branch |
| `tests/systems/test_lore_endgame.py` | module docstring | — | Updated to say "NO status field" instead of "status set to resolved" |
| `tests/systems/test_lore_system.py` | `test_lore_created_builds_line` line 53 | lore line | Was asserting `ln["status"] == "active"`; now asserts `"status" not in ln` and `ln["state"] == "暗"` |
| `tests/systems/test_quest_state.py` | `test_lore_created_existing_tests_unaffected` line 74 | lore line | Was asserting `ln["status"] == "active"`; now asserts `"status" not in ln` |
| `tests/loop/test_density_e2e.py` | line 222 (loop asserting each seeded line) | lore line | Was asserting `ln["status"] == "active"`; now asserts `"status" not in ln` |
| `tests/loop/test_endgame_e2e.py` | `TestPathA::test_line_liujie_after_projection` line 201 | lore line | Was asserting `ln["status"] == "resolved"`; now asserts `"status" not in ln` |
| `tests/loop/test_endgame_e2e.py` | `TestPathB::test_line_liujie_by_catastrophe_after_projection` line 308 | lore line | Same as above for catastrophe path |
| `tests/loop/test_lore_disclosure_A.py` | `test_inactive_lines_excluded` line 182–188 | lore line | Was manually setting `status="resolved"` to test exclusion; renamed to `test_liujie_lines_excluded`, now sets `state="了结"` (the real gate) |
| `tests/llm/test_lore_tools.py` | `_build_world_with_lines` lines 46, 63 | lore line (fixture) | Removed `"status": "active"` from both hard-coded lore line dicts; added `"state": "暗"` |
| `tests/loop/test_turn_density_hook.py` | line 154 (loop asserting each generated line) | lore line | Was asserting `ln["status"] == "active"`; now asserts `"status" not in ln` |

### New regression tests added (`tests/loop/test_quest_transitions.py`)

| Test | What it proves |
|------|---------------|
| `test_no_status_field_after_quest_expired` | After `quest_expired`, line has `state=="了结"` and NO `status` key |
| `test_no_status_field_after_quest_resolved` | After `quest_resolved` (明→了结), line has NO `status` key |
| `test_lore_advanced_stamped_with_meta_day` | `run_lore` emits `lore_advanced.day == meta.day` when `meta.day != store-tail day`; also verifies `last_advanced_day == meta.day` after projection |

---

## Test run

Full suite: **1186 passed, 1 deselected** (was 1183 before this commit; +3 new regression tests).
