# Explicit return-deadline boundaries

An actual native player said “玉印第三天中午前一定还您”. The old parser
rejected the Chinese ordinal, while already-supported forms such as “明天中午前”
silently discarded the before qualifier. These are separate problems. Standard Chinese ordinals one through ninety-nine
are supported alongside the existing positive Arabic-number days; vague ranges
and unsupported numeral forms still require clarification.

New deadlines distinguish an optional `boundary`:

- `exclusive`: the obligation is overdue when the specified day/band begins.
- `inclusive`, including old records without the field: overdue only after the
  specified band. This preserves the engine's existing coarse calendar contract.

The engine does not represent minutes. “中午前” keeps noon as its boundary;
it is not rewritten as a guessed morning deadline. Late actual returns still
fulfill an obligation. Historical due mappings and evidence are not rewritten
or reinterpreted from their old prose.

A new declaration matching an existing open item/debtor/recipient and normalized
deadline reaffirms it. A different deadline for those same parties requires
clarification; it does not amend the existing record or create a duplicate.
The player can explicitly repeat the existing date or cancel the unrecorded
proposal. Cancellation leaves the recorded obligation intact. This is not a
renegotiation or cancellation system for existing promises.

Literal-source checks preserve attached deadline qualifiers as well as the
date and band. They do not establish general semantic entailment: negative,
conditional or quoted intentions still rely on the classifier's interpretation.

The classifier receives only the current debtor's POV-visible open record
IDs, items, recipients and due values to interpret references such as “原约定”.
Those fields cannot substitute for a literal player date or evidence quote;
they are not added to player history or NPC knowledge.

## Actual model verification

[All three runs](reports/2026-10-08/native-deadline-boundaries.json) retain the
failed intermediate result. With an existing inclusive deadline, the first
version explained the conflict correctly but its fresh classifier repeated
the question after an explicit reaffirmation: two calls, zero story commits.
After adding the minimal original-record reference, the same two inputs and
seed produced one conflict clarification and one story commit in three calls.
The old commitment's full record remained unchanged.

With no prior commitment, six actual native responses produced three committed
turns: register “第三天中午前”, rest until day 3 noon, then return the item.
The stored deadline was exclusive; at noon the next narrator prompt contained
`overdue: true`, and the prose explicitly recognized the missed boundary. The
real transfer then fulfilled that same obligation despite being late.

The new-commitment run preceded the final classifier-reference addition; its
domain/host/narrator code was unchanged. These are fixed controlled inputs
through real play_loop with fresh native Astra responses, not a new adaptive
player campaign or DS validation. One sample does not prove general entailment,
and cancellation, prepared routes, replay and privacy received source review
rather than new model fault-injection campaigns. Raw source hashes distinguish
the iterations. No full unit suite was run for this increment.
