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

Generic `fact_rules` can also constrain a predicate's type, min/max, enum or
immutability without registering it as a resource. Rules are enforced during
validation and projection, so a malformed batch cannot enter the durable store.

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
