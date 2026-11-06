"""Host-owned, explicitly requested risk checks for a solo campaign."""
from engine.oracle import Oracle, scene_seed
from kernel.clock import advance
from kernel.contextsystem import ContextSystem, ValidationError
from kernel.events import kernel_event


_APPROACHES = {"潜行", "交涉", "强攻"}
_THRESHOLDS = {"容易": 75, "普通": 55, "困难": 35}
USAGE = "检定 潜行/交涉/强攻 容易/普通/困难 行动描述"


def resolve_check(action, world, scene, turn):
    parts = action.strip().split(maxsplit=3) if isinstance(action, str) else []
    if not parts or parts[0] != "检定":
        return None
    if len(parts) != 4 or parts[1] not in _APPROACHES or parts[2] not in _THRESHOLDS:
        raise ValueError(f"检定格式：{USAGE}")
    _, approach, difficulty, _ = parts
    actor = scene["protagonist"]
    meta = world["meta"]
    seed = scene_seed(meta.get("campaign_seed") or "default", f"check:{actor}:{scene.get('location')}", turn)
    roll = Oracle(seed).d100()
    threshold = _THRESHOLDS[difficulty]
    if roll <= threshold:
        outcome, cost = "成功", 1
    elif roll <= threshold + 20:
        outcome, cost = "付出代价的成功", 2
    else:
        outcome, cost = "受挫", 2
    day, band = advance(meta.get("day") or 1, meta.get("band") or 0, 0, cost)
    delta = {"actor":actor, "approach":approach, "difficulty":difficulty,
             "roll":roll, "threshold":threshold, "outcome":outcome, "bands":cost,
             "end_clock":{"day":day, "band":band}}
    return kernel_event("action_checked", day=meta.get("day") or 1, turn=turn,
                        scene=scene.get("id") or scene["location"], actors=[actor],
                        summary=check_text(delta), deltas=delta)


def check_text(delta):
    return (f"{delta['approach']}（{delta['difficulty']}）：d100 掷出 {delta['roll']}，"
            f"成功线 {delta['threshold']}，{delta['outcome']}。耗时 {delta['bands']} 个时段。")


def check_errors(commit, check):
    if not check or check["outcome"] != "受挫":
        return []
    return [ValidationError(key, "", "failed_check_effect", "检定受挫，本次只结算经过的时间。")
            for key, rows in commit.sections.items() if key != "clock" and rows]


class ChallengeSystem(ContextSystem):
    name = "challenge"

    def event_types(self):
        return {"action_checked"}

    def empty_state(self):
        return {}

    def apply(self, world, event):
        delta = event["deltas"]
        world["systems"][self.name][delta["actor"]] = {**delta, "turn":event["turn"], "source":event["id"]}
