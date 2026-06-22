"""T9: the 3-tier knowledge-access lenses on top of P3a POV tools.

Tiers (user-approved 2026-06-22, "安全地板" default):
  1. POV       — per-agent knows() (already P3a: map/recall/characters/factions).
  2. Ambient   — PUBLIC tier (passerby / 街坊常识): structural place/faction seeds
                 + facts EXPLICITLY marked secrecy=="public". Unmarked (None) and
                 restricted/secret facts NEVER surface — safe floor, no leak even
                 if the narrator forgets to tag. No per-agent gating.
  3. DM        — ground truth (all secrecy, true values), authoring-only; only in
                 build_tool_registry(dm=True).

Also locks the secrecy WRITE-path: the `facts` commit section carries an optional
`secrecy` that flows commit → to_events → fact_asserted → Fact.secrecy.
"""
from __future__ import annotations

import json

from kernel.registry import Registry
from kernel.projection import empty_world, project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.knowledge import KnowledgeSystem
from systems.character import CharacterSystem
from systems.faction import FactionSystem


def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(KnowledgeSystem())
    r.register(CharacterSystem())
    r.register(FactionSystem())
    return r


def _scene(protagonist="hero", present=None, day=1, location="city"):
    return {
        "protagonist": protagonist,
        "present": present if present is not None else [protagonist],
        "day": day,
        "location": location,
    }


def _world_with_secrecy():
    """A city (public seed) + a public common-knowledge fact + a secret fact on a
    Person + an UNMARKED fact (default None secrecy)."""
    r = _reg()
    evs = [
        kernel_event("place_created", day=1, scene="g", summary="city",
                     deltas={"id": "city", "level": 2, "kind": "settlement",
                             "seed": "热闹的集市镇"}, turn=1),
        kernel_event("character_created", day=1, scene="g", summary="leader",
                     deltas={"id": "caravan_leader", "tier": "tracked",
                             "sketch": "商队首领", "goal": "贩货"}, turn=1),
    ]
    w = project(r, iter(evs))
    g = w["systems"]["ontology"]
    # public common-knowledge fact (a passerby would relay this)
    g.assert_fact("city", "近况", "码头近来热闹",
                  day=1, turn=1, source_event="s1", secrecy="public")
    # secret fact — true identity; must NEVER reach the public tier
    g.assert_fact("caravan_leader", "真实身份", "官府卧底",
                  day=1, turn=1, source_event="s2", secrecy="secret")
    # unmarked fact (secrecy=None) — safe floor: NOT ambient
    g.assert_fact("city", "传言", "据说城西埋着宝藏",
                  day=1, turn=1, source_event="s3")
    return r, w


# ---------------------------------------------------------------------------
# Ambient (public / passerby) lens
# ---------------------------------------------------------------------------

def test_ambient_query_returns_public_fact():
    from llm.tools import build_tool_registry
    r, w = _world_with_secrecy()
    reg = build_tool_registry(r, w, _scene())
    out = reg.execute("ambient_query", {"q": "city"})
    assert "码头近来热闹" in out  # public fact surfaces to anyone


def test_ambient_query_hides_secret_fact():
    from llm.tools import build_tool_registry
    r, w = _world_with_secrecy()
    reg = build_tool_registry(r, w, _scene())
    out = reg.execute("ambient_query", {"q": "caravan_leader"})
    assert "官府卧底" not in out  # secret never in public tier


def test_ambient_query_hides_unmarked_fact():
    """Safe floor: a fact with no explicit secrecy (None) is NOT ambient."""
    from llm.tools import build_tool_registry
    r, w = _world_with_secrecy()
    reg = build_tool_registry(r, w, _scene())
    out = reg.execute("ambient_query", {"q": "city"})
    assert "据说城西埋着宝藏" not in out  # unmarked → not leaked


def test_ambient_query_returns_place_seed_on_seed_match():
    from llm.tools import build_tool_registry
    r, w = _world_with_secrecy()
    reg = build_tool_registry(r, w, _scene())
    out = reg.execute("ambient_query", {"q": "集市"})  # match on seed substring
    assert "热闹的集市镇" in out  # public structural seed


def test_ambient_query_ignores_pov_knows_gate():
    """Ambient is the PUBLIC tier: a public fact surfaces even though the
    protagonist has NO knows() grant (unlike POV map_query)."""
    from llm.tools import build_tool_registry
    r, w = _world_with_secrecy()
    reg = build_tool_registry(r, w, _scene())  # hero never granted knows on city.近况
    out = reg.execute("ambient_query", {"q": "近况"})
    assert "码头近来热闹" in out


def test_ambient_query_matches_natural_language_phrase():
    """Regression (caught by the live glm-5.1 probe): the narrator passes a
    natural-language PHRASE, not a keyword. A public fact whose subject name
    appears inside the phrase must still surface — a plain `q in subject`
    substring test (q longer than subject) silently returned nothing."""
    from llm.tools import build_tool_registry
    r, w = _world_with_secrecy()
    reg = build_tool_registry(r, w, _scene())
    out = reg.execute("ambient_query", {"q": "city 最近的风声 异常 街坊都在议论的事"})
    assert "码头近来热闹" in out  # matched via subject("city")-in-phrase


def test_ambient_query_in_default_pov_registry():
    from llm.tools import build_tool_registry
    reg = build_tool_registry(_reg(), empty_world(_reg()), _scene())
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "ambient_query" in names


def test_ambient_query_present_in_dm_registry_too():
    """Public tier is always on — also present when dm=True."""
    from llm.tools import build_tool_registry
    reg = build_tool_registry(_reg(), empty_world(_reg()), _scene(), dm=True)
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "ambient_query" in names


def test_ambient_query_never_raises_on_empty_world():
    from llm.tools import build_tool_registry
    reg = build_tool_registry(_reg(), empty_world(_reg()), _scene())
    out = json.loads(reg.execute("ambient_query", {"q": "anything"}))
    assert "error" not in out


# ---------------------------------------------------------------------------
# DM ground-truth lens (dm=True only)
# ---------------------------------------------------------------------------

def test_dm_world_query_excluded_when_dm_false():
    from llm.tools import build_tool_registry
    reg = build_tool_registry(_reg(), empty_world(_reg()), _scene(), dm=False)
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "dm_world_query" not in names


def test_dm_world_query_present_when_dm_true():
    from llm.tools import build_tool_registry
    reg = build_tool_registry(_reg(), empty_world(_reg()), _scene(), dm=True)
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "dm_world_query" in names


def test_dm_world_query_returns_secret_ground_truth():
    """DM lens bypasses BOTH fog and secrecy — the secret true value is visible."""
    from llm.tools import build_tool_registry
    r, w = _world_with_secrecy()
    reg = build_tool_registry(r, w, _scene(), dm=True)
    out = reg.execute("dm_world_query", {"q": "caravan_leader"})
    assert "官府卧底" in out  # ground truth incl. secret


def test_dm_world_query_returns_unmarked_too():
    from llm.tools import build_tool_registry
    r, w = _world_with_secrecy()
    reg = build_tool_registry(r, w, _scene(), dm=True)
    out = reg.execute("dm_world_query", {"q": "city"})
    assert "据说城西埋着宝藏" in out  # DM sees everything, marked or not


# ---------------------------------------------------------------------------
# secrecy WRITE-path: facts commit section → Fact.secrecy
# ---------------------------------------------------------------------------

def test_facts_section_to_events_carries_secrecy():
    sysm = OntologySystem()
    evs = sysm.to_events(
        "facts",
        [{"subject": "city", "predicate": "近况", "value": "热闹",
          "secrecy": "public"}],
        turn=1, day=1, scene="g",
    )
    assert evs and evs[0]["deltas"].get("secrecy") == "public"


def test_facts_writepath_sets_fact_secrecy_end_to_end():
    r = _reg()
    sysm = OntologySystem()
    fact_evs = sysm.to_events(
        "facts",
        [{"subject": "city", "predicate": "近况", "value": "热闹",
          "secrecy": "public"}],
        turn=1, day=1, scene="g",
    )
    base = [
        kernel_event("place_created", day=1, scene="g", summary="city",
                     deltas={"id": "city", "level": 2, "kind": "settlement",
                             "seed": "城"}, turn=1),
    ]
    w = project(r, iter(base + fact_evs))
    g = w["systems"]["ontology"]
    facts = [f for f in g.facts if f.subject == "city" and f.predicate == "近况"]
    assert facts and facts[0].secrecy == "public"


def test_system_prompt_documents_secrecy():
    """The narrator must be TOLD it can mark a fact's secrecy, else the
    write-path stays dormant (the whole reason secrecy was inert)."""
    from loop.strategy import _SYSTEM_PROMPT, _SYSTEM_PROMPT_HYBRID
    assert "secrecy" in _SYSTEM_PROMPT
    assert "secrecy" in _SYSTEM_PROMPT_HYBRID
