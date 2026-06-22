"""Tests for generate_lore_batch (Task 2).

All tests are offline — no real LLM calls. FakeLLMProvider feeds canned JSON.
"""
from __future__ import annotations

import hashlib
import pytest

from llm.provider import FakeLLMProvider
from loop.density import generate_lore_batch, GEN_THRESHOLD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOWN_ID = "青石镇"
VENUES  = ["市集", "码头"]
FLAVOR  = "边陲集镇"
KIND    = "settlement"

SPECS_2 = [
    {"complexity": "simple",  "stage_count": 2},
    {"complexity": "complex", "stage_count": 5},
]

# Canned model output — one dict per line (matches what generate_lore_batch requests)
def _canned_batch(overrides_per_line=None):
    """Build a canned {"lines": [...]} dict for FakeLLMProvider."""
    defaults = [
        {
            "about": "镇外神秘商队频繁出没",
            "secret": "商队运送被禁止的魔法材料",
            "description": "最近有支陌生商队驻扎在镇外林间",
            "trigger": "玩家询问旅店老板关于商队的消息",
            "l3_anchor": "市集",
            "stages": [{"hint": "发现神秘脚印"}, {"hint": "找到隐藏的货仓"}],
        },
        {
            "about": "码头下方沉睡的古代遗迹",
            "secret": "遗迹是被封印的龙窟",
            "description": "码头渔民报告水下有奇怪的光",
            "trigger": "玩家在码头附近调查",
            "l3_anchor": "码头",
            "stages": [
                {"hint": "渔民讲述目击经过"},
                {"hint": "水面冒出气泡"},
                {"hint": "发现古代铭文"},
                {"hint": "找到入口"},
                {"hint": "面对封印守卫"},
            ],
        },
    ]
    lines = []
    for i, base in enumerate(defaults):
        line = dict(base)
        if overrides_per_line and i < len(overrides_per_line):
            line.update(overrides_per_line[i])
        lines.append(line)
    return {"lines": lines}


def _make_id(town_id: str, about: str, idx: int) -> str:
    short = hashlib.sha256((town_id + about + str(idx)).encode()).hexdigest()[:8]
    return f"gen_{town_id}_{short}"


# ---------------------------------------------------------------------------
# Required keys for create_lore_line
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {"id", "complexity", "about", "anchor", "stages",
                 "threshold", "description", "trigger", "l3_anchor"}


# ---------------------------------------------------------------------------
# Test: happy path — 2 specs → 2 valid skeletons
# ---------------------------------------------------------------------------

def test_generate_lore_batch_happy_path():
    fake = FakeLLMProvider(json_responses=[_canned_batch()])
    result = generate_lore_batch(
        fake,
        town_id=TOWN_ID,
        kind=KIND,
        flavor=FLAVOR,
        venues=VENUES,
        existing_abouts=[],
        specs=SPECS_2,
    )
    assert len(result) == 2

    # First skeleton: simple / 2 stages
    s0 = result[0]
    assert REQUIRED_KEYS.issubset(s0.keys()), f"missing keys: {REQUIRED_KEYS - s0.keys()}"
    assert s0["complexity"] == "simple"
    assert s0["anchor"] == TOWN_ID
    assert s0["threshold"] == GEN_THRESHOLD
    assert len(s0["stages"]) == 2
    assert s0["l3_anchor"] in VENUES

    # Second skeleton: complex / 5 stages
    s1 = result[1]
    assert REQUIRED_KEYS.issubset(s1.keys()), f"missing keys: {REQUIRED_KEYS - s1.keys()}"
    assert s1["complexity"] == "complex"
    assert s1["anchor"] == TOWN_ID
    assert s1["threshold"] == GEN_THRESHOLD
    assert len(s1["stages"]) == 5
    assert s1["l3_anchor"] in VENUES


# ---------------------------------------------------------------------------
# Test: IDs are unique and deterministic
# ---------------------------------------------------------------------------

def test_generate_lore_batch_deterministic_ids():
    """Calling twice with the same inputs must produce the same ids."""
    def make_fake():
        return FakeLLMProvider(json_responses=[_canned_batch()])

    result1 = generate_lore_batch(
        make_fake(), town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    result2 = generate_lore_batch(
        make_fake(), town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    ids1 = [s["id"] for s in result1]
    ids2 = [s["id"] for s in result2]
    assert ids1 == ids2, "ids must be deterministic across calls"
    assert len(set(ids1)) == len(ids1), "ids must be unique"


# ---------------------------------------------------------------------------
# Test: provider is None → []
# ---------------------------------------------------------------------------

def test_generate_lore_batch_none_provider():
    result = generate_lore_batch(
        None,
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    assert result == []


# ---------------------------------------------------------------------------
# Test: provider.complete_json raises → []
# ---------------------------------------------------------------------------

class _RaisingProvider:
    """Minimal stub that always raises from complete_json."""

    def __init__(self, exc=None):
        self._exc = exc or ValueError("simulated LLM failure")

    def complete_json(self, system, user, schema, **kw):
        raise self._exc


def test_generate_lore_batch_provider_raises():
    result = generate_lore_batch(
        _RaisingProvider(ValueError("simulated LLM failure")),
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    assert result == []


def test_generate_lore_batch_provider_raises_runtime_error():
    """complete_json raising a non-ValueError (RuntimeError) must also return []."""
    result = generate_lore_batch(
        _RaisingProvider(RuntimeError("unexpected engine crash")),
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    assert result == []


# ---------------------------------------------------------------------------
# Test: l3_anchor not in venues → coerced to venues[0]
# ---------------------------------------------------------------------------

def test_generate_lore_batch_bad_l3_anchor_rejected():
    """A line whose l3_anchor is not an allowed venue is rejected; if it is never
    corrected across repair rounds, it is dropped (NO silent coercion to venues[0])."""
    canned = _canned_batch(overrides_per_line=[
        {"l3_anchor": "不存在的地点"},   # bad — never in VENUES
        {},                              # valid
    ])
    fake = FakeLLMProvider(json_responses=[canned])  # cycles the same bad response
    result = generate_lore_batch(
        fake,
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    assert len(result) == 1                      # bad-l3 line dropped, valid line kept
    assert result[0]["l3_anchor"] in VENUES


# ---------------------------------------------------------------------------
# Test: missing required model field → that skeleton dropped, others kept
# ---------------------------------------------------------------------------

def test_generate_lore_batch_missing_about_drops_skeleton():
    """A line missing 'about' is dropped; the other line is kept."""
    canned = _canned_batch(overrides_per_line=[
        {"about": ""},          # empty → dropped
        {},                     # fine
    ])
    fake = FakeLLMProvider(json_responses=[canned])
    result = generate_lore_batch(
        fake,
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    # First (empty about) dropped; second kept → 1 skeleton
    assert len(result) == 1
    assert result[0]["about"] != ""


def test_generate_lore_batch_zero_stages_drops_skeleton():
    """A line with 0 stages after correction is dropped."""
    canned = _canned_batch(overrides_per_line=[
        {"stages": []},         # 0 stages → dropped
        {},                     # fine
    ])
    fake = FakeLLMProvider(json_responses=[canned])
    result = generate_lore_batch(
        fake,
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    assert len(result) == 1


def test_missing_description_rejected_then_dropped():
    """A line missing 'description' is rejected (no fallback); if never corrected
    across repair rounds it is dropped — the harness enforces the field."""
    canned = _canned_batch()
    del canned["lines"][0]["description"]
    fake = FakeLLMProvider(json_responses=[canned])  # cycles same → never fixed
    result = generate_lore_batch(
        fake,
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    assert len(result) == 1                  # line missing description dropped
    assert "description" in result[0] and result[0]["description"]


def test_line_with_no_about_is_dropped():
    """A line missing 'about' that is never corrected → dropped; the other kept."""
    canned = _canned_batch()
    del canned["lines"][0]["about"]
    fake = FakeLLMProvider(json_responses=[canned])
    result = generate_lore_batch(
        fake,
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    assert len(result) == 1  # first dropped, second kept


# ---------------------------------------------------------------------------
# Test: model over-produces stages → truncated to stage_count
# ---------------------------------------------------------------------------

def test_generate_lore_batch_stages_truncated():
    """If model returns more stages than stage_count, truncate to spec."""
    # simple spec wants 2 stages, but model returns 5
    canned = _canned_batch(overrides_per_line=[
        {"stages": [{"hint": f"hint{i}"} for i in range(5)]},  # 5 hints, spec=2 → truncate to 2
        {},
    ])
    fake = FakeLLMProvider(json_responses=[canned])
    result = generate_lore_batch(
        fake,
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=SPECS_2,
    )
    assert len(result) == 2
    assert len(result[0]["stages"]) == 2  # truncated
    assert len(result[1]["stages"]) == 5  # complex/5, exact match


# ---------------------------------------------------------------------------
# Test: empty venues → l3_anchor kept as-is (no crash, l3_anchor not coerced)
# ---------------------------------------------------------------------------

def test_generate_lore_batch_empty_venues():
    """When venues is empty, l3_anchor from model is kept as-is (not coerced)."""
    # First line's l3_anchor in canned batch is "市集"
    expected_l3 = _canned_batch()["lines"][0]["l3_anchor"]
    fake = FakeLLMProvider(json_responses=[_canned_batch()])
    result = generate_lore_batch(
        fake,
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=[],
        existing_abouts=[], specs=SPECS_2,
    )
    assert isinstance(result, list)
    assert len(result) == 2
    # l3_anchor must equal whatever the model returned (no venues → no coercion)
    assert result[0]["l3_anchor"] == expected_l3


# ---------------------------------------------------------------------------
# Test: model returns fewer stages than stage_count → skeleton kept, not padded
# ---------------------------------------------------------------------------

def test_generate_lore_batch_fewer_stages_than_spec():
    """Model returns 1 stage for a spec with stage_count=3 → skeleton kept, stages length 1."""
    specs_3 = [{"complexity": "medium", "stage_count": 3}]
    canned = {"lines": [
        {
            "about": "城中古井传言有异",
            "secret": "古井通向地下密道",
            "description": "居民传说古井夜晚会发出声音",
            "trigger": "玩家接近古井",
            "l3_anchor": "市集",
            "stages": [{"hint": "听到异响"}],  # only 1 stage, spec wants 3
        }
    ]}
    fake = FakeLLMProvider(json_responses=[canned])
    result = generate_lore_batch(
        fake,
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=specs_3,
    )
    assert len(result) == 1
    assert len(result[0]["stages"]) == 1  # kept as-is, not padded


# ---------------------------------------------------------------------------
# Test: malformed spec entry (missing keys) → that entry dropped, valid ones kept
# ---------------------------------------------------------------------------

def test_generate_lore_batch_malformed_spec_no_raise():
    """A spec dict missing 'stage_count' must not raise; valid entries are still returned."""
    bad_spec = {"complexity": "simple"}  # missing stage_count
    good_spec = {"complexity": "complex", "stage_count": 5}
    specs = [bad_spec, good_spec]

    # Build canned response matching these 2 specs
    canned = {"lines": [
        {
            "about": "镇外神秘商队频繁出没",
            "secret": "商队运送被禁止的魔法材料",
            "description": "最近有支陌生商队驻扎在镇外林间",
            "trigger": "玩家询问旅店老板关于商队的消息",
            "l3_anchor": "市集",
            "stages": [{"hint": "发现神秘脚印"}, {"hint": "找到隐藏的货仓"}],
        },
        {
            "about": "码头下方沉睡的古代遗迹",
            "secret": "遗迹是被封印的龙窟",
            "description": "码头渔民报告水下有奇怪的光",
            "trigger": "玩家在码头附近调查",
            "l3_anchor": "码头",
            "stages": [
                {"hint": "渔民讲述目击经过"},
                {"hint": "水面冒出气泡"},
                {"hint": "发现古代铭文"},
                {"hint": "找到入口"},
                {"hint": "面对封印守卫"},
            ],
        },
    ]}
    fake = FakeLLMProvider(json_responses=[canned])
    # Must not raise even though bad_spec is malformed
    result = generate_lore_batch(
        fake,
        town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=VENUES, existing_abouts=[], specs=specs,
    )
    # bad_spec entry dropped, good_spec entry kept
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["complexity"] == "complex"


# ---------------------------------------------------------------------------
# Test: GEN_THRESHOLD constant is 50
# ---------------------------------------------------------------------------

def test_gen_threshold_value():
    assert GEN_THRESHOLD == 50


# ---------------------------------------------------------------------------
# Strict validation + harness-repair tests.
#
# The harness ENFORCES the schema rather than guessing at synonyms: each round
# the model's JSON is validated and any line with a missing/wrong field triggers
# a repair turn that NAMES the exact problems, so the model re-emits a conforming
# object (mirroring the engine's commit validation-repair loop). glm-5.1 returns
# valid JSON but with its OWN key vocabulary (summary/title, stages by narrative
# role, omitted trigger/secret) — strict validation rejects that and repairs it.
# ---------------------------------------------------------------------------


def _line(**over):
    """A fully-conforming line object (all 6 required model keys)."""
    d = {"about": "码头浮出裹盐无名尸", "description": "码头浮尸传闻",
         "trigger": "玩家查码头", "secret": "祭港旧俗灭口",
         "l3_anchor": "市集", "stages": [{"hint": "线索一"}, {"hint": "线索二"}]}
    d.update(over)
    return d


def _run(json_responses, specs, venues=None, max_repairs=2):
    fake = FakeLLMProvider(json_responses=json_responses)
    result = generate_lore_batch(
        fake, town_id=TOWN_ID, kind=KIND, flavor=FLAVOR,
        venues=(VENUES if venues is None else venues),
        existing_abouts=[], specs=specs, max_repairs=max_repairs,
    )
    return fake, result


def test_strict_conforming_batch_passes_first_round():
    """A fully-conforming batch passes with no repair (a single call)."""
    fake, res = _run([{"lines": [_line(), _line(l3_anchor="码头")]}], SPECS_2)
    assert len(res) == 2
    required = {"id", "complexity", "about", "anchor", "stages", "threshold",
                "description", "trigger", "secret", "l3_anchor"}
    for sk, spec in zip(res, SPECS_2):
        assert required.issubset(sk.keys())
        assert sk["complexity"] == spec["complexity"]
        assert sk["anchor"] == TOWN_ID and sk["threshold"] == GEN_THRESHOLD
        assert sk["l3_anchor"] in VENUES
        assert all(s["hint"] for s in sk["stages"])
    assert len(fake.calls) == 1  # no repair needed


def test_missing_fields_repaired_round_two():
    """Round-1 line misses required fields → repaired in round 2 → kept."""
    bad = {"lines": [
        {"l3_anchor": "市集", "stages": [{"hint": "a"}, {"hint": "b"}]},  # no about/desc/trigger/secret
        _line(),
    ]}
    good = {"lines": [_line(about="盐尸", secret="祭港"), _line()]}
    fake, res = _run([bad, good], SPECS_2)
    assert len(res) == 2
    assert res[0]["about"] == "盐尸" and res[0]["secret"] == "祭港"
    assert len(fake.calls) == 2  # one repair round


def test_repair_message_names_the_missing_fields():
    """The repair turn fed back to the model NAMES the exact missing fields
    (the harness-correction contract the user asked for)."""
    bad = {"lines": [{"l3_anchor": "市集", "stages": [{"hint": "a"}]}]}  # missing 4 fields
    good = {"lines": [_line()]}
    fake, res = _run([bad, good], [{"complexity": "simple", "stage_count": 2}])
    assert len(fake.calls) == 2
    repair = fake.calls[1][1]  # 2nd call's user turn = the repair feedback
    for field in ("about", "description", "trigger", "secret"):
        assert f'"{field}"' in repair, f"repair must name missing field {field!r}"


def test_never_conforming_line_dropped_after_repairs():
    """A line that never conforms across all rounds is dropped (not coerced)."""
    bad = {"lines": [{"l3_anchor": "市集", "stages": [{"hint": "a"}]}]}  # always missing fields
    fake, res = _run([bad], [{"complexity": "simple", "stage_count": 2}], max_repairs=1)
    assert res == []
    assert len(fake.calls) == 2  # initial + 1 repair attempt


def test_bad_l3_anchor_repaired():
    """l3_anchor not an allowed venue → rejected → repaired to a real venue."""
    bad = {"lines": [_line(l3_anchor="不存在的地方")]}
    good = {"lines": [_line(l3_anchor="码头")]}
    fake, res = _run([bad, good], [{"complexity": "simple", "stage_count": 2}])
    assert len(res) == 1
    assert res[0]["l3_anchor"] == "码头"
    assert "l3_anchor" in fake.calls[1][1]  # repair named it


def test_bad_stage_shape_repaired():
    """Stages not in {\"hint\": ...} form are rejected → repaired."""
    bad = {"lines": [_line(stages=[{"summary": "narrative-role key, not hint"}])]}
    good = {"lines": [_line()]}
    fake, res = _run([bad, good], [{"complexity": "simple", "stage_count": 2}])
    assert len(res) == 1
    assert all(s["hint"] for s in res[0]["stages"])
    assert "hint" in fake.calls[1][1]  # repair named the stage requirement


def test_echoed_complexity_does_not_override_spec():
    """Model echoes complexity='simple' (extra key) but the spec says 'complex'
    → the engine's spec complexity wins; the echoed key is ignored."""
    fake, res = _run([{"lines": [_line(complexity="simple")]}],
                     [{"complexity": "complex", "stage_count": 5}])
    assert len(res) == 1
    assert res[0]["complexity"] == "complex"


def test_extra_keys_tolerated_not_rejected():
    """Harmless extra keys (title/theme/stage_count) do NOT fail validation; the
    engine builds a clean skeleton without them."""
    fake, res = _run([{"lines": [_line(title="盐尸", theme="x", stage_count=2)]}],
                     [{"complexity": "simple", "stage_count": 2}])
    assert len(res) == 1
    assert "title" not in res[0] and "theme" not in res[0]
    assert len(fake.calls) == 1  # conformed first round (extras ignored)
