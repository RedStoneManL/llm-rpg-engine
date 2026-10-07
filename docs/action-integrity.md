# Action integrity and world continuity

Implemented 2026-10-03. Entry point: `run-deepseek.sh`; existing `run.sh` is preserved.

One player action stages its resource resolution, narration and immediate world
hooks in an `EventBatch`. LLM calls run outside SQLite write transactions. Each
hook has a staging savepoint. The final publication checks the source revision,
replays the prospective history, and writes the complete batch and receipt in a
single transaction. The conversation advances after persistence. JSONL is a
repairable export of SQLite; `store.sync_jsonl()` rebuilds it.

`action_id` is a storage receipt key for an exact event batch. It is not a durable
HTTP request queue: a caller retrying a new model generation must use a new action
identity. There is no asynchronous worker scheduler in this change.

New immediate consequences share the player action's turn, including narration,
lore, density, director and catch-up. Rewind increments the revision and resets
the derived conversation. Old saves are readable; legacy turn groups are not
rewritten or retrospectively repaired.

## DeepSeek

Credentials can be supplied as `DEEPSEEK_API_KEY` in the environment, or loaded
from `${RPG_DEEPSEEK_ENV}` / `${XDG_CONFIG_HOME:-$HOME/.config}/llm-rpg-engine/deepseek.env`
(0600). The native provider uses
`deepseek-flash`, defaults to thinking disabled and scopes JSON output to
structured calls. Prose generation stays prose. Tool call groups retain the full
assistant protocol message. Set `DEEPSEEK_THINKING=enabled` to change thinking.

```sh
cd llm-rpg-engine
./run-deepseek.sh --campaign ./campaigns/my-new-adventure \
  --flavor isekai --pitch '轻快的异世界旅行，从普通人的生活开始'
```

The profile enables `RPG_RESOURCE_RULES=1` for new worlds. Set it to `0` before
creation to use the prior free-form resource behavior. Existing saves retain
their event-sourced configuration regardless of the current environment.

## Flavor extensions

All existing flavor tables and `voice` remain supported. A pack may override
`narrative_variations.json`, a list of `{id, hint, weight}` entries. Missing tables
fall back to `default/`. Set `pack.json`'s `variation.enabled` to false to disable
these hints. Sampling uses the campaign seed, action turn and location, excludes
the last two choices when possible and records `variation_sampled`. Rewinding
replays the draw. It cannot guarantee identical model text or literary quality.

Optional `pack.json` resources are numeric facts owned by the protagonist:

```json
{
  "resources": {
    "oxygen": {"initial": 100, "type": "integer", "min": 0, "max": 100, "label": "氧气"}
  }
}
```

When enabled at genesis, `resources_configured` installs these as
`entity.attrs.fact_rules` with `resource: true`. Before narration, a small
structured call interprets an explicit expenditure. Python checks affordability
and computes balances. A refused expenditure leaves them unchanged; subsequent
LLM proposals cannot debit them again. Explicit waiting deadlines are converted
to absolute day/band targets and validated before publication.

This first rule module covers explicit spending/consumption and waiting. It does
not implement a complete market, item ownership transfer, rewards, theft or
combat damage. Those need their own authorized effect resolvers; free-form
changes to a registered balance are rejected. Natural-language intent extraction
still needs evaluation even though arithmetic and persistence are deterministic.

The action gateway locks the pre-action balances of **all registered owners**,
including NPCs and worlds where the acting protagonist has no resources. Only
the protagonist's host-resolved expenditure updates that lock. Proposed fact
changes are repairable by removing the conflicting declaration; unchanged values
and unregistered facts remain allowed. Repair hints do not disclose the locked
balance. Before publication, the gateway replays the actual staged history and
checks every locked balance, even if a backstage hook omitted its event report.
A conflict rejects the whole action without advancing storage or conversation.
This guards structured balances at the action boundary; it does not establish
prose consistency, authorize NPC rewards/transfers, or retrofit historical saves.

Generic `fact_rules` can also constrain a predicate's type, min/max, enum or
immutability without registering it as a resource. Rules are enforced during
validation and projection, so a malformed batch cannot enter the durable store.

## Item transfer provenance

New turn proposals transfer items through `items`, for example
`{"op":"transfer","item":"umbrella","from":"A","to":"B"}`.
The item must be an `Object`; both an established source holder and destination
must be a `Person` or `Place`. An already-held item requires an explicit `from`
matching its holder immediately before that event. First placement of an unheld
item may omit `from` or use `null`; it cannot invent a previous holder.

Transfers are validated sequentially: A→B followed by B→C is valid, while a
second A→C is stale. Same-turn entity-creation sections are staged before items
regardless of JSON key order; the order of rows within `items` is preserved.
New `relations` declarations cannot write `held_by` directly,
and re-declaring an item or holder as an incompatible entity type cannot evade
these checks. The prompt teaches the same schema. Errors can be repaired before
publication; exhausted repairs reject the whole foreground action. A failing
backstage hook loses its staged writes through the existing hook savepoint.

Both `run_turn` and direct `apply_turn` publication check new events against the
prospective history. Historical events remain on the original replay path, so
old saves do not acquire a new mandatory `from` field. Trusted imports/seeding
that append historical events directly are not a substitute for the turn gateway.
Reopen and rewind reconstruct the same single-holder history.

`from` proves a state precondition, **not permission or consent**. A gift, theft,
or handover involving another character can still describe that character as the
true source. The validator does not decide whether the transfer ought to happen,
or prove the prose matches it. Borrow/return promises and due dates remain open.

## Memory and information boundaries

The narrator and player tools use a filtered graph before searching. Explicit
hidden entities require discovery (`discovered_by` or `knows:<id>.discovered`).
Co-presence exposes observable character facets, not unknown private goals.
Private facts may be replaced by the observer's belief. Self-observable state
uses current facts rather than a stale knowledge copy. Player tools cannot switch
into an NPC's private POV; the DM registry is separate.

Legacy unmarked place topology remains public for compatibility. The system does
not prove arbitrary summaries or generated prose are free of all semantic leaks.

Every turn refreshes world context and date-stamped fact anchors. The conversation
cache keeps eight recent exchanges. Long scenes split into six-turn recap chunks;
aged chunks are summarized and recompressed with the existing summary included.
Events retain the exact original prose. Summaries remain fallible derived memory.

The narrator now receives a host-bound `actor_id` and a small canonical inventory
block after POV filtering. It includes only visible self/local holdings, never
infers an unheld item from a redacted/missing relation, and prioritizes `held_by`
over free-form ownership aliases. Descriptive object facts and old events remain
intact. The author examples use the actual actor ID rather than a fixed
`protagonist` placeholder; both narrator strategies rebuild cached context when
the actor changes. The lore hook receives the action's actor explicitly.

Shipped narrators reject a missing actor before resource interpretation or any
narrator call. In a populated ontology the actor must be an existing Person;
empty-world author/bootstrap fixtures can still declare their first named actor.
Generic DM/recap context assembly and custom-strategy contracts are unchanged.

Explicit `held_by` visibility survives both legacy `relation_added` and current
`item_transferred` replay paths, including reopen. A flat `visibility` or nested
`attrs.visibility` may specify public/hidden/secret; restrictive conflicts and
malformed values fail closed. Omission preserves the legacy unmarked-visibility
convention. This changes neither physical ownership nor stored historical events.

Prompt metadata and repair diagnostics are explicitly separated from prose.
This is generation guidance, not a keyword ban or a proof that arbitrary
narration, summaries, or invented ownership aliases are semantically correct.

Malformed or empty narration, leaked JSON envelopes, incomplete required
sections and obvious repetition of recent long narration are repaired or refused
before display. These checks do not prove all prose agrees with world facts.

## Verification

Relevant regressions: `tests/test_action_integrity.py`,
`tests/test_world_continuity.py`, `tests/test_resources.py`. The independent
implementation report and real API evidence are in
[`reports/2026-10-03/report.html`](reports/2026-10-03/report.html) and its `evidence/`
directory. Cross-environment setup and the remaining work are documented in
[`HANDOFF.md`](HANDOFF.md).

Original fake-provider fixtures now declare all required turn sections; fixtures
that intentionally use stub genesis explicitly mark themselves offline. A real
provider failure cannot publish a stub world or erase a previous reroll target.
