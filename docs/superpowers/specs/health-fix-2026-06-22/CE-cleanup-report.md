# CE Cleanup Report — 2026-06-22

Commit range: `06c091c..2c5a34b`
Test summary: **1151 passed, 0 failures** (`PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q`)

---

## C1 — cascade prereq guard

`loop/cascade.py::run_cascade` now checks `world.get("systems", {})` for
`cascade is None` or `ontology is None` at the very top of the function and
returns `[]` (with a debug log) before touching any deep indexes. The guard
fires before the existing `world["systems"]["ontology"]` and
`world["systems"]["cascade"]` accesses (lines 764/778 pre-patch). No cascade
logic changed.

## C4 — ancestor-walk deduplication

Created `loop/graph_utils.py` with `ancestor_of_level(g, place_id, day, level)`.
- `loop/density.py`: local `_ancestor_of_level` removed; imports the canonical
  `ancestor_of_level` aliased as `_ancestor_of_level` so all call sites are
  unchanged.
- `loop/lore_disclosure.py`: `_l2_ancestor` made a one-line wrapper
  `return ancestor_of_level(g, place_id, day, 2)` — the name is preserved so
  `loop/turn.py`'s `from loop.lore_disclosure import _l2_ancestor` needs no
  change.

## E2 — stale docstrings/inline sprint labels

- `systems/lore.py` module docstring: removed T1/T2 sprint labels and the
  false claim that L2/L3/L4 are separate phases; replaced with a factual
  description of the current unified system. Removed `# T5:` inline label.
- `loop/lore.py`: removed `# T4` from world-push surfacing comment; removed
  `option-a:` label from `jit_resequence` section header and function
  docstring. Explanations preserved.
- `loop/lore_disclosure.py`: module docstring dropped `B-mode` and `(Task 2)`
  tags; description preserved.
- `tests/systems/test_lore_active.py`: module docstring removed
  "replaces retired StorySystem tests (T3)"; replaced with "Covers the full
  明账 lifecycle owned by LoreSystem".

## E3 — stale docs fixed

- `docs/2026-06-19-architecture-world-model.md`:
  - Figure 1: `storylines` → `quests` in commit sections list.
  - Figure 2: `Story: 故事线明账` → `Lore: 明账 + 暗线环境推送`.
  - Figure 3: `(Story / Narrative)` → `(Lore / Narrative)`; fleet digest
    line updated (`故事线漏报补` → `暗线漏报补`).
  - System table: `Story 故事线(P2)` row replaced with `Lore 事件线` row
    reflecting actual LoreSystem ownership (quests section, quest_*/lore_*
    events, 暗/明/了结).
  - Footer note: `Story/Narrative` → `Lore/Narrative`.
- `docs/codegraph/INDEX.md`: test count updated from `476` to `1151`.

## E4 — misnomers fixed

- `loop/fleet.py`: `backstop_storylines` renamed to `backstop_quests`
  everywhere (function def, all `log.debug` calls, call site in
  `digest_fleet`, and module docstring). No event-type strings touched.
- `docs/superpowers/specs/endgame-build-2026-06-21/demo.py`: stale
  `ln.get('status')` dump line changed to `ln.get('state')` (the `status`
  field no longer exists on lore line dicts).

---

## Concerns

None. All changes are purely defensive/cosmetic — no cascade logic, no event
types, no replay-affecting symbols were changed. The `backstop_storylines`
rename is Python-symbol-only (not persisted in the event log).
