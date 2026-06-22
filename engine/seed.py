# engine/seed.py
from engine.oracle import load_table
from engine.log import get_logger

log = get_logger("seed")

def _draw_distinct(oracle, entries, k):
    """Weighted draw of up to k DISTINCT entries (sample without replacement).
    Avoids duplicate threads/roles/traits in one opening (variety / anti-convergence)."""
    pool = list(entries)
    out = []
    for _ in range(min(k, len(pool))):
        e = oracle.draw(pool)
        out.append(e)
        pool.remove(e)
    return out

def seed_campaign(genre, oracle):
    """Slot-machine campaign opening: world frame + 3-5 threads + opening NPCs + hooks.
    Deterministic given the oracle's seed. Returns a structured seed (the DM weaves it).
    Threads / roles / per-NPC traits are drawn DISTINCT for variety."""
    frame = oracle.draw(load_table("world_frames", genre))
    arche = load_table("thread_archetypes", genre)
    n_threads = oracle.randint(3, 5)
    threads = []
    for i, a in enumerate(_draw_distinct(oracle, arche, n_threads)):
        threads.append({
            "id": f"th_seed{i+1}", "archetype": a["name"], "type": a.get("type"),
            "speed": oracle.draw([{"weight": 2, "name": "快"}, {"weight": 3, "name": "中"},
                                  {"weight": 2, "name": "慢"}])["name"],
            "endpoint": a.get("endpoint_hint"), "hook": a.get("hook"),
        })
    roles = load_table("npc_roles", genre)
    traits = load_table("npc_traits")
    n_npc = oracle.randint(2, 4)
    npcs = []
    for r in _draw_distinct(oracle, roles, n_npc):
        npcs.append({"role": r["name"], "motivation": r.get("motivation"),
                     "secret": r.get("secret"),
                     "traits": [t["name"] for t in _draw_distinct(oracle, traits, 2)]})
    hooks = [h["name"] for h in _draw_distinct(oracle, arche, 2)]
    seed = {"genre": genre, "frame": frame, "threads": threads, "npcs": npcs,
            "protagonist_hooks": hooks}
    log.debug("seed_campaign genre=%s threads=%d npcs=%d", genre, len(threads), len(npcs))
    return seed
