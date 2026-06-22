"""Density Generation -- Real-Model Demo on GLM-5.1

Demonstrates the full density-generation pipeline with a real LLM:
  1. Build a region-less world (L2 town + 3-4 L3 venues, no pre-seeded lore).
  2. Player enters the town -> engine auto-seeds 暗 lines (density 0.3, target=3).
  3. Run 5-6 turns: brewing, ambient disclosure, day advancement, refresh cycle.
  4. Per turn: dump auto-generated 暗 lines + ambient fragment + narration snippet.
  5. Mark which turn seeded and which refreshed.

Run:
    cd /root/rpg-engine-app
    set -a; . ./.env.local; set +a
    export PYTHONPATH=/root/rpg-engine-app
    python3 docs/superpowers/specs/density-build-2026-06-21/demo.py

DO NOT run this in tests (needs real API keys and real LLM).
"""
import os
import logging
import tempfile
from pathlib import Path

# Surface backstage warnings (e.g. "0 conforming skeletons") in the transcript.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from llm.provider import make_provider
from app.engine import build_engine
from app.play import _build_scene
from loop.turn import run_turn, REQUIRED_SECTIONS
from loop.strategy import AuthorStrategy
from loop.lore_disclosure import station_push_fragment
from kernel.events import kernel_event
from kernel.projection import project

SEED = 20260621

PROVIDER = make_provider(
    "zhipu",
    model=os.environ["GLM_MODEL"],
    base_url=os.environ["GLM_BASE_URL"],
    max_tokens=int(os.environ.get("GLM_MAX_TOKENS", "32768")),
)


def _seed_events(eng):
    """Seed a region-less world with one evocative L2 town + 4 L3 venues.

    No pre-seeded lore lines -- density generation creates them on first entry.
    """
    def place(pid, level, kind, seed, parent=None):
        d = {"id": pid, "level": level, "kind": kind, "seed": seed, "tier": "tracked"}
        if parent:
            d["parent"] = parent
        eng.store.append(kernel_event("place_created", day=1, scene="s1",
                                     summary=pid, deltas=d, turn=0))

    eng.store.append(kernel_event("campaign_seeded", day=1, scene="s1",
                                  summary="seed", deltas={"campaign_seed": SEED}, turn=0))

    # L2 town: no L1 parent (region-less world)
    place("幽港镇", 2, "settlement",
          "环绕古港的阴雨小镇，渔船老旧，盐气和腐木混杂，镇民讳莫如深")

    # L3 venues (4 distinct)
    for vid, vseed in [
        ("渔港码头", "残破的石砌码头，腐烂的渔网悬在桩上"),
        ("盐商会馆", "镶嵌贝壳图案的老会馆，锈迹斑斑的铜门紧闭"),
        ("渡口茶摊", "守渡老人的露天茶摊，四方旅人在此歇脚"),
        ("镇守庙", "供奉海神的小庙，香火冷清，只有几柱残香"),
    ]:
        place(vid, 3, "venue", vseed, parent="幽港镇")

    # Second location (outside the town) for protagonist to start from
    place("官道岔口", 3, "venue", "通往幽港镇的土路岔口，立有风蚀路牌")

    eng.store.append(kernel_event("character_created", day=1, scene="s1",
                                  summary="主角",
                                  deltas={"id": "主角", "tier": "tracked",
                                          "sketch": "浮海而来的江湖人，寻访旧日线索",
                                          "goal": "在这座小镇摸清底细"},
                                  turn=0))
    # Start the protagonist already inside the town (渡口茶摊) so density seeding
    # fires reliably on T1 — the demo's purpose is to show generation, not to test
    # whether the narrator emits an into-town move (that's the demote demo's job).
    eng.store.append(kernel_event("entity_moved", day=1, scene="s1",
                                  summary="到渡口茶摊",
                                  deltas={"who": "主角", "to": "渡口茶摊"}, turn=0))
    eng.world = project(eng.registry, eng.store.iter_events())


def _dump_lines(world, label=""):
    """Print all lore lines anchored to 幽港镇."""
    lines = (world.get("systems", {}).get("lore") or {}).get("lines", {})
    gen_lines = [ln for ln in lines.values() if ln.get("anchor") == "幽港镇"]
    if not gen_lines:
        print(f"  [{label}] 暗线: (无)")
        return
    for ln in gen_lines:
        stages_hint = " | ".join(
            s.get("hint", "?") for s in (ln.get("stages") or [])[:3]
        )
        print(
            f"  [{label}] 暗线 id={ln['id']}\n"
            f"           about={ln.get('about', '')!r}\n"
            f"           complexity={ln.get('complexity')} l3={ln.get('l3_anchor')}\n"
            f"           state={ln.get('state')} stage={ln.get('stage_idx')} "
            f"clues={len(ln.get('clues_dropped', []))}\n"
            f"           stages hint: {stages_hint}"
        )


def _dump_ambient(world, loc):
    """Print station_push_fragment for protagonist at given L3 location."""
    scene = {"protagonist": "主角", "present": [], "day": 1, "id": "x", "location": loc}
    frag = station_push_fragment(None, world, scene)
    if frag:
        # Only show the first 300 chars to keep output compact
        print(f"  [ambient] {frag[:300].replace(chr(10), ' | ')}")
    else:
        print("  [ambient] (无暗线在此处)")


ACTIONS = [
    "我在渡口茶摊坐下，打量幽港镇四周往来人等，留意有无异常。",       # T1: in town -> SEED
    "我在渔港码头转了转，看渔民收网，留意有无异常动静。",              # T2: brew + ambient
    "在盐商会馆门前徘徊，想知道这里为何常年铁将军把门。",              # T3: brew continues
    "去镇守庙上了炷香，和庙祝聊聊镇里最近的怪事。",                   # T4 (advance days)
    "在茶摊住了两晚，天气转晴，我重返码头看有没有新动静。",            # T5 (advance days -> refresh check)
    "码头上又多了几个生面孔，我假装修鞋套套话。",                      # T6
]

# Days to advance at each turn (to trigger refresh)
CLOCK_DAYS = [0, 0, 1, 1, 2, 0]  # total: day 5 by T5 -> triggers refresh at day 4


def main():
    camp = Path(tempfile.mkdtemp(prefix="density_demo_"))
    eng = build_engine(camp, provider=PROVIDER)
    eng.campaign_seed = SEED
    # Use the same provider for both narrator and cascade (real model)
    eng.cascade_provider = PROVIDER

    _seed_events(eng)
    strat = AuthorStrategy()
    prev_scene = None

    print("== 密度生成 DEMO (region-less world: 幽港镇) ==")
    print(f"narrator+cascade provider: {os.environ['GLM_MODEL']}")
    print("初始状态: 幽港镇⊃渔港码头/盐商会馆/渡口茶摊/镇守庙  |  无预种暗线\n")
    _dump_lines(eng.world, "init")
    print()

    seeded_turn = None
    refreshed_turn = None

    for i, (act, clock_days) in enumerate(zip(ACTIONS, CLOCK_DAYS), 1):
        scene = _build_scene(eng)
        scene["protagonist"] = "主角"
        # Show ambient BEFORE the turn (what the narrator sees)
        cur_loc = scene.get("location", "?")
        print(f"--- 回合 T{i} @ {cur_loc} ---")
        print(f"行动: {act}")
        _dump_ambient(eng.world, cur_loc)

        try:
            res = run_turn(
                eng.registry, eng.store, eng.world, scene, act,
                strategy=strat, provider=PROVIDER, embedder=eng.embedder,
                max_repairs=4, required_sections=REQUIRED_SECTIONS,
                cascade_provider=eng.cascade_provider,
                catchup_provider=eng.cascade_provider,
                prev_scene=prev_scene,
            )
        except Exception as exc:
            print(f"[T{i}] ERROR {type(exc).__name__}: {exc}")
            break

        eng.world = res.world
        prev_scene = scene

        # Detect seeding and refresh events in this turn's store
        all_evs = list(eng.store.iter_events())
        turn_evs = [e for e in all_evs if e.get("turn") == i]
        if any(e["type"] == "lore_seeded" for e in turn_evs) and seeded_turn is None:
            seeded_turn = i
            print(f"  *** 首次播种! (T{i}) ***")
        if any(e["type"] == "density_refreshed" for e in turn_evs):
            if refreshed_turn is None:
                refreshed_turn = i
            print(f"  *** density_refreshed (T{i}) ***")

        # Print narration snippet + generated lines
        print(f"  叙事: {(res.narration or '')[:200]}")
        _dump_lines(eng.world, f"T{i}")
        print()

    gen_state = (eng.world.get("systems", {}).get("lore") or {}).get("gen", {})
    print("=== DONE ===")
    print(f"  seeded at T{seeded_turn}, refreshed at T{refreshed_turn}")
    print(f"  gen state: {gen_state}")
    total = len([ln for ln in eng.world.get("systems", {}).get("lore", {}).get("lines", {}).values()
                 if ln.get("anchor") == "幽港镇"])
    print(f"  total lines spawned in 幽港镇: {total}")


main()
