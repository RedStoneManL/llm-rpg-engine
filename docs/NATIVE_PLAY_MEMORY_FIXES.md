# Fixes observed in actual native-model play

A real engine playthrough at `8aedb9b` used a separate player and fresh GPT-6
Astra completion agents. It delivered 10 actions in two segments (1–8, then
9–10 after an environment interruption). A second interruption left action11
uncommitted. The native bridge used actual play_loop, Author, validation,
repairs, and background generation, with research tools disabled. It did not
call DeepSeek; handoff latency is not a production API latency measurement.

## Reaffirming an existing return promise

The actual turn1 action said “玉印我会照约第3天中午还您”. The classifier
returned a commitment and the old host created a second open obligation with
the same actor/item/recipient/deadline: one became two. The actual return at
turn6 fulfilled both; the observed issue was redundant obligation/history,
not a proven unpaid remainder.

The new creation path first runs the existing typed/evidence/deadline checks,
then suppresses only an identical OPEN obligation. Original promise terms and
evidence remain immutable; the existing player source event records the new
literal reaffirmation. Fulfilled obligations do not suppress a later new loan.
Historical duplicate IDs and their fulfillment events still replay unchanged.

A [fresh native after-case](reports/2026-10-07/native-reaffirmation-after.json)
used the identical seed and actual action. The fresh classifier again returned
ready, but the committed count stayed one → one, the original record was
unchanged, and a new literal input source was retained. Two actual completions
were used. Surrounding prose differed, so this is a specific obligation-state
comparison, not a whole-story quality comparison.

## Remembering a newly introduced NPC

Original turn3 introduced陆青 as “一个背着长木尺的女子” and used“她”.
After a fresh-context reopen, turn9 used“他”. Her generated sketch omitted this
detail; the actual turn9 request contained neither“女子” nor“她”. Also,
cast.create.name was not a stored name fact, so existing current-name matching
could not find the Chinese query label. The rejected/transient attempt at
turn9 was kept separate from the actual delivered resumed turn.

New host player-source records may now contain `cast_introductions`: exact
published narration associated with new actor-visible Person IDs, narration
source ID, and creation names that literally occur in that passage. The first
2,048 characters are retained with explicit offsets/truncation; no gender,
speaker, or per-NPC attributes are inferred from the prose. Multi-NPC passages
remain shared scene evidence. These private actor-owned snapshots survive
recap compression and are removed by rewind with their source event. Generic
POV views strip the enclosing private ledger.

An actor-bound reader retrieves historical names/IDs or relevant visible NPCs
before context projection. The passage is historical narration evidence, not a
current-state override or a grant of NPC knowledge. Old saves without this
optional field remain unknown; this change does not retrofit old save8.

The [fresh native after-case](reports/2026-10-07/native-introduction-after.json)
started from the real saved state before NPC creation. A new model introduction
created a different NPC,县令陆青禾, as“挽着袖子的年轻女子”. Four actual actions
covered that introduction, returning to the inn, resting until day2 morning,
and a revisit with deliberately fresh Author context. Ordinary background
summary/catch-up calls ran. The final two-message request contained the exact
503-character original passage despite the old scene being summarized; the
delivered response again used“年轻女子” and“她”. Twelve actual completions
were used. This is one successful after sample with a newly generated NPC,
not an identical-NPC causal pair or a universal model guarantee.

## Remaining observations

- A normal summary in the after-case mistakenly called the protagonist“小满”,
  which is the bell's name. Its exact output is retained in the evidence.
- Eight of the original 10 delivered actions needed the same cast.evolve
  predicate/value repair. This interface friction is still separate work.
- The independent player found coherent custody, returned-seal acknowledgment,
  promised-meal fulfillment, bridge inspection uncertainty, and ferry terms.
  They also reported repetitive asides and low dramatic tension.
- The first playthrough never completed a cross-day action. Only the separate
  four-action introduction after-case reached day2 morning.

Verification for this increment consisted of actual native cases, source/import
checks, and independent code review. No new offline test campaign or DeepSeek
validation was run. Full synthetic prompts/responses and saves are retained in
the companion audit archive; no credentials or private API budget ledger are
included.
