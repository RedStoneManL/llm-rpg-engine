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

Use `--pitch` to describe a world, `--flavor classic|isekai` for a content pack, and `--verbosity concise|medium|rich` or `--style` for narration. Player-defined openings use `--genesis`, `--import-card`, or `--import-world-book`; see [the genesis guide](docs/genesis-blueprint.md). Before creating a save, `python -m app --preview-genesis ...` checks those sources offline without requiring `--campaign` or calling a provider. In play, `/help` lists commands, including `/undo`, `/recall`, `/speak`, and `/quit`. `/recall` searches only material visible in the current protagonist's POV; guessing an internal id does not reveal it.

`/speak <当前公开同场角色> <逐字原话>` is the only CLI shortcut that can ask the
host to record that a particular NPC heard a particular sentence. It never extracts
dialogue from ordinary action prose: the named recipient must be publicly co-present
right now, and only the exact supplied sentence is eligible for that one turn's
receipt. It is not a world fact and does not authorize another NPC, a thought, or an
unselected part of the player input.

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

# Explicit, separate read-only GM server for the same local campaign.
# Switching player/GM mode requires restarting with the other command.
python -m app.gui --campaign ./campaigns/gui-demo --gm --port 8765

# Normal existing campaign: reuse the same provider selection used by the CLI.
# It does not create a demo world or send credentials through the browser.
python -m app.gui --campaign ./campaigns/my-game --provider openai --model <existing-model> --port 8765

# Explicit normal GUI creation: only a previously nonexistent path is accepted.
# Genesis requires a non-fake configured provider; this is not the fixed offline demo.
python -m app.gui --campaign ./campaigns/my-new-game --new-game --pitch "雾港的钟楼谜案" \
  --provider openai --model <existing-model> --port 8765

# or start a disposable temporary campaign (deleted on exit)
python scripts/demo_offline_gui.py --port 8765
```

It uses a fixed local demo strategy plus `FakeLLMProvider`, not a configured
provider, embedder, network service, or API key. On an empty campaign, `--demo`
seeds a replayable Stonebridge town with a protagonist, a held item, two
adjacent places, one public codex entry, and opening narration. That first seed
also writes a small campaign-local offline-demo marker: later `--demo` starts
may reopen only that marked demo, while an existing unmarked campaign is
rejected before any player action so the fixed demo strategy cannot run against
an ordinary saved world. The page can
save an action, show its narration/status, show the current character,
inventory, query recall only across the current protagonist's known POV-visible
material, show an interactive read-only map of the current POV-visible places and
routes (with the current place highlighted), visible adjacent exits, browse public world-setting
entries, and undo the latest turn. Refreshing or restarting the local server
reads the same saved campaign; action submission uses the existing explicit
action-id/revision path rather than a second GUI transaction.

When a campaign has configured numeric resources, the player GUI and CLI also
show the latest protagonist-owned **spent** or **insufficient** adjudication
with its durable turn, requested amount, and resulting balance. This is a
read-only rendering of the existing `resources_resolved` event, not a second
ledger or a natural-language claim inferred from narration.

The player form also has an optional **明确说出原话** section. Select a current public
co-present recipient and enter the exact one sentence to be remembered; both fields
are required together. The browser sends those separately from the normal action text,
the local host revalidates visibility before issuing one receipt, and the original
receipt is retained in the action-id recovery envelope so a cold retry cannot silently
change who heard what. Leaving the section empty keeps the previous behavior: ordinary
action text, thoughts, and narration never become NPC memory automatically.

The dashboard also offers an optional **后续叙事方向** field, equivalent to the CLI's
`//steer`. It is a local server-session preference for the next Author/Hybrid prompts,
not a fact, route, permission, event, transcript item, action receipt, or recovery
parameter. Saving or clearing it never consumes a turn; clearing it resets the author
conversation cache so an old preference cannot linger. A server restart clears it, and
an action-response retry restores the committed result without needing the old direction.

If a browser loses an action response after the engine has published it, the
page retains the original action-id, input, and revision in that browser's
session storage. After a refresh, **恢复上次请求** retries that same envelope
and receives the existing action result instead of making a new turn; only a
confirmed success clears it. Undo is likewise revision-bound, so an accidental
second click cannot retract another turn. Write routes require loopback Host,
matching Origin when sent, and JSON bodies; ordinary cross-site forms are
rejected before a controller runs.

Malformed or incomplete `200` action responses are treated as unconfirmed and
keep that envelope recoverable. A clearly validated server `400` rejection is
different: no turn was accepted, so the page clears the pending envelope and
lets the player edit and submit a new action after a refresh.

The client does not infer that safety merely from an HTTP status or a text
message. The server marks only its narrow, pre-publication validation responses
with `confirmed_rejection: true`; an unmarked 400/500 remains an unknown result
and keeps the original envelope recoverable.

The browser never selects a campaign path: `--campaign` is startup-only. The
server binds only to `127.0.0.1` and serves no files. In its default player
mode it has **no GM route or DTO** and serializes a hand-shaped POV projection,
not a raw event store or canonical world. Graph reads use the existing POV
projection, codex entries are public by design, and page code writes all
narration/settings with `textContent` so world prose and player input cannot
become executable markup.

The map, the author context, the demo strategy, and the GUI's pre-publication
move gate share one `SceneCapabilities` projection. For a player action, the
protagonist can only move along an action-start POV-visible adjacent route;
an invalid proposal must repair both its `moves` and its narration or the whole
action is rejected without publishing. The final event payload is checked again
inside the existing atomic publish path. A newly introduced destination is only
accepted when the same turn creates it and records a direct route from the
current location. NPC simulation and low-level world/bootstrap operations are
not governed by this player-UI policy.

`--gm` is an explicit, separately started, read-only local world-overview
mode—not a front-end tab or an authorization system. It exposes only
`/api/gm/state`; player state/action/undo routes are absent. The GM DTO reads
canonical projected ontology data to show the global place graph, people,
objects, facts, recap rows, and a timeline marked as objective facts, public
narration, recap, or retracted history. It never exposes private
`character_interpretation_recorded` rows, does not add them to player or model
context, and has no edit, delete, advance, or player-action controls. Hidden
places and world events may appear there by design, but never in the default
player server. It also reuses the public-history reference boundary: if an
ordinary narration or recap row references a private-memory event, the GM
endpoint fails closed with a neutral result instead of returning either body.

The player GUI's `--demo` is intentionally the only fixed offline action
strategy. Without it, the GUI requires an already initialized campaign and
uses `AuthorStrategy` with the same `--provider`, `--model`, `--base-url`,
`--max-tokens`, and `--max-repairs` configuration shape as `python -m app`.
Provider initialization errors are shown as local setup guidance; the server
never silently falls back to the demo, and the browser never receives API keys
or provider configuration. This repository validates that wiring with injected
fake providers only; it does not claim live-model generation quality.

Normal player and non-demo GM starts require an already saved `events.db`: a
missing campaign path is rejected before SQLite is opened, so a mistyped
read-only launch does not create an empty campaign directory or store.  The
sole GUI exception is explicit `--new-game`: it accepts only a previously
nonexistent destination, runs the same normal genesis pipeline in a sibling
temporary directory, and publishes that directory only after genesis succeeds.
It never overwrites an existing campaign and does not select the fixed demo
strategy.  To avoid persisting fallback placeholders as a normal world,
`--new-game` rejects the default `fake` provider; use explicit `--demo` for the
fixed offline tour. Use `python -m app` for the fuller CLI session-zero/import workflow,
or explicitly choose an empty directory with `--demo` for the fixed offline
tour.

Both player and GM views include a read-only public world-book browser. It
searches existing Codex title/category/body text and renders details with
`textContent`; it creates or edits no setting entries. Categories are shown
only when the original Codex entry has one—there is no synthetic source label.

Interpretive memory remains disabled for this GUI; raw-store download,
DM-ground-truth tools, multi-user accounts, public deployment, and editing are
outside this slice. The explicit offline demo and GM read-only constructors
disable embedding even if `RPG_EMBEDDER` is set; normal GUI play retains the
existing environment-driven embedding behavior. Visibility follows the existing POV rules: the
protagonist/current location, public co-present people, discovered entities, and
legacy places without restrictive metadata can be visible. It does not claim
every legacy place requires a discovery event; event-owned gradual discovery
of legacy public places remains a future system-level step.

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
