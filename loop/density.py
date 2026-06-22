"""loop.density — density resolution, per-tier caps, complexity roll,
                   LLM batch generation, and the run_density orchestrator.

Task 1 (pure logic, no I/O):
    DENSITY_DEFAULT, CAP_SIMPLE, CAP_MEDIUM, CAP_COMPLEX
    resolve_density, region_scope, count_tier,
    roll_complexity

Task 2 (LLM batch generation):
    GEN_THRESHOLD = 50
    generate_lore_batch(provider, *, town_id, kind, flavor, venues,
                        existing_abouts, specs) -> list[dict]

Task 3 (orchestrator):
    BASE = 10, REFRESH_INTERVAL_DAYS = 3
    STAGE_COUNT = {"simple": 2, "medium": 3, "complex": 5}
    run_density(registry, store, world, protagonist, *, provider,
                day, scene, turn) -> list[dict]

All IDs are deterministic (hashlib sha256, no random/time). All numeric
values are engine-decided; the model only writes story content.
"""
from __future__ import annotations

import hashlib

from engine.log import get_logger
from engine.oracle import Oracle, scene_seed
from kernel.events import kernel_event
from llm.provider import _parse_json_object
from loop.lore import create_lore_line
from loop.graph_utils import ancestor_of_level as _ancestor_of_level

log = get_logger("loop.density")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DENSITY_DEFAULT: float = 0.3
CAP_SIMPLE:  int = 15
CAP_MEDIUM:  int = 8
CAP_COMPLEX: int = 2


# ---------------------------------------------------------------------------
# resolve_density
# ---------------------------------------------------------------------------

def resolve_density(world: dict, town_id: str, day: int) -> float:
    """Return the density float for a town.

    Walks from town_id up contained_by edges to find the L1 ancestor. If that
    ancestor has a 'density' attr, returns it. Otherwise returns DENSITY_DEFAULT.
    """
    g = world["systems"]["ontology"]
    l1_id = _ancestor_of_level(g, town_id, day, level=1)
    if l1_id is None:
        return DENSITY_DEFAULT
    e = g.get_entity(l1_id)
    if e is None:
        return DENSITY_DEFAULT
    density = e.attrs.get("density")
    # density may be absent or falsy (e.g. 0.0 is still valid)
    if density is None:
        return DENSITY_DEFAULT
    return float(density)


# ---------------------------------------------------------------------------
# region_scope
# ---------------------------------------------------------------------------

def region_scope(world: dict, town_id: str, day: int) -> str:
    """Return the L1 region id that contains town_id, or town_id itself if none.

    Used as the scope for complex-line cap counting.
    """
    g = world["systems"]["ontology"]
    l1_id = _ancestor_of_level(g, town_id, day, level=1)
    return l1_id if l1_id is not None else town_id


# ---------------------------------------------------------------------------
# count_tier
# ---------------------------------------------------------------------------

def count_tier(world: dict, scope_id: str, complexity: str) -> int:
    """Count active, unresolved lore lines of the given complexity.

    Counting rules:
        simple / medium  — scope_id is a TOWN; match lines whose anchor==scope_id.
        complex          — scope_id is a REGION; match lines whose
                           region_scope(anchor) == scope_id.

    Only lines with state in (暗,明) are counted (了结 lines free their cap slot).
    """
    lines = world.get("systems", {}).get("lore", {}).get("lines", {})
    day = world.get("meta", {}).get("day") or 1
    count = 0

    for ln in lines.values():
        # Only un-resolved lines occupy cap slots
        if ln.get("state") not in ("暗", "明"):
            continue
        if ln.get("complexity") != complexity:
            continue

        if complexity == "complex":
            # region-level cap: compare the line's anchor's region to scope_id
            anchor = ln.get("anchor")
            if anchor is None:
                continue
            ln_region = region_scope(world, anchor, day)
            if ln_region == scope_id:
                count += 1
        else:
            # simple / medium: per-town cap
            if ln.get("anchor") == scope_id:
                count += 1

    return count


# ---------------------------------------------------------------------------
# roll_complexity
# ---------------------------------------------------------------------------

def roll_complexity(oracle, world: dict, town_id: str, region_id: str) -> str | None:
    """Roll a d100 and return complexity string after applying cap downgrades.

    Tier mapping:
        1–70  → "simple"
        71–95 → "medium"
        96–100→ "complex"

    Downgrade cascade:
        complex but region_id has CAP_COMPLEX complex lines → downgrade to "medium"
        medium  but town_id  has CAP_MEDIUM  medium lines  → downgrade to "simple"
        simple  but town_id  has CAP_SIMPLE  simple lines  → return None (slot skipped)
    """
    r = oracle.d100()

    # Map roll to initial tier
    if r <= 70:
        tier = "simple"
    elif r <= 95:
        tier = "medium"
    else:
        tier = "complex"

    # Downgrade cascade
    if tier == "complex":
        if count_tier(world, region_id, "complex") >= CAP_COMPLEX:
            tier = "medium"

    if tier == "medium":
        if count_tier(world, town_id, "medium") >= CAP_MEDIUM:
            tier = "simple"

    if tier == "simple":
        if count_tier(world, town_id, "simple") >= CAP_SIMPLE:
            return None

    return tier


# ---------------------------------------------------------------------------
# Task 2: LLM batch generation
# ---------------------------------------------------------------------------

# Default threshold for every generated lore line (engine-decided, not model)
GEN_THRESHOLD: int = 50

# Model-written string fields that MUST be present and non-empty in every line.
_REQUIRED_STR_FIELDS = ("about", "description", "trigger", "secret", "l3_anchor")

# Max self-correction rounds. Mirrors the engine's commit validation-repair loop:
# the model sees a precise error naming the exact missing/wrong fields and re-emits.
GEN_MAX_REPAIRS: int = 2


def _validate_gen_lines(lines, n: int, venues: list[str]) -> tuple[dict, list[str]]:
    """Strictly validate the model's 'lines' against the required schema.

    Returns ``(valid_by_index, errors)``: ``valid_by_index`` maps a 0-based line
    index to the raw line dict that passed; ``errors`` is a list of human-readable
    problems that NAME the exact missing/wrong fields, fed back to the model so it
    can self-correct (no lenient guessing — the harness enforces the contract).
    """
    if not isinstance(lines, list):
        return {}, ['The response must be a JSON object {"lines": [...]} whose '
                    '"lines" value is a JSON array.']
    errors: list[str] = []
    valid: dict[int, dict] = {}
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
        for f in _REQUIRED_STR_FIELDS:
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
        else:
            valid[i] = ln
    return valid, errors


def _build_gen_repair(errors: list[str]) -> str:
    """Build the repair turn fed back to the model — names every problem found."""
    return (
        "Your previous JSON did NOT conform to the required schema and was REJECTED "
        "by the game engine. Fix ALL of the following and return the COMPLETE "
        "corrected JSON object (every line), with NO markdown fences and NO commentary:\n"
        + "\n".join(f"  - {e}" for e in errors)
        + '\nReminder: every line object needs EXACTLY these keys — "about", '
        '"description", "trigger", "secret", "l3_anchor", "stages"; each stage is '
        'exactly {"hint": "..."}; add no other keys.'
    )


def _make_skeleton_id(town_id: str, about: str, idx: int) -> str:
    """Return a deterministic, rewind-safe id for a generated lore line.

    Format: ``gen_<town_id>_<sha256[:8]>`` where the hash input is
    town_id + about + str(idx).  No random or time calls — safe to replay.
    """
    raw = (town_id + about + str(idx)).encode()
    short = hashlib.sha256(raw).hexdigest()[:8]
    return f"gen_{town_id}_{short}"


def generate_lore_batch(
    provider,
    *,
    town_id: str,
    kind: str,
    flavor: str,
    venues: list[str],
    existing_abouts: list[str],
    specs: list[dict],
    max_repairs: int = GEN_MAX_REPAIRS,
) -> list[dict]:
    """Generate 暗-line quest skeletons via a strict, self-correcting LLM loop.

    The model authors the STORY fields; the engine fills the mechanical ones. The
    harness ENFORCES the schema rather than guessing at synonyms: each round the
    model's JSON is validated, and any line with a missing/wrong field triggers a
    repair turn that NAMES the exact problems (mirroring the engine's commit
    validation-repair loop) so the model re-emits a conforming object. After
    ``max_repairs`` rounds, only still-conforming lines are kept.

    Engine-decided values (NOT written by the model):
        complexity (from each spec), anchor (town_id), threshold (GEN_THRESHOLD),
        id (deterministic sha256 — no random/time).

    Model-written values (required, validated, repaired if missing/malformed):
        about, description, trigger, secret, l3_anchor (∈ venues), stages[{hint}].

    Fault tolerance: provider None → []; complete_messages raises → []; a response
    that never conforms after repairs → only the conforming lines (possibly []).
    NEVER raises out of this function.
    """
    if provider is None:
        return []
    if not specs:
        return []

    # ------------------------------------------------------------------
    # Build prompt
    # ------------------------------------------------------------------
    # Filter out malformed spec entries before prompt building so a bad spec
    # never raises; per-skeleton loop also guards with try/except for safety.
    valid_spec_indices = [
        i for i, s in enumerate(specs)
        if isinstance(s, dict) and "complexity" in s and "stage_count" in s
    ]
    valid_specs = [specs[i] for i in valid_spec_indices]
    if not valid_specs:
        return []

    n = len(valid_specs)
    # Only stage_count is exposed — complexity is engine-only; showing it tempted
    # the model to echo a 'complexity' key back instead of writing story fields.
    spec_lines = "\n".join(
        f"  line {i + 1}: stages={s['stage_count']}"
        for i, s in enumerate(valid_specs)
    )
    venue_str = ", ".join(venues) if venues else "(none specified)"
    avoid_str = (
        "  " + "\n  ".join(f"- {a}" for a in existing_abouts)
        if existing_abouts
        else "  (none)"
    )

    system = (
        "You are a TRPG world-building assistant generating hidden quest skeletons (暗线). "
        "You MUST return ONLY a JSON object that conforms EXACTLY to the field "
        "specification below — the game engine parses it programmatically and REJECTS "
        "any deviation (missing keys, extra keys, or wrong key names). Write all story "
        "text in Chinese."
    )

    eg_venue = venues[0] if venues else "码头"
    user = (
        f"Town: {town_id} (kind={kind})\n"
        f"Flavor / atmosphere: {flavor}\n"
        f"L3 venues in this town — l3_anchor MUST be EXACTLY one of these: {venue_str}\n"
        f"Existing quest themes to AVOID duplicating:\n{avoid_str}\n\n"
        f"Generate exactly {n} hidden quest skeleton(s), each thematically distinct and fitting "
        f"the town's flavor. Write all text values in Chinese.\n"
        f"The {n} line(s), IN THIS ORDER, must have these stage counts:\n{spec_lines}\n\n"
        f"Return ONLY a JSON object of the form {{\"lines\": [ ...{n} objects... ]}}.\n"
        f"Each line object MUST have EXACTLY these keys (no others):\n"
        f"  \"about\"       — one-line surface hook: what visibly seems off (string)\n"
        f"  \"description\" — one-line player-facing index entry (string)\n"
        f"  \"trigger\"     — what player action would naturally surface this (string)\n"
        f"  \"secret\"      — the hidden truth behind it, not yet shown to the player (string)\n"
        f"  \"l3_anchor\"   — which venue the clue physically lives in, one of [{venue_str}] (string)\n"
        f"  \"stages\"      — array of stage objects, EACH exactly {{\"hint\": \"<one progressive clue line>\"}}\n"
        f"Do NOT include \"complexity\", \"stage_count\", \"title\", \"theme\", or any other key. "
        f"Each stage MUST use the key \"hint\" (not \"hook\"/\"resolution\"/etc.).\n"
        f"Example of ONE line object:\n"
        f"{{\"about\": \"夜里码头总有人影搬运不明货箱\", \"description\": \"码头的夜间走私传闻\", "
        f"\"trigger\": \"玩家夜里留意码头或盘问搬运工\", \"secret\": \"会馆私运违禁盐引\", "
        f"\"l3_anchor\": \"{eg_venue}\", \"stages\": [{{\"hint\": \"入夜后码头有可疑灯火\"}}, "
        f"{{\"hint\": \"搬运工对货箱讳莫如深\"}}]}}"
    )

    # ------------------------------------------------------------------
    # Strict validation-repair loop (mirrors the engine's commit repair):
    # call → validate → if any line breaks the schema, feed back a precise
    # error NAMING the missing/wrong fields → the model re-emits. Keep only
    # the lines that conform after up to `max_repairs` rounds.
    # ------------------------------------------------------------------
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    valid: dict[int, dict] = {}
    for attempt in range(max_repairs + 1):
        try:
            raw = provider.complete_messages(messages)
        except Exception:
            log.exception("generate_lore_batch: complete_messages failed (attempt %d)", attempt)
            break
        parsed = _parse_json_object(raw)
        if isinstance(parsed, dict):
            lines = parsed.get("lines")
        elif isinstance(parsed, list):
            lines = parsed
        else:
            lines = None
        valid, errs = _validate_gen_lines(lines, n, venues)
        if not errs:
            break
        log.debug("generate_lore_batch: attempt %d had %d schema error(s) for town=%s",
                  attempt, len(errs), town_id)
        if attempt < max_repairs:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": _build_gen_repair(errs)})
    if not valid:
        log.warning("generate_lore_batch: 0 conforming skeletons for town=%s after %d attempt(s)",
                    town_id, max_repairs + 1)
        return []

    # ------------------------------------------------------------------
    # Build skeletons from the validated lines + engine-decided fields.
    # ------------------------------------------------------------------
    skeletons: list[dict] = []
    seen_ids: set[str] = set()
    for i, spec in enumerate(valid_specs):
        ln = valid.get(i)
        if ln is None:
            continue  # this line never conformed after repairs; dropped (logged above)
        about = ln["about"].strip()
        stages = [{"hint": s["hint"].strip()}
                  for s in ln["stages"][:spec["stage_count"]]]
        # Deterministic id; resolve collisions with a counter suffix
        sid = _make_skeleton_id(town_id, about, i)
        counter = 0
        while sid in seen_ids:
            counter += 1
            sid = _make_skeleton_id(town_id, about, i) + f"_{counter}"
        seen_ids.add(sid)
        skeletons.append({
            # engine-decided
            "id":         sid,
            "complexity": spec["complexity"],
            "anchor":     town_id,
            "threshold":  GEN_THRESHOLD,
            # lifespan_days omitted — create_lore_line/LoreSystem defaults by complexity
            # model-written (validated present + non-empty)
            "about":       about,
            "description": ln["description"].strip(),
            "trigger":     ln["trigger"].strip(),
            "secret":      ln["secret"].strip(),
            "l3_anchor":   ln["l3_anchor"].strip(),
            "stages":      stages,
        })
    return skeletons


# ---------------------------------------------------------------------------
# Task 3: run_density orchestrator
# ---------------------------------------------------------------------------

# How many lines to target at seeding time (target = round(density * BASE))
BASE: int = 10

# Number of game-days between density refresh checks
REFRESH_INTERVAL_DAYS: int = 3

# How many stages per complexity tier (engine-decided, LLM writes the hints)
STAGE_COUNT: dict[str, int] = {"simple": 2, "medium": 3, "complex": 5}


def _town_venues(g, town_id: str, day: int) -> list[str]:
    """Return ids of L3 venue entities that are direct children (contained_by) of town_id.

    FactGraph has no reverse-neighbor API; we iterate all entities and check
    whether they have level==3 and their contained_by edge points to town_id.
    This is the correct approach: the graph is typically small (hundreds of
    entities), so a full scan is acceptable.
    """
    venues: list[str] = []
    for eid, entity in g.entities.items():
        if entity.attrs.get("level") == 3:
            parents = g.neighbors(eid, "contained_by", day)
            if town_id in parents:
                venues.append(eid)
    return venues


def run_density(
    registry,
    store,
    world: dict,
    protagonist: str | None,
    *,
    provider,
    day: int,
    scene: str,
    turn: int,
) -> list[dict]:
    """Orchestrate density-based lore generation for the protagonist's current town.

    Seeding (first entry):
        If the town has no lore_seeded marker, generate round(density * BASE)
        lines, mark the town as seeded, and return all new events.

    Refresh (subsequent entries):
        If REFRESH_INTERVAL_DAYS have elapsed since last_refresh_day, roll a
        d100 vs density; on a hit, generate 1 new line. Always emit a
        density_refreshed event (records the check, resets the interval).

    The hook is defensive: any unexpected error returns [] so the turn never
    crashes. The caller (run_turn) also wraps in try/except.

    Cap-drift note (seeding batch):
        All target slots are rolled against the current world state. Because
        we do not re-project the world between individual skeleton creations
        within a single batch, the cap checks see the pre-batch state.
        This can let the batch exceed a cap by at most (target - 1) lines
        in a single seeding call — typically 2–3 for density 0.3.  We
        accept this slight over-target in exchange for simplicity (one LLM
        call, deterministic rolls). The next refresh cycle re-checks live caps.

    Returns:
        list of event dicts appended to the store (lore_created events +
        one lore_seeded or density_refreshed).  Empty list on no-op.
    """
    try:
        return _run_density_inner(registry, store, world, protagonist,
                                  provider=provider, day=day, scene=scene, turn=turn)
    except Exception:
        log.exception("run_density: unexpected error — returning [] to keep turn alive")
        return []


def _run_density_inner(
    registry,
    store,
    world: dict,
    protagonist: str | None,
    *,
    provider,
    day: int,
    scene: str,
    turn: int,
) -> list[dict]:
    """Inner (non-defensive) implementation of run_density."""
    if not protagonist:
        return []

    # --- Resolve current L2 town ---
    g = world.get("systems", {}).get("ontology")
    if g is None:
        return []

    # protagonist's current L3 location
    locs = g.neighbors(protagonist, "located_in", day)
    if not locs:
        return []
    l3 = locs[0]
    town = _ancestor_of_level(g, l3, day, level=2)
    if town is None:
        # protagonist may already be at an L2 place
        e = g.get_entity(l3)
        if e and e.attrs.get("level") == 2:
            town = l3
    if town is None:
        return []

    # --- Read gen state and density inputs ---
    gen_state = world.get("systems", {}).get("lore", {}).get("gen", {})
    gen = gen_state.get(town, {})

    density = resolve_density(world, town, day)
    region = region_scope(world, town, day)
    campaign_seed = world.get("meta", {}).get("campaign_seed") or "default"

    # Gather town entity attrs for LLM context
    town_entity = g.get_entity(town)
    kind = (town_entity.attrs.get("kind") or "settlement") if town_entity else "settlement"
    flavor = (town_entity.attrs.get("seed") or "") if town_entity else ""

    # L3 venue ids for l3_anchor coercion
    venues = _town_venues(g, town, day)

    # Existing active line abouts for dedup
    lines = world.get("systems", {}).get("lore", {}).get("lines", {})
    existing_abouts = [
        ln["about"] for ln in lines.values()
        if ln.get("anchor") == town and ln.get("about")
    ]

    appended: list[dict] = []

    # -----------------------------------------------------------------------
    # SEEDING branch
    # -----------------------------------------------------------------------
    if not gen.get("seeded"):
        if provider is None:
            # No provider → cannot generate; do NOT mark seeded so it seeds
            # later when a provider becomes available.
            return []

        target = round(density * BASE)

        # Roll complexity specs for each slot against current world state.
        # Cap-drift: all rolls see the pre-batch world (see docstring).
        specs: list[dict] = []
        for n in range(target):
            oracle = Oracle(scene_seed(campaign_seed, f"density:{town}:{n}", 0))
            cx = roll_complexity(oracle, world, town, region)
            if cx is not None:
                specs.append({"complexity": cx, "stage_count": STAGE_COUNT[cx]})

        if specs:
            skeletons = generate_lore_batch(
                provider,
                town_id=town,
                kind=kind,
                flavor=flavor,
                venues=venues,
                existing_abouts=existing_abouts,
                specs=specs,
            )
            for sk in skeletons:
                try:
                    ev = create_lore_line(store, sk, day=day, scene=scene, turn=turn)
                    appended.append(ev)
                except Exception:
                    log.debug("run_density: create_lore_line failed for %s — skipped",
                              sk.get("id"), exc_info=True)

        # Mark town as seeded even if 0 skeletons came back:
        # a working provider that returned nothing shouldn't retry every turn.
        seed_ev = kernel_event(
            "lore_seeded", day=day, scene=scene,
            summary=f"density seeded: {town} ({len(appended)} lines)",
            deltas={"town": town},
            turn=turn,
        )
        store.append(seed_ev)
        appended.append(seed_ev)
        return appended

    # -----------------------------------------------------------------------
    # REFRESH branch
    # -----------------------------------------------------------------------
    last = gen.get("last_refresh_day")
    if last is None or (day - last) < REFRESH_INTERVAL_DAYS:
        return []

    if provider is None:
        return []

    # Roll whether a new line spawns this interval
    oracle = Oracle(scene_seed(campaign_seed, f"density:{town}:refresh", day))
    spawned = oracle.d100() < density * 100

    if spawned:
        # Roll 1 complexity
        cx = roll_complexity(oracle, world, town, region)
        if cx is not None:
            specs = [{"complexity": cx, "stage_count": STAGE_COUNT[cx]}]
            skeletons = generate_lore_batch(
                provider,
                town_id=town,
                kind=kind,
                flavor=flavor,
                venues=venues,
                existing_abouts=existing_abouts,
                specs=specs,
            )
            for sk in skeletons:
                try:
                    ev = create_lore_line(store, sk, day=day, scene=scene, turn=turn)
                    appended.append(ev)
                except Exception:
                    log.debug("run_density: refresh create_lore_line failed for %s — skipped",
                              sk.get("id"), exc_info=True)

    # Always emit density_refreshed (records the check; folds last_refresh_day=day).
    refresh_ev = kernel_event(
        "density_refreshed", day=day, scene=scene,
        summary=f"density refresh: {town} day={day} spawned={spawned}",
        deltas={"town": town},
        turn=turn,
    )
    store.append(refresh_ev)
    appended.append(refresh_ev)
    return appended
