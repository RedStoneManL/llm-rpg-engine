"""Publish player-facing prose against the frozen result of the current action."""
import json

from kernel.action_effects import turn_end
from kernel.clock import band_name
from llm.structured import complete_structured


_REVIEW = """你负责核对本回合会展示给玩家的正文。结果已冻结，你不能修改世界。
逐项检查正文声称的移动、物品交接、付款、任务完成、时间变化、角色获知消息和亲历来源。
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
    for attempt in range(2):
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
