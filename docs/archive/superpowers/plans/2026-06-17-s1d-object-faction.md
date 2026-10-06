# S1d: 物品 (Object) + 势力 (Faction) Systems — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. `- [ ]` steps, TDD.

**Goal:** Two more registered systems on the shared graph: `ObjectSystem` (inventory) and `FactionSystem` (templated factions + membership), the latter exposing audience-resolution helpers that the 认知 system (S1e) will consume.

**Architecture:** Same system pattern (own events/sections; write `world["systems"]["ontology"]`; require `OntologySystem`). Items = `Object` entities; possession = single-valued `held_by` relation (a thing is with one holder — Person or Place). Factions = `Faction` entities whose attrs define their **canonical ranks/groups once** (`ranks` = ordered list, `groups` = list); membership = multi-valued `member_of` relation (`supersede=False`); a member's rank-in-a-faction = a Fact `rank:{faction}` (predicate-scoped supersession → promotion supersedes cleanly per faction). `FactionSystem` is an **index, not a rule engine**.

**Tech Stack:** Python 3.12 stdlib; reuses S0 kernel + S1a–c. Offline tests, `python3 -m pytest -q --ignore=tests/test_embed_real.py`. Mirror `systems/place.py`/`character.py`.

**Conventions/Guardrails:** TDD per task; `get_logger`; commit per task; `python3`. Never `git init`/`rm -rf .git`/`checkout --orphan`; never delete/modify `_legacy/` or `docs/`; do NOT touch `kernel/` or other systems. Create only the files each task names.

---

## File Structure
- `systems/object.py` (new) — `ObjectSystem`.
- `systems/faction.py` (new) — `FactionSystem` + module helpers `members_of`, `member_rank`.
- `tests/systems/test_object.py`, `tests/systems/test_faction.py` (new).

---

## Task 1: `ObjectSystem` (items + inventory)

`ObjectSystem`: `name="object"`; `event_types={"object_created","item_transferred"}`; `commit_sections={"items"}`; `empty_state()={}`.
- `object_created` deltas `{id, tier?, **attrs}` → `g.add_entity(id,"Object",tier, **attrs)`.
- `item_transferred` deltas `{item, to}` → `g.add_relation(item,"held_by",to, day,turn,source_event)` (single-valued default → supersedes prior holder).

- [ ] **Step 1 — failing tests** `tests/systems/test_object.py` (register OntologySystem+ObjectSystem; drive via project): object_created makes an Object entity; item_transferred sets `held_by`; a second transfer supersedes (`neighbors(item,"held_by",later)==[new]`); `validate("items",...)` flags item_transferred whose `item` or `to` entity is missing (`dangling_ref`); `to_events("items",...)` maps `{op:"create"}`→object_created and `{op:"transfer"}`→item_transferred; `inject(scene={"protagonist":"主角","day":D}, world)` returns a Fragment listing items currently held_by 主角 (scan Object entities; include where `neighbors(item,"held_by",D)==["主角"]`), or None if none.
- [ ] **Step 2 — fail.** **Step 3 — implement `systems/object.py`** (NOTE: filename `object.py` is fine as a module; import as `from systems.object import ObjectSystem`). **Step 4 — pass + full suite green. Step 5 — commit** `git add systems/object.py tests/systems/test_object.py && git commit -m "feat(systems): ObjectSystem — items + possession + inventory inject"`

---

## Task 2: `FactionSystem` core (factions + membership)

`FactionSystem`: `name="faction"`; `event_types={"faction_created","member_changed"}`; `commit_sections={"factions"}`; `empty_state()={}`.
- `faction_created` deltas `{id, tier?, kind?, ranks?, groups?, **attrs}` → `g.add_entity(id,"Faction",tier, kind=, ranks=list, groups=list, **attrs)` (ranks/groups stored as entity attrs — defined once).
- `member_changed` deltas `{person, faction, rank?, group?}` → `g.add_relation(person,"member_of",faction, supersede=False)` (multi-valued) + if `rank`: `g.assert_fact(person, f"rank:{faction}", rank, day,turn,source_event)`; if `group`: `g.assert_fact(person, f"group:{faction}", group, ...)`.

- [ ] **Step 1 — failing tests** `tests/systems/test_faction.py`: faction_created makes a Faction entity carrying `ranks`/`groups` attrs; member_changed adds `member_of` (multi-valued — joining a 2nd faction keeps the 1st: `neighbors(person,"member_of",day)` has both); rank stored as fact `rank:{faction}` and promotion supersedes (point-in-time: old day old rank, new day new rank); `validate("factions",...)`: faction_created missing `id`→missing; member_changed with missing person/faction entity→dangling_ref; `to_events` dispatches on `op` (`"faction"`→faction_created, `"member"`→member_changed).
- [ ] **Step 2 — fail. Step 3 — implement** (core only; helpers in Task 3). **Step 4 — pass + full suite green. Step 5 — commit** `git add systems/faction.py tests/systems/test_faction.py && git commit -m "feat(systems): FactionSystem — templated factions + membership"`

---

## Task 3: Faction audience-resolution helpers (for 认知)

Module-level functions in `systems/faction.py` (used later by the 认知 system to resolve a broadcast audience):
- `members_of(graph, faction, day, *, min_rank=None, group=None) -> list[str]`: ids of persons with a current `member_of`→`faction` relation valid at `day`, optionally filtered: `group` → those whose `value_at(person, f"group:{faction}", day) == group`; `min_rank` → those whose rank index in the faction's `ranks` attr is `>=` index of `min_rank` (rank via `value_at(person, f"rank:{faction}", day)`; if a member has no rank fact, treat as lowest).
- `member_rank(graph, person, faction, day) -> str | None`: the person's rank in the faction at `day`.

- [ ] **Step 1 — failing tests** (in `tests/systems/test_faction.py`): set up faction with `ranks=["学徒","正式","资深","会长"]`, three members at different ranks (one with no rank); `members_of(g,F,day)` returns all 3; `members_of(g,F,day,min_rank="资深")` returns only those at 资深+ (index ≥ 2); `members_of(g,F,day,group="高层")` filters by group; `member_rank(g,person,F,day)` returns the rank or None.
- [ ] **Step 2 — fail. Step 3 — implement** the two helpers (pure functions over the graph; `min_rank` compares list indices in the faction entity's `ranks` attr; unknown rank → index -1 so it's below any real `min_rank`). **Step 4 — pass + full suite green. Step 5 — commit** `git add systems/faction.py tests/systems/test_faction.py && git commit -m "feat(systems): faction audience-resolution helpers (members_of/member_rank)"`

---

## Done criteria for S1d
- Full suite green. ObjectSystem round-trips items+possession+inventory; FactionSystem round-trips factions+multi-faction membership+per-faction rank with promotion supersession; `members_of`/`member_rank` resolve audiences (the substrate 认知 broadcast needs).
- No game logic in `kernel/`.

**Next:** S1e 认知 (knows/believes facts + audience=faction/place entity refs via `members_of` + broadcast/endowment/told grant ops + the two-bucket POV/guardrail write model surfaces in S3).
