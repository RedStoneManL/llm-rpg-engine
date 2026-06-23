# Player-Definable Genesis + SillyTavern Conversion — Design

> Status: design (approved 2026-06-23). Supersedes the "parallel ST-lorebook
> runtime" idea floated earlier — ST formats are an **import source translated
> into our native spec**, not a second runtime.

## Goal

Let the player define **any part of world genesis** at 开局. Whatever they don't
define, the model fills (today's bootstrap). A minimal set of parts is
**required** — the agent surfaces and confirms each, looping until every one is
either given a value or explicitly delegated to the model ("你来定"). Everything
above that floor is creatable by the user *or* the model.

Future (phase 2): ingest SillyTavern (酒馆) **world-books** (世界书 / lorebooks)
and **character cards** (角色卡, V2 JSON) by **LLM-translating them into our
genesis spec** — not by running ST semantics.

## Background: the current bootstrap

`loop/bootstrap.py::bootstrap_world(engine, pitch, *, attempt=0, progress=None)`
runs 9 steps; each `gen_*` generator cleanly separates two decision sources:

1. **Engine rolls STRUCTURE** — counts, terrains, complexity, thresholds — via
   `Oracle(scene_seed(campaign_seed, f"genesis:{step}", attempt))`. Deterministic,
   rewind-safe.
2. **LLM authors CONTENT** — names, descriptions, secrets — via
   `complete_structured` (or plain `complete` for the opening). Deterministic
   stub fallback on error / `provider=None`. **Never raises.**

Generators and their outputs (the shapes the spec must mirror):

| step | engine rolls | LLM authors |
|------|--------------|-------------|
| `gen_frame` | tone, n_factions, n_regions | world_name, central_conflict |
| `gen_regions` | terrains, density, tiers, star graph | region name + seed |
| `gen_local_map` | n_extra_l2, neighbor kinds, n_venues | town/venues/neighbors name + seed |
| `gen_protagonist` | — | name, origin, goal, objective |
| `gen_factions` | count (=n_factions) | name + motivation (distinct) |
| `gen_npcs` | n(2-4), roles, traits | sketch, goal, secret(secrecy=secret) |
| `gen_threads` | n(3-5)+n_p(1-2), types, complexity, threshold, stages | about, description, trigger, secret, l3_anchor, stages[hint] |
| `gen_opening` | — | opening prose |

`bootstrap_world` returns `{"summary", "_state", "_boundaries"}`; `_state` already
carries `frame / regions_summary / local_map / factions_summary / npcs_summary /
threads_summary / protagonist_authored / pitch / attempts`. `reroll_all` /
`reroll_step` reuse `_state`.

## Core idea: a third input — the provided spec

Each generator gains an optional **`provided`** part. The merge rule inside every
generator becomes:

```
value = provided_value  if provided and non-empty
        else (engine roll / LLM author)   # exactly today's path
```

- **Scalars** (world_name, tone, protagonist.name, …): provided wins; else roll/author.
- **Lists** (regions, factions, npcs, threads, venues): **augment** — keep all
  provided items, then author up to `max(len(provided), rolled_count)`. User
  items are always kept; the LLM is told about them so the top-ups stay coherent.

**Hard backward-compat constraint (like debug-mode):** when **no spec** is
provided (`spec=None` / every part absent), every generator takes its current
code path and the **~1538-test suite stays byte-identical**. `provided=None`
defaults guarantee this.

**No new event types.** Generators emit the same events with different
content/counts. `projection` and all `ContextSystem`s are untouched. P2
conversion also emits nothing new — it only produces a spec.

## The canonical GenesisSpec

One structured model; `bootstrap_world` consumes only this. Every part and field
is optional at the type level; "required" is enforced by session-zero (below),
not by the schema. Shapes mirror existing `_state` so generators map 1:1.

```python
GenesisSpec = {
  "world_premise": {                     # → gen_frame        [REQUIRED]
     "genre": str,                       #   (also seeded from `pitch`)
     "tone": str | None,
     "world_name": str | None,
     "central_conflict": str | None,
     "n_factions": int | None,
     "n_regions": int | None,
  } | None,
  "regions": [ {"name": str, "terrain": str|None, "seed": str|None} ] | None,   # → gen_regions (augment; regions[0] = start)
  "local_map": {                         # → gen_local_map
     "town":      {"name": str, "seed": str|None} | None,
     "venues":    [ {"name": str, "seed": str|None} ] | None,   # augment
     "neighbors": [ {"name": str, "kind": str|None, "seed": str|None} ] | None, # augment
  } | None,
  "protagonist": {                       # → gen_protagonist   [REQUIRED: name (+origin)]
     "name": str,
     "origin": str | None,
     "goal": str | None,
     "objective": str | None,            # optional; else model authors the surface objective
  } | None,
  "factions": [ {"name": str, "motivation": str|None} ] | None,                 # → gen_factions (augment)
  "npcs": [ {"sketch": str|None, "role": str|None, "goal": str|None, "secret": str|None} ] | None,  # → gen_npcs (augment, concat)
  "threads": [ {"about": str|None, "description": str|None, "trigger": str|None,
                "secret": str|None, "l3_anchor": str|None, "stages": [str]|None,
                "complexity": str|None, "bound": "campaign"|"protagonist"|None} ] | None,  # → gen_threads (augment, concat)
  "opening": str | None,                 # → gen_opening (verbatim if present)
}
```

Lives in **`loop/genesis_spec.py`** (new, pure — no I/O, no LLM): the dataclass /
TypedDict + `normalize(raw) -> GenesisSpec`, `merge(base, overlay) -> GenesisSpec`,
and `missing_required(spec) -> list[str]`.

## Required-gate semantics

**Minimal required set = the "我是谁 / 我在哪 / 我要干嘛" anchor, minus the part
the engine should own:**

- `world_premise` — at minimum `genre` (题材+基调+钩子). **我在哪 / 什么世界.**
- `protagonist` — at minimum `name` (+ ideally `origin`). **我是谁.**

**`objective` is NOT required input.** Grounded in TRPG design (Gnome Stew
"Anatomy of a Conspiracy"; "Problems, Not Plot"; CoC investigation structure):
the deep arc is the GM's secret, revealed through play, and forcing the player to
write it up front inverts the design. Instead "我要干嘛" is answered by the engine:

- **Surface objective** — `gen_protagonist` already authors `objective`, emitted
  as a public `目标` fact and shown on the opening screen. This is an **output
  guarantee**, not an input requirement.
- **Deep arc** — owned by `gen_threads` (the 1-2 protagonist-bound 暗线), advanced
  by 暗骰, surfaced through play. Never required from the player.

`missing_required(spec)` returns the labels of required parts whose minimal field
is empty. "Required" means **the agent must surface it and get a resolution** —
either a value, or an explicit delegate marker (model-fills). It is never
silently defaulted. It is enforced by session-zero; `bootstrap_world` itself
stays permissive (so `spec=None` still produces a full world — backward compat).

## Input sources

Division of labor: the **file** can define *any* part precisely; the
**interactive** session focuses on the *required floor* so a player can always
just answer 2-3 questions and go; **conversion** (P2) is another producer of the
same spec.

### (a) Blueprint file  — `--genesis PATH`
`load_blueprint(path) -> GenesisSpec` parses YAML or JSON (extension-sniffed;
YAML only if `pyyaml` present, else JSON) → `normalize`. Can specify any subset of
any part, including optional parts and list augments. Malformed file → clear
error printed, abort genesis (do not silently fall back — the user asked for it).

### (b) Interactive session-zero — `app/session_zero.py` (new)
`run_session_zero(spec, *, inputs, out, interactive) -> GenesisSpec`, behind the
same `inputs`/`out` seams as `play_loop`.

- For each label in `missing_required(spec)`, prompt; loop until the user gives a
  value **or** types a delegate token (`/auto`, `你来定`, empty-after-prompt) →
  mark that part for model-fill and stop asking it.
- Already-satisfied required parts: show a one-line confirmation, allow override.
- Optional parts are **not** walked here (use the file). Keep it tight.
- Non-interactive (scripted/no TTY): do not block; return spec unchanged
  (`bootstrap_world` model-fills the rest — preserves test/headless behavior).

### (c) Conversion layer — phase 2, `loop/import_sillytavern.py` (new)
`convert_sillytavern(provider, *, world_book=None, character_card=None) ->
GenesisSpec`. Reads raw ST JSON, uses `complete_structured` with our GenesisSpec
as the **target schema** to translate:

- **world-book** (`{"entries": {idx: {key, keysecondary, content, constant,
  comment, order, position, depth, probability, disable, ...}}}`): the free-text
  `content` of entries → `world_premise` (premise/conflict from constant/always-on
  entries), `factions`, `npcs`, `regions`, and `threads`. Lossy by nature; the LLM
  summarizes and structures, with our validate→repair gate enforcing shape.
- **character card** (V2: `name / description / personality / scenario /
  first_mes / character_book`): → `protagonist` by default (you play the card);
  a flag (`--card-as npc`) routes it to `npcs` instead. An embedded
  `character_book` is merged as a world-book.

Output is just a `GenesisSpec` — it plugs into the same resolution. No ST runtime,
no new event types.

## Resolution pipeline

`resolve_genesis_spec(...)` (in `app/engine.py` or a thin `app/genesis.py`):

```
spec = {}
spec = merge(spec, convert_sillytavern(...))   # P2: if --import-*           [base]
spec = merge(spec, load_blueprint(path))       # if --genesis PATH          [over conversion]
spec = run_session_zero(spec, ...)             # interactive: required-gate  [over file]
# → pass to bootstrap_world; generators model-fill every still-absent part  [lowest]
return bootstrap_world(engine, pitch, spec=spec, progress=...)
```

**Precedence: interactive > file > conversion > model-generated.**

**`merge(base, overlay)`** (unambiguous):
- **Scalars / nested objects**: overlay field replaces base field iff overlay's is
  present & non-empty; otherwise base is kept.
- **List parts**: **augment**. `regions / venues / neighbors / factions` →
  concatenate base+overlay then dedup by normalized `name` (lower, stripped),
  base order first. `npcs / threads` (no stable name) → pure concatenation, no
  dedup.

**`pitch` integration:** `bootstrap_world` keeps its `pitch` arg; internally it
seeds `spec.world_premise.genre = pitch` only when the spec doesn't already set
genre. So `--pitch X` with no spec ≡ today.

## Generator override — per-step contract

Each generator gains a keyword `provided=None`. Behavior when `provided` is falsy
is **identical to today**.

- **`gen_frame(provider, oracle, pitch, *, provided=None)`** — genre =
  `provided.genre or pitch`; tone = `provided.tone or roll`; n_factions/n_regions
  = provided or roll; world_name/central_conflict = provided or author. (When only
  `genre` is provided and equals `pitch`, the path is byte-identical to today.)
- **`gen_regions`** — n = `max(len(provided.regions), frame.n_regions)`; provided
  fill regions[0..k-1] (regions[0] = start); terrains: provided.terrain or
  `_draw_distinct`; names/seeds: provided or author top-ups; star graph spans n.
- **`gen_local_map`** — town/venues/neighbors: provided values win; venues &
  neighbors augment to `max(provided, rolled)`. `venue_names` map still built.
- **`gen_protagonist`** — name/origin/goal/objective: provided wins per-field;
  authors only the empty ones. (Surface `objective` still authored if absent.)
- **`gen_factions`** — count = `max(len(provided), n_factions)`; provided kept
  (distinctness check still applies); author the rest.
- **`gen_npcs`** — count = `max(len(provided), rolled)`; provided NPCs keep their
  sketch/goal/secret (a provided `secret` still emits a `secrecy=secret` fact);
  engine still rolls roles/traits/venue placement for the top-ups.
- **`gen_threads`** — split provided by `bound` into campaign vs protagonist;
  campaign count = `max(provided_campaign, rolled_n)`, protagonist count =
  `max(provided_protagonist, rolled_n_p)`; provided lines keep their authored
  fields; oracle still rolls complexity/threshold/stages for top-ups; `l3_anchor`
  for provided lines validated against real venues (fallback to first venue).
- **`gen_opening(provider, frame, world_summary, *, scene_loc, scene_loc_name,
  provided=None)`** — if `provided` (the opening string) is non-empty, use it
  verbatim (still emit the `narration_recorded` event); else author as today.

`bootstrap_world(engine, pitch="", *, spec=None, attempt=0, progress=None)` threads
`spec.<part>` into each generator. `spec` is stored in `_state` so
`reroll_all`/`reroll_step` reuse it (reroll re-applies the same provided parts;
only the model-filled/rolled portions change). Determinism guarantee becomes
**per-(campaign_seed, spec, attempt)** — same inputs → identical world (rewind/
reroll safe).

## CLI / UX

New flags on `python -m app`:
- `--genesis PATH` — blueprint file.
- `--card-as {protagonist,npc}` (P2, default protagonist).
- `--import-world-book PATH`, `--import-card PATH` (P2).

First-run flow (`app/__main__.py`): resolve spec from flags → if interactive and
required parts missing, run session-zero (ask-until-filled) → `new_game(engine,
pitch, spec=spec, progress=...)` → existing rich intro + reroll loop unchanged.
A `genesis.example.yaml` documents the file format.

## File structure

- **`loop/genesis_spec.py`** (new) — GenesisSpec model + `normalize` / `merge` /
  `missing_required`. Pure.
- **`loop/genesis_blueprint.py`** (new) — `load_blueprint(path)` (YAML/JSON →
  normalize).
- **`app/session_zero.py`** (new) — `run_session_zero` (interactive required-gate).
- **`loop/import_sillytavern.py`** (new, P2) — `convert_sillytavern`.
- **`loop/bootstrap.py`** (modify) — `provided=` on every generator; `spec=` on
  `bootstrap_world` / `reroll_*`; store spec in `_state`.
- **`app/engine.py`** (modify) — `new_game(..., spec=None)`; `resolve_genesis_spec`.
- **`app/__main__.py`** (modify) — new flags + session-zero call.
- **`docs/genesis-blueprint.md`** + `genesis.example.yaml` (new) — file format docs.

## Phasing

- **Phase 1 (working software): player-definable genesis.** spec model +
  required-gate + blueprint file + session-zero + generator override + bootstrap
  threading + CLI. Deliverable: define any part in a file or answer the required
  questions; the model fills the rest. ST not involved.
- **Phase 2: SillyTavern conversion.** `convert_sillytavern` + import flags. Plugs
  into the same resolution. Built after P1 (depends on the spec contract). Gets
  its own implementation plan.

## Testing strategy

- **Byte-identical baseline**: a test asserting that `bootstrap_world(spec=None)`
  produces the same event stream as before (the existing suite is the guard; add
  one explicit "spec=None == legacy path" determinism test per generator).
- **Per-generator override**: provided scalar wins; provided list augments to
  `max(provided, rolled)`; provided items always present; provided secret emits a
  `secrecy=secret` fact.
- **merge / normalize / missing_required**: unit tests incl. precedence and list
  augment/dedup.
- **session-zero**: required-gate loops until filled; delegate token stops asking;
  non-interactive returns unchanged; behind inputs/out seams (mirror
  `test_input_sanitize.py`).
- **blueprint loader**: YAML & JSON parse; malformed → error.
- **determinism**: same (seed, spec, attempt) → identical world; reroll reuses spec.
- **P2 conversion**: against a fixture ST world-book + V2 card, with a
  scripted/fake provider, asserting the produced spec shape (card→protagonist;
  entries→factions/npcs/threads); live probe with glm noted, not in the offline suite.

## Out of scope (YAGNI)

- Running ST lorebook injection semantics (keyword triggers, depth, probability)
  at play time. We translate once at genesis; we do not emulate ST.
- A GUI / web editor for the spec.
- Round-tripping our world back out to ST format.
- Persisting the resolved spec as a new event type (in-memory `_state` covers
  reroll; revisit only if reload-then-reroll is ever needed).
- Streaming (separately deferred).

## Decisions locked

- Native structured spec is canonical; ST is a translated import source (LLM
  conversion), not a runtime.
- Required floor = `world_premise` + `protagonist`; `objective` is a generation
  guarantee + engine threads, not required input.
- File = full power (any part); interactive = required floor only.
- Lists augment (user items always kept; model tops up to rolled count).
- `spec=None` path is byte-identical to today (suite stays green).
- Two phases: P1 player-definable genesis, P2 ST conversion.
