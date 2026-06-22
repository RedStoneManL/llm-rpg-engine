"""Complex-Line Endgame Demo — real GLM-5.1 (or GLM-4.7)

Demonstrates the full complex-line endgame pipeline with a real LLM:
  1. Build L1 region ⊃ L2 town ⊃ L3 venues + ONE complex 暗 line at the town.
  2. The line is pre-brewed to stage 2 (mid stage) at day 1 (nobody will engage it).
  3. Run 6 turns where the player does things ELSEWHERE; narrator advances the clock.
  4. The line's short lifespan (6 days) ensures it goes pending_finale → finale by T4-T5.
  5. Per turn: dump complex line state/stage + any endgame events emitted this turn.
  6. If catastrophe: dump the world_change + cascade region evolution.

Run:
    cd /root/rpg-engine-app
    set -a; . ./.env.local; set +a
    export PYTHONPATH=/root/rpg-engine-app
    python3 docs/superpowers/specs/endgame-build-2026-06-21/demo.py

DO NOT run this in tests (needs real API keys and a real LLM).
"""
import os
import logging
import tempfile
from pathlib import Path

# Surface backstage warnings in the transcript
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from llm.provider import make_provider
from app.engine import build_engine
from app.play import _build_scene
from loop.turn import run_turn, REQUIRED_SECTIONS
from loop.strategy import AuthorStrategy
from loop.lore import create_lore_line, run_lore
from loop.density import region_scope, count_tier
from loop.endgame import FINALE_RESCUE_CHANCE
from kernel.events import kernel_event
from kernel.projection import project

SEED = 20260621

PROVIDER = make_provider(
    "zhipu",
    model=os.environ["GLM_MODEL"],
    base_url=os.environ["GLM_BASE_URL"],
    max_tokens=int(os.environ.get("GLM_MAX_TOKENS", "32768")),
)

# ---------------------------------------------------------------------------
# Pre-brewed complex 暗 line: 5 stages, born at day 1, lifespan 6 days.
# With threshold=100 it always advances when run_lore fires, but the demo
# advances the clock so it goes lifespan-expired → pending_finale → finale
# before stage 4 (last stage) is ever reached.
# ---------------------------------------------------------------------------
_COMPLEX_SK = {
    "id": "守将机密泄露",
    "complexity": "complex",
    "about": "要塞机密外流",
    "secret": "内奸是守将之子,暗通敌国",
    "anchor": "边城",
    "description": "要塞驻军机密悄然流向边境敌方",
    "trigger": "玩家调查守将可疑联系",
    "l3_anchor": "要塞大营",
    "stages": [
        {"hint": "小道消息开始流传"},
        {"hint": "信使深夜出入要塞"},
        {"hint": "要塞粮草账目出现缺口"},
        {"hint": "一名哨兵神秘失踪"},
        {"hint": "机密文书全面外泄"},
    ],
    "threshold": 100,  # always advances when run_lore fires
}

# Lifespan: 6 days — short enough that day 1→7 triggers pending_finale
_LIFESPAN_DAYS = 6


def _seed_events(eng):
    """Seed the world: L1 region ⊃ L2 town ⊃ L3 venues + campaign seed + complex 暗 line."""
    def place(pid, level, kind, seed, parent=None):
        d = {"id": pid, "level": level, "kind": kind, "seed": seed, "tier": "tracked"}
        if parent:
            d["parent"] = parent
        eng.store.append(kernel_event("place_created", day=1, scene="s1",
                                     summary=pid, deltas=d, turn=0))

    eng.store.append(kernel_event("campaign_seeded", day=1, scene="s1",
                                  summary="seed", deltas={"campaign_seed": SEED}, turn=0))

    # L1 region
    place("北境", 1, "region", "荒凉边疆，常年军备，民风剽悍")

    # L2 town (complex line anchor)
    place("边城", 2, "settlement", "驻守要塞的边境重镇，商道咽喉", parent="北境")

    # L3 venues inside the town
    for vid, vseed in [
        ("要塞大营", "驻军营地，戒备森严，士兵来往频繁"),
        ("商行街", "商旅云集的主街，消息灵通"),
        ("城门楼", "厚重城墙上的了望台，俯瞰四野"),
    ]:
        place(vid, 3, "venue", vseed, parent="边城")

    # A DIFFERENT town where the player starts (deliberately away from the complex line)
    place("南渡口", 2, "settlement", "远离要塞的渡口小镇，河上往来客船不绝", parent="北境")
    place("渡口客栈", 3, "venue", "驿道旁的老客栈，旅人聚散之地", parent="南渡口")
    place("河边集市", 3, "venue", "晨起鱼市，晚间变成小赌场", parent="南渡口")

    # Protagonist starts at 南渡口 (away from 边城 → no density seeding or engagement)
    eng.store.append(kernel_event("character_created", day=1, scene="s1",
                                  summary="主角",
                                  deltas={"id": "主角", "tier": "tracked",
                                          "sketch": "流浪江湖的剑客，曾在北境戍边",
                                          "goal": "渡口打听旧友下落"},
                                  turn=0))
    eng.store.append(kernel_event("entity_moved", day=1, scene="s1",
                                  summary="到渡口客栈",
                                  deltas={"who": "主角", "to": "渡口客栈"}, turn=0))

    # Seed the complex 暗 line manually (pre-brewed to stage 2 via lore_advanced events)
    create_lore_line(eng.store, _COMPLEX_SK, day=1, scene="s1", turn=0,
                     lifespan_days=_LIFESPAN_DAYS)
    # Pre-advance to stage 2 so the line is "mid-brew" when the demo starts
    eng.store.append(kernel_event("lore_advanced", day=1, scene="s1",
                                  summary="暗线stage0",
                                  deltas={"id": "守将机密泄露", "stage_idx": 0,
                                          "hint": "小道消息开始流传"},
                                  turn=0))
    eng.store.append(kernel_event("lore_advanced", day=1, scene="s1",
                                  summary="暗线stage1",
                                  deltas={"id": "守将机密泄露", "stage_idx": 1,
                                          "hint": "信使深夜出入要塞"},
                                  turn=0))
    eng.store.append(kernel_event("lore_advanced", day=1, scene="s1",
                                  summary="暗线stage2",
                                  deltas={"id": "守将机密泄露", "stage_idx": 2,
                                          "hint": "要塞粮草账目出现缺口"},
                                  turn=0))

    eng.world = project(eng.registry, eng.store.iter_events())
    eng.world.setdefault("meta", {})["campaign_seed"] = SEED
    eng.world["meta"]["scene"] = "s1"


def _dump_complex_line(world, turn_label=""):
    """Print state/stage of the complex 暗 line."""
    lines = (world.get("systems", {}).get("lore") or {}).get("lines", {})
    ln = lines.get("守将机密泄露")
    if ln is None:
        print(f"  [{turn_label}] 复杂暗线: (未找到)")
        return
    print(
        f"  [{turn_label}] 复杂暗线 '守将机密泄露'\n"
        f"           state={ln.get('state')} stage={ln.get('stage_idx')} "
        f"pending_finale={ln.get('pending_finale', False)}\n"
        f"           state={ln.get('state')} resolved={ln.get('resolved')}"
    )


def _dump_endgame_events(turn_evs, world):
    """Print endgame events emitted this turn."""
    endgame_types = {"quest_world_resolved", "quest_catastrophe", "world_change"}
    found = [e for e in turn_evs if e["type"] in endgame_types]
    if not found:
        print("  [endgame] 本回合无终局事件")
        return
    for ev in found:
        print(f"  [endgame] {ev['type']}: {ev.get('deltas', {})}")
        if ev["type"] == "world_change":
            place = ev["deltas"].get("place", "?")
            summary = ev["deltas"].get("summary", "")
            print(f"  [CASCADE] world_change → place={place!r}")
            print(f"           summary: {summary[:200]}")
            # Show density cap state
            region = region_scope(world, "边城", world.get("meta", {}).get("day", 1))
            cap = count_tier(world, region, "complex")
            print(f"           region={region!r} remaining complex cap slots: {2 - cap}/2")


# Actions: player stays at 南渡口 doing mundane things; never visits 边城
ACTIONS = [
    "我在渡口客栈休息，向掌柜打听有没有来自北境的商队。",          # T1: day 1
    "去河边集市转了一圈，听人说最近北境边境不太平。",               # T2: day 2 (+1)
    "在渡口码头等候，看看有没有认识的人渡河过来。",                 # T3: day 3 (+1)
    "又在客栈住了两天，天色阴沉，我整理行囊，准备明日启程。",       # T4: day 5 (+2) → lifespan 6 → pending_finale
    "天刚亮便起身，沿官道向北走了半日，仍在南渡口境内。",           # T5: day 6 (+1) → finale fires
    "在一处破庙暂避风雨，等候边境方向消息。",                       # T6: day 7 (+1)
]

# Clock advances per turn (in game-days)
CLOCK_DAYS = [0, 1, 1, 2, 1, 1]


def main():
    camp = Path(tempfile.mkdtemp(prefix="endgame_demo_"))
    eng = build_engine(camp, provider=PROVIDER)
    eng.campaign_seed = SEED
    eng.cascade_provider = PROVIDER

    _seed_events(eng)
    strat = AuthorStrategy()
    prev_scene = None

    print("== 复杂线终局 DEMO ==")
    print(f"  narration provider: {os.environ['GLM_MODEL']}")
    print(f"  复杂暗线: 守将机密泄露 (anchor=边城, lifespan={_LIFESPAN_DAYS}天, pre-brewed to stage 2)")
    print(f"  玩家在南渡口 — 永远不会接触边城的复杂线")
    print()
    _dump_complex_line(eng.world, "init")
    print()

    resolved_turn = None
    catastrophe_turn = None

    for i, (act, clock_days) in enumerate(zip(ACTIONS, CLOCK_DAYS), 1):
        scene = _build_scene(eng)
        scene["protagonist"] = "主角"
        cur_loc = scene.get("location", "?")
        cur_day = eng.world.get("meta", {}).get("day", "?")
        print(f"--- 回合 T{i} (game-day {cur_day}) @ {cur_loc} ---")
        print(f"行动: {act}")

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

        # Collect events for this turn
        all_evs = list(eng.store.iter_events())
        turn_evs = [e for e in all_evs if e.get("turn") == i]

        # Print narration snippet
        print(f"  叙事: {(res.narration or '')[:200]}")

        # Dump complex line state
        _dump_complex_line(eng.world, f"T{i}")

        # Dump endgame events
        _dump_endgame_events(turn_evs, eng.world)

        # Mark resolution turn
        if resolved_turn is None and any(e["type"] == "quest_world_resolved" for e in turn_evs):
            resolved_turn = i
            print(f"  *** 世界自行化解! quest_world_resolved @ T{i} ***")
        if catastrophe_turn is None and any(e["type"] == "quest_catastrophe" for e in turn_evs):
            catastrophe_turn = i
            print(f"  *** 终局灾难爆发! quest_catastrophe @ T{i} ***")

        print()

        # If resolved, note the cascade region evolution (places after catastrophe)
        if catastrophe_turn == i:
            places_sys = (eng.world.get("systems", {}).get("place") or {})
            region_places = {
                pid: p for pid, p in
                (places_sys.get("places") or {}).items()
                if "北境" in pid or "边城" in pid or "要塞" in pid
            }
            if region_places:
                print("  [cascade aftermath] 北境/边城区域地点状态:")
                for pid, p in list(region_places.items())[:5]:
                    state_facts = (p.get("facts") or {})
                    print(f"    {pid}: facts_keys={list(state_facts.keys())[:4]}")
                print()

    print("=== DEMO 结束 ===")
    if resolved_turn:
        print(f"  世界救场成功 @ T{resolved_turn}")
    elif catastrophe_turn:
        print(f"  终局灾难爆发 @ T{catastrophe_turn}")
    else:
        print("  暗线仍未了结（尝试延长 CLOCK_DAYS 或增加回合数）")

    # Final line state
    _dump_complex_line(eng.world, "final")

    # Final density cap
    final_day = eng.world.get("meta", {}).get("day", 1)
    region = region_scope(eng.world, "边城", final_day)
    cap = count_tier(eng.world, region, "complex")
    print(f"\n  最终 region={region!r} complex 上限占用: {cap}/2")


main()
