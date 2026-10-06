# docs/ — index

Entry point to the engine's documentation. Consolidated 2026-07-01: live
reference at the top level, historical build record under `archive/`, ephemeral
build scaffolding deleted.

## Live reference (start here)

| Doc | What it is |
|---|---|
| [`HANDOFF.md`](HANDOFF.md) | Cross-environment setup, verified results and concrete remaining development work (2026-10-06). |
| [`reports/2026-10-03/report.html`](reports/2026-10-03/report.html) | Visual before/after architecture report, real-model transcripts and failure evidence; open locally in a browser. |
| [`action-integrity.md`](action-integrity.md) | Atomic actions, DeepSeek profile, resource rules, flavor variation, memory and compatibility limits (2026-10-03). |
| [`MODULE_INDEX.md`](MODULE_INDEX.md) | **Canonical** architecture map — every module, its responsibility, files, interactions, and a "where to fix X" table. Start here. |
| [`2026-06-19-architecture-world-model.md`](2026-06-19-architecture-world-model.md) | Mechanism-level **visual** companion to MODULE_INDEX — ASCII diagrams of the turn loop, push/pull context, and the data substrate + backstage fleet. |
| [`playtest-issues-2026-06-30.md`](playtest-issues-2026-06-30.md) | Current bug/issue register (play5 forensics, I1–I9 — all fixed; I8 待配置). |
| [`backlog.md`](backlog.md) | Open / deferred items (self-knowledge fog 待核实, 429 robustness, NPC aging, public re-push). |
| [`debug-mode.md`](debug-mode.md) | How to use the JSONL tracer + `python -m app.trace` viewer. |
| [`genesis-blueprint.md`](genesis-blueprint.md) | How to player-define a world opening via `--genesis PATH`. |

## Foundational design (the "why")

| Doc | What it is |
|---|---|
| [`2026-06-17-rpg-ultimate-harness-design.md`](2026-06-17-rpg-ultimate-harness-design.md) | Design genesis of the standalone `app` engine — microkernel + ContextSystem registry. Implemented; kept as design rationale. |
| [`2026-06-15-rpg-engine-redesign-design.md`](2026-06-15-rpg-engine-redesign-design.md) | The original `skill`-branch (hermes-embedded) redesign vision. The project's first-principles doc. |
| [`INCIDENT-2026-06-16-git-reset.md`](INCIDENT-2026-06-16-git-reset.md) | Postmortem of the sub-agent git-reset incident — the source of the standing git guardrails. Institutional memory. |

## `archive/` — historical build record

Per-feature design specs + step-by-step build plans + dated decision/feedback
logs. All features here are **built, tested, and indexed in MODULE_INDEX** — the
archive is provenance, not live reference.

- `archive/plans/` — the 9 `skill`-branch phase plans (P1 event-core → P6b hooks).
- `archive/superpowers/plans/` — the `app`-engine build plans (S0–S5, living-world
  P1–P3/phaseB–D, lore, scene, world-clock, endgame, density, questline, debug-mode,
  world-bootstrap, player-genesis, multiturn-compaction).
- `archive/superpowers/specs/` — the design specs (`*-design.md`) for the above + the
  point-in-time health-check.
- `archive/decisions-needed-2026-06-{22,23}.md` — resolved decision logs (still-open
  threads were migrated to `backlog.md`).
- `archive/playtest-feedback-2026-06-{22,23}.md` — live playtest logs; the feedback is
  built (open items migrated to `backlog.md`).

## `codegraph/` — pydeps visual

Auto-generated `engine/` dependency graph (`gen.sh` regenerates the `.svg`/`.dot`).
The stale textual `INDEX.md` was removed — MODULE_INDEX supersedes it.

## What was deleted (2026-07-01 consolidation)

- `codegraph/INDEX.md` — stale codebase index (1151 tests / 6 systems era), fully
  superseded by `MODULE_INDEX.md` (1654 tests / ~14 systems).
- Ephemeral SDD build scaffolding under `superpowers/specs/` — 9 build subdirs of
  `.out` run transcripts, `.py` probes/demos, and per-task review reports
  (density-build, endgame-build, health-fix, p3-build, overnight-runs, lore-AB,
  clock-smoke, unified-quest-demo, lifespan-build). The features they built are
  live + tested; the logs were disposable. Recoverable from git history if needed.
