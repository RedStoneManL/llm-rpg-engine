"""Lore disclosure A/B comparison on glm-5.1.

A complex, realistic town (青石镇) with 8 event-lines spread across 4 venues
(mixed complexity, some pre-brewed). The SAME 6 player actions are run under
disclosure_mode="A" (PULL: model sees a compact L0 index + a fetch_storyline
tool it calls on demand) and ="B" (PUSH: engine auto-pushes the current venue's
L1 beats). Same campaign_seed + same world + same actions ⇒ the ONLY difference
is how lore reaches the narrator. Dumps per-turn: the disclosed context, what A's
model chose to fetch, the narration, repairs/drops — for a side-by-side read.

Run:  cd /root/rpg-engine-app && set -a; . ./.env.local; set +a
      export PYTHONPATH=/root/rpg-engine-app
      python3 docs/superpowers/specs/lore-AB-2026-06-20/compare.py
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
from loop.lore_disclosure import station_push_fragment, index_fragment
from kernel.events import kernel_event
from kernel.projection import project
import loop.strategy as _strat_mod

SEED = 20260620  # fixed → identical director/lore 暗骰 rolls across A and B

# --- instrument A's tool pulls: wrap the registry the strategy builds ---
_real_build = _strat_mod.build_lore_tool_registry
TOOL_LOG: list[tuple] = []


def _logging_build(registry, world, scene):
    reg = _real_build(registry, world, scene)
    real_exec = reg.execute

    def logged_exec(name, args):
        TOOL_LOG.append((name, dict(args)))
        return real_exec(name, args)

    reg.execute = logged_exec
    return reg


_strat_mod.build_lore_tool_registry = _logging_build


# --- the realistic world: 青石镇 (L2) ⊃ 集市/酒馆/码头/城隍庙 (L3) + 8 lore lines ---
def _seed_events():
    evs = [kernel_event("campaign_seeded", day=1, scene="s1", summary="seed",
                        deltas={"campaign_seed": SEED}, turn=0)]

    def place(pid, level, kind, seed, parent=None):
        d = {"id": pid, "level": level, "kind": kind, "seed": seed, "tier": "tracked"}
        if parent:
            d["parent"] = parent
        evs.append(kernel_event("place_created", day=1, scene="s1",
                                summary=pid, deltas=d, turn=0))

    place("青石镇", 2, "settlement", "雨季前的边陲集镇")
    for v, s in [("集市", "嘈杂的露天集市"), ("酒馆", "镇口的悦来酒馆"),
                 ("码头", "镇东的旧货运码头"), ("城隍庙", "香火复盛的城隍庙")]:
        place(v, 3, "venue", s, parent="青石镇")
    evs.append(kernel_event("character_created", day=1, scene="s1", summary="主角",
                            deltas={"id": "主角", "tier": "tracked",
                                    "sketch": "游历至此的江湖客", "goal": "讨生活、管闲事"}, turn=0))
    evs.append(kernel_event("entity_moved", day=1, scene="s1", summary="到集市",
                            deltas={"who": "主角", "to": "集市"}, turn=0))
    return evs


# (id, complexity, l3, desc, trigger, secret, stages, pre_advance_to_stage)
_LINES = [
    ("失踪商队", "medium", "集市", "集市上关于一支失踪商队的窃窃私语",
     "玩家打听商队/失踪的商人/货物去向",
     "商队首领卷款潜逃、栽赃给马匪",
     ["有人在打听商队的下落", "城门记录显示这支商队从没出过城", "首领空宅里翻出烧剩的地契"], 1),
    ("假药郎中", "simple", "集市", "一个游方郎中的摊子前总围着病人",
     "玩家看病/买药/留意郎中",
     "他卖的是掺了滑石粉的假药",
     ["郎中正吹嘘药到病除", "听说有人吃了他的药反而更重了"], 0),
    ("孩童失踪", "complex", "集市", "几户人家在墙上贴寻子的告示",
     "玩家留意告示/失踪的孩童/拐子",
     "丐帮分舵在暗中拐卖孩童",
     ["又一张新的寻子告示", "入夜后巷子里有孩童的哭声", "城外破庙前有可疑的车马", "拐卖孩童的窝点"], 0),
    ("赌局老千", "simple", "酒馆", "酒馆后间的骰局,赢家总是那几张熟脸",
     "玩家赌钱/留意赌局/查老千",
     "庄家用的是灌了铅的骰子",
     ["后间骰局正喧闹", "一个外乡人输红了眼"], 0),
    ("逃兵藏身", "medium", "酒馆", "墙角一个独自闷酒的汉子总低着头",
     "玩家留意可疑的人/打听生面孔/查通缉",
     "他是通缉的逃兵、身负一桩军中秘密",
     ["那汉子警惕地盯着门口", "他袖口滑出半截磨花的军牌"], 1),
    ("走私夜货", "medium", "码头", "码头夜里有不报关的货船靠岸",
     "玩家夜里在码头/留意货船/打听私盐",
     "本地盐枭在走私私盐",
     ["码头有夜行的灯笼", "苫布底下码着一包包的盐"], 0),
    ("芦苇浮尸", "complex", "码头", "水边的苇草丛里似乎缠着什么东西",
     "玩家细看水边/留意浮尸/查命案",
     "死者是揭发盐枭的差役、被灭口抛尸",
     ["水边漂着一片脏污的衣布", "苇草缠住一具发胀的浮尸", "死者怀里塞着半张账册", "灭口的盐枭"], 0),
    ("城隍灵验", "simple", "城隍庙", "城隍庙近来香火极旺,求签的排长队",
     "玩家进庙/上香/打听灵验",
     "并无神迹,是老庙祝在暗中接济孤儿",
     ["庙前求签的人排着长队", "庙祝深夜独自给偏殿添灯油"], 0),
]


def _seed_lore(store):
    for lid, cx, l3, desc, trig, secret, stages, adv in _LINES:
        create_lore_line(store, {
            "id": lid, "complexity": cx, "about": desc, "secret": secret,
            "anchor": "青石镇", "l3_anchor": l3, "description": desc, "trigger": trig,
            "stages": [{"hint": h} for h in stages], "threshold": 50,
        }, day=1, scene="s1", turn=0)
        for i in range(adv + 1):  # pre-brew: drop clues up to stage `adv`
            store.append(kernel_event("lore_advanced", day=1, scene="s1",
                                      summary=f"{lid}→{i}",
                                      deltas={"id": lid, "stage_idx": i,
                                              "hint": stages[i]}, turn=0))


def _build(mode_dir):
    eng = build_engine(mode_dir, provider=PROVIDER)
    eng.campaign_seed = SEED
    for ev in _seed_events():
        eng.store.append(ev)
    _seed_lore(eng.store)
    eng.world = project(eng.registry, eng.store.iter_events())
    return eng


ACTIONS = [
    "我在集市的摊位之间穿行,留心镇上近来的怪事。",
    "我拉住一个老摊主,专打听那支没了音信的商队。",
    "我踱进酒馆,要一壶酒,竖着耳朵听邻桌的闲谈。",
    "我盯上墙角那个独自闷酒、一见门开就缩脖子的汉子。",
    "入夜,我沿着码头慢慢走,看看有什么不对劲。",
    "我蹲到水边,拨开苇草细看那团缠着的东西。",
]


def _run(mode):
    eng = _build(Path(tempfile.mkdtemp(prefix=f"loreab_{mode}_")))
    strat = AuthorStrategy()
    prev_scene = None
    print(f"\n############### MODE {mode} ###############", flush=True)
    for i, act in enumerate(ACTIONS, 1):
        scene = _build_scene(eng)
        scene["disclosure_mode"] = mode
        frag = (index_fragment if mode == "A" else station_push_fragment)(
            eng.registry, eng.world, scene)
        TOOL_LOG.clear()
        try:
            res = run_turn(eng.registry, eng.store, eng.world, scene, act,
                           strategy=strat, provider=PROVIDER, embedder=eng.embedder,
                           max_repairs=4, required_sections=REQUIRED_SECTIONS,
                           cascade_provider=eng.cascade_provider,
                           catchup_provider=eng.cascade_provider, prev_scene=prev_scene)
        except Exception as exc:
            print(f"[{mode}-T{i}] ERROR {type(exc).__name__}: {exc}", flush=True)
            break
        eng.world = res.world
        prev_scene = scene
        loc = scene.get("location")
        print(f"\n[{mode}-T{i}] @{loc} repairs={res.repair_attempts} dropped={res.dropped_sections}", flush=True)
        print(f"  输入: {act}", flush=True)
        print(f"  [上下文里的暗线{'索引' if mode=='A' else '直推'}]\n    "
              + (frag or "(无)").replace("\n", "\n    "), flush=True)
        if mode == "A":
            print(f"  [模型主动 fetch 的线]: {TOOL_LOG or '(没调用工具)'}", flush=True)
        print(f"  « {(res.narration or '')[:240]}", flush=True)


PROVIDER = make_provider("zhipu", model=os.environ["GLM_MODEL"],
                         base_url=os.environ["GLM_BASE_URL"],
                         max_tokens=int(os.environ.get("GLM_MAX_TOKENS", "32768")))

print(f"== LORE A/B COMPARE narrator={os.environ['GLM_MODEL']} ==", flush=True)
print(f"world: 青石镇 ⊃ 集市(3线)/酒馆(2线)/码头(2线)/城隍庙(1线); 8 event-lines, 同种子同世界", flush=True)
_run("B")
_run("A")
print("\n=== DONE ===", flush=True)
