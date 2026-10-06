# Genesis Blueprint — player-defined world openings

Define any part of the opening world yourself; the model fills the rest. A
blueprint is a JSON or YAML file passed with `--genesis PATH`. See
[`genesis.example.yaml`](../genesis.example.yaml) for a complete, commented
template.

Design spec: [`docs/archive/superpowers/specs/2026-06-23-player-definable-genesis-design.md`](archive/superpowers/specs/2026-06-23-player-definable-genesis-design.md).

## The required floor

Two things must be resolved before a world is generated:

- `world_premise.genre` — what kind of world this is (**我在哪**). Also settable
  via `--pitch` or the interactive prompt.
- `protagonist.name` — who you are (**我是谁**).

If you omit a required field and run interactively, the engine **asks for it and
keeps asking until you answer** — or you type `/auto` (or `你来定`, or just press
enter) to let the model decide that part. Everything above the floor is optional:
omit it and the model invents it.

"我要干嘛" is intentionally NOT required input: the engine always generates a
concrete opening objective and shows it on screen, and the deeper arc is carried
by the hidden threads (暗线) — you never have to pre-write the conspiracy.

## The parts

| Part | Shape | Notes |
|------|-------|-------|
| `world_premise` | object | `genre`(req), `tone`, `world_name`, `central_conflict`, `n_regions`, `n_factions` |
| `regions` | list | `regions[0]` is the START region. `{name, terrain, seed}` |
| `local_map` | object | `town{name,seed}`, `venues[{name,seed}]`, `neighbors[{name,kind,seed}]` |
| `protagonist` | object | `name`(req), `origin`, `goal`, `objective` |
| `factions` | list | `{name, motivation}` |
| `npcs` | list | `{sketch, role, goal, secret}` — `secret` is stored `secrecy="secret"` (fog never leaks it) |
| `threads` | list | hidden 暗线: `{about, description, trigger, secret, l3_anchor, stages[str], complexity, bound}` |
| `opening` | string | verbatim opening prose; else authored |

## How sources combine

A spec is assembled from, in increasing precedence:

1. **pitch** (`--pitch` / env / interactive prompt) → seeds `world_premise.genre`.
2. **SillyTavern import** (`--import-world-book` / `--import-card`) → LLM-translated into the spec (see below).
3. **blueprint file** (`--genesis`).
4. **interactive session-zero** → fills/confirms the required floor.

Then the model fills everything still absent at bootstrap.

**Merge rules:**
- **Scalars** (`world_premise.*`, `protagonist.*`, `local_map.town.*`, `opening`):
  a later source's non-empty value wins.
- **Named lists** (`regions`, `factions`, `local_map.venues`, `local_map.neighbors`):
  **augment** — your items are kept (deduped by name), and the model tops the
  list up to its own rolled count.
- **`npcs` / `threads`** (no stable name): appended as-is (no dedup).

So providing two regions when the engine rolls four gives you your two **plus**
two model-authored ones; providing six when it rolls four gives all six.

## threads.bound

Each thread is either a `campaign` line (anchored at the start town) or a
`protagonist` line (anchored to you, the 1–2 personal 暗线). Set `bound:
protagonist` to route a line to the protagonist set; omit it (or `campaign`) for
a world line. A provided `l3_anchor` must name one of your venues; an unknown
anchor is auto-repaired to the first venue.

## Importing from SillyTavern (酒馆)

`--import-world-book PATH` and `--import-card PATH` ingest SillyTavern
world-books / character cards. These are **LLM-translated into this spec** — the
engine does not run SillyTavern's keyword-injection semantics; it reads the
free-text once at genesis and produces structured parts.

- `--import-card` → the protagonist by default; `--card-as npc` imports it as an NPC.
- `--import-world-book` → `world_premise` / `factions` / `npcs` / `threads`.
- Imports merge UNDER a `--genesis` file and your interactive answers (those win).
- Offline (no provider) only the card's name/description are extracted.

## Example

```bash
PYTHONPATH=. python3 -m app --campaign ./mygame --provider zhipu \
    --model glm-5.1 --base-url https://open.bigmodel.cn/api/coding/paas/v4 \
    --genesis genesis.example.yaml
```

YAML requires `pyyaml`; JSON blueprints work with the stdlib alone.
