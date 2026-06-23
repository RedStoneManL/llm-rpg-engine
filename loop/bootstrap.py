from __future__ import annotations
from engine.oracle import Oracle, scene_seed, load_table
from engine.log import get_logger
from kernel.events import kernel_event
from kernel.observability import get_tracer
from llm.structured import complete_structured

log = get_logger("loop.bootstrap")

def _draw_distinct(oracle, entries, k):
    """Weighted draw of up to k DISTINCT entries (sample without replacement)."""
    pool = list(entries)
    out = []
    for _ in range(min(k, len(pool))):
        e = oracle.draw(pool)
        out.append(e)
        pool.remove(e)
    return out


def _empty_str(v) -> bool:
    """True when v is not a non-blank string (None / non-str / blank)."""
    return not (isinstance(v, str) and v.strip())


# ---------------------------------------------------------------------------
# gen_frame — Task 2: world frame (tone / conflict / faction-count / region-count)
# ---------------------------------------------------------------------------

_SYSTEM_GEN_FRAME = (
    "你是 TRPG 世界设定生成器，只返回严格符合字段规范的 JSON，所有故事文本用中文。"
)


def _validate_frame(obj) -> list[str]:
    """Return human-readable problems naming missing/empty world_name/central_conflict."""
    errs = []
    if not isinstance(obj.get("world_name"), str) or not obj["world_name"].strip():
        errs.append('missing or empty string field "world_name"')
    if not isinstance(obj.get("central_conflict"), str) or not obj["central_conflict"].strip():
        errs.append('missing or empty string field "central_conflict"')
    return errs


def gen_frame(
    provider,
    oracle: Oracle,
    pitch: str,
    *,
    provided=None,
) -> tuple[list[dict], dict]:
    """Roll the world frame and name it via the LLM.

    Engine decides: tone (oracle.draw), n_factions/n_regions (oracle.randint).
    LLM writes: world_name, central_conflict (story strings only).

    On LLM error or provider=None → deterministic stub strings; NEVER raises.

    Returns:
        (events, frame)
        frame = {"genre":str,"tone":str,"central_conflict":str,
                 "world_name":str,"n_factions":int,"n_regions":int}
        events = [entity_created(world)] + three fact_asserted(genre/tone/central_conflict)
    """
    # ------------------------------------------------------------------
    # Engine-decided rolls
    # ------------------------------------------------------------------
    provided = provided or {}

    tone_roll = oracle.draw(load_table("tone_axes", "genesis"))["name"]
    nf_roll = oracle.randint(3, 5)
    nr_roll = oracle.randint(3, 5)
    # A provided empty / 0 falls back to the roll (0 regions/factions is nonsensical).
    tone = provided.get("tone") or tone_roll
    n_factions = provided.get("n_factions") or nf_roll
    n_regions = provided.get("n_regions") or nr_roll
    genre = provided.get("genre") or pitch

    # ------------------------------------------------------------------
    # LLM step — strict field-by-field prompt (mirrors generate_lore_batch).
    # Skipped when both authored fields are provided.
    # ------------------------------------------------------------------
    p_name = provided.get("world_name")
    p_conflict = provided.get("central_conflict")
    need_llm = not (p_name and p_conflict)

    if need_llm:
        user = (
            f"玩家给出的世界背景关键词（pitch）：{genre}\n"
            f"已由引擎掷出的世界基调（tone）：{tone}\n\n"
            f"请根据以上信息生成世界命名和核心冲突，以纯 JSON 对象返回，"
            f"不含 Markdown 代码块、不含任何额外说明。\n"
            f"对象 MUST 含有 EXACTLY 下列两个字段（不多不少）：\n"
            f"  \"world_name\"       — 世界或大陆的名称（中文字符串，非空）\n"
            f"  \"central_conflict\" — 驱动整个世界的核心矛盾或冲突（中文字符串，非空）\n"
            f"示例：{{\"world_name\": \"碎镜大陆\", \"central_conflict\": \"皇权与江湖势力之间的生死角力\"}}"
        )
        obj, errors = complete_structured(
            provider,
            system=_SYSTEM_GEN_FRAME,
            user=user,
            validate=_validate_frame,
            max_repairs=2,
            log_label="gen_frame",
        )
        if errors or obj is None:
            # Deterministic stub — never raises
            world_name = "未名之地"
            central_conflict = "一桩悬而未决的乱局"
            if errors != ["no provider"]:
                log.warning("gen_frame: LLM step failed (%s); using stub frame",
                            "; ".join(errors) or "provider is None")
        else:
            world_name = obj["world_name"].strip()
            central_conflict = obj["central_conflict"].strip()
    else:
        world_name = ""
        central_conflict = ""

    # Provided overrides (non-empty wins)
    if p_name:
        world_name = p_name.strip() if isinstance(p_name, str) else p_name
    if p_conflict:
        central_conflict = p_conflict.strip() if isinstance(p_conflict, str) else p_conflict

    # ------------------------------------------------------------------
    # Assemble frame dict
    # ------------------------------------------------------------------
    frame: dict = {
        "genre": genre,
        "tone": tone,
        "world_name": world_name,
        "central_conflict": central_conflict,
        "n_factions": n_factions,
        "n_regions": n_regions,
    }

    # ------------------------------------------------------------------
    # Emit genesis events (turn=0, day=1, scene="genesis")
    # ------------------------------------------------------------------
    events: list[dict] = []

    # Level-0 world anchor entity
    events.append(kernel_event(
        "entity_created",
        turn=0, day=1, scene="genesis",
        summary=f"世界实体建立：{world_name}",
        deltas={
            "id": "world",
            "etype": "Place",
            "tier": "mentioned",
            "attrs": {"level": 0, "kind": "region", "seed": world_name},
        },
    ))

    # Three public fact_asserted events for genre / tone / central_conflict
    for predicate, value in (
        ("genre", genre),
        ("tone", tone),
        ("central_conflict", central_conflict),
    ):
        events.append(kernel_event(
            "fact_asserted",
            turn=0, day=1, scene="genesis",
            summary=f"世界属性：{predicate}={value}",
            deltas={
                "subject": "world",
                "predicate": predicate,
                "value": value,
                "secrecy": "public",
            },
        ))

    return events, frame


# ---------------------------------------------------------------------------
# gen_regions — Task 3: macro L1-region skeleton + pinned adjacency graph
# ---------------------------------------------------------------------------

_SYSTEM_GEN_REGIONS = (
    "你是 TRPG 世界地理生成器，只返回严格符合字段规范的 JSON，所有故事文本用中文。"
)


def _validate_regions(n: int):
    """Return a validator that checks the regions array has exactly n entries,
    each with non-empty name, terrain, and seed fields."""
    def _validate(obj) -> list[str]:
        errs = []
        regions = obj.get("regions")
        if not isinstance(regions, list):
            errs.append('field "regions" must be a JSON array')
            return errs
        if len(regions) != n:
            errs.append(f'field "regions" must have exactly {n} entries, got {len(regions)}')
        for i, r in enumerate(regions):
            if not isinstance(r, dict):
                errs.append(f'regions[{i}] must be a JSON object')
                continue
            if not isinstance(r.get("name"), str) or not r["name"].strip():
                errs.append(f'regions[{i}]: missing or empty string field "name"')
            if not isinstance(r.get("terrain"), str) or not r["terrain"].strip():
                errs.append(f'regions[{i}]: missing or empty string field "terrain"')
            if not isinstance(r.get("seed"), str) or not r["seed"].strip():
                errs.append(f'regions[{i}]: missing or empty string field "seed"')
        return errs
    return _validate


def gen_regions(
    provider,
    oracle: Oracle,
    frame: dict,
    *,
    provided=None,
) -> tuple[list[dict], dict]:
    """Generate the macro L1-region skeleton with a pinned adjacency graph.

    Engine decides: n = frame["n_regions"]; terrains via _draw_distinct; density roll.
    LLM writes: region name and seed strings only.

    On LLM error or provider=None -> deterministic stub; NEVER raises.

    Returns:
        (events, summary)
        summary = {
            "regions": [{"id", "name", "tier", "terrain"}, ...],
            "start_region": "region_0",
            "density": float,
        }
        events = place_created(level=1, kind=region) x n_regions
                 + place_linked(region_0 -- region_i) x (n_regions - 1)
    """
    provided = provided or []
    n = max(len(provided), frame["n_regions"])

    # ------------------------------------------------------------------
    # Engine-decided rolls (oracle only — no random/time). Terrains drawn
    # distinct then padded by cycling so a larger provided count never errors;
    # a provided per-region terrain wins over the rolled one.
    # ------------------------------------------------------------------
    terrain_entries = _draw_distinct(oracle, load_table("terrains", "genesis"), n)
    rolled_terrains = [e["name"] for e in terrain_entries] or ["平原"]
    terrains = [
        (provided[i].get("terrain") if i < len(provided) and provided[i].get("terrain")
         else rolled_terrains[i % len(rolled_terrains)])
        for i in range(n)
    ]
    density = round(oracle.random() * 0.3 + 0.2, 1)

    # ------------------------------------------------------------------
    # Neighbor tier boundary: i=0 start, i in 1..neighbor_count neighbor, rest far
    # Use n//2 neighbors (at least 1 if n>1, capped so "far" can exist for larger n)
    # ------------------------------------------------------------------
    neighbor_count = max(1, n // 2) if n > 1 else 0

    # ------------------------------------------------------------------
    # LLM step
    # ------------------------------------------------------------------
    terrain_lines = "\n".join(
        f"  regions[{i}]: terrain 必须 echo 为 \"{terrains[i]}\""
        for i in range(n)
    )
    user = (
        f"世界名称：{frame['world_name']}\n"
        f"世界基调：{frame['tone']}\n"
        f"核心冲突：{frame['central_conflict']}\n\n"
        f"请为该世界生成 {n} 个宏观大区域（L1 级），以纯 JSON 对象返回，"
        f"不含 Markdown 代码块、不含任何额外说明。\n"
        f"对象 MUST 含有 EXACTLY 一个字段：\n"
        f"  \"regions\" — 长度恰好为 {n} 的数组，每个元素含以下三个字段（不多不少）：\n"
        f"    \"name\"    — 地域名称（中文字符串，非空）\n"
        f"    \"terrain\" — 地形类型（必须原样 echo 引擎已给定值，见下）\n"
        f"    \"seed\"    — 一句话风味描述（中文字符串，非空，不超过20字）\n"
        f"引擎已指定的地形（必须原样 echo，不得修改）：\n"
        f"{terrain_lines}\n"
        f"示例（n=2 时）：{{\"regions\":[{{\"name\":\"铁峰山脉\",\"terrain\":\"山地\",\"seed\":\"矿脉纵横，人迹罕至\"}},"
        f"{{\"name\":\"云泽平原\",\"terrain\":\"平原\",\"seed\":\"沃土千里，战乱频仍\"}}]}}"
    )

    obj, errors = complete_structured(
        provider,
        system=_SYSTEM_GEN_REGIONS,
        user=user,
        validate=_validate_regions(n),
        max_repairs=2,
        log_label="gen_regions",
    )

    # ------------------------------------------------------------------
    # Stub fallback on error / no provider
    # ------------------------------------------------------------------
    if errors or obj is None:
        if errors != ["no provider"]:
            log.warning("gen_regions: LLM step failed (%s); using stub regions", "; ".join(errors))
        raw_regions = [
            {"name": f"地域{i+1}", "terrain": terrains[i], "seed": "一片待探索的疆域"}
            for i in range(n)
        ]
    else:
        raw_regions = obj["regions"]

    # Provided overrides per index (names/seeds); terrains already overridden above.
    for i in range(min(len(provided), n)):
        if provided[i].get("name"):
            raw_regions[i]["name"] = provided[i]["name"]
        if provided[i].get("seed"):
            raw_regions[i]["seed"] = provided[i]["seed"]

    # ------------------------------------------------------------------
    # Build region metadata: tiers, ids
    # ------------------------------------------------------------------
    summary_regions = []
    for i, r in enumerate(raw_regions):
        if i == 0:
            tier = "start"
        elif i <= neighbor_count:
            tier = "neighbor"
        else:
            tier = "far"
        summary_regions.append({
            "id": f"region_{i}",
            "name": r["name"].strip(),
            "tier": tier,
            "terrain": terrains[i],
        })

    summary = {
        "regions": summary_regions,
        "start_region": "region_0",
        "density": density,
    }

    # ------------------------------------------------------------------
    # Emit genesis events (turn=0, day=1, scene="genesis")
    # ------------------------------------------------------------------
    events: list[dict] = []

    # place_created for every region
    for i, r in enumerate(raw_regions):
        region_id = f"region_{i}"
        attrs: dict = {"terrain": terrains[i]}
        if i == 0:
            attrs["density"] = density

        events.append(kernel_event(
            "place_created",
            turn=0, day=1, scene="genesis",
            summary=f"地域建立：{r['name'].strip()}（{terrains[i]}）",
            deltas={
                "id": region_id,
                "level": 1,
                "kind": "region",
                "seed": r["seed"].strip(),
                "tier": "mentioned",
                "attrs": attrs,
            },
        ))

    # place_linked: star graph — region_0 adjacent to every other region
    directions = ["北", "东", "南", "西", "东北", "西北", "东南", "西南"]
    for i in range(1, n):
        direction = directions[(i - 1) % len(directions)]
        events.append(kernel_event(
            "place_linked",
            turn=0, day=1, scene="genesis",
            summary=f"地域连接：region_0 — region_{i}（{direction}）",
            deltas={
                "a": "region_0",
                "b": f"region_{i}",
                "direction": direction,
            },
        ))

    return events, summary


# ---------------------------------------------------------------------------
# gen_local_map — Task 4: start region's L2 places + start town's L3 venues
# ---------------------------------------------------------------------------

_SYSTEM_GEN_LOCAL_MAP = (
    "你是 TRPG 世界地图细化生成器，只返回严格符合字段规范的 JSON，所有故事文本用中文。"
)


def _validate_local_map(n_venues: int, n_neighbors: int):
    """Return a validator for the local map LLM response."""
    def _validate(obj) -> list[str]:
        errs = []
        # Validate town
        town = obj.get("town")
        if not isinstance(town, dict):
            errs.append('field "town" must be a JSON object')
        else:
            if not isinstance(town.get("name"), str) or not town["name"].strip():
                errs.append('town: missing or empty string field "name"')
            if not isinstance(town.get("seed"), str) or not town["seed"].strip():
                errs.append('town: missing or empty string field "seed"')

        # Validate venues array
        venues = obj.get("venues")
        if not isinstance(venues, list):
            errs.append('field "venues" must be a JSON array')
        else:
            if len(venues) != n_venues:
                errs.append(f'field "venues" must have exactly {n_venues} entries, got {len(venues)}')
            for i, v in enumerate(venues):
                if not isinstance(v, dict):
                    errs.append(f'venues[{i}] must be a JSON object')
                    continue
                if not isinstance(v.get("name"), str) or not v["name"].strip():
                    errs.append(f'venues[{i}]: missing or empty string field "name"')
                if not isinstance(v.get("seed"), str) or not v["seed"].strip():
                    errs.append(f'venues[{i}]: missing or empty string field "seed"')

        # Validate neighbors array
        neighbors = obj.get("neighbors")
        if not isinstance(neighbors, list):
            errs.append('field "neighbors" must be a JSON array')
        else:
            if len(neighbors) != n_neighbors:
                errs.append(f'field "neighbors" must have exactly {n_neighbors} entries, got {len(neighbors)}')
            for i, nb in enumerate(neighbors):
                if not isinstance(nb, dict):
                    errs.append(f'neighbors[{i}] must be a JSON object')
                    continue
                if not isinstance(nb.get("name"), str) or not nb["name"].strip():
                    errs.append(f'neighbors[{i}]: missing or empty string field "name"')
                if not isinstance(nb.get("seed"), str) or not nb["seed"].strip():
                    errs.append(f'neighbors[{i}]: missing or empty string field "seed"')

        return errs
    return _validate


def gen_local_map(
    provider,
    oracle: Oracle,
    frame: dict,
    regions_summary: dict,
    *,
    provided=None,
) -> tuple[list[dict], dict]:
    """Generate the start region's L2 places and start town's L3 venues.

    Engine decides: n_extra_l2 (1-2), neighbor kinds via _draw_distinct, n_venues (2-4).
    LLM writes: name/seed strings only.

    On LLM error or provider=None -> deterministic stub; NEVER raises.

    Returns:
        (events, summary)
        summary = {
            "start_town": "town_0",
            "venues": [venue_id, ...],          # >= 2 entries always
            "l2": [{"id", "kind", "name"}, ...],  # town_0 + neighbor l2s
        }
        events:
            place_created(level=2, kind=settlement, id=town_0, parent=start_region)
            place_created(level=2, kind=<drawn>, id=l2_{i}, parent=start_region) x n_extra_l2
            place_created(level=3, kind=venue, id=venue_{i}, parent=town_0) x n_venues
            place_linked(a=town_0, b=l2_{i}) x n_extra_l2
    """
    start_region = regions_summary["start_region"]
    provided = provided or {}
    p_town = provided.get("town") or {}
    p_venues = provided.get("venues") or []
    p_neighbors = provided.get("neighbors") or []

    # ------------------------------------------------------------------
    # Engine-decided rolls. Counts top up to max(provided, rolled); kinds drawn
    # distinct then padded by cycling; a provided per-neighbor kind wins.
    # ------------------------------------------------------------------
    n_extra_l2 = max(len(p_neighbors), oracle.randint(1, 2))
    neighbor_kind_entries = _draw_distinct(oracle, load_table("place_kinds", "genesis"), n_extra_l2)
    rolled_kinds = [e["name"] for e in neighbor_kind_entries] or ["野地"]
    neighbor_kinds = [
        (p_neighbors[i].get("kind") if i < len(p_neighbors) and p_neighbors[i].get("kind")
         else rolled_kinds[i % len(rolled_kinds)])
        for i in range(n_extra_l2)
    ]
    n_venues = max(len(p_venues), oracle.randint(2, 4))

    # ------------------------------------------------------------------
    # LLM step
    # ------------------------------------------------------------------
    venue_lines = "\n".join(
        f"  venues[{i}]: 请给出这个场所的 name 和 seed"
        for i in range(n_venues)
    )
    neighbor_lines = "\n".join(
        f"  neighbors[{i}]: kind 已由引擎指定为 \"{neighbor_kinds[i]}\"，请给出 name 和 seed"
        for i in range(n_extra_l2)
    )
    user = (
        f"世界名称：{frame['world_name']}\n"
        f"世界基调：{frame['tone']}\n"
        f"核心冲突：{frame['central_conflict']}\n\n"
        f"请为该世界的起始区域生成地图细节，以纯 JSON 对象返回，"
        f"不含 Markdown 代码块、不含任何额外说明。\n"
        f"对象 MUST 含有 EXACTLY 下列三个字段（不多不少）：\n"
        f"  \"town\"      — 起始小镇的对象，含 name（中文非空）和 seed（一句话风味，不超过20字）\n"
        f"  \"venues\"    — 长度恰好为 {n_venues} 的数组，每项含 name 和 seed（小镇内场所，如集市、酒馆等）\n"
        f"  \"neighbors\" — 长度恰好为 {n_extra_l2} 的数组，每项含 name 和 seed（邻近地点，种类已给定）\n"
        f"场所列表（{n_venues} 个，均位于小镇内）：\n"
        f"{venue_lines}\n"
        f"邻近地点列表（{n_extra_l2} 个，kind 已指定）：\n"
        f"{neighbor_lines}\n"
        f"示例（n_venues=2, n_neighbors=1）："
        f'{{\"town\":{{\"name\":\"碎石镇\",\"seed\":\"商路要冲，传说众多\"}},'
        f'\"venues\":[{{\"name\":\"老醉酒馆\",\"seed\":\"消息汇聚之处\"}},{{\"name\":\"铁铺\",\"seed\":\"装备齐全\"}}],'
        f'\"neighbors\":[{{\"name\":\"幽林\",\"seed\":\"深处有异兽出没\"}}]}}'
    )

    obj, errors = complete_structured(
        provider,
        system=_SYSTEM_GEN_LOCAL_MAP,
        user=user,
        validate=_validate_local_map(n_venues, n_extra_l2),
        max_repairs=2,
        log_label="gen_local_map",
    )

    # ------------------------------------------------------------------
    # Stub fallback on error / no provider
    # ------------------------------------------------------------------
    if errors or obj is None:
        if errors != ["no provider"]:
            log.warning("gen_local_map: LLM step failed (%s); using stub map", "; ".join(errors))
        stub_venue_names = ["集市", "酒馆", "铁铺", "寺庙"]
        stub_neighbor_names = ["野径", "荒地"]
        obj = {
            "town": {"name": "起始镇", "seed": "烟火气浓厚的小镇"},
            "venues": [
                {"name": stub_venue_names[i % len(stub_venue_names)], "seed": "待探索的场所"}
                for i in range(n_venues)
            ],
            "neighbors": [
                {"name": stub_neighbor_names[i % len(stub_neighbor_names)], "seed": "一片待探索之地"}
                for i in range(n_extra_l2)
            ],
        }

    # Provided overrides (non-empty wins) before names/ids are read off obj.
    if p_town.get("name"):
        obj["town"]["name"] = p_town["name"]
    if p_town.get("seed"):
        obj["town"]["seed"] = p_town["seed"]
    for i in range(min(len(p_venues), n_venues)):
        if p_venues[i].get("name"):
            obj["venues"][i]["name"] = p_venues[i]["name"]
        if p_venues[i].get("seed"):
            obj["venues"][i]["seed"] = p_venues[i]["seed"]
    for i in range(min(len(p_neighbors), n_extra_l2)):
        if p_neighbors[i].get("name"):
            obj["neighbors"][i]["name"] = p_neighbors[i]["name"]
        if p_neighbors[i].get("seed"):
            obj["neighbors"][i]["seed"] = p_neighbors[i]["seed"]

    # ------------------------------------------------------------------
    # Build summary
    # ------------------------------------------------------------------
    town_name = obj["town"]["name"].strip()
    town_seed = obj["town"]["seed"].strip()

    venue_ids = [f"venue_{i}" for i in range(n_venues)]

    l2_summary = [{"id": "town_0", "kind": "settlement", "name": town_name}]
    for i in range(n_extra_l2):
        l2_summary.append({
            "id": f"l2_{i}",
            "kind": neighbor_kinds[i],
            "name": obj["neighbors"][i]["name"].strip(),
        })

    # Build venue_names: {venue_id -> venue_name} so downstream callers (gen_protagonist,
    # gen_opening, _build_world_summary, _print_intro) can reference names, not raw ids.
    venue_names: dict[str, str] = {}
    for i, v in enumerate(obj["venues"]):
        venue_names[f"venue_{i}"] = v["name"].strip()

    summary = {
        "start_town": "town_0",
        "venues": venue_ids,
        "venue_names": venue_names,
        "l2": l2_summary,
    }

    # ------------------------------------------------------------------
    # Emit genesis events (turn=0, day=1, scene="genesis")
    # ------------------------------------------------------------------
    events: list[dict] = []

    # Start town (L2, settlement, tracked)
    events.append(kernel_event(
        "place_created",
        turn=0, day=1, scene="genesis",
        summary=f"起始小镇建立：{town_name}",
        deltas={
            "id": "town_0",
            "level": 2,
            "kind": "settlement",
            "seed": town_seed,
            "parent": start_region,
            "tier": "tracked",
        },
    ))

    # Neighbor L2 places
    for i in range(n_extra_l2):
        nb = obj["neighbors"][i]
        nb_id = f"l2_{i}"
        nb_name = nb["name"].strip()
        nb_seed = nb["seed"].strip()
        nb_kind = neighbor_kinds[i]
        events.append(kernel_event(
            "place_created",
            turn=0, day=1, scene="genesis",
            summary=f"邻近地点建立：{nb_name}（{nb_kind}）",
            deltas={
                "id": nb_id,
                "level": 2,
                "kind": nb_kind,
                "seed": nb_seed,
                "parent": start_region,
                "tier": "tracked",
            },
        ))

    # L3 venues (tracked, parent=town_0)
    for i in range(n_venues):
        v = obj["venues"][i]
        v_id = venue_ids[i]
        v_name = v["name"].strip()
        v_seed = v["seed"].strip()
        events.append(kernel_event(
            "place_created",
            turn=0, day=1, scene="genesis",
            summary=f"场所建立：{v_name}（{v_id}）",
            deltas={
                "id": v_id,
                "level": 3,
                "kind": "venue",
                "seed": v_seed,
                "parent": "town_0",
                "tier": "tracked",
            },
        ))

    # place_linked: town_0 <-> each neighbor L2
    for i in range(n_extra_l2):
        events.append(kernel_event(
            "place_linked",
            turn=0, day=1, scene="genesis",
            summary=f"地点连接：town_0 — l2_{i}",
            deltas={
                "a": "town_0",
                "b": f"l2_{i}",
            },
        ))

    return events, summary


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


def gen_protagonist(
    provider,
    oracle: Oracle,
    frame: dict,
    local_map: dict,
    *,
    provided=None,
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

    user = (
        f"世界名称：{frame.get('world_name', '未名之地')}\n"
        f"世界基调：{frame.get('tone', '冒险')}\n"
        f"核心冲突：{frame.get('central_conflict', '未知冲突')}\n"
        f"起始小镇：{town_name}，起始场所：{first_venue_name}\n"
        f"镇内所有场所：{all_venue_names}\n\n"
        f"请为这个世界创作一位主角，以纯 JSON 对象返回，"
        f"不含 Markdown 代码块、不含任何额外说明。\n"
        f"对象 MUST 含有 EXACTLY 下列四个字段（不多不少）：\n"
        f"  \"name\"      — 符合世界风格的主角姓名（中文字符串，非空）\n"
        f"  \"origin\"    — 主角的身世背景，1-3句话（中文字符串，非空）\n"
        f"  \"goal\"      — 驱动主角行动的核心目标（中文字符串，非空）\n"
        f"  \"objective\" — 主角当前具体的任务或行动目标，即「我现在正在做什么」（中文字符串，非空）；"
        f"用地点的名字（如「{first_venue_name}」「{town_name}」）指代地点，"
        f"绝不要在面向玩家的文本里出现 town_0 / venue_0 这类内部 id\n"
        f"示例：{{\"name\": \"沈云舟\", \"origin\": \"出身江南小镇的落魄秀才，父亲死于一场离奇大火。\","
        f" \"goal\": \"查明父亲死因，为家族翻案\","
        f" \"objective\": \"前往{town_name}的{first_venue_name}寻找据说见过那场大火的老掌柜\"}}"
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
    role_entries = _draw_distinct(oracle, load_table("npc_roles", "genesis"), n)
    rolled_roles = [e["name"] for e in role_entries] or ["旅人"]
    roles = [rolled_roles[i % len(rolled_roles)] for i in range(n)]

    # Draw 2 traits per NPC (distinct within each NPC's draw)
    traits_table = load_table("npc_traits", "genesis")
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


def gen_threads(
    provider,
    oracle: Oracle,
    frame: dict,
    local_map: dict,
    protagonist: str,
    *,
    provided=None,
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
                                  provided=provided)
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
    type_entries = _draw_distinct(oracle, load_table("thread_types", "genesis"), n)
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


# ---------------------------------------------------------------------------
# bootstrap_world — Task 9: orchestrator (steps 1-9) + reroll helpers
# ---------------------------------------------------------------------------

def bootstrap_world(engine, pitch: str = "", *, spec=None, attempt: int = 0, progress=None) -> dict:
    """Run all generation steps + protagonist creation; return a rich result dict.

    Orchestration order (all genesis events: turn=0, day=1, scene="genesis"):
        1. campaign_seeded
        2. gen_frame
        3. gen_regions
        4. gen_local_map
        5. gen_protagonist  (authored protagonist to fit world)
        6. protagonist character_created + fact_asserted events + entity_moved
        7. gen_factions
        8. gen_npcs
        9. gen_threads  → create_lore_line per skeleton
        10. gen_opening
        then project

    Args:
        engine:   The wired engine.
        pitch:    The world background keyword string.
        attempt:  Reroll attempt counter (0 for first run).
        progress: Optional callback(step_index, total_steps, label) called before
                  each generation step. Default None → no-op (existing callers unaffected).

    Returns:
        {
          "summary": {...display fields...},
          "_state": {frame, regions_summary, local_map, factions_summary, protagonist,
                     protagonist_authored, pitch, attempts:{step:attempt}},
          "_boundaries": {step: first_seq_of_that_step},
        }

    Never raises — generators already fall back to stubs.
    """
    from kernel.projection import project as _project
    from loop.lore import create_lore_line
    from app.engine import _PROTAGONIST_ID

    store = engine.store
    provider = engine.provider
    campaign_seed = engine.campaign_seed

    # Resolve the spec: normalize, then seed world_premise.genre from `pitch`
    # when the spec does not already set it. With spec=None + a pitch this is
    # byte-identical to the legacy pitch-only path.
    from loop.genesis_spec import normalize as _normalize_spec
    spec = _normalize_spec(spec)
    wp = dict(spec.get("world_premise") or {})
    if not wp.get("genre") and pitch:
        wp["genre"] = pitch
    if wp:
        spec = {**spec, "world_premise": wp}
    genre = wp.get("genre", pitch)

    boundaries: dict[str, int] = {}

    def _seed(step: str) -> Oracle:
        return Oracle(scene_seed(campaign_seed, f"genesis:{step}", attempt))

    def _progress(idx: int, total: int, label: str) -> None:
        if progress is not None:
            try:
                progress(idx, total, label)
            except Exception:
                pass  # progress callback errors must never abort genesis

    _TOTAL_STEPS = 8

    with get_tracer().span("genesis"):
        # -----------------------------------------------------------------------
        # Step 1: campaign_seeded (mirrors new_game)
        # -----------------------------------------------------------------------
        ev_seed = kernel_event(
            "campaign_seeded",
            turn=0, day=1, scene="genesis",
            summary=f"campaign seed = {campaign_seed}",
            deltas={"campaign_seed": campaign_seed},
        )
        boundaries["campaign_seeded"] = store.append(ev_seed)

        # -----------------------------------------------------------------------
        # Step 2: gen_frame
        # -----------------------------------------------------------------------
        _progress(1, _TOTAL_STEPS, "世界框架")
        with get_tracer().span("gen_frame", step="frame"):
            frame_evs, frame = gen_frame(provider, _seed("frame"), genre,
                                         provided=spec.get("world_premise"))
        boundaries["frame"] = store.append(frame_evs[0])
        for ev in frame_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Step 3: gen_regions
        # -----------------------------------------------------------------------
        _progress(2, _TOTAL_STEPS, "宏观区域")
        region_evs, regions_summary = gen_regions(provider, _seed("regions"), frame,
                                                   provided=spec.get("regions"))
        boundaries["regions"] = store.append(region_evs[0])
        for ev in region_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Step 4: gen_local_map
        # -----------------------------------------------------------------------
        _progress(3, _TOTAL_STEPS, "本地地图")
        local_map_evs, local_map = gen_local_map(provider, _seed("local_map"), frame, regions_summary,
                                                  provided=spec.get("local_map"))
        boundaries["local_map"] = store.append(local_map_evs[0])
        for ev in local_map_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Step 5: gen_protagonist — author protagonist to fit the world
        # -----------------------------------------------------------------------
        _progress(4, _TOTAL_STEPS, "主角")
        _, protagonist_authored = gen_protagonist(provider, _seed("protagonist"), frame, local_map,
                                                  provided=spec.get("protagonist"))

        # -----------------------------------------------------------------------
        # Step 5b: create protagonist (tracked) + public facts + move to first venue
        # -----------------------------------------------------------------------
        protagonist = _PROTAGONIST_ID
        first_venue = local_map["venues"][0]

        ev_char = kernel_event(
            "character_created",
            turn=0, day=1, scene="genesis",
            summary=f"{protagonist} 主角登场（{protagonist_authored['name']}）",
            deltas={
                "id": protagonist,
                "tier": "tracked",
                "sketch": protagonist_authored["origin"],
                "goal": protagonist_authored["goal"],
            },
        )
        boundaries["protagonist"] = store.append(ev_char)

        # fact_asserted for name (公开 — DM 和主角都知道自己叫什么)
        store.append(kernel_event(
            "fact_asserted",
            turn=0, day=1, scene="genesis",
            summary=f"{protagonist} 名字：{protagonist_authored['name']}",
            deltas={
                "subject": protagonist,
                "predicate": "真名",
                "value": protagonist_authored["name"],
                "secrecy": "public",
            },
        ))

        # fact_asserted for starting objective (公开 — player knows what to do)
        store.append(kernel_event(
            "fact_asserted",
            turn=0, day=1, scene="genesis",
            summary=f"{protagonist} 当前目标：{protagonist_authored['objective']}",
            deltas={
                "subject": protagonist,
                "predicate": "目标",
                "value": protagonist_authored["objective"],
                "secrecy": "public",
            },
        ))

        ev_move = kernel_event(
            "entity_moved",
            turn=0, day=1, scene="genesis",
            summary=f"{protagonist} 抵达 {first_venue}",
            deltas={"who": protagonist, "to": first_venue},
        )
        store.append(ev_move)

        # -----------------------------------------------------------------------
        # Step 6: gen_factions
        # -----------------------------------------------------------------------
        _progress(5, _TOTAL_STEPS, "势力")
        faction_evs, factions_summary = gen_factions(provider, _seed("factions"), frame, regions_summary,
                                                      provided=spec.get("factions"))
        boundaries["factions"] = store.append(faction_evs[0])
        for ev in faction_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Step 7: gen_npcs
        # -----------------------------------------------------------------------
        _progress(6, _TOTAL_STEPS, "NPC")
        npc_evs, npcs_summary = gen_npcs(provider, _seed("npcs"), frame, local_map, factions_summary,
                                         provided=spec.get("npcs"))
        boundaries["npcs"] = store.append(npc_evs[0])
        for ev in npc_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Step 8: gen_threads → create_lore_line per skeleton
        # -----------------------------------------------------------------------
        _progress(7, _TOTAL_STEPS, "暗线")
        with get_tracer().span("gen_threads", step="threads"):
            skeletons, threads_summary = gen_threads(
                provider, _seed("threads"), frame, local_map, protagonist,
                provided=spec.get("threads")
            )
        # Append each skeleton via create_lore_line; boundary recovered via SQL below.
        for sk in skeletons:
            create_lore_line(store, sk, day=1, scene="genesis", turn=0)
        boundaries["threads"] = _find_first_lore_seq(store, skeletons)

        # -----------------------------------------------------------------------
        # Step 9: gen_opening
        # -----------------------------------------------------------------------
        _progress(8, _TOTAL_STEPS, "开场")
        with get_tracer().span("gen_opening", step="opening"):
            world_summary = _build_world_summary(frame, regions_summary, local_map, npcs_summary, threads_summary)
            # Resolve the first venue's human-readable name so the prompt never shows an id
            first_venue_name = local_map.get("venue_names", {}).get(first_venue, first_venue)
            opening_evs, narration = gen_opening(
                provider, frame, world_summary,
                scene_loc=first_venue, scene_loc_name=first_venue_name,
                provided=spec.get("opening"),
            )
        boundaries["opening"] = store.append(opening_evs[0])
        for ev in opening_evs[1:]:
            store.append(ev)

        # -----------------------------------------------------------------------
        # Project world
        # -----------------------------------------------------------------------
        engine.world = _project(engine.registry, store.iter_events())

    # -----------------------------------------------------------------------
    # Build result
    # -----------------------------------------------------------------------
    n_lore = len(threads_summary.get("threads", []))
    n_factions_actual = len(factions_summary.get("factions", []))
    n_npcs_actual = len(npcs_summary.get("npcs", []))

    summary = {
        "world_name": frame["world_name"],
        "tone": frame["tone"],
        "central_conflict": frame["central_conflict"],
        "n_regions": frame["n_regions"],
        "n_factions": n_factions_actual,
        "n_npcs": n_npcs_actual,
        "n_lore": n_lore,
        "narration_excerpt": narration[:120] if narration else "",
        "protagonist_name": protagonist_authored["name"],
        "protagonist_origin": protagonist_authored["origin"],
        "protagonist_goal": protagonist_authored["goal"],
        "objective": protagonist_authored["objective"],
    }

    return {
        "summary": summary,
        "_state": {
            "frame": frame,
            "regions_summary": regions_summary,
            "local_map": local_map,
            "factions_summary": factions_summary,
            "npcs_summary": npcs_summary,
            "threads_summary": threads_summary,
            "protagonist": protagonist,
            "protagonist_authored": protagonist_authored,
            "pitch": pitch,
            "spec": spec,
            "attempts": {step: attempt for step in boundaries},
        },
        "_boundaries": boundaries,
    }


def _find_first_lore_seq(store, skeletons: list[dict]) -> int:
    """Return the seq of the first lore_created event (for the thread boundaries).

    create_lore_line does not expose the seq via its return value, so we fall back
    to a direct SQL query on the underlying SQLite connection (store._conn).
    """
    if not skeletons:
        return _read_last_seq(store) or 1
    # Direct SQL: cheapest way to find the first lore_created seq without
    # intercepting create_lore_line's internal store.append call.
    conn = store._conn
    row = conn.execute(
        "SELECT seq FROM events WHERE type='lore_created' AND retracted=0 ORDER BY seq ASC LIMIT 1"
    ).fetchone()
    if row:
        return row[0]
    return _read_last_seq(store) or 1


def _read_last_seq(store) -> int | None:
    """Return the highest seq in the store (non-retracted), or None if empty."""
    conn = store._conn
    row = conn.execute(
        "SELECT MAX(seq) FROM events WHERE retracted=0"
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def _build_world_summary(frame: dict, regions_summary: dict, local_map: dict,
                          npcs_summary: dict, threads_summary: dict) -> str:
    """Build a compact world summary string for gen_opening's world_summary arg."""
    region_names = ", ".join(r["name"] for r in regions_summary.get("regions", []))

    # Resolve start town NAME from l2 list (not raw id)
    start_town_id = local_map.get("start_town", "town_0")
    town_name = start_town_id
    for entry in local_map.get("l2", []):
        if entry.get("id") == start_town_id:
            town_name = entry.get("name", start_town_id)
            break

    # Use venue NAMES (not ids) for the world summary
    venue_ids = local_map.get("venues", [])
    venue_names_map = local_map.get("venue_names", {})
    venue_display = ", ".join(
        venue_names_map.get(vid, vid) for vid in venue_ids
    )

    npc_sketches = ", ".join(n["sketch"] for n in npcs_summary.get("npcs", []))
    thread_abouts = "; ".join(
        t["about"] for t in threads_summary.get("threads", [])
    )
    return (
        f"世界名称：{frame['world_name']}\n"
        f"世界基调：{frame['tone']}\n"
        f"核心冲突：{frame['central_conflict']}\n"
        f"大区域：{region_names}\n"
        f"起始小镇：{town_name}，场所：{venue_display}\n"
        f"开场NPC：{npc_sketches}\n"
        f"暗线：{thread_abouts}"
    )


def reroll_all(engine, prev_result: dict, *, progress=None) -> dict:
    """Retract all genesis events and run a fresh bootstrap_world.

    The previous attempt's overall counter is bumped by 1.
    """
    # Determine previous attempt number (use the 'frame' step attempt as proxy)
    prev_attempts = prev_result.get("_state", {}).get("attempts", {})
    prev_attempt = prev_attempts.get("frame", 0)
    new_attempt = prev_attempt + 1

    # Retract all turn-0 events
    engine.store.retract_from_turn(0)

    pitch = prev_result["_state"]["pitch"]
    prev_spec = prev_result.get("_state", {}).get("spec")
    return bootstrap_world(engine, pitch, spec=prev_spec, attempt=new_attempt, progress=progress)


def reroll_step(engine, prev_result: dict, step: str, *, progress=None) -> dict:
    """Retract from step's boundary and re-run from that step to end.

    Only leaf steps are supported: 'factions', 'npcs', 'threads'.
    For map/region reroll, callers should use reroll_all.

    Preserves upstream summaries from prev_result._state.
    """
    from kernel.projection import project as _project
    from loop.lore import create_lore_line
    from app.engine import _PROTAGONIST_ID

    _LEAF_STEPS = {"factions", "npcs", "threads"}
    if step not in _LEAF_STEPS:
        raise ValueError(f"reroll_step: '{step}' is not a leaf step; use reroll_all for map/region reroll")

    boundaries = prev_result["_boundaries"]
    state = prev_result["_state"]
    frame = state["frame"]
    regions_summary = state["regions_summary"]
    local_map = state["local_map"]
    protagonist = state["protagonist"]
    protagonist_authored = state.get("protagonist_authored", {
        "name": "无名旅者",
        "origin": "来历不明的旅人，只知道自己踏上了这条路。",
        "goal": "找到属于自己的答案",
        "objective": "在起始小镇打听线索，寻找下一步的方向",
    })
    pitch = state["pitch"]
    spec = state.get("spec") or {}
    campaign_seed = engine.campaign_seed
    store = engine.store
    provider = engine.provider

    # Bump this step's attempt; downstream steps keep their own prior attempt counters
    prev_attempts = state.get("attempts", {})
    step_attempt = prev_attempts.get(step, 0) + 1

    def _seed(s: str) -> Oracle:
        # The retracted step uses its newly bumped attempt; every downstream step
        # that was NOT retracted uses its own unchanged prior attempt counter so
        # that npcs/threads oracle rolls are a function of (step, its own attempt)
        # and are not aliased to the triggering step's attempt trajectory.
        if s == step:
            sa = step_attempt
        else:
            sa = prev_attempts.get(s, 0)
        return Oracle(scene_seed(campaign_seed, f"genesis:{s}", sa))

    # Retract from this step's boundary (drops step + everything after)
    store.retract_from_seq(boundaries[step])

    new_boundaries = dict(boundaries)
    new_state = dict(state)

    # Re-run factions if needed
    if step == "factions":
        faction_evs, factions_summary = gen_factions(provider, _seed("factions"), frame, regions_summary,
                                                      provided=spec.get("factions"))
        new_boundaries["factions"] = store.append(faction_evs[0])
        for ev in faction_evs[1:]:
            store.append(ev)
        new_state["factions_summary"] = factions_summary
    else:
        factions_summary = state["factions_summary"]

    # Re-run npcs if needed
    if step in ("factions", "npcs"):
        npc_evs, npcs_summary = gen_npcs(provider, _seed("npcs"), frame, local_map, factions_summary,
                                         provided=spec.get("npcs"))
        new_boundaries["npcs"] = store.append(npc_evs[0])
        for ev in npc_evs[1:]:
            store.append(ev)
        new_state["npcs_summary"] = npcs_summary
    else:
        npcs_summary = state.get("npcs_summary", {"npcs": []})

    # Re-run threads (always, since we retract from factions/npcs/threads boundary)
    skeletons, threads_summary = gen_threads(
        provider, _seed("threads"), frame, local_map, protagonist,
        provided=spec.get("threads")
    )
    for sk in skeletons:
        create_lore_line(store, sk, day=1, scene="genesis", turn=0)
    new_boundaries["threads"] = _find_first_lore_seq(store, skeletons)
    new_state["threads_summary"] = threads_summary

    # Re-run opening
    world_summary = _build_world_summary(frame, regions_summary, local_map, npcs_summary, threads_summary)
    first_venue = local_map["venues"][0]
    first_venue_name = local_map.get("venue_names", {}).get(first_venue, first_venue)
    opening_evs, narration = gen_opening(
        provider, frame, world_summary,
        scene_loc=first_venue, scene_loc_name=first_venue_name,
    )
    new_boundaries["opening"] = store.append(opening_evs[0])
    for ev in opening_evs[1:]:
        store.append(ev)

    # Update attempts
    new_attempts = dict(prev_attempts)
    new_attempts[step] = step_attempt

    # Project
    engine.world = _project(engine.registry, store.iter_events())

    # Build fresh result
    n_lore = len(new_state.get("threads_summary", {}).get("threads", []))
    n_factions_actual = len(new_state.get("factions_summary", {}).get("factions", []))
    n_npcs_actual = len(new_state.get("npcs_summary", {}).get("npcs", []))

    summary = {
        "world_name": frame["world_name"],
        "tone": frame["tone"],
        "central_conflict": frame["central_conflict"],
        "n_regions": frame["n_regions"],
        "n_factions": n_factions_actual,
        "n_npcs": n_npcs_actual,
        "n_lore": n_lore,
        "narration_excerpt": narration[:120] if narration else "",
        "protagonist_name": protagonist_authored["name"],
        "protagonist_origin": protagonist_authored["origin"],
        "protagonist_goal": protagonist_authored["goal"],
        "objective": protagonist_authored["objective"],
    }

    new_state["attempts"] = new_attempts
    new_state["pitch"] = pitch
    new_state["protagonist_authored"] = protagonist_authored

    return {
        "summary": summary,
        "_state": new_state,
        "_boundaries": new_boundaries,
    }


def _gen_threads_fallback(
    oracle: Oracle,
    local_map: dict,
    protagonist: str,
) -> tuple[list[dict], dict]:
    """Deterministic stub fallback: emit >= 3 valid skeletons without LLM."""
    venues = list(local_map["venues"])
    start_town = local_map["start_town"]
    skeletons: list[dict] = []
    summary_threads: list[dict] = []

    # 3 campaign threads (minimum)
    for i in range(3):
        thread_id = f"thread_{i}"
        complexity = "medium"
        threshold = 50
        stage_count = _STAGE_COUNT[complexity]
        sk = _stub_thread_skeleton(
            thread_id, complexity, start_town, threshold, venues, stage_count, i
        )
        skeletons.append(sk)
        summary_threads.append({
            "id": thread_id,
            "type": "阴谋",
            "complexity": complexity,
            "anchor": start_town,
            "about": sk["about"],
        })

    # 1 protagonist-bound thread
    sk = _stub_thread_skeleton(
        "pthread_0", "medium", protagonist, 50, venues, _STAGE_COUNT["medium"], 0
    )
    skeletons.append(sk)
    summary_threads.append({
        "id": "pthread_0",
        "type": "protagonist",
        "complexity": "medium",
        "anchor": protagonist,
        "about": sk["about"],
    })

    summary: dict = {"threads": summary_threads}
    return skeletons, summary
