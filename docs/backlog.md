# Backlog — open / deferred items

Live register of the still-open threads salvaged from the now-archived
playtest-feedback + decisions docs (see `docs/archive/`). Closed items are NOT
listed here — the bulk of the 2026-06-22/23 feedback (#1–#11, #R1–#R8) is built;
only the items below remain. Current bug register for the latest playtest lives
in `playtest-issues-2026-06-30.md`.

---

## B1 · 429 robustness — honor Retry-After + smooth the per-turn burst (开放)
**From:** feedback-2026-06-23 #R6. **Status:** partially done — `_do_post` already
retries 429/5xx with exponential backoff (1/2/4/8s ×4) and `RPG_CASCADE_CONCURRENCY`
caps cascade fan-out. **Open sub-items:**
- Honor the `Retry-After` response header on 429 (use it as the wait when present),
  instead of only the `2**attempt` schedule.
- Optionally throttle the per-turn burst (serialize/space backstage hook calls, or a
  global min-interval under rate pressure).
**Where:** the LLM provider `_do_post` (zhipu/glm path).

## B2 · NPC importance / aging — anti-bloat for auto-created walk-ons (deferred design)
**From:** decisions-2026-06-23 (D-R7 follow-up). **Status:** the auto-resolve/mint
(A', Phase 1+2) is built — bare names become `mentioned` placeholders, co-located
walk-ons get a terse 也在场 line, off-scene hidden. **Not built:** aging/pruning.
- Demote / archive / prune `mentioned` NPCs that haven't recurred in N days (keep the
  first-appearance breadcrumb for traceability, but off the active context).
- Possibly a finer importance axis (主角 / 重要 / 配角 / 路人) beyond the current
  tracked/mentioned two tiers.
**Where:** context assembly + POV surfacing; a backstage demote/prune hook.
**Needs:** brainstorm → spec → plan (its own design effort).

## B3 · Public repository snapshot (authorized 2026-10-06)
The user requested the complete updated engine in the existing public repository,
`github.com/RedStoneManL/llm-rpg-engine`, for continued development elsewhere.
This delivery includes the intervening app fixes and the October action-integrity,
memory, resource-rule and DeepSeek work. See [HANDOFF.md](HANDOFF.md) for setup,
evidence and the current open work. Credentials and personal saves remain local.

---

## Verified closed (kept here so they aren't re-investigated)

- **Self-knowledge fog** (feedback-2026-06-22 #6) — **confirmed handled.** The
  protagonist querying itself gets its own facets: `llm/tools.py:430-449`
  (`characters_query`: `cid == pov_id` bypasses the knows() gate, returns real
  values minus `hidden`/internal prefixes) + `:329-337` (`recall_query` never drops a
  hit about the pov itself). Covered by `tests/llm/test_tools_self_knowledge.py`.
