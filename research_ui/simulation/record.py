"""Shared pipeline record — imported from a stable module path so pickle works."""
from __future__ import annotations
import time
from dataclasses import dataclass, field


@dataclass
class Record:
    seq:            int
    created_ns:     int  = field(default_factory=time.monotonic_ns)
    context:        dict = field(default_factory=dict)
    stage_exits_ns: list[int] = field(default_factory=list)
