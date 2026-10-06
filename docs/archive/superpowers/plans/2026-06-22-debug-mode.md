# Debug Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A langgraph-style structured trajectory recorder for the rpg-engine, primarily for the agent debugger: every LLM call (prompt+output+usage), tool call, backstage hook, commit/repair loop, player input, and emitted event lands in one JSONL; a token-lean `rpg-trace` viewer gives a compact index + per-seq drill-down.

**Architecture:** Reuse the existing `kernel.observability.get_tracer()` span/generation/event seam (already called by `llm/provider.py` per-LLM-call and `loop/turn.py` per-hook). Add a process-singleton `DebugTracer` that writes a span tree + generations + events to JSONL; `get_tracer()` returns it when `RPG_DEBUG_TRACE` is set. Enhance the provider's `generation` to capture prompt-input + completion-output; add a few spans (bootstrap steps, produce/repair) + a player_input event for full coverage. A `python -m app.trace` viewer reads the JSONL.

**Tech Stack:** Python stdlib only (json/os/time/argparse/re). Reuses kernel.observability, llm.provider, loop.turn, loop.bootstrap, app.play, app.__main__.

## Global Constraints
- **Zero-overhead when off:** with no `RPG_DEBUG_TRACE`, `get_tracer()` MUST still return `NoopTracer` (or Langfuse if its key is set) — the full 1331-test suite stays byte-identical. Every new `span()`/`event()` call site is inert under Noop.
- **Enable precedence in `get_tracer()`:** `RPG_DEBUG_TRACE` (path) → DebugTracer (singleton); else `LANGFUSE_PUBLIC_KEY` → LangfuseTracer; else NoopTracer.
- **DebugTracer is a process singleton** (module-level cache) — the span stack must persist across the many `get_tracer()` calls in one run, so nesting is correct. Single-threaded (turns are sequential); the stack is a plain instance list.
- **Never crash the game:** every JSONL write is wrapped try/except → `log.debug` (mirror the existing tracer fault-tolerance). A tracing failure must never kill a turn.
- `time.time()` is allowed here — debug timestamps/durations are NOT event-sourced state (not in the store, not replayed), so they don't violate the Oracle determinism rule.
- **Agent-friendly viewer:** default output is a COMPACT index (one terse line per node, truncated summaries); full prompt+output only via `--show <seq>`. The doc tells the agent: locate via index/filters, drill via `--show`, NEVER `cat` the raw JSONL into context.
- Python3, `PYTHONPATH=/root/rpg-engine-app`. Commit on `app`, per-task, message ends with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Stage ONLY task files (NEVER `git add -A`/`.` — the worktree has unrelated untracked files).

---

## File Structure
- **Modify** `kernel/observability.py` — add `DebugTracer` + `_DebugGen` + wire `get_tracer()` (T1).
- **Modify** `llm/provider.py` — `_do_post` generation captures input; `_record_usage` captures output (T2).
- **Modify** `loop/bootstrap.py`, `loop/turn.py`, `app/play.py` — add spans/events for full coverage (T3).
- **Create** `app/trace.py` — the viewer CLI (`python -m app.trace`) (T4).
- **Modify** `app/__main__.py` — `--debug` flag (T5).
- **Create** `docs/debug-mode.md` — usage doc (T6).
- **Tests:** `tests/kernel/test_debug_tracer.py`, `tests/llm/test_provider_trace.py`, `tests/loop/test_trace_coverage.py`, `tests/app/test_trace_view.py`.

---

## Task 1: DebugTracer + get_tracer wiring

**Files:** Modify `kernel/observability.py`; Test `tests/kernel/test_debug_tracer.py`

**Interfaces:**
- Produces: `DebugTracer(path: str, run: str = "run")` with `.span(name, **attrs)` (ctx mgr), `.generation(name, **attrs)` (ctx mgr → handle with `.finish(output=, usage=)`), `.event(name, **attrs)`. `get_tracer()` returns the singleton `DebugTracer` when `RPG_DEBUG_TRACE` is set.
- JSONL record shape: `{"run","seq","ts","type","name","path","parent_seq", + per-type: span_end has "dur_ms"+"ref_seq"; gen has "input"/"output"/"usage"/"dur_ms"/"attrs"; event has "attrs"}`.

- [ ] **Step 1: Write the failing tests** (`tests/kernel/test_debug_tracer.py`):

```python
import json, os
import pytest
from kernel.observability import DebugTracer

def _records(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]

def test_span_nesting_produces_path(tmp_path):
    p = str(tmp_path / "t.jsonl")
    t = DebugTracer(p, run="r1")
    with t.span("turn", turn=3):
        with t.span("cascade"):
            t.event("note", msg="hi")
    recs = _records(p)
    ev = next(r for r in recs if r["type"] == "event")
    assert ev["path"] == "turn:3▸cascade▸note"      # nested path with attr-enriched label
    assert ev["attrs"] == {"msg": "hi"}
    # span_end carries dur_ms + ref_seq back to its span_start
    ends = [r for r in recs if r["type"] == "span_end"]
    starts = [r for r in recs if r["type"] == "span_start"]
    assert len(ends) == 2 and len(starts) == 2
    assert all("dur_ms" in r for r in ends)

def test_generation_captures_input_output_usage(tmp_path):
    p = str(tmp_path / "t.jsonl")
    t = DebugTracer(p)
    with t.span("turn", turn=1):
        with t.generation("llm", model="glm", input=[{"role":"user","content":"hi"}]) as g:
            g.finish(output="hello", usage={"input": 3, "output": 1})
    gen = next(r for r in _records(p) if r["type"] == "gen")
    assert gen["path"] == "turn:1▸llm"
    assert gen["input"] == [{"role":"user","content":"hi"}]
    assert gen["output"] == "hello"
    assert gen["usage"] == {"input": 3, "output": 1}
    assert "dur_ms" in gen and gen["attrs"]["model"] == "glm"

def test_generation_records_even_without_finish(tmp_path):
    p = str(tmp_path / "t.jsonl")
    t = DebugTracer(p)
    with t.generation("llm", model="x"):
        pass
    gen = next(r for r in _records(p) if r["type"] == "gen")
    assert gen["output"] is None    # graceful: record written on ctx exit

def test_write_failure_never_raises(tmp_path):
    t = DebugTracer("/nonexistent_dir/cannot/write.jsonl")
    with t.span("turn"):           # must not raise despite unwritable path
        t.event("x")

def test_get_tracer_singleton_under_env(tmp_path, monkeypatch):
    import kernel.observability as obs
    monkeypatch.setenv("RPG_DEBUG_TRACE", str(tmp_path / "t.jsonl"))
    obs._DEBUG_TRACER = None       # reset cache
    a = obs.get_tracer(); b = obs.get_tracer()
    assert isinstance(a, obs.DebugTracer) and a is b   # same singleton

def test_get_tracer_noop_when_off(monkeypatch):
    import kernel.observability as obs
    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    obs._DEBUG_TRACER = None
    assert isinstance(obs.get_tracer(), obs.NoopTracer)   # zero-overhead preserved
```

- [ ] **Step 2: Run → FAIL** (`pytest tests/kernel/test_debug_tracer.py -q -p no:cacheprovider`).

- [ ] **Step 3: Implement** in `kernel/observability.py` (add near the top imports: `import time`; add classes; modify `get_tracer`):

```python
class _DebugGen:
    """Generation handle for DebugTracer: stores output/usage; the record is
    written by the generation() ctx-mgr on exit (so a missing finish() still logs)."""
    def __init__(self):
        self.output = None
        self.usage = None
    def finish(self, *, output=None, usage=None):
        if output is not None:
            self.output = output
        if usage is not None:
            self.usage = usage


class DebugTracer:
    """Local structured tracer → JSONL (span tree + generations + events).
    Process-singleton (span stack must persist across get_tracer() calls)."""
    _PATH_KEYS = ("turn", "tool_name", "attempt", "step")

    def __init__(self, path, run="run"):
        self._path = path
        self._run = run
        self._seq = 0
        self._stack = []   # list[(seq, name, attrs)]

    def _next(self):
        self._seq += 1
        return self._seq

    @classmethod
    def _label(cls, name, attrs):
        for k in cls._PATH_KEYS:
            if attrs and k in attrs:
                return f"{name}:{attrs[k]}"
        return name

    def _path_str(self, leaf=None):
        parts = [self._label(n, a) for _, n, a in self._stack]
        if leaf is not None:
            parts.append(leaf)
        return "▸".join(parts)

    def _write(self, rec):
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception:
            log.debug("DebugTracer write failed")

    @contextmanager
    def span(self, name, **attrs):
        seq = self._next()
        parent = self._stack[-1][0] if self._stack else None
        self._stack.append((seq, name, attrs))
        own_path = self._path_str()
        start = time.time()
        self._write({"run": self._run, "seq": seq, "ts": start, "type": "span_start",
                     "name": name, "path": own_path, "parent_seq": parent,
                     "attrs": attrs or None})
        try:
            yield None
        finally:
            dur = int((time.time() - start) * 1000)
            self._stack.pop()
            self._write({"run": self._run, "seq": self._next(), "ts": time.time(),
                         "type": "span_end", "name": name, "path": own_path,
                         "ref_seq": seq, "dur_ms": dur})

    @contextmanager
    def generation(self, name, **attrs):
        seq = self._next()
        parent = self._stack[-1][0] if self._stack else None
        own_path = self._path_str(leaf=name)
        start = time.time()
        h = _DebugGen()
        try:
            yield h
        finally:
            self._write({"run": self._run, "seq": seq, "ts": start, "type": "gen",
                         "name": name, "path": own_path, "parent_seq": parent,
                         "input": attrs.get("input"),
                         "attrs": {k: v for k, v in attrs.items() if k != "input"} or None,
                         "output": h.output, "usage": h.usage,
                         "dur_ms": int((time.time() - start) * 1000)})

    def event(self, name, **attrs):
        seq = self._next()
        parent = self._stack[-1][0] if self._stack else None
        self._write({"run": self._run, "seq": seq, "ts": time.time(), "type": "event",
                     "name": name, "path": self._path_str(leaf=name),
                     "parent_seq": parent, "attrs": attrs or None})
```

And modify `get_tracer()` (add a module global `_DEBUG_TRACER = None` above it):

```python
_DEBUG_TRACER = None

def get_tracer():
    """RPG_DEBUG_TRACE → DebugTracer (singleton); else Langfuse (if key) ; else Noop."""
    global _DEBUG_TRACER
    dbg = os.environ.get("RPG_DEBUG_TRACE")
    if dbg:
        if _DEBUG_TRACER is None or _DEBUG_TRACER._path != dbg:
            _DEBUG_TRACER = DebugTracer(dbg, run=os.environ.get("RPG_DEBUG_RUN", "run"))
        return _DEBUG_TRACER
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        try:
            return LangfuseTracer()
        except Exception as e:
            log.debug("Langfuse unavailable (%s); falling back to NoopTracer", e)
    return NoopTracer()
```

- [ ] **Step 4: Run → PASS.** Also run the full suite once: `pytest -q -p no:cacheprovider` → still 1331 (zero-overhead preserved). **Commit.**

---

## Task 2: provider generation captures input + output

**Files:** Modify `llm/provider.py` (`_do_post` ~line 299, `_record_usage` ~line 23); Test `tests/llm/test_provider_trace.py`

**Interfaces:** Consumes Task 1's DebugTracer. After this, every LLM call's `gen` record has `input` = the request messages and `output` = the completion text.

- [ ] **Step 1: Write the failing test:**

```python
import json
import kernel.observability as obs
from llm import provider as prov

def test_do_post_records_input_and_output(tmp_path, monkeypatch):
    monkeypatch.setenv("RPG_DEBUG_TRACE", str(tmp_path / "t.jsonl"))
    obs._DEBUG_TRACER = None
    # Stub the HTTP layer: make _do_post's urlopen return a canned OpenAI-shape response.
    fake = {"choices": [{"message": {"content": "你好旅人"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
    monkeypatch.setattr(prov, "_http_post_json", lambda *a, **k: fake, raising=False)
    # Call the lowest LLM chokepoint the providers funnel through:
    body = {"model": "glm", "max_tokens": 100,
            "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]}
    with obs.get_tracer().span("turn", turn=1):
        prov._do_post("http://x", {}, body)     # uses the stubbed transport
    gen = next(r for r in (json.loads(l) for l in open(tmp_path / "t.jsonl")) if r["type"] == "gen")
    assert gen["input"] == body["messages"]      # prompt captured
    assert gen["output"] == "你好旅人"             # completion captured
    assert gen["usage"]["output"] == 4
```

NOTE for the implementer: `_do_post` currently calls `urllib.request.urlopen` inline. To make it unit-testable without network, extract the actual HTTP round-trip into a tiny helper `_http_post_json(url, headers, data, timeout)` that `_do_post` calls (and the test monkeypatches). If you prefer, monkeypatch `urllib.request.urlopen` instead — but the helper seam is cleaner and the test above assumes `prov._http_post_json`. Either way, keep the retry/backoff logic intact.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.** In `_do_post`, pass the prompt as `input`:
```python
    with get_tracer().generation("llm", model=body.get("model"),
                                 max_tokens=body.get("max_tokens"),
                                 input=body.get("messages")) as gen:
```
And extend `_record_usage(gen, parsed)` to also capture the completion text in the SAME `finish` call (single finish — don't call finish twice):
```python
def _record_usage(gen, parsed: dict) -> None:
    """Push usage AND the completion text onto a generation handle. Never raises."""
    try:
        usage = parsed.get("usage") or {}
        norm = {
            "input": usage.get("prompt_tokens") if usage.get("prompt_tokens") is not None
                     else usage.get("input_tokens"),
            "output": usage.get("completion_tokens") if usage.get("completion_tokens") is not None
                      else usage.get("output_tokens"),
            "total": usage.get("total_tokens"),
        }
        norm = {k: v for k, v in norm.items() if v is not None}
        # Output text: OpenAI/zhipu shape choices[0].message.content; fall back to the
        # whole message (tool-call turns have content=None) or the raw parsed object.
        out = None
        try:
            msg = (parsed.get("choices") or [{}])[0].get("message") or {}
            out = msg.get("content") or (json.dumps(msg, ensure_ascii=False) if msg else None)
        except Exception:
            out = None
        gen.finish(output=out, usage=norm or None)
    except Exception:
        log.debug("_record_usage failed (non-fatal)")
```

- [ ] **Step 4: Run → PASS.** Full suite still green. **Commit.**

---

## Task 3: capture-coverage spans + player_input event

**Files:** Modify `loop/bootstrap.py`, `loop/turn.py`, `app/play.py`; Test `tests/loop/test_trace_coverage.py`

**Interfaces:** Consumes Task 1. Adds spans so bootstrap steps + produce/repair are pathable, and a player_input event.

- [ ] **Step 1: Write the failing test** (drive a tiny bootstrap + a scripted turn under a DebugTracer, assert the new paths/events appear):

```python
import json
import kernel.observability as obs

def _paths(path):
    return [json.loads(l).get("path", "") for l in open(path, encoding="utf-8")]

def test_bootstrap_steps_are_spanned(tmp_path, monkeypatch):
    monkeypatch.setenv("RPG_DEBUG_TRACE", str(tmp_path / "t.jsonl"))
    obs._DEBUG_TRACER = None
    from app.engine import build_engine
    from loop.bootstrap import bootstrap_world
    # a scripted provider so it runs offline (see tests/loop/test_bootstrap.py ScriptedProvider)
    from tests.loop.test_bootstrap import ScriptedProvider, _canned_local_map_reply  # reuse helpers
    eng = build_engine(tmp_path / "camp", provider=ScriptedProvider([__import__("json").dumps({})]))
    bootstrap_world(eng, "x")
    paths = _paths(tmp_path / "t.jsonl")
    assert any("genesis" in p for p in paths)
    assert any("genesis▸gen_frame" in p or "gen_frame" in p for p in paths)

def test_player_input_event_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("RPG_DEBUG_TRACE", str(tmp_path / "t.jsonl"))
    obs._DEBUG_TRACER = None
    # play_loop with a scripted single input then /quit, fake provider
    ... build engine with a FakeLLMProvider, run play_loop(engine, ["我环顾四周", "/quit"], out=lambda *a: None)
    recs = [json.loads(l) for l in open(tmp_path / "t.jsonl")]
    pin = next(r for r in recs if r["type"] == "event" and r["name"] == "player_input")
    assert "我环顾四周" in (pin["attrs"] or {}).get("text", "")
```

(The implementer fills the second test's engine setup by mirroring `tests/app/test_*` play_loop fixtures.)

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — wrap with `get_tracer().span(...)` (all inert under Noop):
  - `loop/bootstrap.py::bootstrap_world`: wrap the whole body in `with get_tracer().span("genesis"):` and each step call in `with get_tracer().span("gen_frame", step="frame"):` … `span("gen_opening", step="opening")` (use `step=` so the path label is stable). Threads step: `span("gen_threads", step="threads")`.
  - `loop/turn.py::produce_turn`: wrap the initial produce in `with get_tracer().span("produce"):` and each repair iteration in `with get_tracer().span("repair", attempt=attempts+1):`.
  - `app/play.py::play_loop`: at the start of a normal (non-OOC) turn, before `run_turn`, call `get_tracer().event("player_input", text=player_input, turn=turn_no)`.

- [ ] **Step 4: Run → PASS.** Full suite green. **Commit.**

---

## Task 4: rpg-trace viewer (`python -m app.trace`)

**Files:** Create `app/trace.py`; Test `tests/app/test_trace_view.py`

**Interfaces:** A CLI over a trace JSONL. Commands (argparse): positional `file`; flags `--turn N`, `--phase NAME`, `--type {gen,event,span_start,span_end,span}`, `--grep REGEX`, `--show SEQ`, `--tree`, `--stats`, `--json`. Default (no action flag) = compact index of (filtered) records.

- [ ] **Step 1: Write failing tests** against a small fixture JSONL the test writes (records mirroring the Task-1 schema): assert
  - default index has one line per record with `seq`, `path`, `type`, and a truncated summary; respects `--turn`/`--phase`/`--type` filters.
  - `--show <seq>` prints the FULL `input` and `output` of that record (untruncated).
  - `--grep <re>` returns only records whose input/output matches.
  - `--tree` prints an indented tree (deeper paths indented more) with seqs.
  - `--stats` prints per-phase (top-level-under-turn) counts + summed dur_ms + summed tokens.
  - `--json` prints the filtered records as JSON lines (round-trips via json.loads).

```python
import json, subprocess, sys
def _write(path, recs):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
def _run(path, *args):
    return subprocess.run([sys.executable, "-m", "app.trace", str(path), *args],
                          capture_output=True, text=True, cwd="/root/rpg-engine-app",
                          env={"PYTHONPATH": "/root/rpg-engine-app"})
def test_index_and_show(tmp_path):
    f = tmp_path / "t.jsonl"
    _write(f, [
        {"run":"r","seq":1,"type":"span_start","name":"turn","path":"turn:3","attrs":{"turn":3}},
        {"run":"r","seq":2,"type":"gen","name":"llm","path":"turn:3▸cascade▸llm",
         "input":[{"role":"user","content":"AAA"}],"output":"BBBBB","usage":{"output":5},"dur_ms":8100},
    ])
    idx = _run(f).stdout
    assert "turn:3▸cascade▸llm" in idx and "2" in idx          # index line for seq 2
    show = _run(f, "--show", "2").stdout
    assert "AAA" in show and "BBBBB" in show                    # full I/O on drill-down
    assert "AAA" not in idx                                     # index is terse, not full
def test_phase_and_grep_and_stats(tmp_path):
    ... (filter by --phase cascade; --grep BBB finds seq 2; --stats shows cascade dur 8100)
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** `app/trace.py` (stdlib argparse; pure functions for testability):
  - `load(path) -> list[dict]` (json per line, skip blank).
  - `_phase_of(rec)` = the path segment right after the `turn:*` (or `genesis`) prefix, e.g. `turn:3▸cascade▸llm` → `cascade`; `genesis▸gen_frame` → `gen_frame`. None if top-level.
  - `index_line(rec)` = `f"{seq:>5}  {path:<34.34}  {type:<6}  {dur:<7}  {tok:<6}  {summary}"` where summary = truncated (≤80) output (gen) / text (event) / name (span); dur from dur_ms (e.g. `8.1s`); tok from usage.output.
  - filters: `--turn` (path startswith `turn:N`), `--phase` (`_phase_of==name`), `--type` (`span` matches span_start+span_end).
  - `--show SEQ`: print path, attrs, full input (pretty per message), full output, usage, dur.
  - `--grep RE`: keep records where re.search matches json.dumps(input)+output.
  - `--tree`: order by seq; indent = path depth (count of `▸`); print `{indent}{seq} {name} {summary}`.
  - `--stats`: group by `_phase_of`; sum dur_ms (from span_end/gen) + usage tokens + count gens.
  - `--json`: print filtered records as json lines.
  - `main(argv=None)` + `if __name__ == "__main__": main()`.

- [ ] **Step 4: Run → PASS. Commit.**

---

## Task 5: `--debug` flag in the CLI

**Files:** Modify `app/__main__.py`; Test `tests/app/test_trace_view.py` (or test_integration) — assert `--debug` sets `RPG_DEBUG_TRACE` + a trace file is produced.

- [ ] **Step 1: Write failing test:** running `main(["--campaign", str(d), "--debug"], inputs=["/quit"], out=collector, provider=fake)` results in `os.environ["RPG_DEBUG_TRACE"]` set to `<d>/trace.jsonl` AND (after a turn) the file exists with records. (Use the injected provider/inputs seams already in `main`.)
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement:** add `parser.add_argument("--debug", action="store_true", help="...")`. After resolving `campaign_dir`, if `args.debug` and `RPG_DEBUG_TRACE` not already set: `os.environ["RPG_DEBUG_TRACE"] = str(campaign_dir / "trace.jsonl")` and `out(f"[debug] 轨迹写入 {campaign_dir/'trace.jsonl'} — 看: python -m app.trace <file> [--turn N|--phase X|--show SEQ|--tree|--stats]")`. Set it BEFORE `build_engine`/bootstrap so genesis is traced too. Reset `kernel.observability._DEBUG_TRACER = None` when setting (so the singleton picks up the new path).
- [ ] **Step 4: Run → PASS. Full suite green. Commit.**

---

## Task 6: usage documentation `docs/debug-mode.md`

**Files:** Create `docs/debug-mode.md`

- [ ] **Step 1:** Write the doc with these sections (concrete, with real command examples):
  - **What it captures / when to use.**
  - **Enable:** `--debug` (writes `<campaign>/trace.jsonl`) or `export RPG_DEBUG_TRACE=/path/trace.jsonl`. Precedence vs Langfuse/Noop. Zero-overhead when off.
  - **Record schema:** the fields from the plan's Task-1 interface (run/seq/ts/type/name/path/parent_seq/input/output/usage/dur_ms/ref_seq), with `type` values explained.
  - **Path grammar:** `turn:N▸phase▸llm` / `genesis▸gen_frame▸llm` / `turn:N▸produce` / `turn:N▸repair:1`.
  - **Viewer command reference:** every flag with a real example + sample output (index, --show, --tree, --stats, --grep, --json).
  - **Agent debugging recipes:** narration wrong → `--turn N --phase produce --show SEQ`; world drift → `--phase cascade`/`--phase density`/`genesis`; "where did this string come from" → `--grep`; token/latency blowup → `--stats`; opening problem → `--phase genesis --tree`.
  - **Agent protocol (rule):** locate via `--tree`/`--turn`/`--phase` (cheap index) → drill via `--show SEQ`; NEVER `cat` the raw trace.jsonl into context (it is very long).
- [ ] **Step 2: Verify the doc's example commands actually run** against a real trace: enable debug, run one turn, then run each documented `python -m app.trace ...` command and confirm it works (paste real output into the doc). Fix any command that doesn't match the implementation.
- [ ] **Step 3: Commit.**

---

## Self-Review (plan vs spec)
- **§1 DebugTracer + get_tracer singleton/precedence** → Task 1 (incl. zero-overhead Noop test). ✓
- **§2 schema** → Task 1 interface + tests; documented in Task 6. ✓
- **§3 capture: provider I/O** → Task 2; **bootstrap/produce/repair spans + player_input** → Task 3. (tool spans + turn/hook spans already exist — verified in exploration.) ✓
- **§4 agent-friendly viewer** (index/--show/--turn/--phase/--type/--grep/--tree/--stats/--json) → Task 4. ✓
- **§5 enable --debug/RPG_DEBUG_TRACE** → Task 5. ✓
- **§6 docs + agent recipes + protocol** → Task 6. ✓
- **§7 testing** → per-task offline tests + full-suite-green checks (zero-overhead) in Tasks 1/2/3/5. ✓
- **Type consistency:** record schema keys identical across Task 1 (writer), Task 4 (viewer reads `seq`/`path`/`type`/`input`/`output`/`usage`/`dur_ms`), Task 6 (doc). `_DEBUG_TRACER` global + `RPG_DEBUG_TRACE`/`RPG_DEBUG_RUN` env names consistent. ✓
- **YAGNI:** no streaming UI, no cross-run aggregation, no Langfuse round-trip, no trace rotation (per spec Out-of-scope). ✓
