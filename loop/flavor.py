"""Flavor pack resolution + persistence.

A flavor pack is a directory under data/oracles/ with a pack.json manifest. The
active flavor resolves from an explicit choice → the pitch's select_hints → the
default 'classic', and is persisted in the campaign_seeded event so resumed
sessions keep their aesthetic.
"""
from engine.oracle import load_pack_manifest, _ORACLE_DIR


def available_flavors() -> list[str]:
    """Flavor pack names = subdirs of data/oracles/ that contain a pack.json."""
    return sorted(d.name for d in _ORACLE_DIR.iterdir()
                  if d.is_dir() and (d / "pack.json").exists())


def resolve_flavor(explicit, pitch: str, available: list[str] | None = None) -> str:
    """Resolve the active flavor.

    explicit (validated against `available`) wins; else the first non-classic pack
    whose select_hints match the pitch; else 'classic' (the default). Unknown
    explicit → ValueError listing the available packs.
    """
    av = available if available is not None else available_flavors()
    if explicit:
        if explicit not in av:
            raise ValueError(f"unknown flavor {explicit!r}; available: {av}")
        return explicit
    text = pitch or ""
    for name in [n for n in av if n != "classic"]:   # classic is the default; checked last
        hints = load_pack_manifest(name).get("select_hints") or []
        if any(h in text for h in hints):
            return name
    return "classic"


def stored_flavor(store) -> str:
    """Read the flavor persisted in the campaign_seeded event; default 'classic'."""
    for ev in store.iter_events():
        if ev.get("type") == "campaign_seeded":
            return (ev.get("deltas") or {}).get("flavor", "classic")
    return "classic"
