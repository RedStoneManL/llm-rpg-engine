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

### Offline private-memory experiment

`python -m app --campaign ./campaigns/demo --provider fake --offline-private-memory`
enables a deliberately narrow offline experiment. It registers an otherwise-disabled
private interpretation system and uses one fixed local text generator; that private
generation does not call a model, embedder, or network service. It does not override
the foreground `--provider` or embedder configuration. `/memory` can then show only
the current protagonist's own interpretations. The records persist across a restart
made with the same flag and disappear with the whole turn after `/undo` or `/rewind`.

This is not world fact, permission, recall data, recap material, public history, or
model context. The flag is required on every process that should read the experimental
private slice; without it `/memory` stays unavailable and normal play behavior is
unchanged. It has no automatic retry or concurrency behavior.

### Offline CLI status tour

Run the fixed, no-network walkthrough:

```bash
python scripts/demo_offline_cli_status.py
```

It uses a temporary campaign, fake providers, and explicitly fixes its own embedder
to `none`, so it neither needs credentials nor changes an existing save.  The output demonstrates the default disabled `/memory`
message, explicit private-memory activation, three `[已保存]` turn confirmations,
one safe `[摘要] 暂未生成；本回合已保存。` recap failure, `/memory`, `/undo`, and a
reopened session.  A pending recap is public narrative maintenance only: the player
turn is already durable, and the engine may try that aged scene again during a later
turn.  It is not a background job, a promise that a summary exists, or a retry of the
player action.  Real play still requires a configured foreground provider; this tour
does not exercise a real model, embedding, network, compare mode, or concurrent retry.

### Local player dashboard (offline first slice)

The repository also includes a deliberately small, loopback-only player GUI:

```bash
python -m app.gui --campaign ./campaigns/gui-demo --demo --port 8765
# open http://127.0.0.1:8765 in this same machine's browser

# or start a disposable temporary campaign (deleted on exit)
python scripts/demo_offline_gui.py --port 8765
```

It uses a fixed local demo strategy plus `FakeLLMProvider`, not a configured
provider, embedder, network service, or API key. On an empty campaign, `--demo`
seeds a replayable Stonebridge town with a protagonist, a held item, two
adjacent places, one public codex entry, and opening narration. The page can
save an action, show its narration/status, show the current character,
inventory, location and visible adjacent exits, browse public world-setting
entries, and undo the latest turn. Refreshing or restarting the local server
reads the same saved campaign; action submission uses the existing explicit
action-id/revision path rather than a second GUI transaction.

If a browser loses an action response after the engine has published it, the
page retains the original action-id, input, and revision in that browser's
session storage. After a refresh, **恢复上次请求** retries that same envelope
and receives the existing action result instead of making a new turn; only a
confirmed success clears it. Undo is likewise revision-bound, so an accidental
second click cannot retract another turn. Write routes require loopback Host,
matching Origin when sent, and JSON bodies; ordinary cross-site forms are
rejected before a controller runs.

The browser never selects a campaign path: `--campaign` is startup-only. The
server binds only to `127.0.0.1`, serves no files, has no GM/editor route, and
serializes a hand-shaped player DTO rather than a raw event store or canonical
world. Graph reads use the existing POV projection, codex entries are public
by design, and page code writes all narration/settings with `textContent` so
world prose and player input cannot become executable markup. Interpretive
memory remains disabled for this GUI; private interpretations, raw store data,
DM-ground-truth tools, and other-character private data are outside this
first slice. It is an offline player demo, not a full web product or a GM UI.
The dashboard explicitly disables embedding even if `RPG_EMBEDDER` is set;
ordinary application construction still retains its environment-driven default.
Visibility follows the existing POV rules: the protagonist/current location,
co-present people, discovered entities, and legacy places without restrictive
metadata can be visible. It does not claim every legacy place requires a
discovery event.

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
