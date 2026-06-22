"""Focused re-run after the band-semantics prompt fix. Targets the failure mode:
fine-grained actions (brief talk, split-second strike) should be advance:false;
coarse ones (half-day travel, overnight) should advance. 4 turns, glm-5.1."""
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
camp = Path(tempfile.mkdtemp(prefix="clocksmoke2_"))
engine = build_engine(camp, provider=provider)
new_game(engine)
strategy = AuthorStrategy()
print(f"== CLOCK SMOKE 2 (post-fix) narrator={os.environ['GLM_MODEL']} ==", flush=True)

# (input, what we EXPECT the model to do re: time)
plan = [
    ("我在镇口井台边停下，向挑水的老汉打听去苍狼岭的路，几句话便问明了。", "brief talk -> advance:false (same period)"),
    ("我即刻出镇，沿官道朝苍狼岭疾行，从清晨一直走到日头西沉。", "half-day travel -> advance bands (晨->下午/夜)"),
    ("路边窜出一条野狗扑咬，我拔刀一个箭步，几个呼吸便结果了它。", "split-second strike -> advance:false (same period)"),
    ("我在背风的岩凹里和衣而卧，一觉睡到东方泛白。", "overnight -> next day 晨"),
]

prev_scene = None
for i, (inp, expect) in enumerate(plan, 1):
    scene = _build_scene(engine)
    try:
        res = run_turn(
            engine.registry, engine.store, engine.world, scene, inp,
            strategy=strategy, provider=provider, embedder=engine.embedder,
            max_repairs=6, required_sections=REQUIRED_SECTIONS,
            cascade_provider=engine.cascade_provider,
            catchup_provider=engine.cascade_provider, prev_scene=prev_scene,
        )
    except Exception as exc:
        print(f"[T{i}] ERROR: {type(exc).__name__}: {exc}", flush=True); break
    engine.world = res.world
    prev_scene = scene
    clock = (res.commit.sections or {}).get("clock")
    m = engine.world["meta"]
    print(f"\n[T{i}] EXPECT: {expect}", flush=True)
    print(f"     input: {inp}", flush=True)
    print(f"     clock: {clock}", flush=True)
    print(f"     -> day={m.get('day')} band={band_name(m.get('band') or 0)}  repairs={res.repair_attempts} dropped={res.dropped_sections}", flush=True)
