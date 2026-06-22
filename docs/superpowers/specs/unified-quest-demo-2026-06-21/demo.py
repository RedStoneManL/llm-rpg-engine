"""Unified Questline — full-lifecycle demo on glm-5.1.

Drives the WHOLE state machine on the real model to see how it handles the
unified quest system: 暗骰 brews 暗 lines (+ ambient disclosure) → player follows
a clue → narrator SURFACES it (quests:surface, 暗→明) → narrator ADVANCES the 明
quest over turns (in the 明账) → player leaves the town → engine DEMOTES it
(明→暗 + JIT-resequence) → a complex 暗 line world-push surfaces at its 爆点 →
player RESOLVES one (quests:resolve, →了结). Dumps per turn: each quest's state,
which channel moved it, the 明账, the ambient 暗 clues, and the narration.

Run: cd /root/rpg-engine-app && set -a; . ./.env.local; set +a
     export PYTHONPATH=/root/rpg-engine-app
     python3 docs/superpowers/specs/unified-quest-demo-2026-06-21/demo.py
"""
import os
import tempfile
from pathlib import Path

from llm.provider import make_provider
from app.engine import build_engine
from app.play import _build_scene
from loop.turn import run_turn, REQUIRED_SECTIONS
from loop.strategy import AuthorStrategy
from loop.lore import create_lore_line
from loop.lore_disclosure import station_push_fragment
from kernel.events import kernel_event
from kernel.projection import project

SEED = 20260621
PROVIDER = make_provider("zhipu", model=os.environ["GLM_MODEL"],
                         base_url=os.environ["GLM_BASE_URL"],
                         max_tokens=int(os.environ.get("GLM_MAX_TOKENS", "32768")))


def _seed_events():
    evs = [kernel_event("campaign_seeded", day=1, scene="s1", summary="seed",
                        deltas={"campaign_seed": SEED}, turn=0)]

    def place(pid, level, kind, seed, parent=None):
        d = {"id": pid, "level": level, "kind": kind, "seed": seed, "tier": "tracked"}
        if parent:
            d["parent"] = parent
        evs.append(kernel_event("place_created", day=1, scene="s1", summary=pid, deltas=d, turn=0))

    place("青石镇", 2, "settlement", "雨季前的边陲集镇")
    for v, s in [("市集", "嘈杂的露天集市"), ("酒馆", "镇口悦来酒馆"), ("码头", "镇东旧码头")]:
        place(v, 3, "venue", s, parent="青石镇")
    place("邻村", 2, "settlement", "半日脚程外的小村")
    place("村口", 3, "venue", "邻村的老槐树下", parent="邻村")
    evs.append(kernel_event("place_linked", day=1, scene="s1", summary="link",
                            deltas={"a": "青石镇", "b": "邻村", "travel_cost": 2}, turn=0))
    evs.append(kernel_event("character_created", day=1, scene="s1", summary="主角",
                            deltas={"id": "主角", "tier": "tracked",
                                    "sketch": "游历至此的江湖客", "goal": "讨生活、管闲事"}, turn=0))
    evs.append(kernel_event("entity_moved", day=1, scene="s1", summary="到市集",
                            deltas={"who": "主角", "to": "市集"}, turn=0))
    return evs


# (id, complexity, l3, desc, trigger, secret, stages, pre_advance_to)
_LINES = [
    ("失踪商队", "medium", "市集", "集市上关于一支失踪商队的窃窃私语",
     "玩家打听商队/失踪的商人/货物去向", "商队首领卷款潜逃、栽赃马匪",
     ["有人在打听商队的下落", "城门记录显示这支商队从没出过城", "首领空宅里翻出烧剩的地契"], 0),
    ("假药郎中", "simple", "市集", "一个游方郎中的摊子前总围着病人",
     "玩家看病/买药/留意郎中", "他卖的是掺了滑石粉的假药",
     ["郎中正吹嘘药到病除", "听说有人吃了他的药反而更重了"], 0),
    ("码头浮尸", "complex", "码头", "水边的苇草丛里似乎缠着什么东西",
     "玩家细看水边/留意浮尸/查命案", "死者是揭发盐枭的差役、被灭口抛尸",
     ["水边漂着一片脏污的衣布", "苇草缠住一具发胀的浮尸", "死者怀里塞着半张账册"], 1),  # pre-brewed near末
]


def _seed_lines(store):
    for lid, cx, l3, desc, trig, secret, stages, adv in _LINES:
        create_lore_line(store, {"id": lid, "complexity": cx, "about": desc, "secret": secret,
                                 "anchor": "青石镇", "l3_anchor": l3, "description": desc,
                                 "trigger": trig, "stages": [{"hint": h} for h in stages],
                                 "threshold": 100}, day=1, scene="s1", turn=0)  # threshold 100 → 暗骰必推
        for i in range(adv + 1):
            store.append(kernel_event("lore_advanced", day=1, scene="s1", summary=f"{lid}->{i}",
                                      deltas={"id": lid, "stage_idx": i, "hint": stages[i]}, turn=0))


def _dump_quests(world):
    lines = (world.get("systems", {}).get("lore") or {}).get("lines", {})
    for lid, ln in lines.items():
        st = ln.get("state"); idx = ln.get("stage_idx")
        clues = ln.get("clues_dropped", [])
        print(f"     - {lid}[{ln.get('complexity')}] state={st} stage={idx} "
              f"summary={ln.get('summary')!r} clues={len(clues)}", flush=True)


ACTIONS = [
    "我在市集的摊位间穿行,留心镇上近来的怪事。",            # 暗骰 brew + ambient
    "我拉住一个老摊主,专打听那支没了音信的商队。",          # follow clue → narrator surface (暗→明)
    "我顺着城门记录这条线,去查那商队到底出没出过城。",      # narrator advance (明)
    "事情一时查不下去,我索性动身去半日外的邻村透透气。",    # leave 青石镇 → demote-on-leave (明→暗 + JIT)
    "在邻村歇了一晚,我又折回青石镇,径直往码头去看看。",    # complex 码头浮尸 暗骰到末→world-push surface
    "我把商队这桩事的来龙去脉理清,做个了断。",              # resolve (→了结)
]


def main():
    camp = Path(tempfile.mkdtemp(prefix="uqdemo_"))
    eng = build_engine(camp, provider=PROVIDER)
    eng.campaign_seed = SEED
    for ev in _seed_events():
        eng.store.append(ev)
    _seed_lines(eng.store)
    eng.world = project(eng.registry, eng.store.iter_events())
    strat = AuthorStrategy()
    prev_scene = None
    print(f"== UNIFIED QUEST LIFECYCLE DEMO narrator={os.environ['GLM_MODEL']} ==", flush=True)
    print("world: 青石镇⊃市集/酒馆/码头 + 邻村; 3 quests (失踪商队 medium / 假药郎中 simple / 码头浮尸 complex,已酿到中段)", flush=True)
    print("\n--- 初始 quests ---", flush=True); _dump_quests(eng.world)
    for i, act in enumerate(ACTIONS, 1):
        scene = _build_scene(eng)
        amb = station_push_fragment(eng.registry, eng.world, scene)
        try:
            res = run_turn(eng.registry, eng.store, eng.world, scene, act,
                           strategy=strat, provider=PROVIDER, embedder=eng.embedder,
                           max_repairs=4, required_sections=REQUIRED_SECTIONS,
                           cascade_provider=eng.cascade_provider,
                           catchup_provider=eng.cascade_provider, prev_scene=prev_scene)
        except Exception as exc:
            print(f"\n[T{i}] ERROR {type(exc).__name__}: {exc}", flush=True); break
        eng.world = res.world; prev_scene = scene
        q = (res.commit.sections or {}).get("quests")
        print(f"\n[T{i}] @{scene.get('location')} repairs={res.repair_attempts} dropped={res.dropped_sections}", flush=True)
        print(f"  输入: {act}", flush=True)
        print(f"  [叙事模型 quests 段]: {q or '(无)'}", flush=True)
        print(f"  [上下文·暗线环境]: {(amb or '(无)')[:160].replace(chr(10),' ')}", flush=True)
        print(f"  « {(res.narration or '')[:200]}", flush=True)
        print("  --- quests after ---", flush=True); _dump_quests(eng.world)
    print("\n=== DONE ===", flush=True)


main()
