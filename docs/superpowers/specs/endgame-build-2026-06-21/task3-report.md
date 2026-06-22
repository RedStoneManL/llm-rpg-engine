# Task 3 Build Report — e2e test + demo + cleanup

Date: 2026-06-21
Branch: app
Base commit: c4dfdbf

---

## Files created / modified

### Created: `tests/loop/test_endgame_e2e.py` (20 tests)

End-to-end test proving the full pipeline: world creation → `run_lore` orchestration →
final state assertions + density cap release.

**World setup:** L1 region ("region1") ⊃ L2 town ("town1") ⊃ L3 venues ("venue1", "venue2").
Complex 暗 line "e2e_complex_1" anchored at "town1" with 5 stages and threshold=100 (always advances).

#### Pinned seeds

| Constant | Value | Derivation |
|---|---|---|
| `_SEED_PATH_A` | 3 | `Oracle(scene_seed(3, "rescue:e2e_complex_1", 1)).d100()` = 13 ≤ 20 → SUCCESS |
| `_SEED_PATH_B` | 5 | Stages 1/2/3 all FAIL; finale at day=30 roll=74 > 60 → FAIL |
| `_FINALE_DAY`  | 30 | `now_day` used in finale oracle for Path B |

**world_rescue_chance at stage 1, n_stages=5:** `10 + round(1/4 × 40) = 20`

**Path B roll breakdown:**
- stage 1: roll=51 vs chance=20 → FAIL
- stage 2: roll=69 vs chance=30 → FAIL
- stage 3: roll=98 vs chance=40 → FAIL
- finale (day=30): roll=74 vs chance=60 → FAIL → catastrophe

#### How pending_finale is driven to in Path B

Direct mutation of the projected world dict (`ln["pending_finale"] = True`) on the
world projection before calling `run_lore`. This simulates a prior-turn `quest_finale_due`
(set by lifespan expiry path in `run_lore` + LoreSystem apply). The test mirrors exactly
how Task 2 wiring tests handle it — see Task 2 report concern #1. In production, the path
is: lifespan elapses → `quest_finale_due` emitted → LoreSystem apply sets
`pending_finale=True` → next turn's projection has it truthy → finale fires.

#### Test classes

- **`TestPathAWorldRescue`** (6 tests): rescue SUCCESS at stage 1 → `quest_world_resolved(by==world_rescue)`;
  line state==了结 + status==resolved after re-projection; `count_tier(region1, "complex")`
  drops from 1 to 0 (cap released); no `quest_surfaced` emitted same trip; 了结 line not
  re-processed on next call.

- **`TestPathBCatastrophe`** (8 tests): `pending_finale=True` + finale fails → `quest_catastrophe`
  + `world_change` emitted; `world_change.place == region_scope("town1", day)` == "region1";
  `place != "town1"` and `place != "world"` (region-bounded assertion); line 了结 by==catastrophe
  after re-projection; cap released; no `world_change` without CascadeSystem; catastrophe not
  re-fired on 3rd call.

- **`TestDeterminism`** (3 tests): fresh rebuild with same `campaign_seed` → same event type
  sequence for both paths; sanity that Path A produces `quest_world_resolved` and Path B
  produces `quest_catastrophe` (not swapped).

- **`TestRegionBounded`** (2 tests): `region_scope("town1")` == "region1" in the test world;
  `world_change.place` exactly equals `region_scope(anchor, day)`.

- **`test_pinned_seeds_sanity_e2e`** (1 test): verifies all documented pin values produce
  the exact documented rolls (roll=13 for Path A, rolls=51/69/98/74 for Path B).

---

### Created: `docs/superpowers/specs/endgame-build-2026-06-21/demo.py`

Real-model demo mirroring the density `demo.py` structure.

- **World:** L1 region "北境" ⊃ L2 town "边城" ⊃ L3 venues (要塞大营/商行街/城门楼) + separate
  L2 town "南渡口" where the player starts (never visits 边城).
- **Complex 暗 line:** "守将机密泄露", anchor=边城, lifespan_days=6, pre-brewed to stage 2
  via `lore_advanced` events at turn 0.
- **6 turns, CLOCK_DAYS=[0,1,1,2,1,1]:** Day advances from 1 to 7. Lifespan 6 days means
  at day 7 the line goes `pending_finale=True` → next call fires finale (rescue or catastrophe).
- **Per-turn dump:** complex line state/stage + any endgame events (`quest_world_resolved`,
  `quest_catastrophe`, `world_change`) + cascade aftermath on catastrophe.
- **Run command** in file header: `cd /root/rpg-engine-app && set -a; . ./.env.local; set +a && PYTHONPATH=/root/rpg-engine-app python3 docs/superpowers/specs/endgame-build-2026-06-21/demo.py`
- NOT run in tests.

---

### Modified: `tests/loop/test_lore_endgame_wiring.py`

Deleted 2 dead throwaway lines at L570-571 (flagged by code reviewer):
```python
# DELETED:
r, s, w = _reg(with_cascade=True), None, None
r2, s, w = _reg(with_cascade=True), _store(_reg(with_cascade=True)), None
```
Both were immediately overridden by the `r = _reg(with_cascade=True)` rebuild block below. All 27 Task 2 tests still pass.

---

## Test results

- `tests/loop/test_endgame_e2e.py`: 20 passed
- `tests/loop/test_lore_endgame_wiring.py`: 27 passed
- Full suite: **1183 passed, 1 deselected** (was 1163 before Task 3)

---

## Concerns / notes

1. **Path B uses direct `pending_finale` injection** (not a full lifespan scenario). The full
   production path (lifespan → `quest_finale_due` → apply → `pending_finale=True`) is tested
   implicitly by the Task 2 wiring tests; the e2e test focuses on the cap-release payoff.

2. **Demo CLOCK_DAYS=[0,1,1,2,1,1]**: advances day from 1 to 7. With lifespan=6, the
   `quest_finale_due` fires when `now_day - born_day >= 6` i.e. at day 7. The finale
   (rescue or catastrophe) fires on the subsequent `run_lore` call (next turn). SEED=20260621
   may produce rescue success or catastrophe depending on the oracle roll — either outcome
   correctly demonstrates the endgame pipeline.

3. **cascade_provider** in demo is the same real GLM provider; cascade LLM calls for region
   evolution will only fire if `world_change` triggers `_node_verdict` breadth BFS on registered
   place entities in the region. The demo prints cascade aftermath keys if found.
