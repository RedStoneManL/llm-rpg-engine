"""Real-LLM clock smoke: does glm-5.1 produce a well-formed `clock` delta every
turn, and does in-game time advance sensibly? Offline FakeLLM can't answer this.
Drives 5 turns with inputs implying different elapses, prints each turn's clock
declaration, repairs/drops, and the resulting (day, band)."""
import os, tempfile
from pathlib import Path

from llm.provider import make_provider
from app.engine import build_engine, new_game
from app.play import _build_scene
from loop.turn import run_turn, REQUIRED_SECTIONS
from loop.strategy import AuthorStrategy
from kernel.clock import band_name

provider = make_provider("zhipu", model=os.environ["GLM_MODEL"],
                         base_url=os.environ["GLM_BASE_URL"],
                         max_tokens=int(os.environ.get("GLM_MAX_TOKENS", "32768")))
camp = Path(tempfile.mkdtemp(prefix="clocksmoke_"))
engine = build_engine(camp, provider=provider)
new_game(engine)
strategy = AuthorStrategy()
print(f"== CLOCK SMOKE narrator={os.environ['GLM_MODEL']} ==", flush=True)

inputs = [
    "我接下镇长的委托，告别青石镇，即刻动身，徒步赶往三日脚程外的苍狼岭。",
    "我在岭下的乱石坡蹲守，从晌午一直耗到天色擦黑。",
    "我借着夜色，悄悄摸近半山的匪寨。",
    "我潜伏在柴垛后按兵不动，一直熬到次日拂晓才寻得动手的空当。",
    "我猛地窜出，夺过供台上的玉像，转身夺门而逃。",
]

prev_scene = None
dropped_clock_turns = []
for i, inp in enumerate(inputs, 1):
    scene = _build_scene(engine)
    try:
        res = run_turn(
            engine.registry, engine.store, engine.world, scene, inp,
            strategy=strategy, provider=provider, embedder=engine.embedder,
            max_repairs=6, required_sections=REQUIRED_SECTIONS,
            cascade_provider=engine.cascade_provider,
            catchup_provider=engine.cascade_provider,
            prev_scene=prev_scene,
        )
    except Exception as exc:
        print(f"[T{i}] ERROR: {type(exc).__name__}: {exc}", flush=True)
        break
    engine.world = res.world
    prev_scene = scene
    clock = (res.commit.sections or {}).get("clock")
    meta = engine.world["meta"]
    d, b = meta.get("day"), meta.get("band") or 0
    if "clock" in (res.dropped_sections or []):
        dropped_clock_turns.append(i)
    print(f"\n[T{i}] repairs={res.repair_attempts} dropped={res.dropped_sections}", flush=True)
    print(f"    input: {inp}", flush=True)
    print(f"    clock: {clock}", flush=True)
    print(f"    -> day={d} band={band_name(b)}", flush=True)
    print(f"    « {(res.narration or '')[:140]}", flush=True)

print("\n===== SUMMARY =====", flush=True)
fm = engine.world["meta"]
print(f"final clock: day={fm.get('day')} band={band_name(fm.get('band') or 0)}", flush=True)
print(f"turns where clock was DROPPED (model failed to produce valid clock): {dropped_clock_turns or 'none'}", flush=True)
ev = list(engine.store.iter_events())
ca = [e for e in ev if e["type"] == "clock_advanced"]
print(f"clock_advanced events: {len(ca)} / {len(inputs)} turns", flush=True)
for e in ca:
    dl = e.get("deltas", {})
    print(f"   day={e['day']} advance={dl.get('advance')} +{dl.get('days')}d{dl.get('bands')}b  reason={dl.get('reason','')[:40]}", flush=True)
