# llm-rpg-engine

**English** | [中文](README.zh-CN.md)

An LLM-driven RPG engine for exploring a persistent world. The model narrates and proposes changes; a deterministic harness maintains world facts, knowledge, time, memory, and replayable events.

The October 2026 implementation adds atomic player actions, full-action rewind, POV read views, bounded conversation and recap memory, fact indexes, a native DeepSeek profile, seeded flavor variations, and optional resource rules. Existing world bootstrap, factions, hidden storylines, background hooks, and multi-genre packs remain extensible.

**Start here:** [development handoff](docs/HANDOFF.md) · [implementation contract](docs/action-integrity.md) · [visual report](docs/reports/2026-10-03/report.html). Open the HTML report locally after cloning to see the diagrams, comparisons, and real play transcripts.

## Quickstart

Python 3.10+, Bash, and SQLite support are required.

```bash
git clone https://github.com/RedStoneManL/llm-rpg-engine.git
cd llm-rpg-engine
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine"
cp deepseek.env.example "${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine/deepseek.env"
chmod 600 "${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine/deepseek.env"
# Edit that file and set your own DEEPSEEK_API_KEY.
./run-deepseek.sh --campaign ./campaigns/my-adventure --flavor isekai
```

Alternatively, supply `DEEPSEEK_API_KEY` in the environment or point `RPG_DEEPSEEK_ENV` at a configuration file. The default model is `deepseek-flash`; `DEEPSEEK_MODEL` overrides it. `./run-deepseek.sh --help` works without credentials or network access.

An empty campaign directory starts a new world; an existing one resumes it. The original Zhipu Coding Plan launcher remains available as `./run.sh`, with `.env.local.example` for configuration. Provider keys and personal saves are not included in the repository.

Use `--pitch` to describe a world, `--flavor classic|isekai` for a content pack, and `--verbosity concise|medium|rich` or `--style` for narration. Player-defined openings use `--genesis`, `--import-card`, or `--import-world-book`; see [the genesis guide](docs/genesis-blueprint.md). In play, `/help` lists commands, including `/undo`, `/recall`, and `/quit`.

## Architecture

```text
player action + current POV facts + memory + seeded variation
  -> optional resource / wait intent resolution
  -> model narration and structured proposals
  -> required-section validation and repair
  -> staged narration, facts and immediate background consequences
  -> revision check + replay preflight + one SQLite transaction
  -> committed world and conversation
```

SQLite events are the durable source of truth. JSONL is a rebuildable export. Model calls do not hold database write locks. Immediate hooks share the player's root turn, so rewind covers a complete action. Source revisions reject stale writes; receipts identify exact event batches, not retried model requests.

The `ContextSystem` registry retains domain boundaries and adds a resource system. Flavor packs define voice, oracle tables, weighted variation hints, and optional numeric resources. The narrator's tools search a POV-filtered graph; explicit hidden entities require discovery. Events retain original prose, while summaries and recent chat are derived memory.

Background simulation is currently synchronous with player actions. There is no autonomous offline server tick or asynchronous task queue.

## What was verified

- **1,712 passing tests**, with one slow test excluded by the default configuration (2026-10-03).
- **120 deterministic actions**, including 12 injected write failures, 10 full rewinds and salt replays, and eight reopens.
- **Seven existing saves** replayed with equivalent entity/fact/relation history; originals were unchanged.
- **Two real DeepSeek campaigns, 16 actions, 75 API calls** in the final iteration: 16/16 persistence/replay checks, 8/8 resource expectations, and 2/2 explicit wait targets passed. Prior failed iterations are preserved in the report.

These checks do **not** establish full narrative consistency. Manual review still found invented prior details, confused item possession/dates, and prose transactions inconsistent with a refused expenditure. Current resource rules cover explicit spending and consumption; rewards, income, theft, item transfers, and a complete economy require further effect resolvers. Legacy unmarked locations remain public for compatibility, and arbitrary prose or summaries are not proven leak-free.

## Tests and continued development

```bash
python -m pip install pytest numpy
python -m pytest
python scripts/verify_world_continuity.py --output-dir ./campaigns/endurance-check
```

The default tests use fake/scripted providers and do not call paid models. Additional developer tooling is listed in `requirements-dev.txt`; diagram generation may require system Graphviz.

| Directory | Responsibility |
|---|---|
| `app/` | CLI, engine assembly, play and rewind |
| `kernel/`, `systems/` | Domain registry, validation, event projection |
| `engine/`, `facts/` | Atomic event store, oracle, indexed facts |
| `loop/` | Turn staging, resource intent, variation, backstage hooks |
| `llm/`, `context/`, `memory/` | Providers, POV tools, context and memory |
| `data/oracles/` | Genre packs and weighted tables |
| `tests/`, `scripts/` | Regressions and portable fault/recovery exercise |
| `docs/` | Contracts, architecture, handoff and evidence |

The [handoff](docs/HANDOFF.md) records concrete next steps and acceptance gates. The [module index](docs/MODULE_INDEX.md) maps the established engine; the [action-integrity contract](docs/action-integrity.md) covers the new mechanisms and their limits. The October 3 report and its evidence are historical records; paths and source hashes inside raw evidence refer to that experiment environment.

## License

[MIT](LICENSE) © 2026 Xingyu Liu
