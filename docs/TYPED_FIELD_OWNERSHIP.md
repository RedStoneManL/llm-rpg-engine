# Typed field ownership on new fact writes

Systems can register immutable declarations for exact `(entity_type, predicate)`
pairs. The initial declarations are Object.held_by (canonical relation, repair
via items) and Person.located_in (canonical relation, repair via moves).
Duplicate declarations fail registration. This is a canonical representation
contract, not a ban on natural-language ownership descriptions.

The actual DS T3 failure wrote `brass_bell.held_by = keeper` as a generic fact
while its canonical holder remained player. Person.located_in is a separately
source-confirmed collision: a generic fact does not move the relation read by
actor location and co-presence. It is not reported as a second observed DS error.

## Enforcement

A scoped guard observes actual `FactGraph.assert_fact` calls for new event IDs.
It therefore covers generic facts, character evolution, dynamic resource facts,
and other event owners using the same primitive, rather than checking only one
narrator section. It rejects a shadow fact even if its value currently matches
the canonical relation: independent lifetimes can later diverge.

Full-batch creator declarations provide exact prospective types without changing
event order. A prior/current type cannot hide a reserved new type later in the
same proposal. IDs, values and natural-language synonyms never infer a type.
Commit validation uses a private preview; configured-engine raw append and
normal/prepared/backstage staged publication share the new-event check.

Ordinary projection remains permissive. Already durable events are exempt from
the new-write check, including old shadow facts. No old source event is edited
or migrated. Bare raw EventStore/FactGraph usage remains a trusted import/replay
API; this is not an adversarial Python sandbox or an access-control system.

## Repair and compatibility

A namespace error identifies its owning section. Once other domain errors are
resolved, the existing repair budget allows the model to revise the source
section and that owner section together. Previously validated owner rows must
remain an identical ordered prefix; a failed repair/fallback cannot discard
them. An early failure in another domain is not treated as proof that unvisited
owner rows passed.

No fact value is automatically converted into a transfer or movement. New
proposals still pass normal preconditions and the semantic publication gate.
Deleting the incorrect fact is allowed; that alone does not prove the prose is
consistent or the player's intended action succeeded.

Existing canonical generic `relations.located_in` writes remain valid. Ordinary
descriptions, legal owner, borrowed_from and unregistered predicates are not
reserved. Knowledge beliefs use their own prefixed predicates and remain
distinct from canonical physical state. Clock fields live in meta, so arbitrary
entity day/band predicates are not reserved. No movement-route rule is added.

## Actual model evidence

Ten fresh Astra responses exercised three cases through the actual repair,
reconciliation and semantic publication flow:

- The recorded DS T3 proposal was replayed as recorded input, not fresh author
  generation. One coupled repair removed the shadow fact and proposed the
  player-requested player→keeper transfer. Reconciliation and audit followed;
  three responses, one committed transfer, no dropped sections.
- A constructed Person.located_in misuse demonstrated the source-derived second
  domain. Fresh repair used moves instead of a fact; the actor reached courtyard.
  Three responses, no dropped sections. This was not an observed DS failure.
- A fresh normal play-loop action asked for visual inspection without handing
  over the bell. Four responses including one recall tool produced descriptive
  facts and two knowledge records, with no repair, transfer or dropped section.

State routing success is not prose fidelity. The historical case's whole-turn
reconciliation shortened the story, changed keeper pronouns from 她 to 他, and
omitted the visitor beat. The young_porter entity and its descriptive sketch
still committed; its original presence lacked a moves record. These residuals
are retained, not presented as complete lore cleanup.

Focused host checks also exercised raw configured writes, character evolution,
resource fact writes, facts-before-create, legacy replay and legal lore/belief
controls. They are machine-contract checks, not additional model gameplay. An
initial standalone replay-adapter startup error occurred before any model
response; it was corrected without changing production source. No DeepSeek call
or broad offline suite was run. Exact namespaces do not classify arbitrary
semantic synonyms or unrestricted metadata.
