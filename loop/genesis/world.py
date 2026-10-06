"""loop.genesis.world — gen_frame / gen_regions / gen_local_map."""
from __future__ import annotations

from engine.oracle import Oracle, scene_seed, load_table, load_pack_manifest  # noqa: F401
from engine.log import get_logger
from kernel.events import kernel_event  # noqa: F401
from kernel.observability import get_tracer  # noqa: F401
from llm.structured import complete_structured  # noqa: F401

log = get_logger("loop.genesis")
from loop.genesis.common import _draw_distinct, _empty_str  # noqa: F401

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


def _roll_world_seeds(oracle, provided: dict, flavor: str = "classic") -> dict:
    """Distinct-draw world dimension seeds (I6-P2): magic system, power ladder, and
    the world's central tension. Always rolls (preserves oracle draw order);
    provided overrides. Give the world substance + seed the Codex (P3)."""
    magic_roll = oracle.draw(load_table("world_magic", flavor, base="classic"))["name"]
    power_roll = oracle.draw(load_table("world_power", flavor, base="classic"))["name"]
    tension_roll = oracle.draw(load_table("world_tension", flavor, base="classic"))["name"]
    p = provided or {}
    return {
        "magic_system": p.get("magic_system") or magic_roll,
        "power_ladder": p.get("power_ladder") or power_roll,
        "world_tension": p.get("world_tension") or tension_roll,
    }


# The pitch's stated mood biases the tone draw so a player's "轻松明亮/王道" world is
# not overridden by a grim dice roll (both play6 and play7 rolled 生存 despite bright
# pitches). The bright/dark tone sets + pitch-mood hints live in the active flavor
# pack's manifest (pack.json.tone), so each pack defines its own tone universe. A
# mood-neutral / self-contradictory pitch (or a pack with no tone config) draws from
# the full table, preserving anti-convergence. draw() consumes exactly one roll
# regardless of subset, so downstream rolls stay deterministic.
def _tone_for_pitch(oracle, pitch: str, table: list[dict], tone_cfg: dict) -> str:
    """Draw a tone biased toward the pitch's mood, using the pack's tone config.

    tone_cfg keys: bright/dark (tone-name lists), hints_bright/hints_dark (pitch
    keyword lists). Empty/missing config → full-table draw."""
    cfg = tone_cfg or {}
    bright_tones = set(cfg.get("bright") or [])
    dark_tones = set(cfg.get("dark") or [])
    text = pitch or ""
    bright = any(h in text for h in (cfg.get("hints_bright") or []))
    dark = any(h in text for h in (cfg.get("hints_dark") or []))
    if bright and not dark and bright_tones:
        subset = [e for e in table if e["name"] in bright_tones]
    elif dark and not bright and dark_tones:
        subset = [e for e in table if e["name"] in dark_tones]
    else:
        subset = table
    return oracle.draw(subset or table)["name"]


def gen_frame(
    provider,
    oracle: Oracle,
    pitch: str,
    *,
    provided=None,
    flavor: str = "classic",
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

    _tone_cfg = load_pack_manifest(flavor).get("tone") or {}
    tone_roll = _tone_for_pitch(oracle, pitch, load_table("tone_axes", flavor, base="classic"), _tone_cfg)
    nf_roll = oracle.randint(3, 5)
    nr_roll = oracle.randint(3, 5)
    # A provided empty / 0 falls back to the roll (0 regions/factions is nonsensical).
    tone = provided.get("tone") or tone_roll
    n_factions = provided.get("n_factions") or nf_roll
    n_regions = provided.get("n_regions") or nr_roll
    genre = provided.get("genre") or pitch
    world_seeds = _roll_world_seeds(oracle, provided, flavor)

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
            f"已由引擎掷出的世界基调（tone）：{tone}\n"
            f"已掷出的世界维度（务必融入命名与核心冲突，作为世界底色）："
            f"超能体系={world_seeds['magic_system']}；"
            f"实力范式={world_seeds['power_ladder']}；"
            f"主轴张力={world_seeds['world_tension']}\n\n"
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
        "magic_system": world_seeds["magic_system"],
        "power_ladder": world_seeds["power_ladder"],
        "world_tension": world_seeds["world_tension"],
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
    # genre/tone are public surface info; central_conflict is the DEEP TRUTH the
    # player should DISCOVER through play, so it's a DM-only secret fact (#R2) —
    # it still informs every downstream generator via `frame`, but the fog tiers
    # (ambient/passerby) can never relay it and the intro never shows it.
    for predicate, value, secrecy in (
        ("genre", genre, "public"),
        ("tone", tone, "public"),
        ("magic_system", world_seeds["magic_system"], "public"),
        ("power_ladder", world_seeds["power_ladder"], "public"),
        ("world_tension", world_seeds["world_tension"], "public"),
        ("central_conflict", central_conflict, "secret"),
    ):
        events.append(kernel_event(
            "fact_asserted",
            turn=0, day=1, scene="genesis",
            summary=f"世界属性：{predicate}={value}",
            deltas={
                "subject": "world",
                "predicate": predicate,
                "value": value,
                "secrecy": secrecy,
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
    flavor: str = "classic",
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
    terrain_entries = _draw_distinct(oracle, load_table("terrains", flavor, base="classic"), n)
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
    flavor: str = "classic",
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
    neighbor_kind_entries = _draw_distinct(oracle, load_table("place_kinds", flavor, base="classic"), n_extra_l2)
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
