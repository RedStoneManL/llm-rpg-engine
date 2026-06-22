"""P1 live verification on glm-5.1: does the NARRATOR adopt the `world` section
for a region-level event → cascade (cheap fleet) descends the affected areas?
Narrator = glm-5.1; cascade fleet = glm-4.7 (via GLM_CASCADE_MODEL)."""
import os, tempfile, json
from pathlib import Path

from llm.provider import make_provider
from app.engine import build_engine, new_game
from kernel.events import kernel_event
from kernel.projection import project
from loop.strategy import AuthorStrategy
from loop.turn import run_turn, REQUIRED_SECTIONS

provider = make_provider("zhipu", model=os.environ["GLM_MODEL"],
                         base_url=os.environ["GLM_BASE_URL"],
                         max_tokens=int(os.environ.get("GLM_MAX_TOKENS", "32768")))
camp = Path(tempfile.mkdtemp(prefix="p1_"))
engine = build_engine(camp, provider=provider)
new_game(engine)
print(f"== narrator={os.environ['GLM_MODEL']} cascade={getattr(engine.cascade_provider,'model',None)} ==", flush=True)

def pc(pid, level, kind, seed, parent=None):
    d = {"id": pid, "level": level, "kind": kind, "seed": seed, "tier": "tracked"}
    if parent:
        d["parent"] = parent
    return kernel_event("place_created", day=1, scene="city", summary=pid, deltas=d, turn=0)

# Containment hierarchy for cascade to descend:  city(L3) ⊃ market(L2) ⊃ shrine(L1); city ⊃ slums(L2)
for ev in [
    pc("city", 3, "settlement", "依山而建的繁华城邦"),
    pc("market", 2, "venue", "城心的喧闹集市", parent="city"),
    pc("shrine", 1, "venue", "集市角落的古老神龛", parent="market"),
    pc("slums", 2, "settlement", "城墙根下的贫民窟", parent="city"),
    kernel_event("entity_moved", day=1, scene="city", summary="主角入城",
                 deltas={"who": "protagonist", "to": "city"}, turn=0),
]:
    engine.store.append(ev)
engine.world = project(engine.registry, engine.store.iter_events())

scene = {"protagonist": "protagonist", "present": [], "day": 1, "id": "city", "location": "city"}
player_input = ("我抓起火把，狠狠掷向广场中央堆积如山的节庆油料。烈焰冲天而起，"
                "借着入夜的狂风顷刻吞没集市，朝全城的街区疯狂蔓延开去。")

r = run_turn(engine.registry, engine.store, engine.world, scene, player_input,
             strategy=AuthorStrategy(), provider=engine.provider,
             embedder=engine.embedder, max_repairs=6, required_sections=REQUIRED_SECTIONS)
engine.world = r.world

print(f"\n[turn] repairs={r.repair_attempts} dropped={r.dropped_sections} sections={list(r.commit.sections)}", flush=True)
print(f"[narration {len(r.narration)}字] {r.narration[:200]}...", flush=True)
world_sec = r.commit.sections.get("world")
print(f"\n== narrator `world` section: {json.dumps(world_sec, ensure_ascii=False)} ==", flush=True)

# cascade events appended this/next turn
cas = [e for e in engine.store.iter_events()
       if e["type"] in ("place_evolved", "populace_shifted", "world_change")]
print(f"\n== cascade events in store: {len(cas)} ==", flush=True)
for e in cas:
    d = e["deltas"]
    tag = "DEFERRED" if d.get("deferred") else ("WATERMARK" if d.get("deferred_consume_through") else "")
    print(f"  {e['type']:16} {d.get('place') or d.get('id')!r:10} state={d.get('state','')!r} "
          f"mood={d.get('mood','')!r} {tag}", flush=True)

g = engine.world["systems"]["ontology"]
print("\n== resulting place states ==", flush=True)
for pid in ("city", "market", "shrine", "slums"):
    st = g.value_at(pid, "state", day=1)
    if st:
        print(f"  {pid:8} {st!r}", flush=True)

evolved = {e["deltas"].get("id") for e in cas if e["type"] == "place_evolved"}
print(f"\n=== VERDICT: world_section_emitted={bool(world_sec)} cascade_evolved={sorted(evolved)} "
      f"(want narrator to declare areas + cascade descend into districts) ===", flush=True)
