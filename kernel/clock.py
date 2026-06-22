"""kernel.clock — pure in-game-time arithmetic.

The world clock is (day:int, band:int 0..3). Bands per day:
    0=晨  1=中午  2=下午  3=夜晚

Everything collapses to a single integer scale (band-units = day*4 + band)
so advance / elapsed / compare / expiry are plain integer ops. No I/O — fully
deterministic and offline-testable. This is the "time engine" all time-based
systems (lifespans, dormancy, catch-up) build on.
"""
from __future__ import annotations

BANDS: tuple[str, str, str, str] = ("晨", "中午", "下午", "夜晚")


def to_units(day: int, band: int) -> int:
    """Collapse (day, band) to a single comparable integer scale."""
    return day * 4 + band


def from_units(units: int) -> tuple[int, int]:
    """Inverse of to_units: (day, band), band in 0..3 (auto carry)."""
    return (units // 4, units % 4)


def advance(day: int, band: int, ddays: int, dbands: int) -> tuple[int, int]:
    """Advance the clock by ddays whole days + dbands bands; normalize carry."""
    return from_units(to_units(day, band) + ddays * 4 + dbands)


def elapsed(from_units_val: int, to_units_val: int) -> int:
    """Band-unit delta between two clock instants."""
    return to_units_val - from_units_val


def compare(a_units: int, b_units: int) -> int:
    """-1 if a<b, 0 if equal, 1 if a>b."""
    return (a_units > b_units) - (a_units < b_units)


def expired(born_units: int, lifespan_units: int, now_units: int) -> bool:
    """True once at least `lifespan_units` have elapsed since birth."""
    return now_units - born_units >= lifespan_units


def band_name(band: int) -> str:
    """Display name for a band index (wraps defensively)."""
    return BANDS[band % 4]
