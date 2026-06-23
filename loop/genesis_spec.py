"""The canonical GenesisSpec — the single structured model bootstrap consumes.

Pure: no I/O, no LLM. normalize() coerces raw user input into the canonical
shape (dropping empties); merge() overlays one spec onto another (scalars
replace when non-empty, lists augment); missing_required() reports which
required parts lack their minimal field. "Required" is enforced by session-zero,
not here — bootstrap stays permissive.
"""
from __future__ import annotations

# Required floor: part -> the minimal field that must be non-empty.
_REQUIRED = {"world_premise": "genre", "protagonist": "name"}

_PREMISE_FIELDS = ("genre", "tone", "world_name", "central_conflict",
                   "n_factions", "n_regions")
_PROT_FIELDS = ("name", "origin", "goal", "objective")


def _empty(v) -> bool:
    """True for None, blank/whitespace string, empty list/dict."""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def _norm_name(s) -> str:
    return s.strip().lower() if isinstance(s, str) else ""


def _scalar_part(src, fields) -> dict:
    if not isinstance(src, dict):
        return {}
    return {k: src[k] for k in fields if k in src and not _empty(src[k])}


def _name_list(items) -> list:
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict) and not _empty(it.get("name"))]


def normalize(raw) -> dict:
    """Coerce a raw dict (or None) into a canonical GenesisSpec dict.

    Drops empty fields/parts so generators can use plain truthiness. Unknown
    top-level keys are ignored. Never raises.
    """
    if not isinstance(raw, dict):
        return {}
    spec: dict = {}

    wp = _scalar_part(raw.get("world_premise"), _PREMISE_FIELDS)
    if wp:
        spec["world_premise"] = wp
    prot = _scalar_part(raw.get("protagonist"), _PROT_FIELDS)
    if prot:
        spec["protagonist"] = prot

    lm_src = raw.get("local_map")
    if isinstance(lm_src, dict):
        lm: dict = {}
        town = _scalar_part(lm_src.get("town"), ("name", "seed"))
        if town:
            lm["town"] = town
        for key in ("venues", "neighbors"):
            cleaned = _name_list(lm_src.get(key))
            if cleaned:
                lm[key] = cleaned
        if lm:
            spec["local_map"] = lm

    for part in ("regions", "factions"):
        cleaned = _name_list(raw.get(part))
        if cleaned:
            spec[part] = cleaned

    for part in ("npcs", "threads"):
        items = raw.get(part)
        if isinstance(items, list):
            cleaned = [it for it in items
                       if isinstance(it, dict) and any(not _empty(v) for v in it.values())]
            if cleaned:
                spec[part] = cleaned

    if not _empty(raw.get("opening")):
        spec["opening"] = raw["opening"]

    return spec


def _merge_scalar(base, overlay) -> dict:
    out = dict(base or {})
    for k, v in (overlay or {}).items():
        if not _empty(v):
            out[k] = v
    return out


def _augment_by_name(base, overlay) -> list:
    out = list(base or [])
    seen = {_norm_name(it.get("name")) for it in out}
    for it in overlay or []:
        nm = _norm_name(it.get("name"))
        if nm and nm in seen:
            continue
        seen.add(nm)
        out.append(it)
    return out


def merge(base, overlay) -> dict:
    """Overlay `overlay` onto `base`.

    Scalars (world_premise/protagonist/local_map.town): overlay field wins iff
    non-empty. Name-lists (regions/factions/local_map.venues/neighbors):
    augment, dedup by name. npcs/threads: pure concatenation. opening: overlay
    wins iff non-empty.
    """
    base = base or {}
    overlay = overlay or {}
    out = dict(base)

    for part in ("world_premise", "protagonist"):
        if part in base or part in overlay:
            merged = _merge_scalar(base.get(part), overlay.get(part))
            if merged:
                out[part] = merged

    if "local_map" in base or "local_map" in overlay:
        b_lm = base.get("local_map") or {}
        o_lm = overlay.get("local_map") or {}
        lm: dict = {}
        town = _merge_scalar(b_lm.get("town"), o_lm.get("town"))
        if town:
            lm["town"] = town
        for key in ("venues", "neighbors"):
            aug = _augment_by_name(b_lm.get(key), o_lm.get(key))
            if aug:
                lm[key] = aug
        if lm:
            out["local_map"] = lm

    for part in ("regions", "factions"):
        if part in base or part in overlay:
            aug = _augment_by_name(base.get(part), overlay.get(part))
            if aug:
                out[part] = aug

    for part in ("npcs", "threads"):
        if part in base or part in overlay:
            out[part] = list(base.get(part) or []) + list(overlay.get(part) or [])

    if not _empty(overlay.get("opening")):
        out["opening"] = overlay["opening"]

    return out


def missing_required(spec) -> list:
    """Return required part names whose minimal field is empty/absent."""
    spec = spec or {}
    missing = []
    for part, field in _REQUIRED.items():
        p = spec.get(part)
        if not isinstance(p, dict) or _empty(p.get(field)):
            missing.append(part)
    return missing
