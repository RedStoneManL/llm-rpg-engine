# Source-backed first tracking

`items.materialize` represents a particular scene component that an earlier,
currently visible fact already establishes, but which has no Object ID yet.
It is not physical creation, a gift, ownership, permission, or a completed
pickup. The initial `held_by` Place denotes physical scene placement, including
a described attached component; it is not a mechanical attachment simulation.

The author selects an exact host-provided `source_ref`, `source_digest`, and
unique `source_quote`, a fresh `id`, and
`initial: {kind: "scene_component", place: "existing-place-id"}`. A subsequent
actual pickup uses a separate ordinary `transfer`, with that Place as `from`.
The old `create` plus null-source first placement remains endpoint-only evidence.

## Host checks and semantic judgment

V1 sources are current string facts on the actor's unique current Place,
predating the current action. They must be visible in the existing POV projection
with their exact canonical text and provenance. Explicit secret/restricted facts,
changed belief text, unknown facts, same-action facts, and superseded sources are
excluded. The catalog includes at most 24 complete sources of at most 2048
characters each; omission is not proof that no suitable source exists.

An eligible fact with omitted secrecy may be available only through this actor's
knowledge. Its new Object therefore uses the existing hidden/discovered-by-actor
visibility contract; it is not globally disclosed by initialization. Public
sources retain public object defaults. The private origin ledger is never a
generic discovery or knowledge grant for another actor.

The host validates the exact source, digest, quote, actor, Place type, fresh
Object ID, and source-slot consumption. A `(subject, predicate)` slot can initialize
only one Object, including after its text changes. This conservative restriction
can exclude facts describing multiple objects. It does not prove physical-object
uniqueness across different descriptions or facts.

The existing semantic audit additionally judges whether the full source actually
establishes this particular component at this Place, and whether it overlaps a
visible tracked Object or another proposed origin. This is a probabilistic model
attestation. An uncertain origin rejects the candidate immediately; rewriting
prose cannot repair missing source evidence. Ordinary physical claims are still
audited, and all source assessments are included in the host approval binding.

## Publication and ordering

For a materializing candidate, creation-capable sections run first, then the
entire `items` section in its original row order, then other sections in their
original order, with promises last. Thus current-action facts cannot overwrite
the required pre-state before initialization. No event is backdated. The explicit
initialization is separate from subsequent transfer checkpoints and does not
prove continuous custody before tracking began.

The new `object_materialized` event stores the host actor in its persisted
singleton `actors` field. Projection retains private source provenance in the
Object system; generic POV views remove that ledger. Normal and prepared
candidates share the same source-bound audit and publication checks. Raw new
store writes or backstage attempts cannot create materializations without the
host-approved candidate. Existing event replay is unchanged; no old scene
descriptions or item histories are migrated.

This is an in-process application contract, not a sandbox against arbitrary
trusted Python. It neither solves free-form object identity globally nor adds
general crafting, detachment, legal ownership, or consent rules.
