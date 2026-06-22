# Debug Mode

Agent-oriented tracing for the RPG engine. Writes a JSONL trajectory file that
the `python -m app.trace` viewer can slice and drill into without ever loading
the full file into context.

---

## 1. What it captures / when to use

Debug mode attaches a `DebugTracer` to the engine process. Every significant
unit of work emits one or more records:

- **span** — a named block of code (genesis, a turn, produce, repair, cascade,
  etc.). Emits a `span_start` record on entry and a `span_end` record with
  elapsed time on exit.
- **gen** — one LLM call: records the full prompt (`input`), the raw model
  response (`output`), token counts (`usage`), and wall-clock latency
  (`dur_ms`).
- **event** — a one-shot annotation with arbitrary attributes. The engine emits
  one `player_input` event before each turn.

Use debug mode when:

- Narration is wrong and you need to see exactly what the model was sent and
  what it returned.
- World state drifts unexpectedly and you want to trace which backstage phase
  mutated it.
- A run is slow or token-heavy and you need per-phase cost numbers.
- You are inspecting genesis world-building output.
- You want to search for where a specific string entered the LLM context.

When debug is off (no `RPG_DEBUG_TRACE` env var and no `--debug` flag), all
tracer calls go to a `NoopTracer` whose methods are inert one-liners. There is
zero overhead.

---

## 2. Enable

### Via CLI flag

```
python -m app --campaign campaigns/demo --provider zhipu --debug
```

The `--debug` flag sets `RPG_DEBUG_TRACE` to `<campaign>/trace.jsonl` before
any engine code runs, so genesis is captured. It prints a hint line to stdout:

```
[debug] 轨迹 → campaigns/demo/trace.jsonl  (查看: python -m app.trace campaigns/demo/trace.jsonl [--turn N|--phase X|--show SEQ|--tree|--stats])
```

If `RPG_DEBUG_TRACE` is already set in the environment when `--debug` is
passed, the flag does nothing — the pre-set path wins.

### Via environment variable

```
export RPG_DEBUG_TRACE=/path/to/trace.jsonl
python -m app --campaign campaigns/demo --provider zhipu
```

The env var approach is useful when you want to name the file explicitly or
when running from a wrapper script.

### Precedence (three-tier)

```
RPG_DEBUG_TRACE set  →  DebugTracer (local JSONL, this document applies)
LANGFUSE_PUBLIC_KEY  →  LangfuseTracer (remote Langfuse, separate tooling)
neither              →  NoopTracer (zero overhead, nothing written)
```

`RPG_DEBUG_TRACE` always wins. If it is set, Langfuse is not consulted even if
credentials are present.

---

## 3. Record schema

Every record is a JSON object on its own line. All records share these fields:

| Field        | Type          | Present on     | Meaning                                          |
|-------------|---------------|----------------|--------------------------------------------------|
| `run`        | string        | all            | Run label (default `"run"`, override via `RPG_DEBUG_RUN`) |
| `seq`        | integer       | all            | Global monotonic sequence number, 1-based        |
| `ts`         | float         | all            | Unix timestamp (seconds) at record creation      |
| `type`       | string        | all            | Record kind — see below                          |
| `name`       | string        | all            | Human label for the span, gen, or event          |
| `path`       | string        | all            | `▸`-joined path from the root span (see section 4) |
| `parent_seq` | integer\|null | all            | `seq` of the enclosing span, or null if at root  |

Type-specific fields:

**`span_start`** — emitted when a span opens:

| Field   | Type          | Meaning                          |
|---------|---------------|----------------------------------|
| `attrs` | object\|null  | Extra kwargs passed to `span()`  |

**`span_end`** — emitted when a span closes:

| Field     | Type    | Meaning                                         |
|-----------|---------|--------------------------------------------------|
| `ref_seq` | integer | `seq` of the matching `span_start`               |
| `dur_ms`  | integer | Elapsed wall-clock milliseconds for the span     |
| `attrs`   | none    | Not present on span_end                          |

**`gen`** — emitted when an LLM call context-manager exits (written even if
`finish()` was never called, so a crash still leaves a record):

| Field     | Type          | Meaning                                         |
|-----------|---------------|--------------------------------------------------|
| `input`   | list\|null    | Prompt messages sent to the model                |
| `output`  | string\|null  | Raw model response text                          |
| `usage`   | object\|null  | Token counts, e.g. `{"input": 120, "output": 45}` |
| `dur_ms`  | integer       | Wall-clock milliseconds for the LLM call        |
| `attrs`   | object\|null  | Extra kwargs (e.g. `{"model": "glm-4-flash"}`)  |

**`event`** — a one-shot annotation:

| Field   | Type         | Meaning                          |
|---------|--------------|----------------------------------|
| `attrs` | object\|null | Payload kwargs passed to `event()` |

---

## 4. Path grammar

The `path` field encodes the nesting of active spans at the time a record is
written, using `▸` as separator. The first segment is always the root span;
each nested span appends a new segment.

### Label enrichment

When a span is opened with an attribute whose key is one of `turn`,
`tool_name`, `attempt`, or `step`, the label is enriched: `name:value`. For
example, `span("turn", turn=3)` produces the label `turn:3`, and
`span("repair", attempt=1)` produces `repair:1`.

### Concrete patterns

| Situation                         | Example path                            |
|----------------------------------|-----------------------------------------|
| Genesis root span                | `genesis`                               |
| Genesis sub-span (world frame)   | `genesis▸gen_frame:frame`               |
| LLM call inside genesis sub-span | `genesis▸gen_frame:frame▸llm`           |
| Genesis opening narrative LLM    | `genesis▸gen_opening:opening▸llm`       |
| Turn root span                   | `turn:3`                                |
| Produce phase                    | `turn:3▸produce`                        |
| LLM call inside produce          | `turn:3▸produce▸llm`                    |
| First repair round               | `turn:3▸repair:1`                       |
| LLM call inside repair           | `turn:3▸repair:1▸llm`                   |
| Cascade backstage phase          | `turn:3▸cascade:3`                      |
| Director backstage phase         | `turn:3▸director:3`                     |
| Digest fleet backstage phase     | `turn:3▸digest_fleet:3`                 |
| Lore tick backstage phase        | `turn:3▸lore:3`                         |
| Density seed backstage phase     | `turn:3▸density:3`                      |
| Tool call inside LLM loop        | `turn:3▸produce▸tool_loop▸tool:recall`  |
| Player input event (no parent)   | `player_input`                          |

Note: backstage phases (`cascade`, `director`, `digest_fleet`, `catchup`,
`lore`, `density`) pass `turn=N` to `span()`, so their path segment includes
the turn number: `cascade:3`, not `cascade`. The viewer's `--phase` filter
matches on the **base name** before the `:`, so `--phase cascade` matches
`cascade:1`, `cascade:2`, `cascade:3`, etc. across all turns. Use
`--phase cascade:3` (exact) when you want only one specific turn's cascade.

---

## 5. Viewer command reference

```
python -m app.trace <file> [filters] [action]
```

All commands were run against a real trace generated offline with a scripted
provider. Outputs below are real.

### Default: compact index

No action flag — one terse line per record.

```
python -m app.trace /tmp/demo_trace.jsonl
```

Output:
```
    1  genesis                             span_start                    genesis
    2  genesis▸gen_frame:frame             span_start                    gen_frame
    3  genesis▸gen_frame:frame▸llm         gen         400ms     18      {"world_name":"碎镜大陆","central_conflict":"皇权与江湖的生死角力"}
    4  genesis▸gen_frame:frame             span_end    440ms             gen_frame
    5  genesis▸gen_opening:opening         span_start                    gen_opening
    6  genesis▸gen_opening:opening▸llm     gen         620ms     25      你踏入了碎镜大陆的起始之地，晨雾弥漫，江湖气息扑面而来。
    7  genesis▸gen_opening:opening         span_end    660ms             gen_opening
    8  genesis                             span_end    1.1s              genesis
    9  player_input                        event                         向北方走去，寻找失踪的商人
   10  turn:1                              span_start                    turn
   11  turn:1▸produce                      span_start                    produce
   12  turn:1▸produce▸llm                  gen         1.2s      45      [NARRATION]
你向北出发，风中带着淡淡的血腥气。路上一名老妇人拦住了你："客官，我儿失踪三日了！"

[NPC_UPDATES]
{}
[/NPC_…
   13  turn:1▸produce                      span_end    1.2s              produce
   14  turn:1▸digest_fleet:1               span_start                    digest_fleet
   15  turn:1▸digest_fleet:1               span_end    20ms              digest_fleet
   16  turn:1▸director:1                   span_start                    director
   17  turn:1▸director:1                   span_end    10ms              director
   18  turn:1▸cascade:1                    span_start                    cascade
   19  turn:1▸cascade:1                    span_end    10ms              cascade
   20  turn:1▸lore:1                       span_start                    lore
   21  turn:1▸lore:1                       span_end    10ms              lore
   22  turn:1                              span_end    1.3s              turn
   23  player_input                        event                         与老妇人交谈，了解商人信息
   24  turn:2                              span_start                    turn
   25  turn:2▸produce                      span_start                    produce
   26  turn:2▸produce▸llm                  gen         980ms     38      老妇人说："我儿是大陆商路上的行商，三日前去了铁峰矿山便再无音讯。"
   27  turn:2▸produce                      span_end    990ms             produce
   28  turn:2▸repair:1                     span_start                    repair
   29  turn:2▸repair:1▸llm                 gen         1.1s      52      [NARRATION]
老妇人泪眼婆娑：「我儿王大郎去了铁峰矿山，三日无讯，求客官援手！」

[NPC_UPDATES]
{}
[/NPC_UPDATES]
   30  turn:2▸repair:1                     span_end    1.1s              repair
   31  turn:2▸cascade:2                    span_start                    cascade
   32  turn:2▸cascade:2                    span_end    10ms              cascade
   33  turn:2                              span_end    2.1s              turn
```

Column layout: `seq  path  type  dur  tok  summary`

- `tok` column is output token count, blank for non-gen records.
- `dur` column is blank for span_start (not yet closed) and for events.
- `summary` for gen records is the first 80 chars of `output`; for events it is
  the `text` or `msg` attr; for spans it is the span name.

---

### Filters

Filters narrow which records the action operates on. They compose (AND logic).

#### `--turn N`

Only records whose path starts with `turn:N`.

```
python -m app.trace /tmp/demo_trace.jsonl --turn 1
```

```
   10  turn:1                              span_start                    turn
   11  turn:1▸produce                      span_start                    produce
   12  turn:1▸produce▸llm                  gen         1.2s      45      [NARRATION]
...
   22  turn:1                              span_end    1.3s              turn
```

#### `--phase NAME`

Only records where the second `▸`-segment of the path matches NAME. Matching
is by **base name**: `--phase cascade` matches `cascade:1`, `cascade:2`,
`cascade:3`, etc. across all turns. Supply the full enriched segment
(`--phase cascade:3`) for an exact single-turn match.

```
python -m app.trace /tmp/demo_trace.jsonl --phase produce
```

```
   11  turn:1▸produce                      span_start                    produce
   12  turn:1▸produce▸llm                  gen         1.2s      45      [NARRATION]
...
   13  turn:1▸produce                      span_end    1.2s              produce
   25  turn:2▸produce                      span_start                    produce
   26  turn:2▸produce▸llm                  gen         980ms     38      老妇人说：...
   27  turn:2▸produce                      span_end    990ms             produce
```

Combine with `--turn N` to narrow to one turn:

```
python -m app.trace /tmp/demo_trace.jsonl --turn 1 --phase produce
```

```
   11  turn:1▸produce                      span_start                    produce
   12  turn:1▸produce▸llm                  gen         1.2s      45      [NARRATION]
...
   13  turn:1▸produce                      span_end    1.2s              produce
```

For backstage phases, use the natural base name — no turn number needed:

```
python -m app.trace /tmp/demo_trace.jsonl --phase cascade
```

This shows all cascade spans across all turns (`cascade:1`, `cascade:2`, …).
Use `--phase repair` to see all repair rounds, `--phase density` to see all
density seed spans. For a specific turn and phase: `--turn 3 --phase cascade`.

#### `--type TYPE`

Choices: `gen`, `event`, `span_start`, `span_end`, `span` (matches both
span_start and span_end).

```
python -m app.trace /tmp/demo_trace.jsonl --type event
```

```
    9  player_input                        event                         向北方走去，寻找失踪的商人
   23  player_input                        event                         与老妇人交谈，了解商人信息
```

```
python -m app.trace /tmp/demo_trace.jsonl --turn 1 --type gen
```

```
   12  turn:1▸produce▸llm                  gen         1.2s      45      [NARRATION]
你向北出发，风中带着淡淡的血腥气。路上一名老妇人拦住了你："客官，我儿失踪三日了！"
...
```

---

### Actions

Action flags are mutually exclusive. If no action flag is given, the default
compact index is printed.

#### `--show SEQ`

Drill down: print the full content of a single record by sequence number.
Bypasses all filters — look up is global.

```
python -m app.trace /tmp/demo_trace.jsonl --show 12
```

```
seq:  12
path: turn:1▸produce▸llm
type: gen
attrs: {"model": "glm-4-flash"}
dur:  1.2s (1200 ms)
usage: {"input": 120, "output": 45}
--- input ---
[system] 你是TRPG主持人，写叙事。
[user] 玩家：向北方走去，寻找失踪的商人。当前场景：碎镜大陆集镇。
--- output ---
[NARRATION]
你向北出发，风中带着淡淡的血腥气。路上一名老妇人拦住了你："客官，我儿失踪三日了！"

[NPC_UPDATES]
{}
[/NPC_UPDATES]
```

For events:

```
python -m app.trace /tmp/demo_trace.jsonl --show 9
```

```
seq:  9
path: player_input
type: event
attrs: {"text": "向北方走去，寻找失踪的商人", "turn": 1}
```

#### `--grep REGEX`

Show index lines for records whose serialised `input` + `output` content
matches the regex. Only `gen` records have `input`/`output` fields; span and
event records will never match.

```
python -m app.trace /tmp/demo_trace.jsonl --grep "铁峰"
```

```
   26  turn:2▸produce▸llm                  gen         980ms     38      老妇人说："我儿是大陆商路上的行商，三日前去了铁峰矿山便再无音讯。"
   29  turn:2▸repair:1▸llm                 gen         1.1s      52      [NARRATION]
老妇人泪眼婆娑：「我儿王大郎去了铁峰矿山，三日无讯，求客官援手！」
...
```

Regex is a Python `re` pattern applied to the JSON-serialised `input` blob
concatenated with the `output` string.

#### `--tree`

Indented span tree. Depth is determined by the number of `▸` characters in
each record's path. Records are sorted by seq.

```
python -m app.trace /tmp/demo_trace.jsonl --tree
```

```
1 genesis genesis
  2 gen_frame gen_frame
    3 llm {"world_name":"碎镜大陆","central_conflict":"皇权与江湖的生死角力"}
  4 gen_frame gen_frame
  5 gen_opening gen_opening
    6 llm 你踏入了碎镜大陆的起始之地，晨雾弥漫，江湖气息扑面而来。
  7 gen_opening gen_opening
8 genesis genesis
9 player_input 向北方走去，寻找失踪的商人
10 turn turn
  11 produce produce
    12 llm [NARRATION]
你向北出发，风中带着淡淡的血腥气。路上一名老妇人拦住了你："客官，我儿失踪三日了！"
...
  13 produce produce
  14 digest_fleet digest_fleet
  15 digest_fleet digest_fleet
  16 director director
  17 director director
  18 cascade cascade
  19 cascade cascade
  20 lore lore
  21 lore lore
22 turn turn
...
```

Tree lines format: `INDENT seq name summary(60)`. Each `span_end` appears at the
same depth as its `span_start` (same path, same depth). Combines with `--turn`
or `--phase` to narrow scope.

#### `--stats`

Per-phase aggregate table: LLM call count, total duration, total output tokens.
Only `gen` and `span_end` records contribute to duration (span_start does not
to avoid double-counting).

```
python -m app.trace /tmp/demo_trace.jsonl --stats
```

```
phase                      gens    dur_ms   tokens
----------------------------------------------------
cascade                       0        10        0
catchup                       0         0        0
density                       0         7        0
digest_fleet                  0        37        0
director                      0        18        0
gen_frame                     0         0        0
gen_opening                   0         0        0
gen_threads                   0         0        0
genesis                       0       225        0
lore                          0        14        0
produce                       0         0        0
repair                        0         0        0
turn                          0       147        0
```

Phases are the base name of the second `▸`-segment (the part before the first
`:`). All cascade spans across turns (`cascade:1`, `cascade:6`, …) collapse
into one `cascade` row; all repair rounds (`repair:1`, `repair:2`, …) collapse
into one `repair` row. Records at depth 0 (`genesis`, `turn:1`, `turn:6`) use
their own path as the key but also normalised to the base name (`turn`).
Combine with `--turn N` to see stats for a single turn only.

#### `--json`

Print filtered records as raw JSON lines. Useful when piping to `jq` or when
feeding a specific slice to another tool. Subject to all active filters.

```
python -m app.trace /tmp/demo_trace.jsonl --turn 1 --type gen --json
```

```
{"run": "run", "seq": 12, "ts": ..., "type": "gen", "name": "llm", "path": "turn:1▸produce▸llm", "parent_seq": 11, "input": [...], "attrs": {"model": "glm-4-flash"}, "output": "[NARRATION]\n...", "usage": {"input": 120, "output": 45}, "dur_ms": 1200}
```

---

## 6. Agent debugging recipes

### Narration is wrong — inspect the exact prompt and response

Identify the turn number from context, then narrow to `produce`:

```
python -m app.trace campaigns/demo/trace.jsonl --turn 3 --phase produce
```

Locate the `gen` record (seq N) for the LLM call in that phase, then:

```
python -m app.trace campaigns/demo/trace.jsonl --show N
```

`--show` prints the full prompt (`input`) and the raw model output. Check
whether the prompt had the correct world state and whether the model's output
was malformed or semantically wrong. If a repair round was triggered, also
inspect the repair LLM calls:

```
python -m app.trace campaigns/demo/trace.jsonl --turn 3 --phase repair
```

then `--show` the `gen` seq inside it.

### World state drifts unexpectedly between turns

Backstage phases (cascade, lore, director) can mutate world state. Look at the
`span_end` duration column in the index to find which phase was active:

```
python -m app.trace campaigns/demo/trace.jsonl --turn 3
```

Then drill into the suspect phase with `--show` on any `gen` record inside it,
or use `--tree` with `--turn` to see the full shape of that turn's call graph:

```
python -m app.trace campaigns/demo/trace.jsonl --turn 3 --tree
```

For lore-driven state changes:

```
python -m app.trace campaigns/demo/trace.jsonl --phase lore
```

For cascade propagation:

```
python -m app.trace campaigns/demo/trace.jsonl --phase cascade
```

To inspect a single turn's cascade:

```
python -m app.trace campaigns/demo/trace.jsonl --turn 3 --phase cascade
```

For density-based lore seeding:

```
python -m app.trace campaigns/demo/trace.jsonl --phase density
```

### "Where did this string come from?"

Use `--grep` with a distinctive substring from the suspicious narration or fact
string. This searches all `input` + `output` content across every gen record:

```
python -m app.trace campaigns/demo/trace.jsonl --grep "铁峰矿山"
```

The result shows which turns and phases the string appeared in. If it shows up
in a `produce▸llm` input, it was injected into the prompt. If only in output,
the model generated it.

### Token or latency blowup

Use `--stats` for a per-phase cost breakdown:

```
python -m app.trace campaigns/demo/trace.jsonl --stats
```

High `tokens` in `produce` or `repair` typically means context window
creep — check the prompt with `--show`. High `dur_ms` with zero `gens` in a
backstage phase (`cascade`, `director`, `density`) means that phase is doing
expensive world queries, not LLM calls.

Narrow to a single turn to compare across sessions:

```
python -m app.trace campaigns/demo/trace.jsonl --turn 5 --stats
```

### Opening genesis problem

To see the full structure of genesis and all LLM calls inside it:

```
python -m app.trace campaigns/demo/trace.jsonl --phase genesis --tree
```

Note: `genesis` is a depth-0 record; `--phase` matches the second segment,
so `--phase genesis` returns nothing. Use `--tree` directly and scan the top
of the output, or filter by a genesis sub-phase using the base name:

```
python -m app.trace campaigns/demo/trace.jsonl --phase gen_frame
python -m app.trace campaigns/demo/trace.jsonl --phase gen_opening
```

Or for the full genesis overview without filtering:

```
python -m app.trace campaigns/demo/trace.jsonl --tree
```

The genesis block occupies the top of the tree output (seq 1 through the
`genesis` `span_end`). Locate the `gen` record for `gen_frame:frame▸llm` (the
world framing call) and `--show` it.

---

## 7. Agent protocol

**Locate first, drill second. Never read the raw file.**

The trace file for a multi-turn session can be thousands of lines and hundreds
of kilobytes. Loading it into context is token-prohibitive and unnecessary.

**Rule: use the cheap index commands to locate, then `--show SEQ` to drill.**

Workflow:

1. **Get the overview** — run the default index (no flags) or `--tree`. This is
   the cheapest call and fits on a screen.

2. **Narrow the scope** — add `--turn N` and/or `--phase NAME` to reduce the
   result set to only the records that matter for the current question.

3. **Identify the target seq** — from the narrowed index or tree, read the seq
   number of the `gen` record (or span) you want to inspect.

4. **Drill into full content** — `--show SEQ` prints the complete input,
   output, usage, and duration for that one record. This is the only command
   that reveals prompt and response text.

5. **Search across turns** — use `--grep PATTERN` only when you do not know
   which turn introduced a string. It scans all gen input+output fields. After
   finding the matching seq numbers, `--show` each one.

**Do not `cat` or `head` the raw `trace.jsonl` file.** Its raw JSONL form is
unformatted, spans multiple lines per record for long prompts, and does not
give you the filtered, human-readable view the viewer provides. Every piece of
information in the raw file is accessible through a targeted viewer command
at a fraction of the token cost.
