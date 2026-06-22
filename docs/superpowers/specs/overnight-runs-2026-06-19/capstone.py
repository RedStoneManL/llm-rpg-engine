"""Capstone: a full 7-beat adventure through the REAL play_loop on glm-5.1 —
A(knowledge/viewpoint) + B(director) + C(cascade) + D(catch-up) + P1(world-段) +
P2(recap/storylines) all live, with the real wiring (prev_scene, cascade_provider).
The most realistic 看效果, and a window into real-play behavior (static scene etc.)."""
import os, tempfile, json
from pathlib import Path

from llm.provider import make_provider
from app.engine import build_engine, new_game
from app.play import play_loop

provider = make_provider("zhipu", model=os.environ["GLM_MODEL"],
                         base_url=os.environ["GLM_BASE_URL"],
                         max_tokens=int(os.environ.get("GLM_MAX_TOKENS", "32768")))
camp = Path(tempfile.mkdtemp(prefix="capstone_"))
engine = build_engine(camp, provider=provider)
new_game(engine)
print(f"== CAPSTONE: narrator={os.environ['GLM_MODEL']} cheap={getattr(engine.cascade_provider,'model',None)} ==", flush=True)

inputs = [
    "我在青石镇接下镇长的委托：山匪劫走了镇瘟玉像，我要去苍狼岭把它夺回来。",
    "我循着山匪的踪迹追进苍狼岭的密林。",
    "我在山脚的黑市花钱打听玉像的去向和山匪头目的底细。",
    "我摸黑潜入山寨，在头目的库房附近寻找玉像。",
    "库房里，山匪竟用火把点燃了堆积的桐油想毁掉证据——烈焰轰然炸开，借着山风顷刻吞没整座山寨，向四面的林子与崖寨蔓延！",
    "我冲进火海抢出玉像，在坍塌的梁木间夺路而出。",
    "我带着玉像连夜赶回青石镇，把它交还给镇长，了结这桩委托。",
    "/quit",
]
out_lines = []
transcript = camp / "transcript.jsonl"
play_loop(engine, inputs, out=lambda s: out_lines.append(s),
          transcript_path=transcript, max_repairs=6)

# Per-turn from transcript
print("\n===== PER-TURN =====", flush=True)
recs = [json.loads(l) for l in transcript.read_text(encoding="utf-8").splitlines() if l.strip()]
for rec in recs:
    if rec.get("error"):
        print(f"[T{rec.get('turn')}] ERROR: {rec['error']}", flush=True); continue
    secs = list((rec.get("sections") or {}).keys())
    print(f"\n[T{rec.get('turn')}] repairs={rec.get('repair_attempts')} dropped={rec.get('dropped')} sections={secs}", flush=True)
    print(f"   {(rec.get('narration') or '')[:150]}...", flush=True)
    if "world" in secs:
        print(f"   WORLD: {json.dumps(rec['sections']['world'], ensure_ascii=False)}", flush=True)
    if "storylines" in secs:
        print(f"   STORY: {json.dumps(rec['sections']['storylines'], ensure_ascii=False)}", flush=True)

# Final state
g = engine.world["systems"]["ontology"]
story = engine.world["systems"].get("story", {}).get("threads", {})
narr = engine.world["systems"].get("narrative", {})
ev = list(engine.store.iter_events())
fired = [e for e in ev if e["type"] == "director_fired"]
casc = [e for e in ev if e["type"] in ("place_evolved", "world_change", "populace_shifted")]
knows = [f for f in g.facts if f.predicate.startswith("knows:") and f.is_current()]
print("\n===== FINAL STATE =====", flush=True)
print("storyline ledger:", flush=True)
for t, d in story.items():
    print(f"   [{d.get('status')}] {t}: {d.get('summary','')[:48]}", flush=True)
print(f"recap: scenes={len(narr.get('scenes',[]))} summarized={sum(1 for s in narr.get('scenes',[]) if s.get('summary'))} super_summary={'Y' if narr.get('super_summary') else '-'}", flush=True)
print(f"director_fired={len(fired)}  cascade_events={len(casc)}  knows_facts={len(knows)}  entities={len(g.entities)}", flush=True)
print(f"\n=== CAPSTONE DONE: {len(recs)} turns, {len(ev)} total events ===", flush=True)
