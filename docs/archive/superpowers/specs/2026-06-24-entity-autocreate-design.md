# 新实体引入与生命周期 — 设计 (D-R7 / R7 part 2)

> Status: design approved in dialogue (2026-06-23/24). Phase 1 builds now.

## Problem

The model narrates a NEW named NPC/place (e.g. 卡恩) and references it in `moves`
(`who`/`to`) without a valid entity id — entities are id-keyed (npc_0…) and after
the #10 fix the model only sees names, not ids. `moves`/`places` then fail
validation (`dangling_ref: '卡恩' 不存在于图中`) almost every turn and get dropped,
so the world silently loses who-moved / who-appeared.

## Phase 1 — auto-create/resolve (the fix + foundation)

A **pre-validate augment** (`loop/entity_resolve.py::augment_unresolved_refs`),
run on the commit BEFORE `validate_commit` (initial produce + each repair), that
rewrites name-refs to entity ids and mints missing ones:

- **Resolve** `moves.who`(person), `moves.to`/`links.a`/`links.b`/`materialize.id`
  (place) against: (a) existing graph entities (by id, or by 真名 fact), and (b)
  THIS turn's `cast` creates (by their optional `name`) + `places` creates (by
  `id` or `seed`).
- **Mint on miss**: an unresolved name → mint a new id (`npc_auto_N` /
  `place_auto_N`), INJECT a create into the commit so the existing
  validate/created_ids/to_events machinery applies it:
  - person → a `cast` create `{id, op:create, sketch, goal:"（暂未明）", tier:"mentioned"}`
    + a `facts` entry `{subject:id, predicate:"真名", value:<name>, secrecy:"public"}`.
  - place → a `places` create `{id, level:3, kind:"venue", seed:<name>}`
    + the same 真名 fact.
  - `sketch`/seed defaults to a **first-seen breadcrumb** when the model gave none:
    `"（首次现身于{scene}·第{day}天）"`. The create event itself already carries
    turn/day/scene.
- **Tier = importance signal**: a bare auto-minted entity is `mentioned`
  (walk-on placeholder); when the model supplies a real `cast` create with a
  sketch it's `tracked` (has background). Promotable later via `cast op:evolve`.
- **Dedup** by normalized 真名 across turns (the 真名 fact) AND within the turn
  (the name2id map), so 卡恩 is never created twice.
- **Prompt nudge**: tell the model new NPCs that matter should be a `cast` create
  with `name` + `sketch` (+goal); bare mentions become light placeholders.

Constraints: never raises (defensive; runs in the turn loop). When no entity
graph / nothing to resolve → no-op. Byte-identical for commits whose refs are all
already valid ids (the common path) — the augment only acts on unresolved names.

## Phase 2 — importance table (filter + age; build after Phase 1)

On top of the tier model: context-assembly + POV queries surface `tracked` +
scene-relevant by default (don't dump every historical `mentioned` walk-on into
the prompt → anti-bloat / token); age/archive `mentioned` NPCs not seen in N days
(keep the first-seen breadcrumb for traceability). Possibly a finer axis
(主角/重要/配角/路人). Separate spec when we get there.

## Testing

Unit (with a real FactGraph): resolve existing-by-id / existing-by-真名 /
this-turn-cast / this-turn-place; mint+inject for unknown person & place; dedup
across turns + within turn; breadcrumb on bare mint; no-op when all refs valid;
never raises on junk. Integration: a turn whose `moves.who` is a brand-new name →
entity created (mentioned, 真名 stored), move applied, NO dropped section. Full
suite stays green (1605).
