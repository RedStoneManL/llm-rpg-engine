"""Player-facing investigation notebook built from active, actor-owned sources."""
from context.access import pov_world
from kernel.investigations import RESULTS, case_leads
from systems.place import navigate


def investigation_notebook(world, scene, line):
    actor = scene.get("protagonist")
    if not actor or line.get("state") != "明":
        return []
    leads = case_leads(world, line, actor)
    if not leads:
        return []
    graph = pov_world(world, scene, pov=actor)["systems"]["ontology"]
    day = scene.get("day") or 1
    current = scene.get("location")
    attempts = line.get("attempts", {}).get(actor, [])
    entries = []
    for index, ((place, feature), finding) in enumerate(leads.items(), 1):
        entity = graph.get_entity(place)
        if not entity:
            continue
        label = entity.attrs.get("label") or entity.attrs.get("seed") or place
        row = {"number": index, "place": place, "label": str(label), "feature": feature, "status": "待查"}
        if finding:
            result = finding["investigation"]["result"]
            row.update(status=RESULTS[result], source_event_id=finding["event_id"], quote=finding["summary"],
                       day=finding["day"], turn=finding["turn"])
        elif [place, feature] in attempts:
            row["status"] = "本次未取得可核验记录"
        path = navigate(graph, current, place, day)["path"] if current else []
        if not path:
            row["availability"] = "当前没有已知道路"
        elif current == place:
            row["availability"] = "在场"
            if row["status"] != RESULTS["confirmed"]:
                row["action"] = f"查证 {line['id']} {index}"
        else:
            next_place = graph.get_entity(path[1])
            next_label = next_place.attrs.get("label") or next_place.attrs.get("seed") or next_place.id
            row["availability"] = f"下一步可去{next_label}"
            if row["status"] != RESULTS["confirmed"]:
                row["action"] = f"前往{next_label}。"
        entries.append(row)
    return entries


def selected_lead(world, scene, player_input):
    parts = player_input.strip().split() if isinstance(player_input, str) else []
    if not parts or parts[0] != "查证":
        return None
    if len(parts) != 3 or not parts[2].isascii() or not parts[2].isdigit() or len(parts[2]) > 2:
        # A natural-language sentence beginning with 查证 stays a normal action.
        return None
    line = world["systems"].get("lore", {}).get("lines", {}).get(parts[1])
    for row in investigation_notebook(world, scene, line) if line else []:
        if row["number"] == int(parts[2]) and row.get("availability") == "在场":
            return {"quest": line["id"], "place": row["place"], "feature": row["feature"]}
    raise ValueError("这条调查线路目前无法在此查证；请查看任务簿里的地点和线路。")


def alternate_action(world, scene, line, leads):
    if (not leads or any(lead["status"] == RESULTS["confirmed"] for lead in leads)
            or any(lead.get("action") and lead["status"] in {"待查", RESULTS["clue"]} for lead in leads)
            or not any(lead["status"] != "待查" for lead in leads)):
        return None
    from context.native_authoring import native_authoring_context
    people = native_authoring_context(world, scene).get("present_people", [])
    question = line.get("question") or line.get("summary")
    if people:
        person = people[0]
        name = person.get("observable", {}).get("name") or person["id"]
        return f"向{name}打听“{question}”还有哪些可以查证的地方。"
    from context.capabilities import scene_capabilities
    exits = scene_capabilities(world, scene).exits
    if exits:
        return f"前往{exits[0].label}，寻找有关“{question}”的公开线索。"
    return None
