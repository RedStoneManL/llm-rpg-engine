# Decisions needed — autonomous batch R1-R8 (2026-06-23)

Red was AFK while I built the R-batch. Most fixes were unambiguous and are done.
The items below are where I made a judgment call you may want to override, or
where the cure has real tradeoffs I didn't want to pick blindly.

---

## D-R7 (the big one): how should the engine handle a NEW named entity introduced mid-scene?

**Diagnosis (confirmed from the trace):** `moves`/`places` fail validation almost
every turn because the model invents a character in the narration (e.g. **卡恩**, a
new NPC) and then writes `moves: [{"who": "卡恩", ...}]` — but "卡恩" was never
created as an entity, so it has no id and `validate` raises `dangling_ref: '卡恩'
不存在于图中`. The model thinks in NAMES (correct — after the #10 fix we only show
it names, never internal ids), but `moves.who`/`moves.to`/`places.parent` need
real entity ids. So the model's new-NPC movements get dropped → the world silently
loses them.

**Part 1 is DONE** (the player notice now names the dropped sections). **Part 2 —
the actual cure — is your call**, because it touches core validate/apply and the
fog/id boundary:

- **(A) Lenient auto-create (recommended).** When `moves.who` / `moves.to` /
  `cast` references a NAME with no matching entity, auto-create a minimal entity
  for it (a `character` for who, a `place` for to) with an engine-generated id,
  then apply the move. The model introduces-and-moves in one breath; the engine
  backfills the id. Pro: matches how the model naturally writes; keeps names-only
  fog. Con: a typo'd name spawns a junk entity; needs dedup-by-name so "卡恩"
  isn't created twice.
- **(B) Name→id resolution only (no create).** Resolve a name to an EXISTING
  entity's id; if none exists, still drop. Helps when the model uses a name for a
  known NPC, but does NOT help the common case here (卡恩 is brand-new) → moves of
  new NPCs still drop.
- **(C) Prompt the model to declare-before-move.** Tell it: a new NPC must be
  created in `cast` with a fresh id first, then referenced by that id in `moves`.
  Pro: no engine change. Con: fragile (the model only sees names, has no id
  convention for new entities; reasoning models routinely skip the declare step) —
  this is likely why it already fails despite the prompt.

**RESOLVED 2026-06-23: building A' (refined A).** Red greenlit option A with an
importance refinement. Final scope of A':
- Resolve a name → existing entity, or one declared in THIS turn's `cast`/`places`.
- Unresolved bare name → auto-create a **`mentioned` placeholder** + stamp a
  **first-appearance breadcrumb** (`first_seen` scene+day; if no sketch given,
  a minimal "首次现身于<scene>·第N天" note) so it's remembered + the LLM can look
  back at context.
- Name with a sketch (via `cast`) → create **`tracked`** (has background).
- Dedup by normalized name; prompt nudge "有戏的 NPC 走 cast 带 sketch".
Importance is carried by the tier: tracked = important, mentioned = walk-on
(promotable later via `cast op:evolve`).

### STATUS 2026-06-24: A' BUILT (Phase 1, 982b98c) + tier-filter (Phase 2, b8508cc)
Red picked A' and "1+2都搞完". Built: the auto-resolve/mint augment (Phase 1) and
the tier-aware NPC surfacing — terse 也在场 line for co-located walk-ons, off-scene
hidden (Phase 2 filtering/continuity, which solves the prompt-bloat concern).
Suite 1616. **Remaining (NOT built): aging/pruning** — see below.

### Follow-up design item (on the agenda, brainstorm AFTER A'): NPC 重要程度表
Red's concern: don't let auto-created walk-ons bloat the world / the prompt.
A separate design (touches context assembly), to brainstorm next:
- **Filtering**: context-assembly + POV queries surface tracked + scene-relevant
  by default; do NOT dump every historical `mentioned` walk-on into the prompt
  (else prompt bloat + token burn).
- **Aging / anti-bloat**: demote/archive/prune `mentioned` NPCs that haven't
  recurred in N days (keep the first-appearance breadcrumb for traceability, but
  off the active context).
- Possibly a finer importance axis (主角 / 重要 / 配角 / 路人) beyond the current
  tracked/mentioned two tiers.

---

## D-R2 (minor, FYI): the intro's surface premise

R2 stopped the intro from spoiling the deep truth (`central_conflict` is now a
`secrecy=secret` DM fact). The 【世界背景】 line now shows `world_name (tone)` + the
**player's own genre/pitch string** as the surface premise. I chose the genre/pitch
because it's always present and safe (the player wrote it). **Alternative:** have
`gen_frame` author a dedicated 1-line *surface premise* (nicer prose than echoing
the raw pitch) as a separate public field. I went with the simpler genre-echo to
avoid touching gen_frame's prompt/validator. Say the word if you want the authored
surface premise instead.

---

## D-R8 (minor, FYI): default narration style is NEUTRAL

R8 adds a style/voice dial. I defaulted it to **neutral (empty)** so the prompt is
byte-identical to before unless you opt in — you get 日式轻小说 by setting
`STYLE="日式轻小说"` in run.sh (or `--style` / `/style` / `RPG_NARRATION_STYLE`).
**Alternative:** bake 日式轻小说 as the global default. I left it neutral so other
campaigns aren't forced into that voice. (Your run.sh already has the `STYLE=`
knob — just fill it.)

---

## D-R1 (minor, FYI): resume recap omits 所在/第N天

The 【继续游戏】 recap shows 你是 (name) / 当前目标 / 旅程至此 (scene summaries) /
上次 (last scene verbatim). I omitted explicit **current-location** and **day**
because resolving them needs the bitemporal `located_in`/clock day-query and I
didn't want to risk an exception in a block that runs before play_loop — and the
"上次" prose already conveys where you are. Say the word if you want 所在/第N天 added
(I'll resolve them carefully + guard).

---

## D-public-repush: push the R-batch to the public repo?

The R-batch (R5/R4/R2/R7p1/R8/R6/R1) is committed on `app` but NOT yet snapshotted
to the public repo (github.com:RedStoneManL/llm-rpg-engine, currently at v0.2
+ the run.sh fix). These are real quality/safety fixes (esp. R5 secret-leak) worth
publishing. **Want me to re-push?** (Same clean-snapshot flow as before; I'd bump
the public test count + note the fixes.) I left it for your call since publishing
is irreversible.

---

## D-merge / 验收: nothing is merged

Everything this session — the player-definable-genesis feature (commits
1ba5529..9f84bd7) AND this R-batch — lives on branch `app`, UNMERGED. Awaiting your
review/验收. R7-part2 (the moves/places auto-create, D-R7 above) is the one
remaining BUILD item, gated on your pick of option A/B/C.

