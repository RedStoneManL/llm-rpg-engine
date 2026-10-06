# Multi-turn Conversation + 70% Compaction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep one running DM conversation across turns (append a small per-turn delta instead of re-assembling full context every turn), and compact it back to a freshly-assembled tiered context when it reaches 70% of the 200K window.

**Architecture:** `AuthorStrategy` gains a persistent `_thread` (clean story transcript: system + alternating user-delta / narration) separate from the transient per-turn `_messages` (working list with raw JSON + repair rounds + tool exchanges). A fresh turn either rebuilds full context (first turn / after compaction / stateless mode) or appends a delta (`[场况] + push + player`) onto a copy of `_thread`. On turn success, `produce_turn` calls `commit_to_thread(narration)`. Compaction is triggered by the provider's reported `prompt_tokens` crossing 140K, which flags the next fresh turn to take the full-rebuild path (re-running `assemble_context`, which already renders the index / recent-verbatim / summary tiers).

**Tech Stack:** Python 3.10+ stdlib only. pytest. No new dependencies.

## Global Constraints

- All commands run with `PYTHONPATH=/root/rpg-engine-app` and `python3` (e.g. `PYTHONPATH=. python3 -m pytest ...`).
- Branch: `app`. One commit per task. Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- `conversation_mode == "stateless"` MUST reproduce today's `produce` behavior byte-for-byte. The full existing suite is pinned to stateless (conftest) and must stay green (~1616 passed).
- Production default is `conversation_mode == "multiturn"`.
- Context window = 200_000 tokens; compaction threshold = 70% (140_000); recent-verbatim K = 3 (the narrative system's existing recent-N rendering — no change needed in v1, the thread holds recent turns verbatim until compaction).
- The persistent thread stores **narration prose**, never raw JSON commits. Repair rounds and tool-call/tool-result messages never enter the thread.
- No new dependencies. Chinese for any player-facing / prompt text; English for code & identifiers.

---

## File Structure

- `engine/settings.py` — add `conversation_mode` accessor + env (`RPG_CONVERSATION_MODE`). (Task 1)
- `tests/conftest.py` — pin the autouse env fixture to `stateless`. (Task 1)
- `loop/strategy.py` — `_build_delta` helper (Task 2); `AuthorStrategy` state + `commit_to_thread` + `TurnStrategy` no-op default (Task 3); `produce` multiturn restructure (Task 4); compaction trigger (Task 7).
- `loop/turn.py` — call `commit_to_thread` at the `produce_turn` success point. (Task 5)
- `llm/provider.py` — `_norm_usage` + `LLMProvider.last_usage` + `_post` wrapper; route `_do_post` sites through it. (Task 6)
- Tests: `tests/loop/test_multiturn.py` (Tasks 2,3,4,7), `tests/loop/test_turn_multiturn.py` (Task 5), `tests/llm/test_provider_usage.py` (Task 6), `tests/test_fix9_settings.py` (Task 1, extend).

---

## Task 1: `conversation_mode` setting + stateless test pin

**Files:**
- Modify: `engine/settings.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_fix9_settings.py`

**Interfaces:**
- Produces: `settings.get_conversation_mode() -> str` (`"multiturn"|"stateless"`), `settings.set_conversation_mode(mode: str) -> bool`, env `RPG_CONVERSATION_MODE`, module constant `CONVERSATION_MODES = ("multiturn", "stateless")`. Default `"multiturn"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_fix9_settings.py`:

```python
class TestConversationMode:
    def test_default_is_multiturn(self, monkeypatch):
        monkeypatch.delenv("RPG_CONVERSATION_MODE", raising=False)
        from engine import settings
        settings.reset_from_env()
        assert settings.get_conversation_mode() == "multiturn"

    def test_env_override_stateless(self, monkeypatch):
        monkeypatch.setenv("RPG_CONVERSATION_MODE", "stateless")
        from engine import settings
        settings.reset_from_env()
        assert settings.get_conversation_mode() == "stateless"

    def test_set_valid_and_invalid(self):
        from engine import settings
        assert settings.set_conversation_mode("stateless") is True
        assert settings.get_conversation_mode() == "stateless"
        assert settings.set_conversation_mode("bogus") is False
        assert settings.get_conversation_mode() == "stateless"  # unchanged
        settings.set_conversation_mode("multiturn")

    def test_invalid_env_falls_back_to_multiturn(self, monkeypatch):
        monkeypatch.setenv("RPG_CONVERSATION_MODE", "nonsense")
        from engine import settings
        settings.reset_from_env()
        assert settings.get_conversation_mode() == "multiturn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/test_fix9_settings.py::TestConversationMode -v`
Expected: FAIL with `AttributeError: module 'engine.settings' has no attribute 'get_conversation_mode'`.

- [ ] **Step 3: Implement in `engine/settings.py`**

After `VERBOSITY_LEVELS = (...)` add:

```python
CONVERSATION_MODES = ("multiturn", "stateless")
```

Add module state near `_style`:

```python
# Turn-authoring conversation mode: "multiturn" keeps one running DM conversation
# across turns + compacts at 70% of the window; "stateless" rebuilds full context
# every turn (the original behavior; the byte-identical fallback). (multiturn)
_conversation_mode: str = "multiturn"
```

Add a parser next to `_parse_verbosity`:

```python
def _parse_conversation_mode(raw: str) -> str:
    """Return *raw* if a valid conversation mode, else 'multiturn'."""
    v = (raw or "").strip().lower()
    return v if v in CONVERSATION_MODES else "multiturn"
```

In `reset_from_env`, add `_conversation_mode` to the `global` line and set it:

```python
    global _verbosity, _max_tool_rounds, _style, _conversation_mode
    ...
    _conversation_mode = _parse_conversation_mode(
        os.environ.get("RPG_CONVERSATION_MODE", "multiturn")
    )
```

Add accessors after `set_style`:

```python
def get_conversation_mode() -> str:
    """Return the current turn-authoring conversation mode."""
    return _conversation_mode


def set_conversation_mode(mode: str) -> bool:
    """Set the conversation mode. Returns False on invalid input (state unchanged)."""
    global _conversation_mode
    v = mode.strip().lower() if mode else ""
    if v not in CONVERSATION_MODES:
        return False
    _conversation_mode = v
    return True
```

- [ ] **Step 4: Pin the test suite to stateless in `tests/conftest.py`**

In `_hermetic_rpg_env`, add `"RPG_CONVERSATION_MODE"` to the delenv tuple, and right before `yield` force stateless so the existing suite exercises the byte-identical path:

```python
    # Existing suite asserts today's per-turn assembly → pin stateless. New
    # multi-turn tests opt in via settings.set_conversation_mode("multiturn").
    from engine import settings as _s
    monkeypatch.setenv("RPG_CONVERSATION_MODE", "stateless")
    _s.reset_from_env()
    yield
```

(The teardown `reset_from_env()` already present restores defaults after monkeypatch reverts the env.)

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=. python3 -m pytest tests/test_fix9_settings.py -q`
Expected: PASS. Note: `test_default_is_multiturn` and the env tests call `monkeypatch.setenv/delenv` + `reset_from_env` themselves, overriding the autouse pin within the test body — they pass because they re-read env explicitly.

- [ ] **Step 6: Commit**

```bash
git add engine/settings.py tests/conftest.py tests/test_fix9_settings.py
git commit -m "feat(settings): conversation_mode (multiturn|stateless); pin test suite to stateless

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `_build_delta` per-turn delta renderer

**Files:**
- Modify: `loop/strategy.py`
- Test: `tests/loop/test_multiturn.py` (create)

**Interfaces:**
- Consumes: `station_push_fragment(registry, world, scene)` (already imported in strategy.py), `assemble_context` (already imported), `band_name` (from `kernel.clock`).
- Produces: `_build_delta(registry, world, scene, player_input) -> str` — a compact continuing-turn user message: a `【此刻】` time/place header + the push fragment (when present) + `[player] <input>`. Never raises; no embedder recall.

- [ ] **Step 1: Write the failing test**

Create `tests/loop/test_multiturn.py`:

```python
from loop.strategy import _build_delta


def _scene():
    return {"protagonist": "protagonist", "present": [], "day": 3, "location": "town_a"}


def _world():
    return {"meta": {"day": 3, "band": 1}, "systems": {}}


def test_build_delta_has_header_push_and_player():
    out = _build_delta(None, _world(), _scene(), "我推门进去")
    assert "【此刻】" in out
    assert "第 3 天" in out
    assert "[player] 我推门进去" in out


def test_build_delta_is_short_no_full_context():
    # The delta must not carry a full assembled context block; it is a few lines.
    out = _build_delta(None, _world(), _scene(), "看看四周")
    assert out.count("\n") < 8


def test_build_delta_never_raises_on_sparse_world():
    out = _build_delta(None, {}, {"location": ""}, "等待")
    assert "[player] 等待" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_multiturn.py -v`
Expected: FAIL with `ImportError: cannot import name '_build_delta'`.

- [ ] **Step 3: Implement in `loop/strategy.py`**

Add the import near the top imports (with the other `from ... import`):

```python
from kernel.clock import band_name
```

Add the helper just above `class AuthorStrategy`:

```python
def _build_delta(registry, world: dict, scene: dict, player_input: str) -> str:
    """Compact continuing-turn message for the running conversation.

    The model already holds the world in-thread; this carries only what's new:
    a time/place header, any backstage 暗线 push (station_push_fragment), and the
    player's action. Cheap (no embedder recall — that is a compaction-time cost).
    """
    meta = (world or {}).get("meta", {}) or {}
    day = (scene or {}).get("day") or meta.get("day") or 1
    band = meta.get("band") or 0
    loc = (scene or {}).get("location") or ""
    header = f"【此刻】第 {day} 天 · {band_name(band)}"
    if loc:
        header += f" · 在 {loc}"
    parts = [header]
    try:
        frag = station_push_fragment(registry, world, scene)
    except Exception:
        frag = None
    if frag:
        parts.append(frag)
    parts.append(f"[player] {player_input}")
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_multiturn.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add loop/strategy.py tests/loop/test_multiturn.py
git commit -m "feat(strategy): _build_delta — compact per-turn delta renderer

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Persistent-thread state + `commit_to_thread` (+ ABC no-op default)

**Files:**
- Modify: `loop/strategy.py`
- Test: `tests/loop/test_multiturn.py`

**Interfaces:**
- Produces: on `AuthorStrategy` — instance state `_thread: list|None`, `_pending_user: str|None`, `_compaction_due: bool` (in addition to existing `_messages: list|None`); method `commit_to_thread(self, narration: str) -> None`. On `TurnStrategy` ABC — a no-op `commit_to_thread(self, narration: str) -> None` default.
- Contract of `commit_to_thread`: if `self._thread is not None` and `self._pending_user is not None`, append `{"role":"user","content":self._pending_user}` then `{"role":"assistant","content":narration}` to `_thread`, then set `_pending_user = None`. Otherwise no-op.

- [ ] **Step 1: Write the failing test**

Add to `tests/loop/test_multiturn.py`:

```python
from loop.strategy import AuthorStrategy


def test_commit_to_thread_appends_pair_with_narration():
    s = AuthorStrategy()
    s._thread = [{"role": "system", "content": "sys"}]
    s._pending_user = "【此刻】... [player] 开门"
    s.commit_to_thread("你推开了门，门后是一条窄巷。")
    assert len(s._thread) == 3
    assert s._thread[1] == {"role": "user", "content": "【此刻】... [player] 开门"}
    assert s._thread[2] == {"role": "assistant", "content": "你推开了门，门后是一条窄巷。"}
    assert s._pending_user is None  # consumed


def test_commit_to_thread_noop_when_no_thread():
    s = AuthorStrategy()
    assert s._thread is None
    s._pending_user = "x"
    s.commit_to_thread("narr")  # must not raise, must not create a thread
    assert s._thread is None


def test_commit_to_thread_noop_when_no_pending():
    s = AuthorStrategy()
    s._thread = [{"role": "system", "content": "sys"}]
    s._pending_user = None
    s.commit_to_thread("narr")
    assert len(s._thread) == 1  # unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_multiturn.py -k commit_to_thread -v`
Expected: FAIL with `AttributeError: 'AuthorStrategy' object has no attribute 'commit_to_thread'`.

- [ ] **Step 3: Implement in `loop/strategy.py`**

Add the no-op default to the `TurnStrategy` ABC (after `repair_sections`):

```python
    def commit_to_thread(self, narration: str) -> None:
        """Append the just-succeeded turn to the strategy's persistent multi-turn
        thread (if it keeps one). No-op by default."""
        return None
```

In `AuthorStrategy`, extend the class-level state declarations (next to `_messages`):

```python
    _messages: list | None = None       # transient working list for the current turn
    _thread: list | None = None         # persistent multi-turn conversation (multiturn)
    _pending_user: str | None = None    # user msg for the in-flight turn (committed on success)
    _compaction_due: bool = False       # set when usage crosses 70%; consumed next fresh turn
```

Add the method on `AuthorStrategy`:

```python
    def commit_to_thread(self, narration: str) -> None:
        """Append [pending user delta, narration prose] to the persistent thread on
        a successful turn. Narration only — raw JSON / repair / tool messages stay
        in the transient _messages. No-op in stateless mode (_thread is None)."""
        if self._thread is not None and self._pending_user is not None:
            self._thread.append({"role": "user", "content": self._pending_user})
            self._thread.append({"role": "assistant", "content": narration})
            self._pending_user = None
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_multiturn.py -k commit_to_thread -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add loop/strategy.py tests/loop/test_multiturn.py
git commit -m "feat(strategy): persistent thread state + commit_to_thread (ABC no-op default)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `produce` multi-turn restructure

**Files:**
- Modify: `loop/strategy.py` (rewrite `AuthorStrategy.produce`, lines ~337-397)
- Test: `tests/loop/test_multiturn.py`

**Interfaces:**
- Consumes: `settings.get_conversation_mode()` (Task 1), `_build_delta` (Task 2), `_thread/_pending_user/_compaction_due` + `commit_to_thread` (Task 3).
- Produces: restructured `produce` — fresh turns build `_messages` either by full rebuild (first turn / `_compaction_due` / stateless) or by appending a `_build_delta` onto a copy of `_thread`; repairs append to `_messages` (unchanged). `repair_sections` is UNCHANGED (still appends to `self._messages`).

- [ ] **Step 1: Write the failing test**

Add to `tests/loop/test_multiturn.py`:

```python
import json
from engine import settings
from llm.provider import FakeLLMProvider


def _commit_json(narr):
    return json.dumps({"narration": narr, "moves": [], "places": [], "cast": [],
                       "facts": [], "clock": [{"advance": False, "days": 0,
                       "bands": 0, "reason": "原地"}]})


def test_multiturn_first_turn_opens_thread_then_continuing_uses_delta(monkeypatch):
    settings.set_conversation_mode("multiturn")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("一"), _commit_json("二")])
    world = {"meta": {"day": 1, "band": 0}, "systems": {}}
    scene = {"protagonist": "protagonist", "present": [], "day": 1, "location": "loc1"}

    c1 = s.produce(None, world, scene, "开局动作", provider=prov)
    assert c1.narration == "一"
    # First turn: working list is [system, full_user]; thread reset to [system].
    assert s._messages[0]["role"] == "system"
    assert len(s._thread) == 1 and s._thread[0]["role"] == "system"
    s.commit_to_thread(c1.narration)            # simulate produce_turn success
    assert len(s._thread) == 3                   # system + user + assistant

    c2 = s.produce(None, world, scene, "第二步", provider=prov)
    assert c2.narration == "二"
    # Continuing turn: working = thread(3) + delta(1) = 4; the new user msg is a delta.
    assert len(s._messages) == 5                 # 3 thread + delta-user + assistant(raw)
    assert "[player] 第二步" in s._messages[3]["content"]
    assert "【此刻】" in s._messages[3]["content"]
    settings.set_conversation_mode("multiturn")


def test_multiturn_thread_stores_narration_not_raw_json(monkeypatch):
    settings.set_conversation_mode("multiturn")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("散文一")])
    world = {"meta": {"day": 1, "band": 0}, "systems": {}}
    scene = {"protagonist": "protagonist", "present": [], "day": 1, "location": "loc1"}
    c1 = s.produce(None, world, scene, "动作", provider=prov)
    s.commit_to_thread(c1.narration)
    assert s._thread[2] == {"role": "assistant", "content": "散文一"}
    assert "moves" not in s._thread[2]["content"]   # not the raw JSON commit


def test_repair_appends_to_working_not_thread(monkeypatch):
    settings.set_conversation_mode("multiturn")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("一"), _commit_json("一修")])
    world = {"meta": {"day": 1, "band": 0}, "systems": {}}
    scene = {"protagonist": "protagonist", "present": [], "day": 1, "location": "loc1"}
    s.produce(None, world, scene, "动作", provider=prov)
    before = len(s._thread)
    s.produce(None, world, scene, "动作", provider=prov, repair="补 clock 段")
    assert len(s._thread) == before              # repair never grows the thread
    assert s._messages[-2]["content"] == "补 clock 段"


def test_stateless_mode_keeps_thread_none(monkeypatch):
    settings.set_conversation_mode("stateless")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("一"), _commit_json("二")])
    world = {"meta": {"day": 1, "band": 0}, "systems": {}}
    scene = {"protagonist": "protagonist", "present": [], "day": 1, "location": "loc1"}
    s.produce(None, world, scene, "a", provider=prov)
    s.commit_to_thread("一")
    s.produce(None, world, scene, "b", provider=prov)
    assert s._thread is None                      # stateless never opens a thread
    settings.set_conversation_mode("multiturn")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_multiturn.py -k "multiturn or repair_appends or stateless_mode" -v`
Expected: FAIL (continuing turn still rebuilds full context → `_thread` not used; assertions on `_messages` length / delta content fail).

- [ ] **Step 3: Replace `AuthorStrategy.produce` in `loop/strategy.py`**

Replace the whole method body (the current lines ~337-397) with:

```python
    def produce(
        self,
        registry: Registry,
        world: dict,
        scene: dict,
        player_input: str,
        *,
        provider,
        embedder=None,
        repair: str | None = None,
    ) -> TurnCommit:
        multiturn = (_settings.get_conversation_mode() == "multiturn")

        if repair is not None and self._messages is not None:
            # Repair: continue the working list in place (prior assistant output
            # is already there). Never touches the persistent thread.
            self._messages.append({"role": "user", "content": repair})
        elif multiturn and self._thread is not None and not self._compaction_due:
            # Continuing turn: append a compact delta onto a copy of the thread.
            delta = _build_delta(registry, world, scene, player_input)
            self._pending_user = delta
            self._messages = list(self._thread) + [{"role": "user", "content": delta}]
        else:
            # First turn / compaction / stateless: full context rebuild.
            ctx = assemble_context(registry, world, scene,
                                   query=player_input, embedder=embedder)
            frag = station_push_fragment(registry, world, scene)
            if frag:
                ctx = (ctx + "\n\n" + frag) if ctx else frag
            parts = []
            if ctx:
                parts.append(ctx)
            parts.append(f"[player] {player_input}")
            full_user = "\n\n".join(parts)
            self._pending_user = full_user
            self._messages = [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": full_user},
            ]
            if multiturn:
                # Reset the thread to a bare system base; the turn's user+narration
                # are appended by commit_to_thread on success (so no duplication).
                self._thread = [{"role": "system", "content": _system_prompt()}]
                self._compaction_due = False

        log.debug("AuthorStrategy.produce msgs=%d repair=%r multiturn=%s",
                  len(self._messages), bool(repair), multiturn)

        if repair is None and provider.supports_tools():
            tool_reg = build_tool_registry(registry, world, scene)
            schemas = tool_reg.schemas()
            if schemas:
                rounds = _settings.get_max_tool_rounds()
                raw = provider.complete_with_tools(
                    self._messages, schemas, tool_reg.execute,
                    max_tool_rounds=rounds,
                )
                self._messages.append({"role": "assistant", "content": raw})
                data = _data_or_safe(raw)
                return TurnCommit.from_dict(data)

        raw = provider.complete_messages(self._messages)
        self._messages.append({"role": "assistant", "content": raw})
        data = _data_or_safe(raw)
        return TurnCommit.from_dict(data)
```

Note: in `stateless` mode `multiturn` is False → the `else` branch runs every fresh turn building `[system, full_user]`, `_thread` stays `None`, and repairs append to `_messages` — identical to the original control flow. `repair_sections` (below `produce`) is unchanged.

- [ ] **Step 4: Run the new tests + the stateless guarantee**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_multiturn.py -v`
Expected: PASS (all).

Run (byte-identical guarantee — the whole suite is pinned stateless by conftest):
`PYTHONPATH=. python3 -m pytest tests/loop -q`
Expected: PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add loop/strategy.py tests/loop/test_multiturn.py
git commit -m "feat(strategy): multi-turn produce — running thread + per-turn delta (stateless unchanged)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Wire `commit_to_thread` into `produce_turn`

**Files:**
- Modify: `loop/turn.py` (the `produce_turn` success return, ~line 226)
- Test: `tests/loop/test_turn_multiturn.py` (create)

**Interfaces:**
- Consumes: `strategy.commit_to_thread(narration)` (Task 3); `produce_turn(...) -> (commit, attempts, dropped_sections)`.
- Produces: `produce_turn` calls `strategy.commit_to_thread(commit.narration)` exactly once, immediately before its final `return`, so only the finalized (post-repair) narration enters the thread.

- [ ] **Step 1: Write the failing test**

Create `tests/loop/test_turn_multiturn.py`:

```python
from engine import settings
from loop.strategy import AuthorStrategy
from loop.turn import produce_turn
from llm.provider import FakeLLMProvider
from kernel.registry import Registry
from kernel.projection import empty_world
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem


def _make_registry():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    return r


def test_produce_turn_success_commits_one_pair_to_thread():
    settings.set_conversation_mode("multiturn")
    registry = _make_registry()
    world = empty_world(registry)
    scene = {"protagonist": "hero", "present": [], "day": 1, "location": "town"}
    s = AuthorStrategy()
    prov = FakeLLMProvider(json_responses=[{"narration": "第一段叙事"}])
    produce_turn(registry, world, scene, "动作", strategy=s, provider=prov)
    # First fresh turn opens thread=[system]; on success commit_to_thread appends
    # [pending-user, narration] → length 3; assistant entry is the narration prose.
    assert len(s._thread) == 3
    assert s._thread[2] == {"role": "assistant", "content": "第一段叙事"}
    settings.set_conversation_mode("multiturn")
```

This mirrors the proven `test_produce_turn_returns_commit_without_writing_store`
setup (`tests/loop/test_turn.py:309`): no `required_sections` (default), a canned
commit with only `narration`, no store needed (`produce_turn` doesn't write one).

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_turn_multiturn.py -v`
Expected: FAIL (`len(s._thread) == 1`, because `produce_turn` never calls `commit_to_thread`).

- [ ] **Step 3: Implement in `loop/turn.py`**

Find the success return of `produce_turn` (after the repair loop, the final `return commit, attempts, dropped_sections`, ~line 226). Immediately before it, add:

```python
    # Multi-turn: append this finalized turn (clean narration only) to the
    # strategy's persistent conversation. No-op for stateless / non-multiturn.
    strategy.commit_to_thread(commit.narration)
    return commit, attempts, dropped_sections
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_turn_multiturn.py -v`
Expected: PASS.

Run regression: `PYTHONPATH=. python3 -m pytest tests/loop -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add loop/turn.py tests/loop/test_turn_multiturn.py
git commit -m "feat(turn): commit finalized narration to the multi-turn thread on success

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Provider `last_usage` exposure

**Files:**
- Modify: `llm/provider.py`
- Test: `tests/llm/test_provider_usage.py` (create)

**Interfaces:**
- Produces: module helper `_norm_usage(usage: dict) -> dict | None` (keys `input`/`output`/`total`); `LLMProvider.last_usage: dict | None = None` class attribute; `LLMProvider._post(self, url, headers, body, **kw) -> dict` wrapping `_do_post` and setting `self.last_usage`. All real-provider `_do_post(...)` call sites route through `self._post(...)`.

- [ ] **Step 1: Write the failing test**

Create `tests/llm/test_provider_usage.py`:

```python
from llm.provider import _norm_usage, OpenAIProvider, FakeLLMProvider
import llm.provider as provider_mod


def test_norm_usage_openai_keys():
    assert _norm_usage({"prompt_tokens": 100, "completion_tokens": 20,
                        "total_tokens": 120}) == {"input": 100, "output": 20, "total": 120}


def test_norm_usage_anthropic_keys():
    assert _norm_usage({"input_tokens": 50, "output_tokens": 10}) == {
        "input": 50, "output": 10, "total": None}


def test_norm_usage_empty_is_none():
    assert _norm_usage({}) is None


def test_fake_provider_last_usage_defaults_none():
    assert FakeLLMProvider().last_usage is None


def test_post_sets_last_usage(monkeypatch):
    # _do_post → _http_post_json; stub the HTTP layer to return a usage block.
    def fake_http(url, data, timeout):
        return {"choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 150000, "completion_tokens": 5}}
    monkeypatch.setattr(provider_mod, "_http_post_json", fake_http)
    p = OpenAIProvider(model="m", api_key="k")
    p.complete_messages([{"role": "user", "content": "x"}])
    assert p.last_usage == {"input": 150000, "output": 5, "total": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/llm/test_provider_usage.py -v`
Expected: FAIL with `ImportError: cannot import name '_norm_usage'`.

- [ ] **Step 3: Implement in `llm/provider.py`**

Add the helper near `_record_usage`:

```python
def _norm_usage(usage: dict) -> dict | None:
    """Normalize an OpenAI/Anthropic usage block to {input,output,total}, or None
    when empty. input=prompt/input tokens; output=completion/output tokens."""
    if not usage:
        return None
    inp = usage.get("prompt_tokens")
    if inp is None:
        inp = usage.get("input_tokens")
    out = usage.get("completion_tokens")
    if out is None:
        out = usage.get("output_tokens")
    return {"input": inp, "output": out, "total": usage.get("total_tokens")}
```

Refactor `_record_usage` to reuse it (replace its inline `norm = {...}` construction with `norm = _norm_usage(parsed.get("usage") or {})`, keeping the rest — the `gen.finish(...)` call — intact).

On the `LLMProvider` ABC, add the class attribute + wrapper (after the docstring, before `complete_messages`):

```python
    last_usage: dict | None = None  # {input,output,total} of the most recent call

    def _post(self, url: str, headers: dict, body: dict, **kw) -> dict:
        """_do_post + capture normalized token usage onto self.last_usage."""
        resp = _do_post(url, headers, body, **kw)
        self.last_usage = _norm_usage(resp.get("usage") or {})
        return resp
```

Replace every real-provider `_do_post(url, headers, body)` / `_do_post(url, headers, body, ...)` call inside `OpenAIProvider`, `ZhipuProvider`, `AnthropicProvider` (the 8 sites: complete / complete_messages / complete_with_tools for each) with `self._post(...)` — identical args. Do NOT change the module-level `_do_post` definition or `FakeLLMProvider`/`ScriptedToolProvider` (they don't hit HTTP; they inherit `last_usage = None`).

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. python3 -m pytest tests/llm/test_provider_usage.py -v`
Expected: PASS.

Run regression (providers are widely used): `PYTHONPATH=. python3 -m pytest tests/llm -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add llm/provider.py tests/llm/test_provider_usage.py
git commit -m "feat(provider): expose last_usage (normalized prompt/completion tokens) via _post wrapper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Compaction trigger (70% of 200K)

**Files:**
- Modify: `loop/strategy.py`
- Test: `tests/loop/test_multiturn.py`

**Interfaces:**
- Consumes: `provider.last_usage` (Task 6); `_compaction_due` (Task 3); the `produce` fresh-turn paths (Task 4).
- Produces: module constants `CONTEXT_WINDOW = 200_000`, `COMPACTION_RATIO = 0.70`; `AuthorStrategy._maybe_flag_compaction(self, provider) -> None` setting `_compaction_due` when `provider.last_usage["input"] > CONTEXT_WINDOW * COMPACTION_RATIO`; `produce` calls it after each fresh-turn provider call (multiturn only). When `_compaction_due`, the next fresh turn takes the full-rebuild path (already implemented in Task 4) and resets the thread.

- [ ] **Step 1: Write the failing test**

Add to `tests/loop/test_multiturn.py`:

```python
def test_usage_over_threshold_flags_then_compacts(monkeypatch):
    settings.set_conversation_mode("multiturn")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("一"), _commit_json("二")])
    world = {"meta": {"day": 1, "band": 0}, "systems": {}}
    scene = {"protagonist": "protagonist", "present": [], "day": 1, "location": "loc1"}

    c1 = s.produce(None, world, scene, "动作一", provider=prov)
    prov.last_usage = {"input": 150000, "output": 10, "total": 150010}  # > 140k
    s._maybe_flag_compaction(prov)
    assert s._compaction_due is True
    s.commit_to_thread(c1.narration)

    # Next fresh turn: compaction_due → full rebuild path → thread reset to [system].
    s.produce(None, world, scene, "动作二", provider=prov)
    assert s._compaction_due is False              # consumed
    assert len(s._thread) == 1                      # reset to bare system base
    assert s._messages[0]["role"] == "system"       # rebuilt full, not a delta
    settings.set_conversation_mode("multiturn")


def test_usage_under_threshold_does_not_flag(monkeypatch):
    settings.set_conversation_mode("multiturn")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("一")])
    world = {"meta": {"day": 1, "band": 0}, "systems": {}}
    scene = {"protagonist": "protagonist", "present": [], "day": 1, "location": "loc1"}
    s.produce(None, world, scene, "动作", provider=prov)
    prov.last_usage = {"input": 1000, "output": 10, "total": 1010}
    s._maybe_flag_compaction(prov)
    assert s._compaction_due is False
    settings.set_conversation_mode("multiturn")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_multiturn.py -k threshold -v`
Expected: FAIL with `AttributeError: 'AuthorStrategy' object has no attribute '_maybe_flag_compaction'`.

- [ ] **Step 3: Implement in `loop/strategy.py`**

Add module constants near the top (after imports):

```python
CONTEXT_WINDOW = 200_000      # model context window (tokens)
COMPACTION_RATIO = 0.70       # compact when a call's prompt tokens cross this fraction
```

Add the method on `AuthorStrategy`:

```python
    def _maybe_flag_compaction(self, provider) -> None:
        """Flag the next fresh turn to rebuild full context when the last call's
        prompt size crossed the compaction threshold. Relies on provider.last_usage
        (None when the provider reports no usage → never flags)."""
        usage = getattr(provider, "last_usage", None)
        tok = usage.get("input") if usage else None
        if tok and tok > CONTEXT_WINDOW * COMPACTION_RATIO:
            self._compaction_due = True
```

In `produce`, call it after each fresh-turn provider call (multiturn only). In the tool path, after `self._messages.append({"role": "assistant", "content": raw})` and before `data = _data_or_safe(raw)`:

```python
                if repair is None and multiturn:
                    self._maybe_flag_compaction(provider)
```

And in the plain path, after `self._messages.append({"role": "assistant", "content": raw})`:

```python
        if repair is None and multiturn:
            self._maybe_flag_compaction(provider)
```

(Repairs never trigger compaction — the flag is consumed only at the next fresh turn.)

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_multiturn.py -v`
Expected: PASS (all).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. python3 -m pytest -q`
Expected: PASS (~1616 + new tests; stateless pin keeps existing behavior).

- [ ] **Step 6: Commit**

```bash
git add loop/strategy.py tests/loop/test_multiturn.py
git commit -m "feat(strategy): compaction trigger — flag full rebuild at 70% of 200K window

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- D1 conversation_mode setting → Task 1. ✓
- D2 thread vs working + state fields → Task 3 (+ used in Task 4). ✓
- D3 per-turn flow (first/continuing/repair/compaction/stateless) → Task 4. ✓
- D4 delta → Task 2. ✓
- D5 commit_to_thread on success → Tasks 3 + 5. ✓
- D6 compaction trigger + provider.last_usage → Tasks 6 + 7. ✓
- D7 narration-not-JSON in thread → Task 3/4 (asserted in `test_multiturn_thread_stores_narration_not_raw_json`). ✓
- Decision table (200K/70%/K=3/narration/multiturn-default/token-trigger) → constants in Task 7, default in Task 1, K=3 needs no code (thread holds recent verbatim until compaction; post-compaction recent-N is the narrative system's existing rendering). ✓
- Drift/safety: stateless fallback (Task 1 + byte-identical suite), POV tools still per fresh turn (unchanged in Task 4). ✓
- Phasing: P1 = Tasks 1-5; P2 = Tasks 6-7. ✓

**Placeholder scan:** No TBD/TODO. Task 5 Step 1 carries an implementer NOTE to confirm `produce_turn`'s real signature (the test calls it) — this is a verification instruction with a concrete fallback, not a placeholder; the code to add (Step 3) is complete.

**Type consistency:** `last_usage` is `dict|None` with keys `input/output/total` everywhere (Tasks 6,7). `_compaction_due` bool set in Task 3, read/cleared in Tasks 4,7. `_build_delta(registry, world, scene, player_input)` signature consistent (Tasks 2,4). `commit_to_thread(narration)` consistent (Tasks 3,5). `_messages` = working, `_thread` = persistent — used consistently.

**Out of scope (per spec):** bespoke compact index renderer, streaming, char-count proxy, thread persistence across restarts.
