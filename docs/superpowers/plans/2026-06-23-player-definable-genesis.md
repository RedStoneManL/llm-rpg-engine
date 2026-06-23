# Player-Definable Genesis + SillyTavern Conversion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the player define any genesis part (via a blueprint file or an interactive required-gate), have the model fill the rest, and (phase 2) import SillyTavern world-books / character-cards by LLM-translating them into the same native spec.

**Architecture:** A pure `GenesisSpec` dict is the single source `bootstrap_world` consumes. Each `gen_*` generator gains an optional `provided=` part that wins over both the engine roll and the LLM author — scalars replace, lists augment to `max(provided, rolled)`. When no spec is provided, every code path is byte-identical to today.

**Tech Stack:** Python 3.10+, stdlib + the existing `Oracle` / `complete_structured` / `kernel_event` seams. Opportunistic `pyyaml` for blueprint files. No new third-party deps; no new event types.

Spec: `docs/superpowers/specs/2026-06-23-player-definable-genesis-design.md`.

## Global Constraints

- Run everything as `PYTHONPATH=/root/rpg-engine-app python3 -m pytest ...`; the repo is the hermes worktree on branch `app`.
- **Byte-identical baseline:** when a generator's `provided` is falsy and `bootstrap_world`'s `spec` is `None`/empty, behavior MUST equal today's. The pre-existing suite (`tests/loop/test_bootstrap.py`, `tests/app/test_engine.py`, ~1538 total) is the guard and MUST stay green after every task.
- **Generators never raise** — preserve every existing stub-fallback path. `provided` handling must not introduce an uncaught raise.
- **No new event types.** Generators emit the same events with different content/counts. Do not touch `kernel/`, `systems/`, or `projection`.
- **Determinism via `Oracle` only** — no wall-clock, no `random`, no `Date`. Always perform the same oracle draws the generator does today (roll, then override), so determinism is a clean function of `(campaign_seed, spec, attempt)`.
- Story text in Chinese; code/identifiers/keys in English.
- Commit per task. Stage ONLY the files named in that task — NEVER `git add -A`. Every commit message ends with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- No vacuous assertions (`assert x or True`, `assert True`). Every test must be able to fail.
- Interactive code (session-zero) is tested behind injected `inputs`/`out` seams, mirroring `tests/app/test_input_sanitize.py`.

## The override pattern (applies to every generator task)

Each generator keeps its current structure and adds `*, provided=None`. The
transformation is always:

1. `provided = provided or <empty default>` at the top.
2. **Structure rolls:** perform the SAME oracle draws as today, unconditionally
   (so the draw sequence is unchanged when nothing is provided), then choose
   `provided_value if not empty else rolled_value`. For counts, the effective
   count is `max(len(provided_list), rolled_count)`; pad rolled helper lists
   (terrains, kinds, roles) by cycling (`helper[i % len(helper)]`) so a larger
   provided count never index-errors.
3. **Content authoring:** if every authored field for the part is supplied by
   `provided`, SKIP the LLM call and build directly from `provided`; otherwise
   run the existing `complete_structured` / stub path unchanged, then override
   per-field/per-index with the non-empty `provided` values.

When `provided` is empty this collapses to exactly today's code path.

## File Structure

- Create `loop/genesis_spec.py` — `normalize` / `merge` / `missing_required` (pure).
- Create `loop/genesis_blueprint.py` — `load_blueprint(path)`.
- Create `app/session_zero.py` — `run_session_zero(...)`.
- Create `loop/import_sillytavern.py` — `convert_sillytavern(...)` (P2).
- Modify `loop/bootstrap.py` — `provided=` on every generator; `spec=` on `bootstrap_world` / `reroll_all` / `reroll_step`; store spec in `_state`.
- Modify `app/engine.py` — `new_game(..., spec=None)` + `resolve_genesis_spec(...)`.
- Modify `app/__main__.py` — `--genesis` / import flags + session-zero wiring.
- Create `docs/genesis-blueprint.md` + `genesis.example.yaml`.
- Create `tests/loop/test_genesis_spec.py`, `tests/loop/test_genesis_blueprint.py`, `tests/app/test_session_zero.py`, `tests/loop/test_bootstrap_provided.py`, `tests/loop/test_import_sillytavern.py`.

---

## Task 1: GenesisSpec model (`loop/genesis_spec.py`)

**Files:**
- Create: `loop/genesis_spec.py`
- Test: `tests/loop/test_genesis_spec.py`

**Interfaces:**
- Produces: `normalize(raw: dict | None) -> dict`, `merge(base: dict, overlay: dict) -> dict`, `missing_required(spec: dict) -> list[str]`. Required floor = `{"world_premise": "genre", "protagonist": "name"}`. Name-list parts dedup by `name`; `npcs`/`threads` concat; scalar parts (`world_premise`, `protagonist`, `local_map.town`) merge field-by-field with overlay-non-empty-wins; `local_map.venues`/`neighbors`/`regions`/`factions` augment by name; `opening` overlay-wins.

- [ ] **Step 1: Write the failing tests**

```python
# tests/loop/test_genesis_spec.py
from loop.genesis_spec import normalize, merge, missing_required


def test_normalize_drops_empty_fields_and_parts():
    spec = normalize({
        "world_premise": {"genre": "日式西幻", "tone": "  ", "world_name": ""},
        "protagonist": {},
        "factions": [{"name": "教会"}, {"name": "  "}, {"motivation": "x"}],
        "opening": "   ",
    })
    assert spec["world_premise"] == {"genre": "日式西幻"}
    assert "protagonist" not in spec          # all-empty part dropped
    assert spec["factions"] == [{"name": "教会"}]  # nameless/blank dropped
    assert "opening" not in spec


def test_normalize_none_and_garbage():
    assert normalize(None) == {}
    assert normalize("nope") == {}
    assert normalize({"unknown_key": 1}) == {}


def test_merge_scalar_overlay_wins_when_nonempty():
    base = normalize({"world_premise": {"genre": "a", "tone": "暗黑"}})
    overlay = normalize({"world_premise": {"genre": "b", "world_name": "X"}})
    out = merge(base, overlay)
    assert out["world_premise"] == {"genre": "b", "tone": "暗黑", "world_name": "X"}


def test_merge_name_list_augments_and_dedups():
    base = normalize({"factions": [{"name": "教会", "motivation": "m1"}]})
    overlay = normalize({"factions": [{"name": "教会", "motivation": "dup"},
                                       {"name": "盗贼公会"}]})
    out = merge(base, overlay)
    names = [f["name"] for f in out["factions"]]
    assert names == ["教会", "盗贼公会"]        # base kept, dup dropped, new appended


def test_merge_npcs_concat_no_dedup():
    base = normalize({"npcs": [{"sketch": "老者"}]})
    overlay = normalize({"npcs": [{"sketch": "老者"}]})
    out = merge(base, overlay)
    assert len(out["npcs"]) == 2               # concat, no dedup


def test_merge_local_map_town_and_venues():
    base = normalize({"local_map": {"town": {"name": "起点镇"},
                                     "venues": [{"name": "酒馆"}]}})
    overlay = normalize({"local_map": {"town": {"seed": "雾气弥漫"},
                                        "venues": [{"name": "铁铺"}]}})
    out = merge(base, overlay)
    assert out["local_map"]["town"] == {"name": "起点镇", "seed": "雾气弥漫"}
    assert [v["name"] for v in out["local_map"]["venues"]] == ["酒馆", "铁铺"]


def test_missing_required():
    assert set(missing_required({})) == {"world_premise", "protagonist"}
    assert missing_required(normalize({
        "world_premise": {"genre": "x"},
        "protagonist": {"name": "凛"},
    })) == []
    assert missing_required(normalize({
        "world_premise": {"tone": "x"},          # genre missing
        "protagonist": {"name": "凛"},
    })) == ["world_premise"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_genesis_spec.py -q`
Expected: FAIL (`ModuleNotFoundError: loop.genesis_spec`).

- [ ] **Step 3: Implement `loop/genesis_spec.py`**

```python
"""The canonical GenesisSpec — the single structured model bootstrap consumes.

Pure: no I/O, no LLM. normalize() coerces raw user input into the canonical
shape (dropping empties); merge() overlays one spec onto another (scalars
replace when non-empty, lists augment); missing_required() reports which
required parts lack their minimal field. "Required" is enforced by session-zero,
not here — bootstrap stays permissive.
"""
from __future__ import annotations

# Required floor: part -> the minimal field that must be non-empty.
_REQUIRED = {"world_premise": "genre", "protagonist": "name"}

_PREMISE_FIELDS = ("genre", "tone", "world_name", "central_conflict",
                   "n_factions", "n_regions")
_PROT_FIELDS = ("name", "origin", "goal", "objective")


def _empty(v) -> bool:
    """True for None, blank/whitespace string, empty list/dict."""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def _norm_name(s) -> str:
    return s.strip().lower() if isinstance(s, str) else ""


def _scalar_part(src, fields) -> dict:
    if not isinstance(src, dict):
        return {}
    return {k: src[k] for k in fields if k in src and not _empty(src[k])}


def _name_list(items) -> list:
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict) and not _empty(it.get("name"))]


def normalize(raw) -> dict:
    """Coerce a raw dict (or None) into a canonical GenesisSpec dict.

    Drops empty fields/parts so generators can use plain truthiness. Unknown
    top-level keys are ignored. Never raises.
    """
    if not isinstance(raw, dict):
        return {}
    spec: dict = {}

    wp = _scalar_part(raw.get("world_premise"), _PREMISE_FIELDS)
    if wp:
        spec["world_premise"] = wp
    prot = _scalar_part(raw.get("protagonist"), _PROT_FIELDS)
    if prot:
        spec["protagonist"] = prot

    lm_src = raw.get("local_map")
    if isinstance(lm_src, dict):
        lm: dict = {}
        town = _scalar_part(lm_src.get("town"), ("name", "seed"))
        if town:
            lm["town"] = town
        for key in ("venues", "neighbors"):
            cleaned = _name_list(lm_src.get(key))
            if cleaned:
                lm[key] = cleaned
        if lm:
            spec["local_map"] = lm

    for part in ("regions", "factions"):
        cleaned = _name_list(raw.get(part))
        if cleaned:
            spec[part] = cleaned

    for part in ("npcs", "threads"):
        items = raw.get(part)
        if isinstance(items, list):
            cleaned = [it for it in items
                       if isinstance(it, dict) and any(not _empty(v) for v in it.values())]
            if cleaned:
                spec[part] = cleaned

    if not _empty(raw.get("opening")):
        spec["opening"] = raw["opening"]

    return spec


def _merge_scalar(base, overlay) -> dict:
    out = dict(base or {})
    for k, v in (overlay or {}).items():
        if not _empty(v):
            out[k] = v
    return out


def _augment_by_name(base, overlay) -> list:
    out = list(base or [])
    seen = {_norm_name(it.get("name")) for it in out}
    for it in overlay or []:
        nm = _norm_name(it.get("name"))
        if nm and nm in seen:
            continue
        seen.add(nm)
        out.append(it)
    return out


def merge(base, overlay) -> dict:
    """Overlay `overlay` onto `base`.

    Scalars (world_premise/protagonist/local_map.town): overlay field wins iff
    non-empty. Name-lists (regions/factions/local_map.venues/neighbors):
    augment, dedup by name. npcs/threads: pure concatenation. opening: overlay
    wins iff non-empty.
    """
    base = base or {}
    overlay = overlay or {}
    out = dict(base)

    for part in ("world_premise", "protagonist"):
        if part in base or part in overlay:
            merged = _merge_scalar(base.get(part), overlay.get(part))
            if merged:
                out[part] = merged

    if "local_map" in base or "local_map" in overlay:
        b_lm = base.get("local_map") or {}
        o_lm = overlay.get("local_map") or {}
        lm: dict = {}
        town = _merge_scalar(b_lm.get("town"), o_lm.get("town"))
        if town:
            lm["town"] = town
        for key in ("venues", "neighbors"):
            aug = _augment_by_name(b_lm.get(key), o_lm.get(key))
            if aug:
                lm[key] = aug
        if lm:
            out["local_map"] = lm

    for part in ("regions", "factions"):
        if part in base or part in overlay:
            aug = _augment_by_name(base.get(part), overlay.get(part))
            if aug:
                out[part] = aug

    for part in ("npcs", "threads"):
        if part in base or part in overlay:
            out[part] = list(base.get(part) or []) + list(overlay.get(part) or [])

    if not _empty(overlay.get("opening")):
        out["opening"] = overlay["opening"]

    return out


def missing_required(spec) -> list:
    """Return required part names whose minimal field is empty/absent."""
    spec = spec or {}
    missing = []
    for part, field in _REQUIRED.items():
        p = spec.get(part)
        if not isinstance(p, dict) or _empty(p.get(field)):
            missing.append(part)
    return missing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_genesis_spec.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add loop/genesis_spec.py tests/loop/test_genesis_spec.py
git commit -m "feat: canonical GenesisSpec model (normalize/merge/missing_required)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Blueprint file loader (`loop/genesis_blueprint.py`)

**Files:**
- Create: `loop/genesis_blueprint.py`
- Test: `tests/loop/test_genesis_blueprint.py`

**Interfaces:**
- Consumes: `loop.genesis_spec.normalize`.
- Produces: `load_blueprint(path: str | Path) -> dict` (a normalized GenesisSpec). Raises `BlueprintError(str)` on unreadable/malformed input. `.json` → stdlib `json`; `.yaml`/`.yml` → `yaml.safe_load` (raise `BlueprintError` instructing JSON if pyyaml absent).

- [ ] **Step 1: Write the failing tests**

```python
# tests/loop/test_genesis_blueprint.py
import json
import pytest
from loop.genesis_blueprint import load_blueprint, BlueprintError


def test_load_json_blueprint(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({
        "world_premise": {"genre": "日式西幻"},
        "protagonist": {"name": "凛", "origin": "流浪剑士"},
        "factions": [{"name": "教会"}],
    }), encoding="utf-8")
    spec = load_blueprint(p)
    assert spec["world_premise"]["genre"] == "日式西幻"
    assert spec["protagonist"]["name"] == "凛"
    assert spec["factions"] == [{"name": "教会"}]


def test_load_yaml_blueprint(tmp_path):
    pytest.importorskip("yaml")
    p = tmp_path / "g.yaml"
    p.write_text(
        "world_premise:\n  genre: 日式西幻\nprotagonist:\n  name: 凛\n",
        encoding="utf-8")
    spec = load_blueprint(p)
    assert spec["world_premise"]["genre"] == "日式西幻"
    assert spec["protagonist"]["name"] == "凛"


def test_missing_file_raises(tmp_path):
    with pytest.raises(BlueprintError):
        load_blueprint(tmp_path / "nope.json")


def test_malformed_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(BlueprintError):
        load_blueprint(p)


def test_non_object_top_level_raises(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(BlueprintError):
        load_blueprint(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_genesis_blueprint.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `loop/genesis_blueprint.py`**

```python
"""Load a player-authored genesis blueprint file into a normalized GenesisSpec.

.json → stdlib json; .yaml/.yml → pyyaml if installed. The file may specify any
subset of any spec part; absent parts are model-filled at bootstrap.
"""
from __future__ import annotations

import json
from pathlib import Path

from loop.genesis_spec import normalize


class BlueprintError(Exception):
    """Raised when a blueprint file cannot be read or parsed."""


def load_blueprint(path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise BlueprintError(f"genesis blueprint not found: {path}")
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    try:
        if suffix in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError as e:
                raise BlueprintError(
                    f"{path} is YAML but pyyaml is not installed; "
                    f"use a .json blueprint or `pip install pyyaml`"
                ) from e
            raw = yaml.safe_load(text)
        else:
            raw = json.loads(text)
    except BlueprintError:
        raise
    except Exception as e:
        raise BlueprintError(f"failed to parse {path}: {e}") from e

    if not isinstance(raw, dict):
        raise BlueprintError(
            f"{path}: top-level must be an object/mapping, got {type(raw).__name__}")
    return normalize(raw)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_genesis_blueprint.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add loop/genesis_blueprint.py tests/loop/test_genesis_blueprint.py
git commit -m "feat: genesis blueprint file loader (JSON/YAML -> GenesisSpec)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Override for `gen_frame` + `gen_protagonist` + `gen_opening`

**Files:**
- Modify: `loop/bootstrap.py` (`gen_frame`, `gen_protagonist`, `gen_opening`)
- Test: `tests/loop/test_bootstrap_provided.py` (new; add the frame/protagonist/opening tests here)

**Interfaces:**
- Produces: `gen_frame(provider, oracle, pitch, *, provided=None)`, `gen_protagonist(provider, oracle, frame, local_map, *, provided=None)`, `gen_opening(provider, frame, world_summary, *, scene_loc, scene_loc_name=None, provided=None)`. `provided` is the matching spec part (`world_premise` dict / `protagonist` dict / `opening` string). Falsy `provided` ⇒ unchanged behavior.

- [ ] **Step 1: Write the failing tests**

```python
# tests/loop/test_bootstrap_provided.py
from engine.oracle import Oracle, scene_seed
from llm.provider import FakeLLMProvider
import loop.bootstrap as B


def _oracle(step="frame", attempt=0):
    return Oracle(scene_seed(12345, f"genesis:{step}", attempt))


def test_gen_frame_provided_scalars_win_and_skip_llm():
    # Both authored fields provided -> no provider needed, values used verbatim.
    evs, frame = B.gen_frame(
        provider=None, oracle=_oracle(), pitch="ignored",
        provided={"genre": "日式西幻", "world_name": "阿斯特兰",
                  "central_conflict": "魔王复苏", "n_regions": 4, "n_factions": 3},
    )
    assert frame["genre"] == "日式西幻"
    assert frame["world_name"] == "阿斯特兰"
    assert frame["central_conflict"] == "魔王复苏"
    assert frame["n_regions"] == 4 and frame["n_factions"] == 3


def test_gen_frame_no_provided_matches_pitch_path():
    # provided=None must behave exactly as the pitch-only path (deterministic).
    e1, f1 = B.gen_frame(provider=None, oracle=_oracle(), pitch="武侠")
    e2, f2 = B.gen_frame(provider=None, oracle=_oracle(), pitch="武侠", provided=None)
    assert f1 == f2
    assert f1["genre"] == "武侠"


def test_gen_protagonist_provided_name_kept_objective_authored():
    prov = FakeLLMProvider(json_responses=[{
        "name": "应被覆盖", "origin": "应被覆盖",
        "goal": "应被覆盖", "objective": "前往酒馆打听消息"}])
    frame = {"world_name": "阿斯特兰", "tone": "史诗", "central_conflict": "魔王复苏"}
    local_map = {"start_town": "town_0", "venues": ["venue_0"],
                 "venue_names": {"venue_0": "酒馆"}, "l2": [{"id": "town_0", "name": "起点镇"}]}
    _, authored = B.gen_protagonist(
        prov, _oracle("protagonist"), frame, local_map,
        provided={"name": "凛", "origin": "流浪剑士"})
    assert authored["name"] == "凛"               # provided wins
    assert authored["origin"] == "流浪剑士"
    assert authored["objective"] == "前往酒馆打听消息"  # authored (not provided)


def test_gen_opening_provided_used_verbatim():
    evs, narration = B.gen_opening(
        provider=None, frame={"world_name": "X"}, world_summary="...",
        scene_loc="venue_0", scene_loc_name="酒馆",
        provided="这是玩家自定义的开场白。")
    assert narration == "这是玩家自定义的开场白。"
    assert evs[0]["deltas"]["text"] == "这是玩家自定义的开场白。"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap_provided.py -q`
Expected: FAIL (`gen_frame() got an unexpected keyword argument 'provided'`).

- [ ] **Step 3: Modify the three generators in `loop/bootstrap.py`**

`gen_frame` — change signature to `def gen_frame(provider, oracle, pitch, *, provided=None):`. Replace the engine-rolls block + LLM block (lines computing `tone`/`n_factions`/`n_regions` and the `complete_structured` call through the `world_name`/`central_conflict` assignment) with:

```python
    provided = provided or {}

    # Engine-decided rolls — always drawn (order preserved), then overridden.
    tone_roll = oracle.draw(load_table("tone_axes", "genesis"))["name"]
    nf_roll = oracle.randint(3, 5)
    nr_roll = oracle.randint(3, 5)
    tone = provided.get("tone") or tone_roll
    n_factions = provided.get("n_factions") or nf_roll
    n_regions = provided.get("n_regions") or nr_roll
    genre = provided.get("genre") or pitch

    p_name = provided.get("world_name")
    p_conflict = provided.get("central_conflict")
    if p_name and p_conflict:
        world_name, central_conflict = p_name, p_conflict
    else:
        user = (  # ... UNCHANGED existing prompt, but use `genre` if it references pitch ...
            ...
        )
        obj, errors = complete_structured(
            provider, system=_SYSTEM_GEN_FRAME, user=user,
            validate=_validate_frame, max_repairs=2, log_label="gen_frame")
        if errors or obj is None:
            world_name = "未名之地"
            central_conflict = "一桩悬而未决的乱局"
            if errors != ["no provider"]:
                log.warning("gen_frame: LLM step failed (%s); using stub frame",
                            "; ".join(errors) or "provider is None")
        else:
            world_name = obj["world_name"].strip()
            central_conflict = obj["central_conflict"].strip()
        world_name = p_name or world_name
        central_conflict = p_conflict or central_conflict
```

Then in the `frame` dict use `"genre": genre` (instead of `pitch`). Keep the rest (event emission) unchanged — note the genre/tone/central_conflict facts should now emit `genre`/`tone`/`central_conflict` variables (the `genre` fact value becomes `genre`, not raw `pitch`).

`gen_protagonist` — change signature to `def gen_protagonist(provider, oracle, frame, local_map, *, provided=None):`. After `provided = provided or {}` at the top, decide whether to skip the LLM:

```python
    provided = provided or {}
    _ = oracle.random()   # keep the existing attempt-seed consume

    # ... existing town_name / first_venue_name / all_venue_names resolution ...

    needed = [f for f in ("name", "origin", "goal", "objective")
              if _empty_str(provided.get(f))]
    if not needed:
        authored = {f: provided[f].strip() if isinstance(provided[f], str) else provided[f]
                    for f in ("name", "origin", "goal", "objective")}
        return [], authored

    # ... existing user-prompt + complete_structured + stub fallback,
    #     producing `authored` exactly as today, THEN override: ...
    for f in ("name", "origin", "goal", "objective"):
        if not _empty_str(provided.get(f)):
            authored[f] = provided[f].strip() if isinstance(provided[f], str) else provided[f]
    return [], authored
```

Add a module-level helper near the top of `bootstrap.py`:

```python
def _empty_str(v) -> bool:
    return not (isinstance(v, str) and v.strip())
```

`gen_opening` — add `provided=None` to the signature (after `scene_loc_name`). At the very top of the body:

```python
    if isinstance(provided, str) and provided.strip():
        narration = provided.strip()
        events = [kernel_event(
            "narration_recorded", turn=0, day=1, scene="genesis",
            summary="开场叙事", deltas={"scene": "genesis", "text": narration})]
        return events, narration
```

- [ ] **Step 4: Run the new tests AND the existing bootstrap suite**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap_provided.py tests/loop/test_bootstrap.py -q`
Expected: PASS (new frame/protagonist/opening tests + all existing bootstrap tests green — byte-identical baseline holds).

- [ ] **Step 5: Commit**

```bash
git add loop/bootstrap.py tests/loop/test_bootstrap_provided.py
git commit -m "feat: provided= override for gen_frame/gen_protagonist/gen_opening

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Override for `gen_regions` + `gen_local_map`

**Files:**
- Modify: `loop/bootstrap.py` (`gen_regions`, `gen_local_map`)
- Test: `tests/loop/test_bootstrap_provided.py` (append)

**Interfaces:**
- Produces: `gen_regions(provider, oracle, frame, *, provided=None)` (`provided` = regions list; `regions[0]` = start region), `gen_local_map(provider, oracle, frame, regions_summary, *, provided=None)` (`provided` = `{town, venues, neighbors}`). Effective counts = `max(len(provided_list), rolled)`; helper lists (terrains, kinds) padded by cycling.

- [ ] **Step 1: Write the failing tests** (append to `tests/loop/test_bootstrap_provided.py`)

```python
def test_gen_regions_provided_names_kept_and_count_topped_up():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c", "n_regions": 3}
    evs, summary = B.gen_regions(
        provider=None, oracle=_oracle("regions"), frame=frame,
        provided=[{"name": "王都", "terrain": "平原"}, {"name": "北境冰原"}])
    names = [r["name"] for r in summary["regions"]]
    assert names[0] == "王都" and names[1] == "北境冰原"   # provided kept, in order
    assert len(summary["regions"]) == 3                    # topped up to rolled n
    assert summary["regions"][0]["terrain"] == "平原"       # provided terrain kept
    assert summary["start_region"] == "region_0"


def test_gen_regions_more_provided_than_rolled_expands():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c", "n_regions": 3}
    prov = [{"name": f"地域{i}"} for i in range(6)]
    _, summary = B.gen_regions(provider=None, oracle=_oracle("regions"),
                               frame=frame, provided=prov)
    assert len(summary["regions"]) == 6                    # max(6, 3)


def test_gen_local_map_provided_town_and_venue_augment():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c"}
    regions = {"start_region": "region_0"}
    _, lm = B.gen_local_map(
        provider=None, oracle=_oracle("local_map"), frame=frame,
        regions_summary=regions,
        provided={"town": {"name": "晨曦镇"}, "venues": [{"name": "魔法学院"}]})
    # town name surfaces via l2 list
    town = next(e for e in lm["l2"] if e["id"] == "town_0")
    assert town["name"] == "晨曦镇"
    assert "魔法学院" in lm["venue_names"].values()        # provided venue kept
    assert len(lm["venues"]) >= 2                          # still >= rolled minimum
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap_provided.py -q -k "regions or local_map"`
Expected: FAIL (unexpected keyword `provided`).

- [ ] **Step 3: Modify `gen_regions` and `gen_local_map`**

`gen_regions` — signature gains `*, provided=None`. After computing the rolled `n = frame["n_regions"]`:

```python
    provided = provided or []
    n = max(len(provided), frame["n_regions"])

    # terrains: draw distinct up to table size, then pad by cycling to length n
    terrain_entries = _draw_distinct(oracle, load_table("terrains", "genesis"), n)
    rolled_terrains = [e["name"] for e in terrain_entries] or ["平原"]
    terrains = [
        (provided[i].get("terrain") if i < len(provided) and provided[i].get("terrain")
         else rolled_terrains[i % len(rolled_terrains)])
        for i in range(n)
    ]
    density = round(oracle.random() * 0.3 + 0.2, 1)
    neighbor_count = max(1, n // 2) if n > 1 else 0
```

Decide whether the LLM is needed, then override per index:

```python
    need_llm = any(i >= len(provided) or _empty_str(provided[i].get("name"))
                   for i in range(n))
    if need_llm:
        # ... existing prompt (built for n) + complete_structured + stub fallback,
        #     producing raw_regions (length n) exactly as today ...
    else:
        raw_regions = [{"name": provided[i]["name"], "seed": provided[i].get("seed") or "一片疆域"}
                       for i in range(n)]
    # Override names/seeds with provided where present
    for i in range(min(len(provided), n)):
        if provided[i].get("name"):
            raw_regions[i]["name"] = provided[i]["name"]
        if provided[i].get("seed"):
            raw_regions[i]["seed"] = provided[i]["seed"]
```

The rest (tiers, ids, `place_created`, star graph over `range(1, n)`) is unchanged — it already keys off `n` and `terrains[i]`.

`gen_local_map` — signature gains `*, provided=None`. Replace the rolls block:

```python
    provided = provided or {}
    p_town = provided.get("town") or {}
    p_venues = provided.get("venues") or []
    p_neighbors = provided.get("neighbors") or []

    n_extra_l2 = max(len(p_neighbors), oracle.randint(1, 2))
    kind_entries = _draw_distinct(oracle, load_table("place_kinds", "genesis"), n_extra_l2)
    rolled_kinds = [e["name"] for e in kind_entries] or ["野地"]
    neighbor_kinds = [
        (p_neighbors[i].get("kind") if i < len(p_neighbors) and p_neighbors[i].get("kind")
         else rolled_kinds[i % len(rolled_kinds)])
        for i in range(n_extra_l2)
    ]
    n_venues = max(len(p_venues), oracle.randint(2, 4))
```

Decide LLM need and override the assembled `obj` before building the summary/events:

```python
    need_llm = (
        _empty_str(p_town.get("name")) or _empty_str(p_town.get("seed"))
        or any(i >= len(p_venues) or _empty_str(p_venues[i].get("name")) for i in range(n_venues))
        or any(i >= len(p_neighbors) or _empty_str(p_neighbors[i].get("name")) for i in range(n_extra_l2))
    )
    if need_llm:
        # ... existing prompt + complete_structured + stub fallback producing `obj` ...
    else:
        obj = {"town": {}, "venues": [{} for _ in range(n_venues)],
               "neighbors": [{} for _ in range(n_extra_l2)]}
    # Override with provided
    if p_town.get("name"): obj["town"]["name"] = p_town["name"]
    if p_town.get("seed"): obj["town"]["seed"] = p_town["seed"]
    obj["town"].setdefault("name", "起始镇")
    obj["town"].setdefault("seed", "一座小镇")
    for i in range(n_venues):
        if i < len(p_venues):
            if p_venues[i].get("name"): obj["venues"][i]["name"] = p_venues[i]["name"]
            if p_venues[i].get("seed"): obj["venues"][i]["seed"] = p_venues[i]["seed"]
        obj["venues"][i].setdefault("name", f"场所{i+1}")
        obj["venues"][i].setdefault("seed", "待探索的场所")
    for i in range(n_extra_l2):
        if i < len(p_neighbors):
            if p_neighbors[i].get("name"): obj["neighbors"][i]["name"] = p_neighbors[i]["name"]
            if p_neighbors[i].get("seed"): obj["neighbors"][i]["seed"] = p_neighbors[i]["seed"]
        obj["neighbors"][i].setdefault("name", f"邻地{i+1}")
        obj["neighbors"][i].setdefault("seed", "一片待探索之地")
```

The rest (`town_name`/`venue_ids`/`venue_names`/`l2_summary`/events) is unchanged.

- [ ] **Step 4: Run new tests + existing bootstrap suite**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap_provided.py tests/loop/test_bootstrap.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add loop/bootstrap.py tests/loop/test_bootstrap_provided.py
git commit -m "feat: provided= override for gen_regions/gen_local_map (augment + pin)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Override for `gen_factions` + `gen_npcs`

**Files:**
- Modify: `loop/bootstrap.py` (`gen_factions`, `gen_npcs`)
- Test: `tests/loop/test_bootstrap_provided.py` (append)

**Interfaces:**
- Produces: `gen_factions(provider, oracle, frame, regions_summary, *, provided=None)` (`provided` = factions list), `gen_npcs(provider, oracle, frame, local_map, factions, *, provided=None)` (`provided` = npcs list). Effective count = `max(len(provided), rolled)`; provided NPC `secret` still emits a `secrecy="secret"` fact.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_gen_factions_provided_kept_and_topped_up():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c", "n_factions": 3}
    _, summary = B.gen_factions(
        provider=None, oracle=_oracle("factions"), frame=frame,
        regions_summary={}, provided=[{"name": "光明教会", "motivation": "净化魔物"}])
    names = [f["name"] for f in summary["factions"]]
    assert names[0] == "光明教会"
    assert len(summary["factions"]) == 3


def test_gen_npcs_provided_secret_emits_secret_fact():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c"}
    local_map = {"venues": ["venue_0", "venue_1"]}
    factions = {"factions": [{"id": "faction_0", "name": "教会"}]}
    evs, summary = B.gen_npcs(
        provider=None, oracle=_oracle("npcs"), frame=frame,
        local_map=local_map, factions=factions,
        provided=[{"sketch": "白发老者", "goal": "守护遗物", "secret": "他是堕落的圣骑士"}])
    assert summary["npcs"][0]["sketch"] == "白发老者"
    secret_facts = [e for e in evs if e["type"] == "fact_asserted"
                    and e["deltas"].get("secrecy") == "secret"
                    and e["deltas"].get("value") == "他是堕落的圣骑士"]
    assert len(secret_facts) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap_provided.py -q -k "factions or npcs"`
Expected: FAIL.

- [ ] **Step 3: Modify `gen_factions` and `gen_npcs`**

`gen_factions` — signature gains `*, provided=None`. After `n = frame["n_factions"]`:

```python
    provided = provided or []
    n = max(len(provided), frame["n_factions"])

    need_llm = any(i >= len(provided)
                   or _empty_str(provided[i].get("name"))
                   or _empty_str(provided[i].get("motivation"))
                   for i in range(n))
    if need_llm:
        # ... existing prompt (built for n) + complete_structured + stub
        #     producing raw_factions (length n) ...
    else:
        raw_factions = [{"name": provided[i]["name"], "motivation": provided[i]["motivation"]}
                        for i in range(n)]
    for i in range(min(len(provided), n)):
        if provided[i].get("name"): raw_factions[i]["name"] = provided[i]["name"]
        if provided[i].get("motivation"): raw_factions[i]["motivation"] = provided[i]["motivation"]
```

The event/summary loop over `raw_factions` is unchanged.

`gen_npcs` — signature gains `*, provided=None`. After `n = oracle.randint(2, 4)`:

```python
    provided = provided or []
    n = max(len(provided), oracle.randint(2, 4))
    role_entries = _draw_distinct(oracle, load_table("npc_roles", "genesis"), n)
    rolled_roles = [e["name"] for e in role_entries] or ["旅人"]
    roles = [rolled_roles[i % len(rolled_roles)] for i in range(n)]
    traits_table = load_table("npc_traits", "genesis")
    traits_per_npc = [_draw_distinct(oracle, traits_table, 2) for _ in range(n)]
    venues = local_map["venues"]

    need_llm = any(i >= len(provided)
                   or _empty_str(provided[i].get("sketch"))
                   or _empty_str(provided[i].get("goal"))
                   or _empty_str(provided[i].get("secret"))
                   for i in range(n))
    if need_llm:
        # ... existing prompt (built for n with roles/traits) + complete_structured
        #     + stub producing raw_npcs (length n) ...
    else:
        raw_npcs = [{"sketch": provided[i]["sketch"], "goal": provided[i]["goal"],
                     "secret": provided[i]["secret"]} for i in range(n)]
    for i in range(min(len(provided), n)):
        for f in ("sketch", "goal", "secret"):
            if provided[i].get(f):
                raw_npcs[i][f] = provided[i][f]
        if provided[i].get("role"):
            roles[i] = provided[i]["role"]
```

The event/summary loop is unchanged (it already emits the `secrecy="secret"` fact from `raw_npcs[i]["secret"]`).

- [ ] **Step 4: Run new tests + existing bootstrap suite**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap_provided.py tests/loop/test_bootstrap.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add loop/bootstrap.py tests/loop/test_bootstrap_provided.py
git commit -m "feat: provided= override for gen_factions/gen_npcs (augment + secret fact)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Override for `gen_threads`

**Files:**
- Modify: `loop/bootstrap.py` (`gen_threads` / `_gen_threads_inner`)
- Test: `tests/loop/test_bootstrap_provided.py` (append)

**Interfaces:**
- Produces: `gen_threads(provider, oracle, frame, local_map, protagonist, *, provided=None)` (`provided` = threads list). Provided lines split by `bound` (`"protagonist"` → protagonist-bound, else campaign). Counts: campaign = `max(provided_campaign, rolled_n)`, protagonist = `max(provided_protagonist, rolled_n_p)`. Provided `l3_anchor` validated against `local_map["venues"]` (fallback to first venue).

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_gen_threads_provided_campaign_line_kept():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c"}
    local_map = {"venues": ["venue_0", "venue_1"], "start_town": "town_0"}
    prov = [{"about": "教堂地下的低语", "description": "异常的祷声",
             "trigger": "夜探教堂", "secret": "封印松动",
             "l3_anchor": "venue_0", "stages": ["听到声响", "发现密道"],
             "bound": "campaign"}]
    skeletons, summary = B.gen_threads(
        provider=None, oracle=_oracle("threads"), frame=frame,
        local_map=local_map, protagonist="protagonist", provided=prov)
    abouts = [t["about"] for t in summary["threads"]]
    assert "教堂地下的低语" in abouts
    kept = next(s for s in skeletons if s["about"] == "教堂地下的低语")
    assert kept["l3_anchor"] == "venue_0"
    assert [st["hint"] for st in kept["stages"]] == ["听到声响", "发现密道"]


def test_gen_threads_provided_bad_anchor_falls_back_to_venue():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c"}
    local_map = {"venues": ["venue_0"], "start_town": "town_0"}
    prov = [{"about": "x", "description": "y", "trigger": "z", "secret": "s",
             "l3_anchor": "不存在的地点", "stages": ["a"], "bound": "campaign"}]
    skeletons, _ = B.gen_threads(provider=None, oracle=_oracle("threads"),
                                 frame=frame, local_map=local_map,
                                 protagonist="protagonist", provided=prov)
    kept = next(s for s in skeletons if s["about"] == "x")
    assert kept["l3_anchor"] == "venue_0"        # invalid anchor repaired
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap_provided.py -q -k threads`
Expected: FAIL.

- [ ] **Step 3: Modify `gen_threads` / `_gen_threads_inner`**

`gen_threads` — add `*, provided=None` and forward it: `return _gen_threads_inner(provider, oracle, frame, local_map, protagonist, provided=provided)` (and the `except` fallback ignores `provided`, as today).

`_gen_threads_inner` — add `*, provided=None`. After computing `venues`:

```python
    provided = provided or []
    prov_campaign = [t for t in provided if (t.get("bound") or "campaign") != "protagonist"]
    prov_prot = [t for t in provided if (t.get("bound") or "campaign") == "protagonist"]
```

Change the campaign count to `n = max(len(prov_campaign), oracle.randint(3, 5))` and the protagonist count to `n_p = max(len(prov_prot), oracle.randint(1, 2))`. The per-line complexity/threshold/stage rolls already loop `for _ in range(n)` / `range(n_p)`, so they cover the larger counts unchanged.

In the campaign skeleton-build loop, when an LLM line is missing for index `i` (or to override with a provided line), apply the provided line first. Replace the body of the campaign `for i in range(n):` loop's skeleton assembly so that for `i < len(prov_campaign)` the provided fields win:

```python
        p = prov_campaign[i] if i < len(prov_campaign) else None
        if p is not None:
            anchor_venue = p.get("l3_anchor")
            if not (isinstance(anchor_venue, str) and anchor_venue in venues):
                anchor_venue = venues[0] if venues else (eg_venue)
            p_stages = p.get("stages") or []
            stages = ([{"hint": s.strip()} for s in p_stages if isinstance(s, str) and s.strip()]
                      or [{"hint": f"线索提示{j+1}"} for j in range(stage_count)])
            sk = {
                "id": thread_id, "complexity": p.get("complexity") or complexity,
                "anchor": start_town, "threshold": threshold,
                "about": p.get("about") or "待揭晓的悬案",
                "description": p.get("description") or "一条未解之谜",
                "trigger": p.get("trigger") or "玩家主动调查",
                "secret": p.get("secret") or "隐藏的真相",
                "l3_anchor": anchor_venue, "stages": stages,
            }
        elif campaign_lines is not None:
            # ... existing LLM-line branch ...
        else:
            # ... existing stub branch ...
```

Apply the identical pattern in the protagonist `for i in range(n_p):` loop using `prov_prot` and `anchor=protagonist`. Note `provided[i].stages` are plain strings → wrap each as `{"hint": ...}` (provided threads use a string list per the spec; LLM lines use `{"hint": ...}` objects).

- [ ] **Step 4: Run new tests + existing bootstrap suite**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap_provided.py tests/loop/test_bootstrap.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add loop/bootstrap.py tests/loop/test_bootstrap_provided.py
git commit -m "feat: provided= override for gen_threads (campaign/protagonist split)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Thread `spec` through `bootstrap_world` + reroll reuse

**Files:**
- Modify: `loop/bootstrap.py` (`bootstrap_world`, `reroll_all`, `reroll_step`)
- Test: `tests/loop/test_bootstrap_provided.py` (append)

**Interfaces:**
- Consumes: each generator's `provided=` (Tasks 3-6); `loop.genesis_spec` shapes.
- Produces: `bootstrap_world(engine, pitch="", *, spec=None, attempt=0, progress=None)`. Stores the resolved spec in `_state["spec"]`; `reroll_all`/`reroll_step` reuse `_state["spec"]` so provided parts persist across rerolls. Determinism: same `(campaign_seed, spec, attempt)` ⇒ identical world.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_bootstrap_world_spec_threads_protagonist(tmp_path):
    from app.engine import build_engine
    engine = build_engine(tmp_path / "c")
    spec = {"world_premise": {"genre": "日式西幻"},
            "protagonist": {"name": "凛", "origin": "流浪剑士"}}
    result = engine and __import__("loop.bootstrap", fromlist=["bootstrap_world"]) \
        .bootstrap_world(engine, "", spec=spec)
    assert result["summary"]["protagonist_name"] == "凛"
    assert result["_state"]["spec"] == spec


def test_bootstrap_world_same_seed_same_spec_deterministic(tmp_path):
    from app.engine import build_engine
    from loop.bootstrap import bootstrap_world
    # build_engine derives the seed from the campaign dir NAME; two dirs both
    # named "camp" share a seed, so spec=None genesis must be identical.
    e1 = build_engine(tmp_path / "x" / "camp")
    e2 = build_engine(tmp_path / "y" / "camp")
    r1 = bootstrap_world(e1, "武侠", spec=None)
    r2 = bootstrap_world(e2, "武侠", spec=None)
    assert r1["summary"]["world_name"] == r2["summary"]["world_name"]
    assert r1["summary"]["objective"] == r2["summary"]["objective"]
    assert r1["summary"]["protagonist_name"] == r2["summary"]["protagonist_name"]
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap_provided.py -q -k "spec_threads or spec_none"`
Expected: FAIL (`bootstrap_world() got an unexpected keyword argument 'spec'`).

- [ ] **Step 3: Modify `bootstrap_world` + reroll helpers**

`bootstrap_world` — signature `def bootstrap_world(engine, pitch="", *, spec=None, attempt=0, progress=None):`. Near the top:

```python
    from loop.genesis_spec import normalize
    spec = normalize(spec)
    # pitch seeds world_premise.genre when the spec does not set it
    wp = dict(spec.get("world_premise") or {})
    if not wp.get("genre") and pitch:
        wp["genre"] = pitch
    if wp:
        spec = {**spec, "world_premise": wp}
    genre = wp.get("genre", pitch)
```

Pass the matching part into each generator call:

```python
    frame_evs, frame = gen_frame(provider, _seed("frame"), genre, provided=spec.get("world_premise"))
    region_evs, regions_summary = gen_regions(provider, _seed("regions"), frame, provided=spec.get("regions"))
    local_map_evs, local_map = gen_local_map(provider, _seed("local_map"), frame, regions_summary, provided=spec.get("local_map"))
    _, protagonist_authored = gen_protagonist(provider, _seed("protagonist"), frame, local_map, provided=spec.get("protagonist"))
    ...
    faction_evs, factions_summary = gen_factions(provider, _seed("factions"), frame, regions_summary, provided=spec.get("factions"))
    npc_evs, npcs_summary = gen_npcs(provider, _seed("npcs"), frame, local_map, factions_summary, provided=spec.get("npcs"))
    skeletons, threads_summary = gen_threads(provider, _seed("threads"), frame, local_map, protagonist, provided=spec.get("threads"))
    opening_evs, narration = gen_opening(provider, frame, world_summary, scene_loc=first_venue, scene_loc_name=first_venue_name, provided=spec.get("opening"))
```

Add `"spec": spec` to the returned `_state` dict.

`reroll_all` — signature gains `spec` reuse: read `prev_spec = prev_result["_state"].get("spec")` and call `bootstrap_world(engine, pitch, spec=prev_spec, attempt=new_attempt, progress=progress)`.

`reroll_step` — read `spec = state.get("spec") or {}`; pass the matching `provided=spec.get(<part>)` into the `gen_factions`/`gen_npcs`/`gen_threads` calls it re-runs; carry `"spec"` forward in `new_state`.

- [ ] **Step 4: Run new tests + full bootstrap & engine suites**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_bootstrap.py tests/loop/test_bootstrap_provided.py tests/app/test_engine.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add loop/bootstrap.py tests/loop/test_bootstrap_provided.py
git commit -m "feat: thread GenesisSpec through bootstrap_world + reroll reuse

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Interactive session-zero (`app/session_zero.py`)

**Files:**
- Create: `app/session_zero.py`
- Test: `tests/app/test_session_zero.py`

**Interfaces:**
- Consumes: `loop.genesis_spec.{merge, missing_required, normalize}`.
- Produces: `run_session_zero(spec: dict, *, inputs, out, interactive: bool = True) -> dict`. For each `missing_required` part, prompts and reads from `inputs` until a value is given or a delegate token (`/auto`, `你来定`, or empty line) is read; a value sets the part's minimal field (`world_premise.genre` / `protagonist.name`). Non-interactive ⇒ returns spec unchanged. Delegate ⇒ leaves the part absent (model fills).

- [ ] **Step 1: Write the failing tests**

```python
# tests/app/test_session_zero.py
from app.session_zero import run_session_zero


def _run(spec, lines):
    out = []
    result = run_session_zero(spec, inputs=iter(lines), out=out.append, interactive=True)
    return result, "\n".join(out)


def test_asks_until_required_filled():
    # genre then name provided
    spec, _ = _run({}, ["日式西幻", "凛"])
    assert spec["world_premise"]["genre"] == "日式西幻"
    assert spec["protagonist"]["name"] == "凛"


def test_already_satisfied_required_not_reasked():
    # genre already present -> only the missing protagonist is asked (one prompt).
    out = []
    result = run_session_zero(
        {"world_premise": {"genre": "x"}},
        inputs=iter(["凛"]), out=out.append, interactive=True)
    assert result["protagonist"]["name"] == "凛"
    assert result["world_premise"]["genre"] == "x"
    prompts = [line for line in out if line.endswith("：")]
    assert len(prompts) == 1     # genre satisfied -> not re-asked


def test_delegate_token_leaves_part_absent():
    spec, _ = _run({"protagonist": {"name": "凛"}}, ["/auto"])  # genre delegated
    assert "world_premise" not in spec or "genre" not in spec.get("world_premise", {})


def test_non_interactive_returns_unchanged():
    base = {"protagonist": {"name": "凛"}}
    result = run_session_zero(base, inputs=iter([]), out=lambda *_: None, interactive=False)
    assert result == base
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tests/app/test_session_zero.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `app/session_zero.py`**

```python
"""Interactive session-zero: enforce the minimal required floor before genesis.

For each required part the spec is missing (world_premise / protagonist), prompt
the player and loop until they give a value or explicitly delegate to the model.
Behind injected inputs/out seams (mirrors app.play.play_loop) for testability.
"""
from __future__ import annotations

from loop.genesis_spec import merge, missing_required, normalize

_DELEGATE = {"/auto", "你来定", "auto", ""}

# Required part -> (minimal field, human prompt).
_PROMPTS = {
    "world_premise": ("genre", "【世界】这是个什么样的世界？（题材/基调/一句话钩子，"
                                "或输入 /auto 让模型决定）："),
    "protagonist":   ("name", "【主角】你是谁？（至少给个名字，"
                                "或输入 /auto 让模型决定）："),
}


def run_session_zero(spec, *, inputs, out, interactive: bool = True) -> dict:
    spec = normalize(spec)
    if not interactive:
        return spec

    it = iter(inputs)
    for part in missing_required(spec):
        field, prompt = _PROMPTS[part]
        while True:
            out(prompt)
            try:
                raw = next(it)
            except StopIteration:
                return spec   # input exhausted — leave remaining to the model
            line = (raw or "").strip()
            if line.lower() in _DELEGATE:
                out("（已交由模型生成）")
                break
            spec = merge(spec, {part: {field: line}})
            break
    return spec
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python3 -m pytest tests/app/test_session_zero.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/session_zero.py tests/app/test_session_zero.py
git commit -m "feat: interactive session-zero required-gate (ask-until-filled-or-delegate)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: `new_game(spec=)` + `resolve_genesis_spec` in `app/engine.py`

**Files:**
- Modify: `app/engine.py` (`new_game`; add `resolve_genesis_spec`)
- Test: `tests/app/test_engine.py` (append)

**Interfaces:**
- Consumes: `loop.bootstrap.bootstrap_world` (Task 7), `loop.genesis_blueprint.load_blueprint` (Task 2), `app.session_zero.run_session_zero` (Task 8), `loop.import_sillytavern.convert_sillytavern` (Task 12; imported lazily so P1 works without it).
- Produces: `new_game(engine, pitch="", *, spec=None, progress=None) -> dict` (forwards `spec` to `bootstrap_world`); `resolve_genesis_spec(provider, *, pitch="", blueprint_path=None, world_book_path=None, card_path=None, card_as="protagonist", inputs=None, out=None, interactive=False) -> dict` (seeds `world_premise.genre` from `pitch` as the base, then merges conversion→file→session-zero; precedence interactive > file > conversion > pitch). Seeding pitch first means a player who already gave a pitch is NOT re-asked for the premise by session-zero.

- [ ] **Step 1: Write the failing tests** (append to `tests/app/test_engine.py`)

```python
def test_new_game_forwards_spec(tmp_path):
    from app.engine import build_engine, new_game
    engine = build_engine(tmp_path / "c")
    result = new_game(engine, "", spec={"protagonist": {"name": "凛"}})
    assert result["summary"]["protagonist_name"] == "凛"


def test_resolve_genesis_spec_file_then_session(tmp_path):
    import json
    from app.engine import resolve_genesis_spec
    bp = tmp_path / "g.json"
    bp.write_text(json.dumps({"world_premise": {"genre": "日式西幻"}}), encoding="utf-8")
    # session-zero fills the still-missing protagonist name
    spec = resolve_genesis_spec(
        provider=None, blueprint_path=bp,
        inputs=iter(["凛"]), out=lambda *_: None, interactive=True)
    assert spec["world_premise"]["genre"] == "日式西幻"
    assert spec["protagonist"]["name"] == "凛"
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tests/app/test_engine.py -q -k "forwards_spec or resolve_genesis"`
Expected: FAIL.

- [ ] **Step 3: Modify `app/engine.py`**

Update `new_game`:

```python
def new_game(engine, pitch="", *, spec=None, progress=None) -> dict:
    from loop.bootstrap import bootstrap_world
    log.debug("new_game: delegating to bootstrap_world pitch=%r spec=%s", pitch, bool(spec))
    return bootstrap_world(engine, pitch, spec=spec, progress=progress)
```

Add:

```python
def resolve_genesis_spec(provider, *, pitch="", blueprint_path=None,
                         world_book_path=None, card_path=None,
                         card_as="protagonist",
                         inputs=None, out=None, interactive=False) -> dict:
    """Resolve a GenesisSpec from all sources: pitch -> conversion -> file -> session-zero.

    Precedence (later wins): interactive > file > conversion > pitch >
    (model-fill at bootstrap). Seeding pitch as the base means a player who
    already gave a pitch is not re-asked for the premise by session-zero.
    """
    from loop.genesis_spec import merge, normalize
    spec: dict = normalize({"world_premise": {"genre": pitch}}) if pitch else {}

    if world_book_path or card_path:
        try:
            from loop.import_sillytavern import convert_sillytavern
            wb = _read_json(world_book_path) if world_book_path else None
            card = _read_json(card_path) if card_path else None
            spec = merge(spec, convert_sillytavern(
                provider, world_book=wb, character_card=card, card_as=card_as))
        except ImportError:
            (out or (lambda *_: None))("[导入] SillyTavern 转换层尚未可用，已跳过。")

    if blueprint_path:
        from loop.genesis_blueprint import load_blueprint
        spec = merge(spec, load_blueprint(blueprint_path))

    if interactive and inputs is not None:
        from app.session_zero import run_session_zero
        spec = run_session_zero(spec, inputs=inputs, out=(out or (lambda *_: None)),
                                interactive=True)

    return normalize(spec)


def _read_json(path):
    import json
    from pathlib import Path
    return json.loads(Path(path).read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run tests + engine suite**

Run: `PYTHONPATH=. python3 -m pytest tests/app/test_engine.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/engine.py tests/app/test_engine.py
git commit -m "feat: new_game(spec=) + resolve_genesis_spec source pipeline

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: CLI wiring — `--genesis` flag + session-zero in first-run flow

**Files:**
- Modify: `app/__main__.py`
- Test: `tests/app/test_main_genesis.py` (new)

**Interfaces:**
- Consumes: `app.engine.resolve_genesis_spec`, `new_game(spec=)`.
- Produces: `--genesis PATH` flag; first-run flow resolves a spec (blueprint + interactive session-zero when no `--pitch`/blueprint fully covers the required floor) and passes it to `new_game`.

- [ ] **Step 1: Write the failing test**

```python
# tests/app/test_main_genesis.py
import json
from llm.provider import FakeLLMProvider


def _provider():
    return FakeLLMProvider(json_responses=[{
        "narration": "你站在晨曦镇的街道上。",
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "未推进"}],
    }])


def test_main_genesis_file_drives_protagonist(tmp_path):
    from app import __main__ as M
    bp = tmp_path / "g.json"
    bp.write_text(json.dumps({
        "world_premise": {"genre": "日式西幻"},
        "protagonist": {"name": "凛", "origin": "流浪剑士"},
    }), encoding="utf-8")
    out = []
    M.main(
        ["--campaign", str(tmp_path / "camp"), "--genesis", str(bp)],
        inputs=iter(["", "/quit"]),     # empty -> start game; then quit
        out=out.append, provider=_provider())
    combined = "\n".join(out)
    assert "凛" in combined              # authored protagonist surfaced in intro
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tests/app/test_main_genesis.py -q`
Expected: FAIL (`unrecognized arguments: --genesis`).

- [ ] **Step 3: Modify `app/__main__.py`**

Add the argument near `--pitch`:

```python
    parser.add_argument(
        "--genesis", default=None, dest="genesis",
        help="Path to a genesis blueprint file (JSON/YAML). Defines any subset "
             "of world parts; the model fills the rest.")
```

In the `if not events:` first-run block, after resolving `pitch` and before `new_game`, resolve the spec:

```python
        from app.engine import resolve_genesis_spec
        spec = resolve_genesis_spec(
            provider,
            pitch=pitch,
            blueprint_path=args.genesis,
            inputs=inputs_iter if _interactive else None,
            out=out,
            interactive=_interactive,
        )
        result = new_game(engine, pitch, spec=spec, progress=_progress_cb)
```

Pass `pitch=pitch` so the premise the player already typed at the existing
"[新游戏] 请输入世界背景关键词" prompt seeds `world_premise.genre` BEFORE
session-zero's required-check — session-zero then only asks for the still-missing
protagonist (no double-ask). Keep the existing pitch-reading block. Session-zero
consumes from `inputs_iter`, the same iterator the reroll loop and play loop read
from, so in real interactive use the lines chain naturally (pitch → session-zero →
reroll → turns). When `--genesis` or the pitch already covers the required floor,
`missing_required` is empty and session-zero asks nothing.

- [ ] **Step 4: Run test + the existing main/play suites**

Run: `PYTHONPATH=. python3 -m pytest tests/app/ -q`
Expected: PASS (new test + existing app tests; the empty-spec interactive path must not regress `test_input_sanitize.py` etc.).

- [ ] **Step 5: Commit**

```bash
git add app/__main__.py tests/app/test_main_genesis.py
git commit -m "feat: --genesis flag + session-zero in first-run flow

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Blueprint docs + example

**Files:**
- Create: `docs/genesis-blueprint.md`
- Create: `genesis.example.yaml`

**Interfaces:** none (documentation). Must match the `GenesisSpec` parts/fields from Task 1 exactly.

- [ ] **Step 1: Write `genesis.example.yaml`**

A complete, commented example specifying every part (so a user can copy and trim), including: `world_premise` (genre/tone/world_name/central_conflict/n_regions/n_factions), `protagonist` (name/origin/goal/objective), `regions` (list), `local_map` (town/venues/neighbors), `factions`, `npcs`, `threads` (with `bound`), `opening`. Add a header comment: "Every part is optional; delete what you want the model to invent. Lists augment — your items are kept and the model tops up to its rolled count."

- [ ] **Step 2: Write `docs/genesis-blueprint.md`**

Document: the required floor (world_premise.genre + protagonist.name) and that anything else is model-filled; the merge/augment semantics; how to run (`./run.sh` style with `--genesis path`); a JSON alternative; that `threads.bound` routes a line to campaign vs protagonist; that `npcs`/`threads` concat while named lists dedup. Link the spec.

- [ ] **Step 3: Verify the example loads**

Run: `PYTHONPATH=. python3 -c "from loop.genesis_blueprint import load_blueprint; import json; print(json.dumps(load_blueprint('genesis.example.yaml'), ensure_ascii=False)[:200])"`
Expected: prints a normalized spec dict (no error).

- [ ] **Step 4: Commit**

```bash
git add docs/genesis-blueprint.md genesis.example.yaml
git commit -m "docs: genesis blueprint format guide + commented example

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: SillyTavern conversion layer (`loop/import_sillytavern.py`) — P2

**Files:**
- Create: `loop/import_sillytavern.py`
- Create: `tests/loop/test_import_sillytavern.py`
- Create: `tests/fixtures/st_worldbook.json`, `tests/fixtures/st_card_v2.json`

**Interfaces:**
- Consumes: `llm.structured.complete_structured`, `loop.genesis_spec.normalize`.
- Produces: `convert_sillytavern(provider, *, world_book=None, character_card=None, card_as="protagonist") -> dict` (a normalized GenesisSpec). Deterministic stub when `provider=None` (extracts what it can structurally, no LLM); never raises.

- [ ] **Step 1: Write the failing tests + fixtures**

Create minimal fixtures: `st_worldbook.json` = `{"entries": {"0": {"key": ["王国"], "content": "光之王国由教会统治，与北方魔物对峙。", "constant": true, "comment": "世界观"}, "1": {"key": ["盗贼公会"], "content": "盗贼公会暗中操纵黑市。", "constant": false}}}`. `st_card_v2.json` = `{"spec": "chara_card_v2", "data": {"name": "凛", "description": "流浪的剑士，背负失忆之谜。", "personality": "沉默寡言", "scenario": "抵达晨曦镇", "first_mes": "你推开酒馆的门……"}}`.

```python
# tests/loop/test_import_sillytavern.py
import json
from pathlib import Path
from llm.provider import FakeLLMProvider
from loop.import_sillytavern import convert_sillytavern

FIX = Path(__file__).parent.parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_card_becomes_protagonist_offline_stub():
    # provider=None -> structural extraction only, never raises
    spec = convert_sillytavern(None, character_card=_load("st_card_v2.json"))
    assert spec["protagonist"]["name"] == "凛"
    assert "流浪" in spec["protagonist"]["origin"]


def test_card_as_npc_routes_to_npcs():
    spec = convert_sillytavern(None, character_card=_load("st_card_v2.json"), card_as="npc")
    assert "protagonist" not in spec
    assert any("凛" in (n.get("sketch", "") + n.get("goal", "")) or n.get("name") == "凛"
               for n in spec["npcs"])


def test_worldbook_llm_translation_shape():
    # scripted provider returns a spec-shaped translation
    prov = FakeLLMProvider(json_responses=[{
        "world_premise": {"genre": "日式西幻", "central_conflict": "教会与魔物对峙"},
        "factions": [{"name": "光之教会"}, {"name": "盗贼公会"}],
    }])
    spec = convert_sillytavern(prov, world_book=_load("st_worldbook.json"))
    assert spec["world_premise"]["genre"] == "日式西幻"
    assert [f["name"] for f in spec["factions"]] == ["光之教会", "盗贼公会"]
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_import_sillytavern.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `loop/import_sillytavern.py`**

```python
"""Translate SillyTavern world-books / character cards into our GenesisSpec.

The LLM does the translation (free-text ST entries -> our structured parts),
validated/repaired against the spec shape. With provider=None we still extract
what is structurally unambiguous (a card's name/description) so offline/tests
work without a model. Never raises — returns the best spec it can.
"""
from __future__ import annotations

from loop.genesis_spec import merge, normalize
from llm.structured import complete_structured
from engine.log import get_logger

log = get_logger("loop.import_st")

_SYSTEM = ("你是设定转换器：把酒馆(SillyTavern)世界书/角色卡翻译成游戏引擎的"
           "结构化 genesis spec，只返回严格符合字段规范的 JSON，故事文本用中文。")


def _card_data(card) -> dict:
    if not isinstance(card, dict):
        return {}
    return card.get("data") if isinstance(card.get("data"), dict) else card


def _card_to_protagonist(card) -> dict:
    d = _card_data(card)
    name = (d.get("name") or "").strip()
    origin = (d.get("description") or d.get("personality") or "").strip()
    out = {}
    if name:
        out["name"] = name
    if origin:
        out["origin"] = origin
    return out


def _validate_spec_shape(obj) -> list:
    if not isinstance(obj, dict):
        return ['response must be a JSON object']
    return []   # normalize() is tolerant; accept any object and clean it


def convert_sillytavern(provider, *, world_book=None, character_card=None,
                        card_as: str = "protagonist") -> dict:
    spec: dict = {}

    # 1. Character card -> protagonist (default) or npc (structural, no LLM needed).
    if character_card is not None:
        prot = _card_to_protagonist(character_card)
        if prot:
            if card_as == "npc":
                d = _card_data(character_card)
                spec = merge(spec, {"npcs": [{
                    "sketch": prot.get("origin") or prot.get("name"),
                    "goal": (d.get("scenario") or "").strip() or "（未定）",
                    "secret": "（来自导入角色卡，待补充）",
                }]})
            else:
                spec = merge(spec, {"protagonist": prot})

    # 2. World-book -> world/factions/npcs/threads via LLM translation.
    if world_book is not None:
        entries = []
        wb_entries = world_book.get("entries") if isinstance(world_book, dict) else None
        if isinstance(wb_entries, dict):
            for v in wb_entries.values():
                if isinstance(v, dict) and isinstance(v.get("content"), str) and v["content"].strip():
                    entries.append(v["content"].strip())
        if entries and provider is not None:
            user = (
                "下面是酒馆世界书的条目内容，请翻译为我们的 genesis spec JSON。\n"
                "可包含字段：world_premise{genre,tone,world_name,central_conflict}、"
                "factions[{name,motivation}]、npcs[{sketch,goal,secret}]、"
                "threads[{about,description,trigger,secret,bound}]。\n"
                "只返回 JSON 对象，省略无法从条目推断的字段。\n\n"
                "世界书条目：\n- " + "\n- ".join(entries)
            )
            obj, errors = complete_structured(
                provider, system=_SYSTEM, user=user,
                validate=_validate_spec_shape, max_repairs=2,
                log_label="import_sillytavern")
            if not errors and isinstance(obj, dict):
                spec = merge(spec, normalize(obj))
            else:
                log.warning("convert_sillytavern: world-book translation failed (%s)",
                            "; ".join(errors) if errors else "no object")

    return normalize(spec)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python3 -m pytest tests/loop/test_import_sillytavern.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add loop/import_sillytavern.py tests/loop/test_import_sillytavern.py tests/fixtures/st_worldbook.json tests/fixtures/st_card_v2.json
git commit -m "feat: SillyTavern world-book/character-card -> GenesisSpec conversion (P2)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 13: CLI import flags + P2 docs

**Files:**
- Modify: `app/__main__.py` (import flags)
- Modify: `docs/genesis-blueprint.md` (add an "Importing from SillyTavern" section)
- Test: `tests/app/test_main_genesis.py` (append)

**Interfaces:**
- Consumes: `resolve_genesis_spec` (already routes `world_book_path`/`card_path`/`card_as`).
- Produces: `--import-world-book PATH`, `--import-card PATH`, `--card-as {protagonist,npc}` flags wired into the first-run `resolve_genesis_spec` call.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_main_import_card_drives_protagonist(tmp_path):
    import json
    from app import __main__ as M
    card = tmp_path / "card.json"
    card.write_text(json.dumps({"spec": "chara_card_v2", "data": {
        "name": "凛", "description": "流浪剑士"}}), encoding="utf-8")
    out = []
    M.main(
        ["--campaign", str(tmp_path / "camp"), "--import-card", str(card)],
        inputs=iter(["", "/quit"]), out=out.append, provider=_provider())
    assert "凛" in "\n".join(out)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tests/app/test_main_genesis.py -q -k import_card`
Expected: FAIL (`unrecognized arguments: --import-card`).

- [ ] **Step 3: Modify `app/__main__.py`**

Add the flags:

```python
    parser.add_argument("--import-world-book", default=None, dest="import_world_book",
                        help="Path to a SillyTavern world-book JSON to translate into the genesis spec.")
    parser.add_argument("--import-card", default=None, dest="import_card",
                        help="Path to a SillyTavern character card (V2 JSON) to translate.")
    parser.add_argument("--card-as", default="protagonist", choices=["protagonist", "npc"],
                        dest="card_as", help="Import the character card as the protagonist (default) or an NPC.")
```

Extend the `resolve_genesis_spec` call from Task 10:

```python
        spec = resolve_genesis_spec(
            provider,
            blueprint_path=args.genesis,
            world_book_path=args.import_world_book,
            card_path=args.import_card,
            card_as=args.card_as,
            inputs=inputs_iter if _interactive else None,
            out=out,
            interactive=_interactive,
        )
```

- [ ] **Step 4: Write the "Importing from SillyTavern" docs section**

In `docs/genesis-blueprint.md`: explain that import is an **LLM translation** into our spec (not an ST runtime); `--import-card` → protagonist by default (`--card-as npc` to import as an NPC); `--import-world-book` → world_premise/factions/npcs/threads; imports merge UNDER a `--genesis` file and interactive answers (file/interactive win); offline (no provider) extracts only the card's name/description.

- [ ] **Step 5: Run tests + full app suite, then commit**

Run: `PYTHONPATH=. python3 -m pytest tests/app/ -q`
Expected: PASS.

```bash
git add app/__main__.py docs/genesis-blueprint.md tests/app/test_main_genesis.py
git commit -m "feat: --import-world-book/--import-card/--card-as flags + P2 docs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] Full suite green: `PYTHONPATH=. python3 -m pytest -q` (must be ≥ prior count, all passing — the byte-identical baseline means no pre-existing test changed).
- [ ] Live smoke (manual, optional, needs key via run.sh): `./run.sh` with a `--genesis genesis.example.yaml` and with `--import-card` against a real ST card; confirm the intro reflects the provided parts and the model fills the rest.

## Self-Review notes (filled by plan author)

- **Spec coverage:** GenesisSpec model (T1), required-gate (T1 missing_required + T8 session-zero), blueprint file (T2), interactive (T8), generator override all 8 generators (T3-T6), bootstrap threading + reroll reuse (T7), resolution pipeline/precedence (T9), CLI (T10/T13), ST conversion (T12), docs (T11/T13). All spec sections mapped.
- **Byte-identical constraint:** enforced as a step in every generator task (run existing `tests/loop/test_bootstrap.py`) and at final verification.
- **Type consistency:** `provided` is the matching spec PART (dict for scalar parts, list for list parts, str for opening) across T3-T7; `resolve_genesis_spec` kwargs match the `--import-*`/`--genesis` flags in T10/T13; `convert_sillytavern` signature in T9's lazy import matches T12's definition.
