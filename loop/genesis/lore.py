"""loop.genesis.lore — gen_codex / gen_threads / gen_opening + thread tables."""
from __future__ import annotations

from engine.oracle import Oracle, scene_seed, load_table  # noqa: F401
from engine.log import get_logger
from kernel.events import kernel_event  # noqa: F401
from kernel.observability import get_tracer  # noqa: F401
from llm.structured import complete_structured  # noqa: F401

log = get_logger("loop.genesis")
from loop.genesis.common import _draw_distinct, _empty_str  # noqa: F401

# ---------------------------------------------------------------------------
# gen_threads — Task 7: campaign 暗线 + protagonist-bound 暗线
# ---------------------------------------------------------------------------

_SYSTEM_GEN_THREADS = (
    "You are a TRPG world-building assistant generating hidden quest skeletons (暗线). "
    "You MUST return ONLY a JSON object that conforms EXACTLY to the field "
    "specification below — the game engine parses it programmatically and REJECTS "
    "any deviation (missing keys, extra keys, or wrong key names). Write all story "
    "text in Chinese."
)

# Speed roll table: 快→70, 中→50, 慢→30 threshold
_SPEED_TABLE = [
    {"weight": 2, "name": "快", "threshold": 70},
    {"weight": 3, "name": "中", "threshold": 50},
    {"weight": 2, "name": "慢", "threshold": 30},
]

# Complexity bias table (campaign-level: bias medium/complex)
_COMPLEXITY_TABLE = [
    {"weight": 3, "name": "medium"},
    {"weight": 2, "name": "simple"},
    {"weight": 2, "name": "complex"},
]

# stage count per complexity
_STAGE_COUNT: dict[str, int] = {"simple": 2, "medium": 3, "complex": 5}


def _make_validate_threads(n: int, venues: list[str]):
    """Return a validate callable for complete_structured that checks the {"lines": [...]}
    object has EXACTLY n conforming thread line dicts with all required fields and
    l3_anchor in venues.  Returns list[str] of human-readable problems ([] = conforms).
    """
    required_str = ("about", "description", "trigger", "secret", "l3_anchor")

    def _validate(obj) -> list[str]:
        errors: list[str] = []
        lines = obj.get("lines")
        if not isinstance(lines, list):
            errors.append('The response must be a JSON object {"lines": [...]} whose '
                          '"lines" value is a JSON array.')
            return errors
        if len(lines) != n:
            errors.append(f'Expected EXACTLY {n} object(s) in "lines", but got {len(lines)}.')
        for i in range(n):
            if i >= len(lines):
                errors.append(f"Line {i + 1}: missing entirely.")
                continue
            ln = lines[i]
            if not isinstance(ln, dict):
                errors.append(f"Line {i + 1}: must be a JSON object.")
                continue
            probs: list[str] = []
            for f in required_str:
                v = ln.get(f)
                if not isinstance(v, str) or not v.strip():
                    probs.append(f'missing or empty string field "{f}"')
            l3 = ln.get("l3_anchor")
            if venues and isinstance(l3, str) and l3.strip() and l3.strip() not in venues:
                probs.append(f'"l3_anchor" must be EXACTLY one of {venues}, got "{l3}"')
            stages = ln.get("stages")
            if not isinstance(stages, list) or not stages:
                probs.append('"stages" must be a non-empty JSON array')
            else:
                for si, s in enumerate(stages):
                    if (not isinstance(s, dict) or not isinstance(s.get("hint"), str)
                            or not s["hint"].strip()):
                        probs.append(
                            f'stage {si + 1} must be an object whose only key is a '
                            f'non-empty string "hint"')
            if probs:
                errors.append(f"Line {i + 1}: " + "; ".join(probs) + ".")
        return errors

    return _validate


def _skeleton_from_provided(p, thread_id, complexity, anchor, threshold,
                            venues, stage_count, eg_venue):
    """Build a lore skeleton from a player-provided thread line.

    Repairs l3_anchor to a real venue (provided lines may name a bad anchor) and
    wraps string stages as {"hint": ...}. Empty fields fall back to the rolled
    complexity / generic strings so the skeleton is always create_lore_line-safe.
    """
    anchor_venue = p.get("l3_anchor")
    if not (isinstance(anchor_venue, str) and anchor_venue in venues):
        anchor_venue = venues[0] if venues else eg_venue
    stages = [{"hint": s.strip()} for s in (p.get("stages") or [])
              if isinstance(s, str) and s.strip()]
    if not stages:
        stages = [{"hint": f"线索提示{j + 1}"} for j in range(stage_count)]
    return {
        "id": thread_id,
        "complexity": p.get("complexity") or complexity,
        "anchor": anchor,
        "threshold": threshold,
        "about": p.get("about") or "待揭晓的悬案",
        "description": p.get("description") or "一条未解之谜",
        "trigger": p.get("trigger") or "玩家主动调查",
        "secret": p.get("secret") or "隐藏的真相",
        "l3_anchor": anchor_venue,
        "stages": stages,
    }


def _stub_thread_skeleton(
    thread_id: str,
    complexity: str,
    anchor: str,
    threshold: int,
    venues: list[str],
    stage_count: int,
    idx: int,
) -> dict:
    """Build a deterministic stub skeleton (used in fallback path)."""
    venue = venues[idx % len(venues)]
    stages = [{"hint": f"线索提示{j + 1}"} for j in range(stage_count)]
    return {
        "id": thread_id,
        "complexity": complexity,
        "anchor": anchor,
        "threshold": threshold,
        "about": "待揭晓的悬案",
        "description": "一条未解之谜",
        "trigger": "玩家主动调查",
        "secret": "隐藏的真相",
        "l3_anchor": venue,
        "stages": stages,
    }


# ---------------------------------------------------------------------------
# gen_codex — I6-P3b: author 2-3 world-setting text blocks (实力等级/编年史/势力志)
# ---------------------------------------------------------------------------

_SYSTEM_GEN_CODEX = (
    "你是世界设定典编纂者，只返回严格符合字段规范的 JSON，所有文本用中文。"
)


def _validate_codex(obj) -> list[str]:
    errs: list[str] = []
    entries = obj.get("entries")
    if not isinstance(entries, list):
        return ['field "entries" must be a JSON array']
    if not (2 <= len(entries) <= 4):
        errs.append(f'"entries" must have 2-4 items, got {len(entries)}')
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            errs.append(f'entries[{i}] must be a JSON object')
            continue
        for f in ("kind", "title", "body"):
            if not isinstance(e.get(f), str) or not e[f].strip():
                errs.append(f'entries[{i}]: missing or empty string field "{f}"')
    return errs


def gen_codex(provider, oracle, frame: dict, factions: dict | None = None,
              *, provided=None) -> list:
    """Author 2-3 'world setting codex' text blocks seeded by the world dimensions,
    in ONE LLM call (keeps genesis from ballooning — I9). Deterministic stub on
    failure; NEVER raises. Returns codex_entry_added events.

    provided: optional list of {kind,title,body} blocks to use verbatim (skip LLM).
    """
    _ = oracle.random()  # seed participation
    provided = provided or []

    if provided and all(isinstance(b, dict) and str(b.get("body", "")).strip()
                        for b in provided):
        blocks = list(provided)
    else:
        magic = frame.get("magic_system", "")
        power = frame.get("power_ladder", "")
        tension = frame.get("world_tension", "")
        conflict = frame.get("central_conflict", "")
        faction_names = "、".join(
            f.get("name", "") for f in (factions or {}).get("factions", [])
        ) or "（暂无）"
        user = (
            f"世界名称：{frame.get('world_name', '未名之地')}\n"
            f"世界维度：超能体系={magic}；实力范式={power}；主轴张力={tension}\n"
            f"核心冲突（DM 视角，可作编年史的暗线）：{conflict}\n"
            f"主要势力：{faction_names}\n\n"
            f"请据此编纂 2-3 篇『世界设定典』文字块，以纯 JSON 对象返回（不含 markdown）。\n"
            f"对象含字段 \"entries\"：一个数组，每个元素 EXACTLY 含 "
            f"{{\"kind\": 类别, \"title\": 标题, \"body\": 正文}}。必须包含：\n"
            f"  · 一篇 kind=\"实力等级\"：依据『实力范式』与『超能体系』，写清这个世界的力量如何分阶、"
            f"如何晋升、各阶的名称与标志；\n"
            f"  · 一篇 kind=\"编年史\"：依据『主轴张力』，按时间脉络写几个关键历史节点；\n"
            f"  · 可选一篇 kind=\"势力志\"：概述主要势力的格局与彼此关系。\n"
            f"每篇 body 用中文散文，150-300 字，具体可感、勿空泛。"
        )
        obj, errors = complete_structured(
            provider, system=_SYSTEM_GEN_CODEX, user=user,
            validate=_validate_codex, max_repairs=2, log_label="gen_codex",
        )
        if errors or obj is None:
            if errors != ["no provider"]:
                log.warning("gen_codex: LLM step failed (%s); using stub codex",
                            "; ".join(errors) or "provider is None")
            blocks = [
                {"kind": "实力等级", "title": "力量阶序",
                 "body": f"这个世界以『{power or '修行'}』论高下，由低到高层层递进，"
                         f"各阶皆有其名与凭证。"},
                {"kind": "编年史", "title": "世事简史",
                 "body": f"近世以来，{tension or '风云变幻'}；旧秩序与新势力此消彼长，"
                         f"埋下今日乱局的种子。"},
            ]
        else:
            blocks = obj["entries"]

    events = []
    for i, b in enumerate(blocks):
        events.append(kernel_event(
            "codex_entry_added", turn=0, day=1, scene="genesis",
            summary=f"世界设定典：{b.get('title', '')}",
            deltas={
                "id": f"codex_{i}",
                "kind": b.get("kind", ""),
                "title": b.get("title", ""),
                "body": b.get("body", ""),
            },
        ))
    return events


def gen_threads(
    provider,
    oracle: Oracle,
    frame: dict,
    local_map: dict,
    protagonist: str,
    *,
    provided=None,
    flavor: str = "classic",
) -> tuple[list[dict], dict]:
    """Generate 3-5 campaign-level 暗线 + 1-2 protagonist-bound 暗线 (lore skeletons).

    Engine decides: n, types (distinct), complexity, threshold, stage_count, id, anchor.
    LLM writes: about, description, trigger, secret, l3_anchor, stages[{hint}].

    l3_anchor is ALWAYS a real venue from local_map["venues"] — NO floating anchors.
    The validate loop rejects any l3_anchor not in the venue list (mirrors generate_lore_batch).

    On LLM error or provider=None -> deterministic stub; NEVER raises.

    Returns:
        (skeletons, summary)
        skeletons: list of dicts passable to create_lore_line (all _REQUIRED keys present)
        summary = {"threads": [{"id", "type", "complexity", "anchor"}, ...]}
    """
    try:
        return _gen_threads_inner(provider, oracle, frame, local_map, protagonist,
                                  provided=provided, flavor=flavor)
    except Exception:
        log.exception("gen_threads: unexpected error — returning stub skeletons")
        return _gen_threads_fallback(oracle, local_map, protagonist)


def _gen_threads_inner(
    provider,
    oracle: Oracle,
    frame: dict,
    local_map: dict,
    protagonist: str,
    *,
    provided=None,
    flavor: str = "classic",
) -> tuple[list[dict], dict]:
    venues = list(local_map["venues"])
    start_town = local_map["start_town"]
    venue_str = ", ".join(venues) if venues else "(none specified)"
    eg_venue = venues[0] if venues else "码头"

    provided = provided or []
    prov_campaign = [t for t in provided if (t.get("bound") or "campaign") != "protagonist"]
    prov_prot = [t for t in provided if (t.get("bound") or "campaign") == "protagonist"]

    # ------------------------------------------------------------------ #
    # Campaign threads: engine-decided rolls
    # ------------------------------------------------------------------ #
    n = max(len(prov_campaign), oracle.randint(3, 5))
    type_entries = _draw_distinct(oracle, load_table("thread_types", flavor, base="classic"), n)
    rolled_types = [e["name"] for e in type_entries] or ["事件"]
    thread_types = [rolled_types[i % len(rolled_types)] for i in range(n)]

    # Per-thread rolls: complexity, threshold, stage_count
    complexities: list[str] = []
    thresholds: list[int] = []
    stage_counts: list[int] = []
    for _ in range(n):
        complexity = oracle.draw(_COMPLEXITY_TABLE)["name"]
        speed = oracle.draw(_SPEED_TABLE)
        complexities.append(complexity)
        thresholds.append(speed["threshold"])
        stage_counts.append(_STAGE_COUNT[complexity])

    # Protagonist-bound thread rolls
    n_p = max(len(prov_prot), oracle.randint(1, 2))
    p_complexities: list[str] = []
    p_thresholds: list[int] = []
    p_stage_counts: list[int] = []
    for _ in range(n_p):
        complexity = oracle.draw(_COMPLEXITY_TABLE)["name"]
        speed = oracle.draw(_SPEED_TABLE)
        p_complexities.append(complexity)
        p_thresholds.append(speed["threshold"])
        p_stage_counts.append(_STAGE_COUNT[complexity])

    # ------------------------------------------------------------------ #
    # LLM step: campaign threads via complete_structured
    # ------------------------------------------------------------------ #
    campaign_spec_lines = "\n".join(
        f"  line {i + 1}: stages={stage_counts[i]}"
        for i in range(n)
    )
    campaign_user = (
        f"世界名称：{frame['world_name']}\n"
        f"世界基调：{frame['tone']}\n"
        f"核心冲突：{frame['central_conflict']}\n"
        f"暗线类型：campaign（anchor={start_town}）\n"
        f"L3 场所（l3_anchor 必须 EXACTLY 取自此列表）：{venue_str}\n\n"
        f"生成 {n} 条暗线骨架，每条主题各异，与世界风味契合，文本全部用中文。\n"
        f"{n} 条暗线按顺序的阶段数（stage count）：\n{campaign_spec_lines}\n\n"
        f"返回 ONLY 一个 JSON 对象：{{\"lines\": [ ...{n} 个对象... ]}}。\n"
        f"每个对象 MUST 含有 EXACTLY 下列字段（不多不少）：\n"
        f"  \"about\"       — 表面可见的异常（字符串，非空）\n"
        f"  \"description\" — 玩家可见的索引条目（字符串，非空）\n"
        f"  \"trigger\"     — 何种玩家行为会自然引出此线（字符串，非空）\n"
        f"  \"secret\"      — 背后的隐藏真相（字符串，非空，仅 DM 知晓）\n"
        f"  \"l3_anchor\"   — 线索实体所在的场所，必须 EXACTLY 取自 [{venue_str}]（字符串）\n"
        f"  \"stages\"      — 阶段数组，每项 EXACTLY {{\"hint\": \"<一句进度提示>\"}}\n"
        f"禁止包含 \"complexity\"、\"stage_count\"、\"title\"、\"theme\" 或其他字段。\n"
        f"每个 stage 必须用 \"hint\" 键（不得用 \"hook\"/\"resolution\" 等）。\n"
        f"示例（一条暗线）：\n"
        f"{{\"about\": \"夜里码头总有人影搬运不明货箱\", \"description\": \"码头的夜间走私传闻\", "
        f"\"trigger\": \"玩家夜里留意码头或盘问搬运工\", \"secret\": \"会馆私运违禁盐引\", "
        f"\"l3_anchor\": \"{eg_venue}\", \"stages\": [{{\"hint\": \"入夜后码头有可疑灯火\"}}, "
        f"{{\"hint\": \"搬运工对货箱讳莫如深\"}}]}}"
    )
    campaign_obj, campaign_errors = complete_structured(
        provider,
        system=_SYSTEM_GEN_THREADS,
        user=campaign_user,
        validate=_make_validate_threads(n, venues),
        max_repairs=2,
        log_label="gen_threads/campaign",
    )

    # ------------------------------------------------------------------ #
    # LLM step: protagonist-bound threads via complete_structured
    # ------------------------------------------------------------------ #
    prot_spec_lines = "\n".join(
        f"  line {i + 1}: stages={p_stage_counts[i]}"
        for i in range(n_p)
    )
    prot_user = (
        f"世界名称：{frame['world_name']}\n"
        f"世界基调：{frame['tone']}\n"
        f"核心冲突：{frame['central_conflict']}\n"
        f"暗线类型：protagonist（anchor={protagonist}）\n"
        f"L3 场所（l3_anchor 必须 EXACTLY 取自此列表）：{venue_str}\n\n"
        f"生成 {n_p} 条主角专属暗线骨架，每条主题各异，与世界风味契合，文本全部用中文。\n"
        f"{n_p} 条暗线按顺序的阶段数（stage count）：\n{prot_spec_lines}\n\n"
        f"返回 ONLY 一个 JSON 对象：{{\"lines\": [ ...{n_p} 个对象... ]}}。\n"
        f"每个对象 MUST 含有 EXACTLY 下列字段（不多不少）：\n"
        f"  \"about\"       — 表面可见的异常（字符串，非空）\n"
        f"  \"description\" — 玩家可见的索引条目（字符串，非空）\n"
        f"  \"trigger\"     — 何种玩家行为会自然引出此线（字符串，非空）\n"
        f"  \"secret\"      — 背后的隐藏真相（字符串，非空，仅 DM 知晓）\n"
        f"  \"l3_anchor\"   — 线索实体所在的场所，必须 EXACTLY 取自 [{venue_str}]（字符串）\n"
        f"  \"stages\"      — 阶段数组，每项 EXACTLY {{\"hint\": \"<一句进度提示>\"}}\n"
        f"禁止包含 \"complexity\"、\"stage_count\"、\"title\"、\"theme\" 或其他字段。\n"
        f"每个 stage 必须用 \"hint\" 键（不得用 \"hook\"/\"resolution\" 等）。\n"
        f"示例（一条暗线）：\n"
        f"{{\"about\": \"夜里码头总有人影搬运不明货箱\", \"description\": \"码头的夜间走私传闻\", "
        f"\"trigger\": \"玩家夜里留意码头或盘问搬运工\", \"secret\": \"会馆私运违禁盐引\", "
        f"\"l3_anchor\": \"{eg_venue}\", \"stages\": [{{\"hint\": \"入夜后码头有可疑灯火\"}}, "
        f"{{\"hint\": \"搬运工对货箱讳莫如深\"}}]}}"
    )
    prot_obj, prot_errors = complete_structured(
        provider,
        system=_SYSTEM_GEN_THREADS,
        user=prot_user,
        validate=_make_validate_threads(n_p, venues),
        max_repairs=2,
        log_label="gen_threads/protagonist",
    )

    # ------------------------------------------------------------------ #
    # Build skeletons — lines align by index with oracle rolls when conformed
    # ------------------------------------------------------------------ #
    skeletons: list[dict] = []
    summary_threads: list[dict] = []

    # Campaign threads: use conformed LLM lines by index, or deterministic stub
    campaign_lines = campaign_obj["lines"] if (not campaign_errors and campaign_obj) else None
    for i in range(n):
        thread_id = f"thread_{i}"
        complexity = complexities[i]
        threshold = thresholds[i]
        stage_count = stage_counts[i]
        thread_type = thread_types[i]

        p = prov_campaign[i] if i < len(prov_campaign) else None
        if p is not None:
            sk = _skeleton_from_provided(
                p, thread_id, complexity, start_town, threshold, venues, stage_count, eg_venue)
        elif campaign_lines is not None:
            ln = campaign_lines[i]
            stages = [{"hint": s["hint"].strip()} for s in ln["stages"][:stage_count]]
            sk = {
                "id": thread_id,
                "complexity": complexity,
                "anchor": start_town,
                "threshold": threshold,
                "about": ln["about"].strip(),
                "description": ln["description"].strip(),
                "trigger": ln["trigger"].strip(),
                "secret": ln["secret"].strip(),
                "l3_anchor": ln["l3_anchor"].strip(),
                "stages": stages,
            }
        else:
            sk = _stub_thread_skeleton(
                thread_id, complexity, start_town, threshold, venues, stage_count, i
            )
        skeletons.append(sk)
        summary_threads.append({
            "id": thread_id,
            "type": thread_type,
            "complexity": complexity,
            "anchor": start_town,
            "about": sk["about"],
        })

    # Protagonist-bound threads: use conformed LLM lines by index, or deterministic stub
    prot_lines = prot_obj["lines"] if (not prot_errors and prot_obj) else None
    for i in range(n_p):
        thread_id = f"pthread_{i}"
        complexity = p_complexities[i]
        threshold = p_thresholds[i]
        stage_count = p_stage_counts[i]

        p = prov_prot[i] if i < len(prov_prot) else None
        if p is not None:
            sk = _skeleton_from_provided(
                p, thread_id, complexity, protagonist, threshold, venues, stage_count, eg_venue)
        elif prot_lines is not None:
            ln = prot_lines[i]
            stages = [{"hint": s["hint"].strip()} for s in ln["stages"][:stage_count]]
            sk = {
                "id": thread_id,
                "complexity": complexity,
                "anchor": protagonist,
                "threshold": threshold,
                "about": ln["about"].strip(),
                "description": ln["description"].strip(),
                "trigger": ln["trigger"].strip(),
                "secret": ln["secret"].strip(),
                "l3_anchor": ln["l3_anchor"].strip(),
                "stages": stages,
            }
        else:
            sk = _stub_thread_skeleton(
                thread_id, complexity, protagonist, threshold, venues, stage_count, i
            )
        skeletons.append(sk)
        # protagonist-bound uses type "protagonist"
        summary_threads.append({
            "id": thread_id,
            "type": "protagonist",
            "complexity": complexity,
            "anchor": protagonist,
            "about": sk["about"],
        })

    summary: dict = {"threads": summary_threads}
    return skeletons, summary


# ---------------------------------------------------------------------------
# gen_opening — Task 8: opening scene narration (protagonist POV)
# ---------------------------------------------------------------------------

_SYSTEM_GEN_OPENING = (
    "你是跑团（TRPG）主持人（DM），现在为玩家写开场叙事。"
    "以主角视角，用第二人称（「你」）叙述：主角刚刚落脚在起始镇的某个地点，"
    "用具体可感的细节描绘环境氛围与周遭人物，给玩家留下可回应的钩子，"
    "但绝不替玩家决定下一步行动。"
    "只输出叙事散文本身，不要任何 JSON / 结构化数据 / 元说明。"
    "重要：用地点的名字指代地点，绝不要在面向玩家的文本里出现 town_0 / venue_0 这类内部 id。"
)


def gen_opening(
    provider,
    frame: dict,
    world_summary: str,
    *,
    scene_loc: str,
    scene_loc_name: str | None = None,
    provided=None,
) -> tuple[list[dict], str]:
    """Write the opening-scene narration (protagonist POV, landing in the start town).

    Calls provider.complete(system, user) — a plain prose call, NOT complete_structured.
    On provider=None or any call failure → deterministic stub narration mentioning
    frame['world_name']; NEVER raises.

    Args:
        provider:       LLMProvider with a .complete(system, user) method, or None.
        frame:          World frame dict (must contain 'world_name').
        world_summary:  Compact textual summary of the world (regions/town/venues/NPCs).
        scene_loc:      The internal L3 venue id where the protagonist lands.
        scene_loc_name: Human-readable name for scene_loc (falls back to scene_loc if None).
                        Passed to the LLM prompt so the narration uses names, not ids.

    Returns:
        (events, narration)
        events  — exactly one narration_recorded event whose deltas["text"] == narration.
        narration — the prose string.
    """
    # Player-provided opening prose is used verbatim (still emits the event).
    if isinstance(provided, str) and provided.strip():
        narration = provided.strip()
        events = [kernel_event(
            "narration_recorded", turn=0, day=1, scene="genesis",
            summary="开场叙事", deltas={"scene": "genesis", "text": narration})]
        return events, narration

    world_name = frame.get("world_name", "未名之地")
    narration: str | None = None
    # Use the human-readable name in the prompt; fall back to id only as last resort
    scene_display = scene_loc_name if scene_loc_name else scene_loc

    if provider is not None:
        try:
            user = (
                f"{world_summary}\n\n"
                f"主角当前所在地点：{scene_display}\n"
                f"请写一段开场叙事，以主角视角落脚于起始镇，给玩家留下可回应的钩子。\n"
                f"重要：用地点的名字指代地点，绝不要在面向玩家的文本里出现 town_0 / venue_0 这类内部 id。"
            )
            narration = provider.complete(_SYSTEM_GEN_OPENING, user)
        except Exception:
            log.exception("gen_opening: provider.complete failed; using stub narration")
            narration = None

    if not narration or not narration.strip():
        narration = (
            f"你踏入了{world_name}的起始之地，四周的景象让你感到既陌生又充满可能。"
            f"这里的每一个角落似乎都藏着尚未揭开的秘密，等待着你去探索。"
        )

    events: list[dict] = [
        kernel_event(
            "narration_recorded",
            turn=0, day=1, scene="genesis",
            summary="开场叙事",
            deltas={"scene": "genesis", "text": narration},
        )
    ]
    return events, narration
