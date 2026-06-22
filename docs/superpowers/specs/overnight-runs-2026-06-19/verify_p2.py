"""P2 live verification on glm-5.1 (multi-turn): a quest develops across scenes.
Checks: narrator declares `storylines` → ledger populates; recap records narration
and TIERS (old scenes summarized once aged out); both force-pushed into context."""
import os, tempfile, json
from pathlib import Path

from llm.provider import make_provider
from app.engine import build_engine, new_game
from kernel.projection import project
from context.assembler import assemble_context
from loop.strategy import AuthorStrategy
from loop.turn import run_turn, REQUIRED_SECTIONS

provider = make_provider("zhipu", model=os.environ["GLM_MODEL"],
                         base_url=os.environ["GLM_BASE_URL"],
                         max_tokens=int(os.environ.get("GLM_MAX_TOKENS", "32768")))
camp = Path(tempfile.mkdtemp(prefix="p2_"))
engine = build_engine(camp, provider=provider)
new_game(engine)
strat = AuthorStrategy()
print(f"== narrator={os.environ['GLM_MODEL']} cheap={getattr(engine.cascade_provider,'model',None)} ==", flush=True)

# 5 distinct scenes (so recap can tier) developing ONE clear quest storyline.
turns = [
    ("sc01", "镇长把我叫去，委托我找回被山匪劫走的镇子圣物——一尊镇瘟疫的玉像。我郑重接下了这桩委托。"),
    ("sc02", "我沿着山匪逃窜的方向追进密林，寻找他们留下的踪迹。"),
    ("sc03", "我在山脚黑市花钱打听到山匪老巢的位置，以及他们头目的来历。"),
    ("sc04", "我摸到山寨外围，观察守卫换岗的规律，寻找潜入的破绽。"),
    ("sc05", "我趁夜潜入山寨，在头目卧房里翻找玉像的下落。"),
]

for i, (sid, line) in enumerate(turns, 1):
    scene = {"protagonist": "protagonist", "present": [], "day": i, "id": sid, "location": sid}
    r = run_turn(engine.registry, engine.store, engine.world, scene, line,
                 strategy=strat, provider=engine.provider, embedder=engine.embedder,
                 cascade_provider=engine.cascade_provider,
                 max_repairs=6, required_sections=REQUIRED_SECTIONS)
    engine.world = r.world
    sl = r.commit.sections.get("storylines")
    threads = engine.world["systems"].get("story", {}).get("threads", {})
    narr = engine.world["systems"].get("narrative", {})
    n_scenes = len(narr.get("scenes", []))
    n_summ = sum(1 for s in narr.get("scenes", []) if s.get("summary"))
    print(f"\n[T{i} {sid}] repairs={r.repair_attempts} storylines_decl={json.dumps(sl, ensure_ascii=False) if sl else None}", flush=True)
    print(f"[T{i}] ledger={[(t,d.get('status'),d.get('summary','')[:24]) for t,d in threads.items()]}", flush=True)
    print(f"[T{i}] recap: scenes_recorded={n_scenes} summarized={n_summ} super_summary={'Y' if narr.get('super_summary') else '-'}", flush=True)

# Final: confirm force-push into assembled context
scene = {"protagonist": "protagonist", "present": [], "day": 6, "id": "sc06", "location": "sc06"}
ctx = assemble_context(engine.registry, engine.world, scene, query="玉像")
has_recap = "往昔概要" in ctx or "概要" in ctx
threads = engine.world["systems"].get("story", {}).get("threads", {})
has_ledger = any((d.get("summary","")[:10] in ctx) for d in threads.values()) if threads else False
print(f"\n== assembled context (len={len(ctx)}): recap_block={has_recap} storyline_ledger_in_ctx={has_ledger} ==", flush=True)
# show the continuity-relevant lines
for ln in ctx.splitlines():
    if any(k in ln for k in ("概要", "故事线", "明账", "storyline", "玉像", "委托")):
        print("  CTX|", ln[:110], flush=True)

print(f"\n=== VERDICT: storyline_ledger={len(threads)} threads; recap scenes recorded; "
      f"force-push recap={has_recap} ledger={has_ledger} ===", flush=True)
