# Flavor Pack System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the genesis aesthetic (seed tables + narration voice + tone behaviour) a swappable flavor pack; ship `classic` (byte-identical to today) and `isekai`.

**Architecture:** A flavor pack is `data/oracles/<flavor>/` + `pack.json`. `load_table` gains a `base=` fallback so a pack overrides only the tables it changes, falling back to `classic`. Genesis threads an active-flavor string (default `"classic"`); the pack's `voice` feeds the existing `__STYLE__` prompt seam, and its `tone` config drives the tone mood-bias. Flavor resolves from `--flavor`/pitch/default and persists in the `campaign_seeded` event so resumed sessions keep their voice.

**Tech Stack:** Python 3 (no venv, interpreter `python3`), pytest, JSON oracle tables. Run tests with `PYTHONPATH=. python3 -m pytest`.

## Global Constraints

- **Byte-identical classic:** with no `FLAVOR`/`--flavor` and a non-isekai pitch, genesis rolls AND the narration prompt must be identical to pre-change `main`. This is the primary regression gate.
- **Full suite green at every task:** `PYTHONPATH=. python3 -m pytest -q` (currently 1666 passed, 1 deselected) must stay green after each task.
- **Commit per task**, messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Interpreter `python3`, `PYTHONPATH=/root/rpg-engine-app`. Branch `app`. Never `git init/reset --hard/rebase`.
- Genesis loads pass `base="classic"`; all non-genesis `load_table` callers keep the default `base="default"` (do not touch `loop/director.py`, `engine/cli.py`, `engine/seed.py`).

## File Structure

- `engine/oracle.py` — `load_table(name, genre=None, base="default")`; new `load_pack_manifest(flavor)`.
- `data/oracles/genesis/*` → `data/oracles/classic/*` (git mv) + new `classic/pack.json`.
- `data/oracles/isekai/*` (new: protagonist_origins/hooks/quirks, tone_axes) + `isekai/pack.json`.
- `loop/genesis/world.py`, `loop/genesis/cast.py`, `loop/genesis/lore.py` — thread `flavor="classic"`; tone reads manifest.
- `loop/strategy.py` — delete hardcoded voice directives; voice from `_settings`.
- `engine/settings.py` — `_pack_voice` default + `set_pack_voice`/`get_style` resolution.
- `app/engine.py` — flavor resolution + manifest load + `world["flavor"]`.
- `app/__main__.py` — `--flavor` flag; store at genesis, recover on resume.
- `run.sh` — `FLAVOR=""` knob.

---

## Phase P1 — pack loader + classic migration (regression gate)

### Task 1: `load_table` gains a `base=` fallback

**Files:**
- Modify: `engine/oracle.py:41-47`
- Test: `tests/engine/test_oracle.py` (create if absent; else append)

**Interfaces:**
- Produces: `load_table(name, genre=None, base="default") -> list|dict` — resolves `data/oracles/<genre>/<name>.json` then `data/oracles/<base>/<name>.json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_oracle.py
from engine.oracle import load_table

def test_load_table_base_fallback(tmp_path, monkeypatch):
    import engine.oracle as o
    root = tmp_path / "oracles"
    (root / "flavorX").mkdir(parents=True)
    (root / "classic").mkdir(parents=True)
    (root / "flavorX" / "only_here.json").write_text('[{"name":"X"}]', encoding="utf-8")
    (root / "classic" / "fallback_me.json").write_text('[{"name":"C"}]', encoding="utf-8")
    monkeypatch.setattr(o, "_ORACLE_DIR", root)
    # present in flavor dir -> flavor wins
    assert load_table("only_here", "flavorX", base="classic") == [{"name": "X"}]
    # absent in flavor -> falls back to base=classic (NOT default)
    assert load_table("fallback_me", "flavorX", base="classic") == [{"name": "C"}]

def test_load_table_default_base_unchanged():
    # existing callers (no base) still fall back to default/
    import engine.oracle as o
    assert isinstance(load_table("event_types"), list)  # lives in default/
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/engine/test_oracle.py -q`
Expected: FAIL (`load_table() got an unexpected keyword argument 'base'`).

- [ ] **Step 3: Implement**

```python
# engine/oracle.py — replace load_table
def load_table(name, genre=None, base="default"):
    """Load data/oracles/<genre>/<name>.json, falling back to <base>/ then default/."""
    chain = ([genre] if genre else []) + [base, "default"]
    seen = set()
    for sub in chain:
        if sub in seen:
            continue
        seen.add(sub)
        p = _ORACLE_DIR / sub / f"{name}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"oracle table not found: {name} (genre={genre}, base={base})")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python3 -m pytest tests/engine/test_oracle.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/oracle.py tests/engine/test_oracle.py
git commit -m "feat(oracle): load_table base= fallback for flavor packs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Migrate `genesis/` → `classic/` and thread the flavor arg

Renames the table dir and threads a `flavor="classic"` parameter through every genesis generator so table loads use `load_table(name, flavor, base="classic")`. Default `"classic"` keeps all existing tests + callers byte-identical (classic/ holds the same files genesis/ did).

**Files:**
- Move: `data/oracles/genesis/` → `data/oracles/classic/` (git mv, all 12 json files)
- Modify: `loop/genesis/world.py` (`gen_frame`, `_roll_world_seeds`, `gen_local_map`, loads at :36-38,106,294,528)
- Modify: `loop/genesis/cast.py` (`gen_protagonist`→`_roll_protagonist_seeds`, `gen_npcs`, loads at :36-38,379,384)
- Modify: `loop/genesis/lore.py` (`gen_threads` chain, load at :297)
- Test: existing genesis suite is the regression gate (`tests/loop/test_bootstrap.py`, `test_tone_mood_bias.py`, etc.)

**Interfaces:**
- Produces: each generator accepts a keyword `flavor: str = "classic"`; internal loads become `load_table(<name>, flavor, base="classic")`.
- Consumes: `load_table(..., base=...)` from Task 1.

- [ ] **Step 1: Move the table directory**

```bash
git mv data/oracles/genesis data/oracles/classic
```

- [ ] **Step 2: Run the suite to see the expected breakage**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap.py -q`
Expected: FAIL (`oracle table not found: ... genre=genesis` — the loads still pass `"genesis"`, dir is gone).

- [ ] **Step 3: Update `_roll_world_seeds` + `gen_frame` (world.py)**

```python
# loop/genesis/world.py
def _roll_world_seeds(oracle, provided: dict, flavor: str = "classic") -> dict:
    magic_roll = oracle.draw(load_table("world_magic", flavor, base="classic"))["name"]
    power_roll = oracle.draw(load_table("world_power", flavor, base="classic"))["name"]
    tension_roll = oracle.draw(load_table("world_tension", flavor, base="classic"))["name"]
    p = provided or {}
    return {
        "magic_system": p.get("magic_system") or magic_roll,
        "power_ladder": p.get("power_ladder") or power_roll,
        "world_tension": p.get("world_tension") or tension_roll,
    }
```

In `gen_frame` add `flavor: str = "classic"` to the signature (after `provided`), and:
```python
    tone_roll = _tone_for_pitch(oracle, pitch, load_table("tone_axes", flavor, base="classic"))
    ...
    world_seeds = _roll_world_seeds(oracle, provided, flavor)
```

- [ ] **Step 4: Update `gen_local_map` (world.py) loads at :294,:528**

Add `flavor: str = "classic"` to `gen_local_map`'s signature and change:
```python
    terrain_entries = _draw_distinct(oracle, load_table("terrains", flavor, base="classic"), n)
    ...
    neighbor_kind_entries = _draw_distinct(oracle, load_table("place_kinds", flavor, base="classic"), n_extra_l2)
```

- [ ] **Step 5: Update `_roll_protagonist_seeds` + `gen_protagonist` + `gen_npcs` (cast.py)**

```python
# loop/genesis/cast.py
def _roll_protagonist_seeds(oracle, flavor: str = "classic") -> dict:
    return {
        "origin": oracle.draw(load_table("protagonist_origins", flavor, base="classic"))["name"],
        "hook": oracle.draw(load_table("protagonist_hooks", flavor, base="classic"))["name"],
        "quirk": oracle.draw(load_table("protagonist_quirks", flavor, base="classic"))["name"],
    }
```
Add `flavor: str = "classic"` to `gen_protagonist` and pass it: `seeds = _roll_protagonist_seeds(oracle, flavor)`.
Add `flavor: str = "classic"` to `gen_npcs` and change:
```python
    role_entries = _draw_distinct(oracle, load_table("npc_roles", flavor, base="classic"), n)
    ...
    traits_table = load_table("npc_traits", flavor, base="classic")
```

- [ ] **Step 6: Update `gen_threads` (lore.py) load at :297**

Add `flavor: str = "classic"` to `gen_threads` (and its inner helper that owns :297 if separate), and change:
```python
    type_entries = _draw_distinct(oracle, load_table("thread_types", flavor, base="classic"), n)
```

- [ ] **Step 7: Run the full suite (regression gate)**

Run: `PYTHONPATH=. python3 -m pytest -q`
Expected: PASS, same count as before the task (1666). Existing tests call generators without `flavor` → defaults to `"classic"` → loads the migrated tables → identical rolls.

- [ ] **Step 8: Commit**

```bash
git add data/oracles/classic loop/genesis/world.py loop/genesis/cast.py loop/genesis/lore.py
git commit -m "refactor(genesis): rename genesis/ tables to classic/ + thread flavor arg

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Pack manifest loader + `classic/pack.json`

**Files:**
- Modify: `engine/oracle.py` (add `load_pack_manifest`)
- Create: `data/oracles/classic/pack.json`
- Test: `tests/engine/test_oracle.py` (append)

**Interfaces:**
- Produces: `load_pack_manifest(flavor: str) -> dict` — returns the parsed `<flavor>/pack.json`, or `{}` if the file is missing (never raises on missing; raises only on malformed JSON with a clear message).

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_oracle.py (append)
from engine.oracle import load_pack_manifest

def test_load_pack_manifest_classic_has_voice_and_tone():
    m = load_pack_manifest("classic")
    assert m["name"]
    assert m["voice"]                     # classic voice = the old literary directive
    assert set(m["tone"]) >= {"bright", "dark", "hints_bright", "hints_dark"}

def test_load_pack_manifest_missing_returns_empty():
    assert load_pack_manifest("no_such_flavor") == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/engine/test_oracle.py -k manifest -q`
Expected: FAIL (`cannot import name 'load_pack_manifest'`).

- [ ] **Step 3: Implement the loader**

```python
# engine/oracle.py (add)
def load_pack_manifest(flavor: str) -> dict:
    """Load data/oracles/<flavor>/pack.json. Missing file -> {} (neutral defaults).
    Malformed JSON raises ValueError with the path."""
    p = _ORACLE_DIR / flavor / "pack.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"malformed pack manifest {p}: {e}") from e
```

- [ ] **Step 4: Create `classic/pack.json`** (voice = the current hardcoded 丙 directive verbatim; tone = the current constants from `loop/genesis/world.py`)

```json
{
  "name": "经典·硬核西幻/武侠",
  "desc": "接地气的西幻/武侠世界，细腻文学笔法。",
  "voice": "融合细腻描写与戏剧张力：重环境氛围、角色的神态动作与内心、以及有张力的对话；多用具体可感的细节，少堆空泛形容。",
  "tone": {
    "bright": ["冒险", "热血", "日常", "治愈"],
    "dark": ["悬疑", "生存", "恩怨", "权谋"],
    "hints_bright": ["轻松","明亮","王道","日常","温馨","治愈","热血","搞笑","欢乐","阳光","轻快","冒险","甜"],
    "hints_dark": ["黑暗","暗黑","残酷","绝望","压抑","阴郁","沉重","惊悚","恐怖","致郁","血腥","悲"]
  },
  "select_hints": ["武侠","西幻","克苏鲁","权谋","硬核"]
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `PYTHONPATH=. python3 -m pytest tests/engine/test_oracle.py -k manifest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add engine/oracle.py data/oracles/classic/pack.json tests/engine/test_oracle.py
git commit -m "feat(oracle): pack manifest loader + classic/pack.json

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase P2 — voice extraction

### Task 4: Extract the hardcoded voice into the pack, injected via settings

Removes the hardcoded voice directives from the prompt templates; the injected `__STYLE__` fragment now carries the pack's voice (explicit `RPG_NARRATION_STYLE`/`--style`/`/style` still overrides).

**Files:**
- Modify: `engine/settings.py` (`_pack_voice` + `set_pack_voice` + `get_style` resolution)
- Modify: `loop/strategy.py:169,221` (remove hardcoded voice text)
- Test: `tests/test_fix9_settings.py` (append) + `tests/loop/test_strategy.py`

**Interfaces:**
- Produces: `settings.set_pack_voice(v: str)`, and `settings.get_style()` returns explicit `_style` if set, else `_pack_voice` (else `""`).
- Consumes: `_style_fragment(get_style())` in `loop/strategy.py` (unchanged mechanism).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fix9_settings.py (append)
import engine.settings as st

def test_pack_voice_is_default_style_when_no_explicit():
    st.reset_from_env()                 # clears explicit style
    st.set_pack_voice("日式轻小说笔法")
    assert st.get_style() == "日式轻小说笔法"

def test_explicit_style_overrides_pack_voice():
    st.set_pack_voice("pack默认")
    st.set_style("玩家指定")             # explicit wins
    assert st.get_style() == "玩家指定"
    st.reset_from_env()
```

Also assert the hardcoded literary line is gone from the template:
```python
# tests/loop/test_strategy.py (append)
from loop import strategy
def test_narrate_template_has_no_hardcoded_voice():
    assert "融合细腻描写与戏剧张力" not in strategy._NARRATE_PROMPT_TEMPLATE
    assert "重环境氛围" not in strategy._NARRATE_PROMPT_TEMPLATE
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/test_fix9_settings.py -k pack_voice tests/loop/test_strategy.py -k hardcoded -q`
Expected: FAIL (`set_pack_voice` missing; template still contains the line).

- [ ] **Step 3: Implement settings**

```python
# engine/settings.py — add near _style
_pack_voice: str = ""

def set_pack_voice(v: str) -> None:
    global _pack_voice
    _pack_voice = _parse_style(v)

# modify get_style() to resolve explicit-over-pack:
def get_style() -> str:
    return _style or _pack_voice
```
Add `_pack_voice` to the `global` list in `reset_from_env` and reset it to `""` there.

- [ ] **Step 4: Remove hardcoded voice from templates**

`loop/strategy.py:169` — change the 甲 line to drop the trailing voice clause, keeping mechanics:
```
__STYLE__【narration 文风】__VERBOSITY__推进局面但绝不替玩家决定下一步；严守保密事实，绝不在 narration 中直接揭露。
```
`loop/strategy.py:221` — change the 丙 line to only the injected style + a neutral mechanic:
```
__STYLE__【文风】以具体可感的细节叙事，少堆空泛形容。
```
(The literary directive now lives in `classic/pack.json.voice`, injected via `__STYLE__`.)

- [ ] **Step 5: Run to verify it passes + full suite**

Run: `PYTHONPATH=. python3 -m pytest -q`
Expected: PASS. Note: some prompt-snapshot tests may assert old text — update them to the new template (voice now arrives via `__STYLE__`). If a test asserted "重环境氛围" appears in the built prompt, set `set_pack_voice(load_pack_manifest("classic")["voice"])` in that test's setup or assert on the injected fragment instead.

- [ ] **Step 6: Commit**

```bash
git add engine/settings.py loop/strategy.py tests/test_fix9_settings.py tests/loop/test_strategy.py
git commit -m "refactor(strategy): voice from pack, drop hardcoded literary directive

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase P3 — tone-bias into the manifest

### Task 5: `_tone_for_pitch` reads the pack's tone config

Moves `_BRIGHT_TONES`/`_DARK_TONES`/`_BRIGHT_PITCH_HINTS`/`_DARK_PITCH_HINTS` out of `world.py` into the pack manifest; `_tone_for_pitch` takes a `tone_cfg`.

**Files:**
- Modify: `loop/genesis/world.py` (`_tone_for_pitch` signature + `gen_frame` passes cfg; remove the 4 constants)
- Test: `tests/loop/test_tone_mood_bias.py` (update)

**Interfaces:**
- Produces: `_tone_for_pitch(oracle, pitch, table, tone_cfg: dict) -> str`. `tone_cfg` has `bright`/`dark`/`hints_bright`/`hints_dark` lists; empty/missing → full-table draw.

- [ ] **Step 1: Update the test to pass a cfg**

```python
# tests/loop/test_tone_mood_bias.py — replace the module constants import
from engine.oracle import Oracle, load_table, load_pack_manifest
from loop.genesis.world import _tone_for_pitch
_TABLE = load_table("tone_axes", "classic", base="classic")
_CFG = load_pack_manifest("classic")["tone"]
_BRIGHT = set(_CFG["bright"]); _DARK = set(_CFG["dark"])

def test_bright_pitch_always_draws_a_bright_tone():
    for seed in range(30):
        t = _tone_for_pitch(Oracle(seed), "轻松明亮的王道异世界冒险谭", _TABLE, _CFG)
        assert t in _BRIGHT, f"seed={seed} drew {t!r}"

def test_dark_pitch_always_draws_a_dark_tone():
    for seed in range(30):
        t = _tone_for_pitch(Oracle(seed), "黑暗残酷压抑绝望的末世", _TABLE, _CFG)
        assert t in _DARK
```
(Keep the neutral + contradictory + one-roll-determinism tests, passing `_CFG`.)

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_tone_mood_bias.py -q`
Expected: FAIL (`_tone_for_pitch()` takes 3 args / import of removed constants).

- [ ] **Step 3: Implement**

```python
# loop/genesis/world.py — replace the 4 constants + _tone_for_pitch
def _tone_for_pitch(oracle, pitch: str, table: list[dict], tone_cfg: dict) -> str:
    """Draw a tone biased toward the pitch's mood, using the pack's tone config."""
    cfg = tone_cfg or {}
    bright_tones = set(cfg.get("bright") or [])
    dark_tones = set(cfg.get("dark") or [])
    text = pitch or ""
    bright = any(h in text for h in (cfg.get("hints_bright") or []))
    dark = any(h in text for h in (cfg.get("hints_dark") or []))
    if bright and not dark and bright_tones:
        subset = [e for e in table if e["name"] in bright_tones]
    elif dark and not bright and dark_tones:
        subset = [e for e in table if e["name"] in dark_tones]
    else:
        subset = table
    return oracle.draw(subset or table)["name"]
```
In `gen_frame`, load the cfg and pass it:
```python
    _tone_cfg = load_pack_manifest(flavor).get("tone") or {}
    tone_roll = _tone_for_pitch(oracle, pitch, load_table("tone_axes", flavor, base="classic"), _tone_cfg)
```

- [ ] **Step 4: Run to verify + full suite**

Run: `PYTHONPATH=. python3 -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add loop/genesis/world.py tests/loop/test_tone_mood_bias.py
git commit -m "refactor(genesis): tone mood-bias reads pack manifest, not hardcoded constants

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase P4 — flavor selection + persistence

### Task 6: Resolve the active flavor + persist it at genesis

**Files:**
- Create: `loop/flavor.py` (resolution helper)
- Modify: `app/engine.py` (call resolver, load manifest, set pack voice, expose `world["flavor"]`)
- Modify: `loop/bootstrap.py` (`bootstrap_world` accepts `flavor`, stores it in `campaign_seeded` deltas, threads to generators)
- Test: `tests/loop/test_flavor.py` (create)

**Interfaces:**
- Produces: `resolve_flavor(explicit: str|None, pitch: str, available: list[str]) -> str` — explicit (validated) → pitch `select_hints` match → `"classic"`.
- Produces: `available_flavors() -> list[str]` — dirs under `data/oracles/` that contain a `pack.json`.
- Consumes: `load_pack_manifest`, `bootstrap_world(..., flavor=...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/loop/test_flavor.py
from loop.flavor import resolve_flavor, available_flavors

def test_available_includes_classic_and_isekai():
    av = available_flavors()
    assert "classic" in av and "isekai" in av

def test_explicit_wins():
    assert resolve_flavor("isekai", "普通西幻", ["classic","isekai"]) == "isekai"

def test_unknown_explicit_raises():
    import pytest
    with pytest.raises(ValueError):
        resolve_flavor("nope", "", ["classic","isekai"])

def test_pitch_autoselect_isekai():
    assert resolve_flavor(None, "社畜穿越异世界轻小说", ["classic","isekai"]) == "isekai"

def test_pitch_neutral_is_classic():
    assert resolve_flavor(None, "一个剑与魔法的世界", ["classic","isekai"]) == "classic"
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_flavor.py -q`
Expected: FAIL (module missing). (`isekai` availability fails until Task 8 — mark that one test xfail or run `-k "not isekai"` until Task 8; re-enable in Task 8.)

- [ ] **Step 3: Implement `loop/flavor.py`**

```python
from engine.oracle import load_pack_manifest, _ORACLE_DIR

def available_flavors() -> list[str]:
    return sorted(d.name for d in _ORACLE_DIR.iterdir()
                  if d.is_dir() and (d / "pack.json").exists())

def resolve_flavor(explicit, pitch: str, available: list[str]) -> str:
    if explicit:
        if explicit not in available:
            raise ValueError(f"unknown flavor {explicit!r}; available: {available}")
        return explicit
    text = pitch or ""
    for name in [n for n in available if n != "classic"]:   # classic is the default, checked last
        hints = load_pack_manifest(name).get("select_hints") or []
        if any(h in text for h in hints):
            return name
    return "classic"
```

- [ ] **Step 4: Thread flavor through `bootstrap_world`**

In `loop/bootstrap.py` `bootstrap_world(engine, pitch="", *, spec=None, attempt=0, progress=None, flavor="classic")`: add `flavor` to the `campaign_seeded` deltas (`deltas={"campaign_seed": campaign_seed, "flavor": flavor}`) and pass `flavor` to every `gen_*` call that now accepts it (`gen_frame`, `gen_local_map`, `gen_protagonist`, `gen_npcs`, `gen_threads`).

- [ ] **Step 5: Wire resolution in `app/engine.py`**

Where the engine is built / genesis kicked off (`new_game` path): resolve `flavor = resolve_flavor(explicit_flavor, pitch, available_flavors())`, call `settings.set_pack_voice(load_pack_manifest(flavor).get("voice",""))`, pass `flavor` to `bootstrap_world`. Expose the stored flavor via projection as `world["flavor"]` (read `campaign_seeded` deltas in the relevant ContextSystem/projection, defaulting `"classic"`).

- [ ] **Step 6: Run tests**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_flavor.py -k "not isekai" -q && PYTHONPATH=. python3 -m pytest -q`
Expected: PASS (isekai availability test deferred to Task 8).

- [ ] **Step 7: Commit**

```bash
git add loop/flavor.py app/engine.py loop/bootstrap.py tests/loop/test_flavor.py
git commit -m "feat(genesis): resolve + persist active flavor (explicit/pitch/classic)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Recover flavor voice on resume

**Files:**
- Modify: `app/engine.py` or `app/__main__.py` (on load of an existing store, read `world["flavor"]` → `set_pack_voice`)
- Test: `tests/app/test_flavor_resume.py` (create)

**Interfaces:**
- Consumes: `world["flavor"]` projection from Task 6; `settings.set_pack_voice`, `load_pack_manifest`.

- [ ] **Step 1: Write the failing test**

```python
# tests/app/test_flavor_resume.py — genesis with flavor, rebuild engine, assert voice recovered
# (mirror the _build_engine + canned provider pattern from tests/app/test_ux_fixes_45.py)
def test_resume_recovers_pack_voice(tmp_path):
    import engine.settings as st
    # ... new_game with flavor="isekai" into tmp_path, then simulate a fresh launch:
    st.reset_from_env()                       # explicit style cleared (fresh process)
    # load existing store -> engine wiring should call set_pack_voice(isekai voice)
    # assert st.get_style() == load_pack_manifest("isekai")["voice"]
```

- [ ] **Step 2: Run to verify it fails**; **Step 3:** on the resume path (existing store branch in `app/__main__.py`), after building the engine read `world["flavor"]` and `set_pack_voice(load_pack_manifest(flavor).get("voice",""))`. **Step 4:** run suite. **Step 5:** commit.

```bash
git add app/__main__.py tests/app/test_flavor_resume.py
git commit -m "feat(play): resumed campaign recovers its flavor voice

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase P5 — isekai pack + run.sh knob

### Task 8: The isekai content pack

**Files:**
- Create: `data/oracles/isekai/protagonist_origins.json`, `protagonist_hooks.json`, `protagonist_quirks.json`, `tone_axes.json`, `pack.json`
- Test: `tests/loop/test_flavor.py` (re-enable isekai assertions) + `tests/loop/test_isekai_pack.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/loop/test_isekai_pack.py
from engine.oracle import load_table, load_pack_manifest
from engine.oracle import Oracle
from loop.genesis.cast import _roll_protagonist_seeds

def test_isekai_origins_are_modern_transported():
    names = [e["name"] for e in load_table("protagonist_origins", "isekai", base="classic")]
    assert any("社畜" in n or "高中生" in n or "穿越" in n or "魂穿" in n for n in names)

def test_isekai_seeds_roll_from_isekai_pool():
    seeds = _roll_protagonist_seeds(Oracle(3), "isekai")
    allnames = {e["name"] for e in load_table("protagonist_origins","isekai",base="classic")}
    assert seeds["origin"] in allnames

def test_isekai_manifest_voice_is_light_novel():
    assert "轻小说" in load_pack_manifest("isekai")["voice"]
```

- [ ] **Step 2: Run to verify it fails**; **Step 3:** create the 5 files from the spec's "isekai" section (origins/hooks/quirks/tone_axes verbatim, and `isekai/pack.json` with the manifest from the spec's Architecture example). **Step 4:** run `PYTHONPATH=. python3 -m pytest tests/loop/test_isekai_pack.py tests/loop/test_flavor.py -q` (re-enable the isekai availability test). **Step 5:** run full suite. **Step 6:** commit.

```bash
git add data/oracles/isekai tests/loop/test_isekai_pack.py tests/loop/test_flavor.py
git commit -m "feat(flavor): isekai content pack (社畜/高中生/魂穿 + 日式轻小说 voice)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: `--flavor` CLI flag + run.sh knob + end-to-end

**Files:**
- Modify: `app/__main__.py` (add `--flavor` argparse; pass to the resolver in Task 6)
- Modify: `run.sh` (`FLAVOR=""` knob + arg wiring)
- Test: `tests/app/test_flavor_cli.py` (create — argparse parses `--flavor`; unknown errors)

- [ ] **Step 1: Write the failing test** (argparse accepts `--flavor`, and an end-to-end new_game with `--flavor isekai` stores flavor=isekai in the first event).

- [ ] **Step 2: Run to verify it fails**; **Step 3:** add:
```python
# app/__main__.py argparse
parser.add_argument("--flavor", default=None, dest="flavor",
    help="World flavor pack: classic | isekai (default: auto by pitch, else classic).")
```
Pass `args.flavor` into the flavor resolution from Task 6.

- [ ] **Step 4: run.sh** — add in the config block:
```bash
FLAVOR=""                 # 世界风味包。留空=按 pitch 自动认;可填 classic(硬核西幻/武侠) | isekai(日式轻小说异世界穿越)
```
and in the args assembly: `if [ -n "$FLAVOR" ]; then args+=(--flavor "$FLAVOR"); fi`.

- [ ] **Step 5:** run full suite; `bash -n run.sh`. **Step 6:** commit.

```bash
git add app/__main__.py run.sh tests/app/test_flavor_cli.py
git commit -m "feat(cli): --flavor flag + run.sh FLAVOR knob

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] `PYTHONPATH=. python3 -m pytest -q` fully green.
- [ ] Manual: `FLAVOR=classic` genesis identical to a pre-change run (spot-check tone + protagonist archetype pools + narration prompt).
- [ ] Manual: `FLAVOR=isekai` (or an isekai pitch) → 社畜/高中生/魂穿-type protagonist, bright tone, light-novel prose.
- [ ] `world["flavor"]` persists; resume keeps the voice.
