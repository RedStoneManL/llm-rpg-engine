# engine/director.py
from engine.log import get_logger

log = get_logger("director")

BIG_THRESHOLD = 75            # 量级:>=75 大事件
CRIT_BASE = 95                # 暴击基线(张力抬高它,高潮更难但仍可)
DORMANT_RATIO = 0.5           # 类型:埋线 vs 前台 的基础比例
TENSION_GATE = 0.6           # 张力>=此 → 非暴击前台压成休眠

def pacing_probability(scenes_since_event):
    """30%→60% band with a cooldown dip right after an event."""
    if scenes_since_event <= 0:
        return 0.15
    return min(0.30 + 0.06 * (scenes_since_event - 1), 0.60)

def compute_pacing(events):
    """Derive pacing from the event stream: scene ordinal, scenes since last
    director_fired, and a rough tension level."""
    scenes, last_fire_idx, tension = [], -1, 0.0
    for ev in events:
        sc = ev.get("scene")
        if not scenes or scenes[-1] != sc:
            scenes.append(sc)
        t = ev["type"]
        if t == "director_fired":
            last_fire_idx = len(scenes) - 1
        if t in ("combat_result", "character_reveal", "thread_resolve", "villain_knowledge_gain"):
            tension = min(1.0, tension + 0.3)
        elif t in ("action", "dialogue_beat"):
            tension = max(0.0, tension - 0.1)
    ordinal = len(scenes)
    since = (ordinal - 1 - last_fire_idx) if last_fire_idx >= 0 else ordinal
    pacing = {"scene_ordinal": ordinal, "scenes_since_event": max(0, since),
              "tension": round(tension, 2), "current_scene": scenes[-1] if scenes else None}
    log.debug("compute_pacing %s", pacing)
    return pacing

SPEED_CADENCE = {"快": 3, "中": 6, "慢": 12}
MIN_ACTIVE_THREADS = 2

def _scene_ordinals(events):
    scenes = []
    for ev in events:
        sc = ev.get("scene")
        if not scenes or scenes[-1] != sc:
            scenes.append(sc)
    return {sc: i + 1 for i, sc in enumerate(scenes)}, len(scenes)

def thread_due_scores(events, threads, oracle):
    """Per active non-dormant thread: due = scenes_since_advance / cadence(speed) * jitter."""
    ords, total = _scene_ordinals(events)
    scores = []
    for tid, th in threads.items():
        if th.get("status") == "已解锁" or th.get("dormant"):
            continue
        last_ord = ords.get(th.get("last_advanced_scene"), 0)
        since = total - last_ord
        cadence = SPEED_CADENCE.get(th.get("speed"), 6)
        jitter = 0.7 + 0.6 * oracle.random()
        scores.append((tid, round((since / cadence) * jitter, 3)))
    scores.sort(key=lambda x: -x[1])
    log.debug("thread_due_scores %s", scores)
    return scores

def pick_thread_to_advance(events, threads, oracle, *, threshold=1.0):
    scores = thread_due_scores(events, threads, oracle)
    if scores and scores[0][1] >= threshold:
        return scores[0][0]
    return None

def director_check(scenes_since_event, tension, oracle, *, tables):
    """Pure: hidden d100 vs pacing prob → two-axis outcome (type × magnitude) + drawn seed."""
    prob = pacing_probability(scenes_since_event)
    roll = oracle.d100() / 100.0
    if roll >= prob:
        log.debug("director quiet (roll %.2f >= prob %.2f)", roll, prob)
        return {"triggered": False, "prob": prob, "roll": roll}
    # magnitude
    m = oracle.d100()
    crit_threshold = min(99, CRIT_BASE + int(tension * 4))   # 高张力 → 暴击更难
    if m >= crit_threshold:
        magnitude = "crit"
    elif m >= BIG_THRESHOLD:
        magnitude = "big"
    else:
        magnitude = "small"
    # type
    is_dormant = oracle.chance(DORMANT_RATIO)
    if not is_dormant and tension >= TENSION_GATE and magnitude != "crit":
        is_dormant = True                                    # 重要时刻只埋不扰(暴击除外)
    typ = "dormant_thread" if is_dormant else "front_stage"
    valence = ("boon" if oracle.chance(0.5) else "disaster") if magnitude == "crit" else None
    seed = {"event_type": oracle.draw(tables["event_types"]),
            "twist": oracle.draw(tables["twists"])}
    out = {"triggered": True, "type": typ, "magnitude": magnitude, "valence": valence,
           "seed": seed, "prob": prob, "roll": roll}
    log.debug("director FIRED type=%s mag=%s valence=%s", typ, magnitude, valence)
    return out
