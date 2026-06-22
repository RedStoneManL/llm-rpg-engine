import json
from pathlib import Path

from engine.log import get_logger

log = get_logger("projection")


def empty_projections():
    return {
        "state": {"day": None, "scene": None, "location": None, "stats": {}},
        "characters": {}, "threads": {}, "promises": [],
        "villains": {}, "timeline": [],
        "pacing": {"scenes_since_event": 0, "last_event_scene": None},
    }


def _new_char(name):
    return {"name": name, "profile": {}, "trust": None, "evolution": []}


def apply(proj, ev):
    t = ev["type"]
    proj["state"]["day"] = ev["day"]
    proj["state"]["scene"] = ev["scene"]
    proj["timeline"].append({"day": ev["day"], "scene": ev["scene"], "summary": ev["summary"]})
    d = ev.get("deltas", {})

    if t == "relationship_change":
        for actor in ev["actors"]:
            c = proj["characters"].setdefault(actor, _new_char(actor))
            key = f"{actor}.trust"
            if key in d:
                # delta is "from→to"; keep prior trust if "to" is empty (malformed)
                c["trust"] = str(d[key]).split("→")[-1].strip() or c["trust"]
            c["evolution"].append({"scene": ev["scene"], "change": ev["summary"]})

    elif t in ("character_reveal", "character_development"):
        for actor in ev["actors"]:
            c = proj["characters"].setdefault(actor, _new_char(actor))
            for k, v in d.items():
                if k.startswith(f"{actor}."):
                    c["profile"][k.split(".", 1)[1]] = v
            c["evolution"].append({"scene": ev["scene"], "change": ev["summary"]})

    elif t == "thread_open":
        tid = (ev.get("thread_refs") or [ev["id"]])[0]
        proj["threads"][tid] = {
            "id": tid, "name": d.get("name", ev["summary"]),
            "type": d.get("type"), "speed": d.get("speed"),
            "status": d.get("status", "活跃"),
            "endpoint": d.get("endpoint"), "beats": list(d.get("beats", [])),
            "reveal_conditions": list(d.get("reveal_conditions", [])),
            "dormant": bool(d.get("dormant")), "trigger": d.get("trigger"),
            "progress": d.get("progress", 0), "clues": list(d.get("clues", [])),
            "last_advanced_scene": ev["scene"],
        }

    elif t == "thread_advance":
        for tid in (ev.get("thread_refs") or []):
            th = proj["threads"].get(tid)
            if th:
                th["progress"] = d.get("progress", th["progress"])
                th["clues"] += list(d.get("clues+", []))
                th["dormant"] = False
                th["last_advanced_scene"] = ev["scene"]

    elif t == "thread_resolve":
        for tid in (ev.get("thread_refs") or []):
            if tid in proj["threads"]:
                proj["threads"][tid]["status"] = "已解锁"

    elif t == "promise_made":
        proj["promises"].append({"id": ev["id"], "text": ev["summary"],
                                 "made_scene": ev["scene"], "kept": False})

    elif t == "promise_kept":
        ref = d.get("promise_id")
        for p in proj["promises"]:
            if ref and p["id"] == ref:
                p["kept"] = True

    elif t == "villain_knowledge_gain":
        for actor in ev["actors"]:
            v = proj["villains"].setdefault(actor, {"knows": []})
            v["knows"].append({"fact": ev["summary"], "source": d.get("source"),
                               "channel": d.get("channel"), "delay": d.get("delay")})

    elif t in ("level_change", "item_change", "location_change", "combat_result"):
        if "location" in d:
            proj["state"]["location"] = d["location"]
        for k, v in d.items():
            if k != "location":
                proj["state"]["stats"][k] = v

    elif t in ("director_fired", "oracle_roll"):
        proj["pacing"]["last_event_scene"] = ev["scene"]
        proj["pacing"]["scenes_since_event"] = 0

    return proj


def project(events):
    proj = empty_projections()
    for ev in events:
        if ev.get("retracted"):
            continue
        apply(proj, ev)
    log.debug("project folded chars=%d threads=%d", len(proj["characters"]), len(proj["threads"]))
    return proj


def write_projections(proj, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("state", "characters", "threads", "promises", "villains", "pacing"):
        (out_dir / f"{name}.json").write_text(
            json.dumps(proj[name], ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Timeline", ""]
    for t in proj["timeline"]:
        lines.append(f"- Day{t['day']} [{t['scene']}] {t['summary']}")
    (out_dir / "timeline.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
