"""loop.entity_resolve — #R7 A' / Phase 1: pre-validate entity auto-resolve/create.

The model narrates a NEW named NPC/place (e.g. 卡恩) and references it in
`moves`/`links`/`materialize` by NAME — but those refs must be entity ids, and
after the #10 fog fix the model only sees names for invented entities, so the
section bounces as `dangling_ref` and gets dropped (the world silently loses the
move). This augment runs on the commit BEFORE validate_commit:

- resolve a ref to an existing entity (by id, or by 真名 fact), or to one declared
  in THIS turn's `cast`/`places`;
- mint + INJECT a create for an unresolved name so the existing
  validate/created_ids/to_events machinery applies it:
    person → a `cast` create (mentioned tier) + a 真名 fact + a first-seen sketch;
    place  → a `places` create (L3 venue, seed=name) + a 真名 fact;
- dedup by normalized 真名 (across turns via the fact, within the turn via the
  name map), so a name is never created twice.

Never raises (runs in the turn loop). No-op when there's no entity graph or when
every ref is already a valid id (the common path → byte-identical commit).
"""
from __future__ import annotations

from engine.log import get_logger

log = get_logger("loop.entity_resolve")


def _norm(s) -> str:
    return s.strip().lower() if isinstance(s, str) else ""


def augment_unresolved_refs(commit, world, *, scene: str = "", day: int = 0) -> list:
    """Resolve/mint unresolved name-refs in commit.sections (mutates in place).

    Returns the list of newly minted entity ids (for logging/tests). Never raises.
    """
    try:
        return _augment(commit, world, scene=scene, day=day)
    except Exception:
        log.exception("augment_unresolved_refs failed (non-fatal)")
        return []


def _augment(commit, world, *, scene: str, day: int) -> list:
    g = (world or {}).get("systems", {}).get("ontology") if isinstance(world, dict) else None
    if g is None:
        return []
    sections = commit.sections

    def _items(key):
        v = sections.get(key)
        return v if isinstance(v, list) else []

    # ---- build name -> id map -------------------------------------------------
    name2id: dict[str, str] = {}
    # existing entities carrying a 真名 fact
    try:
        for eid in list(getattr(g, "entities", {}) or {}):
            for f in g.current_facts(eid):
                if getattr(f, "predicate", None) == "真名":
                    nm = _norm(getattr(f, "value", None))
                    if nm:
                        name2id.setdefault(nm, eid)
                    break
    except Exception:
        pass
    # this-turn cast creates (optional `name`) + places (seed/id) + facts(真名)
    for c in _items("cast"):
        if isinstance(c, dict) and c.get("op", "create") == "create" and c.get("id"):
            nm = _norm(c.get("name"))
            if nm:
                name2id.setdefault(nm, c["id"])
    for p in _items("places"):
        if isinstance(p, dict) and p.get("id"):
            nm = _norm(p.get("seed")) or _norm(p.get("name"))
            if nm:
                name2id.setdefault(nm, p["id"])
    for fct in _items("facts"):
        if isinstance(fct, dict) and fct.get("predicate") == "真名" and fct.get("subject"):
            nm = _norm(fct.get("value"))
            if nm:
                name2id.setdefault(nm, fct["subject"])

    # ids that will exist after this commit applies (so refs to them resolve)
    pending: set[str] = set()
    for c in _items("cast"):
        if isinstance(c, dict) and c.get("id"):
            pending.add(c["id"])
    for p in _items("places"):
        if isinstance(p, dict) and p.get("id"):
            pending.add(p["id"])

    minted: list[str] = []
    counter = [0]

    def _exists(ref: str) -> bool:
        return g.get_entity(ref) is not None or ref in pending

    def _mint(kind: str, name: str) -> str:
        base = "npc_auto" if kind == "person" else "place_auto"
        counter[0] += 1
        mid = f"{base}_{day}_{counter[0]}"
        while g.get_entity(mid) is not None or mid in pending:
            counter[0] += 1
            mid = f"{base}_{day}_{counter[0]}"
        pending.add(mid)
        minted.append(mid)
        breadcrumb = f"（首次现身于{scene or '此处'}·第{day}天）"
        if kind == "person":
            sections.setdefault("cast", [])
            if not isinstance(sections["cast"], list):
                sections["cast"] = []
            sections["cast"].append({
                "id": mid, "op": "create",
                "sketch": breadcrumb, "goal": "（暂未明）", "tier": "mentioned",
            })
        else:
            sections.setdefault("places", [])
            if not isinstance(sections["places"], list):
                sections["places"] = []
            sections["places"].append({
                "id": mid, "level": 3, "kind": "venue", "seed": name or breadcrumb,
            })
        sections.setdefault("facts", [])
        if not isinstance(sections["facts"], list):
            sections["facts"] = []
        sections["facts"].append({
            "subject": mid, "predicate": "真名", "value": name, "secrecy": "public",
        })
        return mid

    def _resolve(ref, kind: str):
        if not isinstance(ref, str) or not ref.strip():
            return ref
        r = ref.strip()
        if _exists(r):
            return r
        nm = _norm(r)
        if nm in name2id:
            return name2id[nm]
        mid = _mint(kind, r)
        name2id[nm] = mid
        return mid

    for m in _items("moves"):
        if isinstance(m, dict):
            if "who" in m:
                m["who"] = _resolve(m.get("who"), "person")
            if "to" in m:
                m["to"] = _resolve(m.get("to"), "place")
    for lnk in _items("links"):
        if isinstance(lnk, dict):
            if "a" in lnk:
                lnk["a"] = _resolve(lnk.get("a"), "place")
            if "b" in lnk:
                lnk["b"] = _resolve(lnk.get("b"), "place")
    for mt in _items("materialize"):
        if isinstance(mt, dict) and "id" in mt:
            mt["id"] = _resolve(mt.get("id"), "place")

    return minted
