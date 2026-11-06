"""A lead is a place to look. Only an observed finding can support a conclusion."""
from __future__ import annotations

from kernel.contextsystem import ValidationError


RESULTS = {"miss": "查无所获", "clue": "有线索，待核实", "confirmed": "有可核验的证据"}
MAX_LEADS = 8


def valid_lead(lead):
    return (isinstance(lead, dict) and set(lead) == {"place", "feature"}
            and all(isinstance(value, str) and 0 < len(value) <= 160 and value == value.strip()
                    for value in lead.values()))


def declared_leads(objective):
    if objective.get("kind") != "investigate":
        return []
    return [{"place": objective["place"], "feature": objective["feature"]}, *objective.get("alternatives", [])]


def objective_errors(objective, world):
    if (set(objective) - {"kind", "place", "feature", "alternatives"} or
            not valid_lead({key: objective.get(key) for key in ("place", "feature")}) or
            not isinstance(objective.get("alternatives", []), list)):
        return ["调查须声明 place 和 feature，alternatives 是可选的其他查证地点与对象列表"]
    graph = world.get("systems", {}).get("ontology")
    leads = declared_leads(objective)
    if (len(leads) > MAX_LEADS or not all(valid_lead(lead) for lead in leads)
            or len({(lead["place"], lead["feature"]) for lead in leads}) != len(leads)):
        return [f"调查最多声明 {MAX_LEADS} 条互不重复的地点及对象"]
    if not graph or any(not (place := graph.get_entity(lead["place"])) or place.etype != "Place" for lead in leads):
        return ["调查线路必须指向已有地点"]
    return []


def findings(world, line, actor):
    opened = line.get("surfaced_turn")
    if not actor or type(opened) is not int:
        return
    for row in world["systems"].get("cascade", {}).get("changes", []):
        tag = row.get("investigation")
        if (isinstance(tag, dict) and tag.get("quest") == line["id"] and tag.get("result") in RESULTS
                and row.get("observer") == actor and row.get("source") == "author_world"
                and row.get("public_observation") is True and row.get("visibility") == "public"
                and (row.get("turn") or 0) > opened):
            yield row


def case_leads(world, line, actor):
    """Ordered lead identities with their most recent result; every attempt stays in the ledger."""
    known = {(lead["place"], lead["feature"]): None for lead in declared_leads(line.get("objective") or {})}
    for row in findings(world, line, actor):
        known[row["place"], row["feature"]] = row
        follow_up = row["investigation"].get("follow_up")
        if valid_lead(follow_up):
            known.setdefault((follow_up["place"], follow_up["feature"]), None)
    return known


def finding_error(world, actor, place, feature, tag, available_places):
    if (not isinstance(tag, dict) or set(tag) - {"quest", "result", "follow_up"}
            or not isinstance(tag.get("quest"), str) or not isinstance(tag.get("result"), str)
            or tag["result"] not in RESULTS or not valid_lead({"place": place, "feature": feature})):
        return "调查观察须给出 quest、result（miss/clue/confirmed）、地点及具名 feature"
    line = world["systems"].get("lore", {}).get("lines", {}).get(tag["quest"])
    if not line or line.get("state") != "明" or (line.get("objective") or {}).get("kind") == "deliver":
        return "只能给已接取且尚未结案的调查记录发现"
    known = case_leads(world, line, actor)
    key = place, feature
    if known and key not in known:
        return "调查对象须来自原委托或此前实际观察给出的后续去处"
    if "follow_up" in tag:
        lead = tag["follow_up"]
        if (tag["result"] == "confirmed" or not valid_lead(lead) or lead["place"] not in available_places):
            return "后续查证处须是当前位置或当前可达的公开相邻地点，且尚未结案"
        if (lead["place"], lead["feature"]) not in (known.keys() | {key}) and len(known.keys() | {key}) >= MAX_LEADS:
            return f"同一调查最多保留 {MAX_LEADS} 条线路"
    return None


def check_finding(world, actor, place, feature, tag, available_places, field):
    error = finding_error(world, actor, place, feature, tag, available_places)
    return [ValidationError("world", field, "player_world_observation_boundary", error)] if error else []
