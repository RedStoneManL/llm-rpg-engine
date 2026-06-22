from __future__ import annotations
from dataclasses import dataclass, field

def _current(end): return end is None
def _valid_at(start, end, day): return day >= start and (end is None or day < end)

@dataclass
class Fact:
    subject: str
    predicate: str
    value: object
    event_time_start: int
    ingest_turn: int
    source_event: str
    event_time_end: int | None = None
    secrecy: str | None = None       # public | restricted | secret (None == unset)
    def is_current(self): return _current(self.event_time_end)
    def valid_at(self, day): return _valid_at(self.event_time_start, self.event_time_end, day)

@dataclass
class Relation:
    src: str
    rel: str                         # held_by | located_in | member_of | ...
    dst: str
    event_time_start: int
    ingest_turn: int
    source_event: str
    event_time_end: int | None = None
    attrs: dict = field(default_factory=dict)
    def is_current(self): return _current(self.event_time_end)
    def valid_at(self, day): return _valid_at(self.event_time_start, self.event_time_end, day)
