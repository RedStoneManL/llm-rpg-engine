# M3 public-history projection

Commits `375f249` and `c52be64` add `public_event_history` and the explicit
`Engine.public_history()` outlet. It reads full `include_retracted=True`
history, removes private memory rows, preserves normal row order/body/retracted
state, and rejects normal `thread_refs`/`chunk_ids` that name private IDs.

Real SQLite tests cover active/retracted private rows, explicit private
references, and a default no-memory Engine compatibility control. Targeted:
3 passed. Full offline suite: 1724 passed, 1 deselected in 16.60s.

Limits: this is a new Engine API. Existing GUI/CLI/GM/trace/raw-store paths do
not call it and are not accepted as filtered outputs. Nested payloads and
unknown logging/export outlets are outside this slice.
