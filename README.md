# llm-rpg-engine

**English** | [中文](README.zh-CN.md)

**An event-sourced, LLM-driven tabletop-RPG (跑团 / TRPG) engine — a living world that the model narrates and the harness keeps honest.**

The core idea: give the LLM **full narrative autonomy**, but frame it with a **deterministic harness** — an append-only event log, seeded hidden dice, fog-of-war that the model physically cannot violate, and a strict commit/repair gate. The model writes the story; the engine guarantees the world stays consistent, replayable, and rewindable.

> Status: **v0.2** — a complete playable loop (`define or roll a world → play turns → the world reacts → endgame → rewind`), with **player-definable genesis** (blueprint file / interactive session-zero / SillyTavern import) layered over the model-filled default. ~1540 offline tests, validated live against a reasoning LLM (GLM / zai `glm-5.1`).

---

## What makes it different

- **Event-sourced truth.** Every change is an append-only event; the world is a pure projection of the log. Replays are byte-identical, so you can `rewind` any number of turns and the entire world (facts, relationships, NPC trust, quests) rolls back deterministically.
- **A living world, not a script.** Hidden per-turn seeded dice (暗骰) advance background storylines, ripple region-level events down through nested places (波状传播), and let off-screen plots brew on the world clock — even when the player isn't looking.
- **Harness-enforced fog-of-war.** The narrator queries the world through **read-only POV tools** that *physically can't return* what the point-of-view character doesn't know. A secret can't leak because the tool never surfaces it — not because the prompt asked nicely.
- **The model writes story; the engine owns structure.** Oracle rolls decide *counts, complexity, and structure* (deterministic, rewind-safe); the LLM only writes the *prose and content*. This is the cure for mode-collapse — concrete, distinct, dice-given seeds the model riffs on.
- **Strict commit gate.** Each turn the model returns narration + a structured commit; a validator bounces malformed output back to the model (field-by-field, naming what's missing) until it conforms, then explodes it into events.

---

## A taste

A world bootstrapped from the pitch `东方武侠悬疑` (oriental wuxia mystery) — the oracle rolled the skeleton (a region graph, factions, NPCs with secrets, hidden storylines), the model authored the world and this opening:

> **沉疴渡·茶棚**
> 泥腥味先于一切涌进你的鼻腔。你踩上沉疴渡码头的最后一块跳板时，脚下的腐木发出一声闷响……碧落十三泽的水是浑浊的铁锈色，岸边浮着一层油膜般的光泽。正前方支着一间茶棚，四根柱子歪了三根。棚下坐着稀稀落落几人——靠东角一个灰衣老者正低头拨弄一只粗陶药罐，他的左手只剩三根指头，断口处的疤痕已经发白。

NPCs come pre-loaded with hidden secrets (stored as `secrecy="secret"` facts the passerby tier can never leak), e.g. *"他是蚀骨城前城主的遗孤，隐姓埋名只为查明当年满门被屠的幕后真凶。"*

---

## Architecture

```
player input ─► AuthorStrategy ──► LLM (with POV fog tools) ──► narration + structured commit
                     │                                                   │
                     ▼                                          validate / repair gate
              assembled context                                         │
              (fog-filtered)                                            ▼
                                                          to_events ─► append-only EventStore
                                                                        │  (SQLite + JSONL mirror)
                                                                        ▼
                                                              project()  ──► World
                                                          (pure fold over events)
                                                                        │
        ┌───────────────────────────────────────────────────────────────┤  post-turn hooks
        ▼            ▼            ▼            ▼            ▼             ▼  (hidden, seeded, non-fatal)
   digest/arc   暗骰 director  §10 cascade   catch-up    lore 暗骰   density-gen
                                                                     world clock advances
```

- **Microkernel + ContextSystem registry.** The kernel knows nothing about RPGs. Each subsystem is a `ContextSystem` that declares the event types it owns, how it folds events into its slice of the world, how it validates a commit section, and how it injects/recalls context. Registered systems: `ontology` (a bitemporal fact graph), `place`, `character`, `object`, `faction`, `knowledge`, `director`, `cascade`, `time` (world clock), `narrative`, `scene`, `lore` (the unified questline).
- **Bitemporal fact graph.** Facts carry valid-time (game days) × transaction-time (event order), so "what was true on day 12" and "what does Alice *believe*" are both first-class. Knowledge is just the knower's own fact (`knows:{key}`), which is what makes source-side fog possible.
- **Unified questline.** One quest model with `state ∈ {暗 hidden, 明 surfaced, 了结 resolved}`. Hidden lines are advanced by the engine's 暗骰; surfaced lines by the player + narrator. Lines have game-time lifespans, dormancy when the player is away, and bounded "world-rescue vs catastrophe" endgames for the big ones.

---

## Quickstart

Requirements: Python 3.10+. No heavy deps for the core (stdlib + a thin OpenAI-compatible HTTP client); see `requirements.txt`.

```bash
# 1. install (a venv is recommended)
pip install -r requirements.txt

# 2. set your LLM key (GLM / zai, OpenAI-compatible). .env.local is gitignored.
cp .env.local.example .env.local
$EDITOR .env.local          # set ZHIPU_API_KEY=...

# 3. run — bootstraps a fresh world on first launch, then drops you into play
./run.sh
# or directly:
PYTHONPATH=. python3 -m app --campaign ./campaign --provider zhipu \
    --model glm-5.1 --base-url https://open.bigmodel.cn/api/coding/paas/v4
```

Bias the opening world with a pitch:

```bash
RPG_BOOTSTRAP_PITCH="东方武侠悬疑" ./run.sh
```

In-play OOC commands: `/recall <q>` (search memory), `/rewind <N>` / `/undo` (roll back), `/compare on|off` (dual-strategy A/B), `/help`, `/quit`.

Want to run fully offline (no API key) to explore the mechanics? The test suite uses deterministic fake providers — `PYTHONPATH=. python3 -m pytest -q`.

---

## How a turn works

1. **Assemble context** — fog-filtered: the narrator sees the scene, what the protagonist knows, and ambient public knowledge; hard secrets are withheld at the source.
2. **Author** — the model writes narration and (via native function-calling) may call read-only POV tools (`map_query`, `recall_query`, `characters_query`, `factions_query`, `ambient_query`) before committing.
3. **Validate / repair** — the structured commit is checked section-by-section; failures bounce back to the model until it conforms.
4. **Apply** — sections explode into events, appended to the store; the world is re-projected.
5. **The world reacts** (hidden, seeded, each non-fatal): backstage digest, 暗骰 director, region→sub-place cascade, off-screen catch-up, lore 暗骰 advancement, density-driven new storylines, and the world clock advances.

Everything is deterministic given the campaign seed + event log, so `/rewind` re-projects an earlier world exactly.

---

## World bootstrap (the opening)

`new_game` runs a semi-interactive **slot-machine** genesis: the oracle distinct-draws from small, extensible dimension tables (`data/oracles/genesis/`) to fix the *structure* — a macro region-adjacency graph (pinned so the geography can't drift later), a starting town with venues, factions, opening NPCs, and 3–5 campaign storylines (+1–2 bound to the protagonist) — and the LLM authors the *content*. You can reroll the whole thing or individual leaf steps before play begins.

### Player-definable genesis

Every genesis part is **definable** — and whatever you don't define, the model fills. There is one canonical `GenesisSpec` that `new_game` consumes; sources merge into it (precedence: interactive > file > import > pitch):

- **Blueprint file** (`--genesis world.yaml|json`) — specify any subset of any part (`world_premise / regions / local_map / protagonist / factions / npcs / threads / opening`). Scalars override; lists *augment* (your items are kept, the model tops up to the rolled count). See [`genesis.example.yaml`](genesis.example.yaml) and [`docs/genesis-blueprint.md`](docs/genesis-blueprint.md).
- **Interactive session-zero** — the engine asks for the minimal required floor (`world_premise.genre` + `protagonist.name` — "what world / who you are") and keeps asking until each is answered or you type `/auto` to delegate it. "What you're doing" is *not* required input: the engine always generates a concrete opening objective on screen, and the deeper arc lives in the hidden threads (暗线).
- **SillyTavern import** (`--import-card card.json` / `--import-world-book wb.json`, `--card-as protagonist|npc`) — an **LLM translation layer** that converts 酒馆 character-cards / world-books *into* the native spec. The engine doesn't run SillyTavern's injection semantics; it reads the free-text once at genesis and produces structured parts.

```bash
# define a world in a file; the model fills the rest
./run.sh   # or: python -m app --campaign ./mygame --provider zhipu --model glm-5.1 \
           #            --base-url <url> --genesis genesis.example.yaml
```

---

## Determinism, fog, and observability

- **Determinism / rewind** — all dice are `Oracle(scene_seed(campaign_seed, key, salt))`; no wall-clock or RNG in the replay path. Rewind = retract events + re-project.
- **Knowledge tiers** — three lenses over the same graph: **POV** (a specific agent's `knows`), **public/ambient** (what a random passerby could relay — `secrecy=="public"` only, structurally barred from secrets), and **DM** (full ground truth, authoring-only).
- **Observability** — run with `--debug` (or set `RPG_DEBUG_TRACE=/path/trace.jsonl`) to record a structured, langgraph-style trajectory (every LLM call's prompt/output/usage, every hook span, every event) to JSONL. Inspect with the agent-friendly `python -m app.trace <file>` viewer: a compact index by default, `--show SEQ` for one node's full prompt+output, plus `--turn/--phase/--grep/--tree/--stats`. See [`docs/debug-mode.md`](docs/debug-mode.md).
- **Tunable narration** — `--verbosity concise|medium|rich` (or `/verbosity` mid-session) dials how terse vs lavish the DM is. The opening generates a full intro: an authored protagonist (name / 身世 / goal), the current locale (region + town + venues), the world backdrop, and a concrete starting objective.

---

## Project layout

```
app/        CLI entry (python -m app), engine wiring, play loop, world bootstrap
kernel/     microkernel: event store, projection, registry, validation, recall, observability
systems/    the ContextSystems (ontology/place/character/faction/knowledge/director/
            cascade/time/narrative/scene/lore)
facts/      the bitemporal fact graph
loop/       per-turn pipeline: turn, strategy, the backstage hooks, bootstrap
llm/        provider (OpenAI/zhipu/anthropic/fake), POV tools, structured-output harness
memory/     recall / importance / reflection
context/    context assembly
engine/     oracle, embeddings, logging
data/       oracle tables (default + genesis dimension tables)
docs/       design specs & implementation plans (the architecture, decision-by-decision)
tests/      ~1540 offline tests (deterministic) + live-LLM probes
```

The `docs/superpowers/specs/` and `docs/superpowers/plans/` directories document the design and the decisions behind each subsystem — start there to understand *why* it's built this way.

---

## Testing

```bash
PYTHONPATH=. python3 -m pytest -q          # ~1540 offline tests, deterministic, no network
```

Offline tests use fake/scripted providers so the whole engine is exercised without an API key. Live behavior (does a real reasoning model call the tools, keep secrets, generate a coherent world?) is validated by probe scripts under `docs/`.

---

## Status & roadmap

**v0.2** — complete core loop + structured debug tracing + tunable narration + **player-definable genesis** (blueprint file / interactive session-zero / SillyTavern world-book & character-card import; the model fills whatever you don't define), validated offline (~1540 tests) + live. Next: optional streaming; world-impact "push" surfacing for the director; tuning the living-world numbers through real play.

## License

[MIT](LICENSE) © 2026 Xingyu Liu
