"""loop.genesis.cast — gen_protagonist / gen_factions / gen_npcs."""
from __future__ import annotations

from engine.oracle import Oracle, scene_seed, load_table  # noqa: F401
from engine.log import get_logger
from kernel.events import kernel_event  # noqa: F401
from kernel.observability import get_tracer  # noqa: F401
from llm.structured import complete_structured  # noqa: F401

log = get_logger("loop.genesis")
from loop.genesis.common import _draw_distinct, _empty_str  # noqa: F401

# ---------------------------------------------------------------------------
# gen_protagonist — Task 4b: author the protagonist to fit the generated world
# ---------------------------------------------------------------------------

_SYSTEM_GEN_PROTAGONIST = (
    "你是 TRPG 主角背景生成器，只返回严格符合字段规范的 JSON，所有故事文本用中文。"
)


def _validate_protagonist(obj) -> list[str]:
    """Return human-readable problems naming missing/empty required protagonist fields."""
    errs = []
    for field in ("name", "origin", "goal", "objective"):
        if not isinstance(obj.get(field), str) or not obj[field].strip():
            errs.append(f'missing or empty string field "{field}"')
    return errs


def _roll_protagonist_seeds(oracle, flavor: str = "classic") -> dict:
    """Distinct-draw protagonist dimension seeds (I6-P1) to break gen_protagonist
    mode-collapse (every run was 守塔人遗孤+手臂旧疤+符文). Returns short archetypes
    {origin, hook, quirk} the LLM riffs on — NOT the literal output."""
    return {
        "origin": oracle.draw(load_table("protagonist_origins", flavor, base="classic"))["name"],
        "hook": oracle.draw(load_table("protagonist_hooks", flavor, base="classic"))["name"],
        "quirk": oracle.draw(load_table("protagonist_quirks", flavor, base="classic"))["name"],
    }


def _protagonist_seed_block(seeds: dict) -> str:
    """Render rolled protagonist seeds as a prompt direction (融进世界设定, 勿照抄)."""
    return (
        "【主角种子·创作方向(据此发挥，要融进上面的世界设定，勿照抄字面)】\n"
        f"  出身原型：{seeds.get('origin', '')}\n"
        f"  卷入此局的明面缘由：{seeds.get('hook', '')}\n"
        f"  一个区别于他人的具体特征：{seeds.get('quirk', '')}\n\n"
    )


def gen_protagonist(
    provider,
    oracle: Oracle,
    frame: dict,
    local_map: dict,
    *,
    provided=None,
    flavor: str = "classic",
) -> tuple[list, dict]:
    """Author a protagonist that fits the generated world frame.

    Engine context: world frame (tone/conflict/world_name) + local_map (first venue).
    LLM writes: name, origin (身世/background 1-3 sentences), goal (driving goal),
                objective (concrete starting quest — "what I'm doing right now").

    On LLM error or provider=None → deterministic stub values; NEVER raises.

    Returns:
        (events, authored)
        events = []   (no events emitted here; bootstrap_world emits them directly)
        authored = {"name": str, "origin": str, "goal": str, "objective": str}
    """
    # Use oracle to anchor the call into the attempt-seed scheme (no rolls needed)
    _ = oracle.random()   # consume one draw so seed participates in attempt space

    provided = provided or {}
    # Skip the LLM entirely when every authored field is supplied.
    if not any(_empty_str(provided.get(f)) for f in ("name", "origin", "goal", "objective")):
        return [], {f: (provided[f].strip() if isinstance(provided[f], str) else provided[f])
                    for f in ("name", "origin", "goal", "objective")}

    start_town = local_map.get("start_town", "town_0")
    venues = local_map.get("venues", [])
    venue_names = local_map.get("venue_names", {})

    # Resolve town name from l2 list
    town_name = start_town
    for entry in local_map.get("l2", []):
        if entry.get("id") == start_town:
            town_name = entry.get("name", start_town)
            break

    # Resolve first venue name (prefer name, fall back to id)
    first_venue_id = venues[0] if venues else None
    first_venue_name = (
        venue_names.get(first_venue_id, first_venue_id)
        if first_venue_id else "起始场所"
    )

    # All venue names for context (comma-joined)
    all_venue_names = "、".join(
        venue_names.get(vid, vid) for vid in venues
    ) if venues else "（无场所）"

    seeds = _roll_protagonist_seeds(oracle, flavor)
    user = (
        f"世界名称：{frame.get('world_name', '未名之地')}\n"
        f"世界基调：{frame.get('tone', '冒险')}\n"
        f"核心冲突：{frame.get('central_conflict', '未知冲突')}\n"
        f"起始小镇：{town_name}，起始场所：{first_venue_name}\n"
        f"镇内所有场所：{all_venue_names}\n\n"
        f"{_protagonist_seed_block(seeds)}"
        f"【基调把控】主角身世要贴合世界基调「{frame.get('tone', '冒险')}」。"
        f"若基调偏明亮/王道/热血/日常，就往平实、带朴素向往或小抱负的方向写，别硬凹惨剧——"
        f"避免灭门/冤狱/血仇/孤儿+旧伤+封印这类沉重悲情套路（除非基调确实阴郁）；"
        f"'普通人'与留白往往比惨烈更耐读、更有代入感。\n\n"
        f"请为这个世界创作一位主角，以纯 JSON 对象返回，"
        f"不含 Markdown 代码块、不含任何额外说明。\n"
        f"对象 MUST 含有 EXACTLY 下列四个字段（不多不少）：\n"
        f"  \"name\"      — 符合世界风格的主角姓名（中文字符串，非空）\n"
        f"  \"origin\"    — 主角的身世背景，1-3句话（中文字符串，非空）\n"
        f"  \"goal\"      — 驱动主角行动的核心目标（中文字符串，非空）\n"
        f"  \"objective\" — 主角当前具体的任务或行动目标，即「我现在正在做什么」（中文字符串，非空）；"
        f"用地点的名字（如「{first_venue_name}」「{town_name}」）指代地点，"
        f"绝不要在面向玩家的文本里出现 town_0 / venue_0 这类内部 id\n"
        f"示例（仅示意 JSON 结构与字段，切勿照搬其内容或桥段）："
        f"{{\"name\": \"<符合世界与上面种子的姓名>\","
        f" \"origin\": \"<由出身原型展开的 1-3 句身世，融入世界设定>\","
        f" \"goal\": \"<驱动主角行动的核心目标>\","
        f" \"objective\": \"<由卷入缘由展开、用地点名字指代的当前具体行动>\"}}"
    )

    obj, errors = complete_structured(
        provider,
        system=_SYSTEM_GEN_PROTAGONIST,
        user=user,
        validate=_validate_protagonist,
        max_repairs=2,
        log_label="gen_protagonist",
    )

    if errors or obj is None:
        if errors != ["no provider"]:
            log.warning("gen_protagonist: LLM step failed (%s); using stub protagonist",
                        "; ".join(errors) or "provider is None")
        authored = {
            "name": "无名旅者",
            "origin": "来历不明的旅人，只知道自己踏上了这条路。",
            "goal": "找到属于自己的答案",
            "objective": "在起始小镇打听线索，寻找下一步的方向",
        }
    else:
        authored = {
            "name": obj["name"].strip(),
            "origin": obj["origin"].strip(),
            "goal": obj["goal"].strip(),
            "objective": obj["objective"].strip(),
        }

    # Provided overrides (non-empty wins) — e.g. provided name kept, objective authored.
    for f in ("name", "origin", "goal", "objective"):
        if not _empty_str(provided.get(f)):
            authored[f] = provided[f].strip() if isinstance(provided[f], str) else provided[f]

    return [], authored


# ---------------------------------------------------------------------------
# gen_factions — Task 5: world factions (count = frame["n_factions"])
# ---------------------------------------------------------------------------

_SYSTEM_GEN_FACTIONS = (
    "你是 TRPG 世界势力生成器，只返回严格符合字段规范的 JSON，所有故事文本用中文。"
)


def _validate_factions(n: int):
    """Return a validator that checks the factions array has exactly n entries,
    each with non-empty, distinct name and motivation fields."""
    def _validate(obj) -> list[str]:
        errs = []
        factions = obj.get("factions")
        if not isinstance(factions, list):
            errs.append('field "factions" must be a JSON array')
            return errs
        if len(factions) != n:
            errs.append(f'field "factions" must have exactly {n} entries, got {len(factions)}')
        seen_names: set[str] = set()
        for i, f in enumerate(factions):
            if not isinstance(f, dict):
                errs.append(f'factions[{i}] must be a JSON object')
                continue
            name = f.get("name")
            if not isinstance(name, str) or not name.strip():
                errs.append(f'factions[{i}]: missing or empty string field "name"')
            else:
                lower = name.strip().lower()
                if lower in seen_names:
                    errs.append(f'factions[{i}]: "name" must be distinct across all factions')
                seen_names.add(lower)
            motivation = f.get("motivation")
            if not isinstance(motivation, str) or not motivation.strip():
                errs.append(f'factions[{i}]: missing or empty string field "motivation"')
        return errs
    return _validate


def gen_factions(
    provider,
    oracle: Oracle,
    frame: dict,
    regions_summary: dict,
    *,
    provided=None,
) -> tuple[list[dict], dict]:
    """Generate the world's factions (count = frame["n_factions"]).

    Engine decides: count (already in frame["n_factions"]).
    LLM writes: name and motivation strings only; must be distinct across factions.

    On LLM error or provider=None -> deterministic stub names like "势力{i+1}"; NEVER raises.

    Returns:
        (events, summary)
        summary = {"factions": [{"id": "faction_{i}", "name": str}, ...]}
        events = faction_created x n_factions
                 each deltas: {"op":"faction","id":"faction_{i}","tier":"mentioned",
                                "seed":<name>,"motivation":<motivation>}
    """
    provided = provided or []
    n = max(len(provided), frame["n_factions"])

    # ------------------------------------------------------------------
    # LLM step — strict per-index field-naming prompt
    # ------------------------------------------------------------------
    faction_lines = "\n".join(
        f"  factions[{i}]: 请给出该势力的 name（中文非空）和 motivation（中文非空，一句话）"
        for i in range(n)
    )
    user = (
        f"世界名称：{frame['world_name']}\n"
        f"世界基调：{frame['tone']}\n"
        f"核心冲突：{frame['central_conflict']}\n\n"
        f"请为该世界生成 {n} 个主要势力，以纯 JSON 对象返回，"
        f"不含 Markdown 代码块、不含任何额外说明。\n"
        f"对象 MUST 含有 EXACTLY 一个字段：\n"
        f"  \"factions\" — 长度恰好为 {n} 的数组，每个元素含以下两个字段（不多不少）：\n"
        f"    \"name\"       — 势力名称（中文字符串，非空，各势力之间必须各不相同）\n"
        f"    \"motivation\" — 驱动该势力行动的核心目标或动机（中文字符串，非空，一句话）\n"
        f"势力列表（{n} 个）：\n"
        f"{faction_lines}\n"
        f"示例（n=2 时）：{{\"factions\":[{{\"name\":\"铁血盟\",\"motivation\":\"以武力统一七国\"}},"
        f"{{\"name\":\"云隐宫\",\"motivation\":\"守护上古禁法不被滥用\"}}]}}"
    )

    obj, errors = complete_structured(
        provider,
        system=_SYSTEM_GEN_FACTIONS,
        user=user,
        validate=_validate_factions(n),
        max_repairs=2,
        log_label="gen_factions",
    )

    # ------------------------------------------------------------------
    # Stub fallback on error / no provider
    # ------------------------------------------------------------------
    if errors or obj is None:
        if errors != ["no provider"]:
            log.warning("gen_factions: LLM step failed (%s); using stub factions", "; ".join(errors))
        raw_factions = [
            {"name": f"势力{i+1}", "motivation": "目标尚待揭晓"}
            for i in range(n)
        ]
    else:
        raw_factions = obj["factions"]

    # Provided overrides per index (name/motivation).
    for i in range(min(len(provided), n)):
        if provided[i].get("name"):
            raw_factions[i]["name"] = provided[i]["name"]
        if provided[i].get("motivation"):
            raw_factions[i]["motivation"] = provided[i]["motivation"]

    # ------------------------------------------------------------------
    # Emit genesis events (turn=0, day=1, scene="genesis")
    # ------------------------------------------------------------------
    events: list[dict] = []
    summary_factions: list[dict] = []

    for i, f in enumerate(raw_factions):
        faction_id = f"faction_{i}"
        name = f["name"].strip()
        motivation = f["motivation"].strip()

        events.append(kernel_event(
            "faction_created",
            turn=0, day=1, scene="genesis",
            summary=f"势力建立：{name}",
            deltas={
                "op": "faction",
                "id": faction_id,
                "tier": "mentioned",
                "seed": name,
                "motivation": motivation,
            },
        ))
        summary_factions.append({"id": faction_id, "name": name})

    summary: dict = {"factions": summary_factions}
    return events, summary


# ---------------------------------------------------------------------------
# gen_npcs — Task 6: generate 2-4 opening NPCs with hard secrets
# ---------------------------------------------------------------------------

_SYSTEM_GEN_NPCS = (
    "你是 TRPG NPC 生成器，只返回严格符合字段规范的 JSON，所有故事文本用中文。"
)


def _validate_npcs(n: int):
    """Return a validator that checks the npcs array has exactly n entries,
    each with non-empty sketch, goal, and secret string fields."""
    def _validate(obj) -> list[str]:
        errs = []
        npcs = obj.get("npcs")
        if not isinstance(npcs, list):
            errs.append('field "npcs" must be a JSON array')
            return errs
        if len(npcs) != n:
            errs.append(f'field "npcs" must have exactly {n} entries, got {len(npcs)}')
        for i, npc in enumerate(npcs):
            if not isinstance(npc, dict):
                errs.append(f'npcs[{i}] must be a JSON object')
                continue
            if not isinstance(npc.get("sketch"), str) or not npc["sketch"].strip():
                errs.append(f'npcs[{i}]: missing or empty string field "sketch"')
            if not isinstance(npc.get("goal"), str) or not npc["goal"].strip():
                errs.append(f'npcs[{i}]: missing or empty string field "goal"')
            if not isinstance(npc.get("secret"), str) or not npc["secret"].strip():
                errs.append(f'npcs[{i}]: missing or empty string field "secret"')
        return errs
    return _validate


def gen_npcs(
    provider,
    oracle: Oracle,
    frame: dict,
    local_map: dict,
    factions: dict,
    *,
    provided=None,
    flavor: str = "classic",
) -> tuple[list[dict], dict]:
    """Generate 2-4 opening NPCs, each with a hard secret tagged secrecy='secret'.

    Engine decides: n = oracle.randint(2,4); roles via _draw_distinct from npc_roles;
                    2 traits per NPC via _draw_distinct from npc_traits.
    LLM writes: sketch, goal, secret strings only.

    On LLM error or provider=None -> deterministic stub; NEVER raises.

    Returns:
        (events, summary)
        summary = {"npcs": [{"id": "npc_{i}", "role": str}, ...]}
        events:
            character_created(id=npc_{i}, tier='mentioned', sketch, goal) x n
            fact_asserted(subject=npc_{i}, predicate='真实身份', value=<secret>,
                          secrecy='secret') x n
            entity_moved(who=npc_{i}, to=<venue_id>) x n
    """
    # ------------------------------------------------------------------
    # Engine-decided rolls (oracle only)
    # ------------------------------------------------------------------
    provided = provided or []
    n = max(len(provided), oracle.randint(2, 4))
    role_entries = _draw_distinct(oracle, load_table("npc_roles", flavor, base="classic"), n)
    rolled_roles = [e["name"] for e in role_entries] or ["旅人"]
    roles = [rolled_roles[i % len(rolled_roles)] for i in range(n)]

    # Draw 2 traits per NPC (distinct within each NPC's draw)
    traits_table = load_table("npc_traits", flavor, base="classic")
    traits_per_npc = [_draw_distinct(oracle, traits_table, 2) for _ in range(n)]

    venues = local_map["venues"]

    # ------------------------------------------------------------------
    # LLM step — strict per-index field-naming prompt
    # ------------------------------------------------------------------
    npc_lines = "\n".join(
        f"  npcs[{i}]: 角色定位={roles[i]}，性格特质={traits_per_npc[i][0]['name']}/{traits_per_npc[i][1]['name']}；"
        f"请给出 sketch（外貌或性格一句话描述）、goal（当前核心目标）、secret（隐藏的真实身份或秘密，供DM专用）"
        for i in range(n)
    )
    faction_names = "、".join(f["name"] for f in factions.get("factions", []))
    user = (
        f"世界名称：{frame['world_name']}\n"
        f"世界基调：{frame['tone']}\n"
        f"核心冲突：{frame['central_conflict']}\n"
        f"世界势力：{faction_names}\n\n"
        f"请为该世界的起始场景生成 {n} 个开场 NPC，以纯 JSON 对象返回，"
        f"不含 Markdown 代码块、不含任何额外说明。\n"
        f"对象 MUST 含有 EXACTLY 一个字段：\n"
        f"  \"npcs\" — 长度恰好为 {n} 的数组，每个元素含以下三个字段（不多不少）：\n"
        f"    \"sketch\"  — NPC 外貌或行为的一句话描述（中文字符串，非空）\n"
        f"    \"goal\"    — NPC 当前的核心目标或动机（中文字符串，非空）\n"
        f"    \"secret\"  — NPC 隐藏的真实身份或秘密，仅供 DM 知晓（中文字符串，非空）\n"
        f"NPC 列表（{n} 个，各 NPC 的角色和性格已由引擎指定）：\n"
        f"{npc_lines}\n"
        f"示例（n=2 时）：{{\"npcs\":["
        f"{{\"sketch\":\"戴兜帽的旅人，目光深邃\",\"goal\":\"寻找失散的家人\",\"secret\":\"实为被通缉的前朝刺客\"}},"
        f"{{\"sketch\":\"笑容和善的酒馆掌柜\",\"goal\":\"积攒财富后离开此地\",\"secret\":\"暗中为叛军传递情报\"}}]}}"
    )

    obj, errors = complete_structured(
        provider,
        system=_SYSTEM_GEN_NPCS,
        user=user,
        validate=_validate_npcs(n),
        max_repairs=2,
        log_label="gen_npcs",
    )

    # ------------------------------------------------------------------
    # Stub fallback on error / no provider
    # ------------------------------------------------------------------
    if errors or obj is None:
        if errors != ["no provider"]:
            log.warning("gen_npcs: LLM step failed (%s); using stub NPCs", "; ".join(errors))
        stub_secrets = [
            "实为流亡贵族后裔",
            "曾是帝国秘密侦探",
            "身负灭门血仇待报",
            "掌握改变格局的禁术",
        ]
        raw_npcs = [
            {
                "sketch": f"神秘的{roles[i]}，来历不明",
                "goal": "韬光养晦，等待时机",
                "secret": stub_secrets[i % len(stub_secrets)],
            }
            for i in range(n)
        ]
    else:
        raw_npcs = obj["npcs"]

    # Provided overrides per index (sketch/goal/secret + optional role); a
    # provided secret still flows into the secrecy="secret" fact below.
    for i in range(min(len(provided), n)):
        for f in ("sketch", "goal", "secret"):
            if provided[i].get(f):
                raw_npcs[i][f] = provided[i][f]
        if provided[i].get("role"):
            roles[i] = provided[i]["role"]

    # ------------------------------------------------------------------
    # Emit genesis events (turn=0, day=1, scene="genesis")
    # ------------------------------------------------------------------
    events: list[dict] = []
    summary_npcs: list[dict] = []

    for i, npc in enumerate(raw_npcs):
        npc_id = f"npc_{i}"
        sketch = npc["sketch"].strip()
        goal = npc["goal"].strip()
        secret = npc["secret"].strip()
        role = roles[i]
        venue_id = venues[i % len(venues)]

        # character_created
        events.append(kernel_event(
            "character_created",
            turn=0, day=1, scene="genesis",
            summary=f"NPC 登场：{npc_id}（{role}）",
            deltas={
                "id": npc_id,
                "tier": "mentioned",
                "sketch": sketch,
                "goal": goal,
            },
        ))

        # fact_asserted — hard secret, secrecy="secret"
        events.append(kernel_event(
            "fact_asserted",
            turn=0, day=1, scene="genesis",
            summary=f"NPC 秘密（DM 专用）：{npc_id}",
            deltas={
                "subject": npc_id,
                "predicate": "真实身份",
                "value": secret,
                "secrecy": "secret",
            },
        ))

        # entity_moved — place NPC at a venue
        events.append(kernel_event(
            "entity_moved",
            turn=0, day=1, scene="genesis",
            summary=f"NPC 位置：{npc_id} → {venue_id}",
            deltas={
                "who": npc_id,
                "to": venue_id,
            },
        ))

        summary_npcs.append({"id": npc_id, "role": role, "sketch": sketch})

    summary: dict = {"npcs": summary_npcs}
    return events, summary
