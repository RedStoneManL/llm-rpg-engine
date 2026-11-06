"""Publish player-facing prose against the frozen result of the current action."""
import json

from kernel.action_effects import turn_end
from kernel.clock import band_name
from llm.structured import complete_structured


_REVIEW = """你负责核对本回合会展示给玩家的正文。结果已冻结，你不能修改世界。
逐项检查正文声称的移动、物品交接、付款、任务完成、时间变化、角色获知消息和亲历来源。
只有 belief/declared、public_notice 或 attributed_information 时，不足以让说话者宣称亲眼目击相应事件。
判断实际发生、拟议、条件、否定和转述的区别。环境描写、自然对白无需逐字对应事件。
现有地点/手中物品不等于本回合移动/交易。未列为成果的意图不能当作已经完成。
输出 JSON {"supported":true或false,"reason":"有矛盾时指出具体句子及与哪条结果冲突"}。"""


def _outcome(world, scene, sections):
    from context.capabilities import scene_capabilities
    capability = scene_capabilities(world, scene, actor=scene.get("protagonist"))
    labels = {place.id: place.label for place in capability.places}
    location = capability.location
    for move in sections.get("moves") or []:
        if move.get("who") == scene.get("protagonist"):
            location = move["to"]
    for place in sections.get("places") or []:
        labels.setdefault(place.get("id"), place.get("seed") or place.get("name"))
    day, band = turn_end(world, sections.get("clock"))
    return {
        "day": day, "band": band_name(band), "location": labels.get(location),
        "sections": sections, "resource_result": scene.get("_resolution_prompt"),
    }


def review_player_narration(strategy, commit, world, scene, player_input, provider):
    outcome = _outcome(world, scene, commit.sections)
    proposed = commit.narration
    buckets = world.get("systems", {}).get("narrative", {}).get("scenes", [])
    previous = next((bucket["raw"][-1] for bucket in reversed(buckets) if bucket.get("raw")), None)
    for attempt in range(2):
        if previous and proposed.strip() == previous.strip():
            if attempt:
                break
            proposed = strategy.finalize_narration(
                commit, provider=provider, repair="上次正文与这次逐字相同。根据现场给出一个具体可查验的方向；"
                "没有新发现时可以直说，不能改变已经冻结的结果。")
            continue
        material = json.dumps({"intent": player_input, "start_context": strategy._pending_user,
                               "outcome": outcome, "prose": proposed}, ensure_ascii=False)
        reply, errors = complete_structured(
            provider, system=_REVIEW, user=material, max_repairs=0, log_label="narration_review",
            validate=lambda obj: [] if type(obj.get("supported")) is bool and isinstance(obj.get("reason"), str)
            else ["请给出 supported 布尔值和 reason 字符串。"],
        )
        if errors:
            break
        if reply["supported"]:
            return proposed
        if attempt == 0:
            proposed = strategy.finalize_narration(commit, provider=provider, repair=reply["reason"] or "重写无依据的结果。")
    # A failed review cannot become an extra die roll, payment or movement.
    # This display is derived from the same accepted ending clock and location.
    where = f"，你在{outcome['location']}" if outcome["location"] else ""
    return f"第{outcome['day']}天{outcome['band']}{where}。"


def settle_investigation_claims(commit, world, provider):
    """Review proposed positive findings and conclusions against their literal source."""
    import copy
    from kernel.turncommit import TurnCommit

    lines = world.get("systems", {}).get("lore", {}).get("lines", {})
    claims = {}
    for index, row in enumerate(commit.sections.get("world") or []):
        finding = row.get("investigation") or {}
        if finding.get("result") == "confirmed":
            line = lines[finding["quest"]]
            claims[f"world/{index}"] = {"question": line.get("question") or line["summary"],
                                        "source": row["summary"], "conclusion": "该观察已回答调查问题"}
    for index, row in enumerate(commit.sections.get("quests") or []):
        if row.get("op") == "resolve" and row.get("outcome") != "abandoned" and row.get("evidence"):
            line = lines[row["id"]]
            if (line.get("objective") or {}).get("kind") != "deliver":
                claims[f"quests/{index}"] = {"question": line.get("question") or line["summary"],
                                             "source": row["evidence"]["quote"], "conclusion": row.get("summary")}
    if not claims:
        return commit

    def validate(obj):
        if obj.keys() == claims.keys() and all(type(value) is bool for value in obj.values()):
            return []
        return ["为每个给定编号输出一个布尔值。"]

    verdicts, errors = complete_structured(
        provider, system="根据 source 原文独立判断每项 conclusion 是否成立且回答了原调查 question。"
        "只有原文足以支持才为 true；线索、查空、传闻不能补全缺失事实。若调查本来就是核实不在场，确凿的否定证据也可以回答问题。"
        "只输出给定编号到布尔值的 JSON 对象。", user=json.dumps(claims, ensure_ascii=False),
        validate=validate, max_repairs=0, log_label="investigation_evidence",
    )
    sections = copy.deepcopy(commit.sections)
    rejected = {key for key in claims if errors or not verdicts[key]}
    for index, row in enumerate(sections.get("world") or []):
        if f"world/{index}" in rejected:
            row["investigation"]["result"] = "clue"
    if "quests" in sections:
        sections["quests"] = [row for index, row in enumerate(sections["quests"]) if f"quests/{index}" not in rejected]
    return TurnCommit(commit.narration, sections)
