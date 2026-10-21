"""Base class and shared context for collectors."""
from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence

from ..config import LEVEL_BASIC, Settings
from ..model import Reading
from ..snmpcli import SnmpClient
from ..util import RateCalc


class Context:
    """What every collector may rely on: settings, a clock, and a note sink."""

    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.clock = clock
        self.level = settings.level
        self.rates = RateCalc()
        self.notes: List[str] = []       # debug-level messages, drained by the engine

    def debug(self, text: str) -> None:
        self.notes.append(text)


class Collector:
    """One hardware area.

    Subclasses set ``name``, ``groups`` (the key prefixes they own) and
    ``min_level``, and implement ``collect``.  ``discover`` is optional: it is
    called when hardware detection runs (first cycle, then hourly) for
    collectors that keep a cache of what exists (network interfaces, mounts).
    Small tables are re-walked whenever their collector is scheduled, which makes them
    self-healing and needs no separate discovery step.
    """

    name = "base"
    groups: Sequence[str] = ()
    min_level = LEVEL_BASIC
    interval = 0          # minimum seconds between runs (0 = every poll)

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.last_run: Optional[float] = None

    def discover(self, client: SnmpClient) -> None:  # pragma: no cover - default is a no-op
        return None

    def collect(self, client: SnmpClient) -> List[Reading]:
        raise NotImplementedError


# ---- helpers for reading table rows ----------------------------------------------------
def natural_key(index: str):
    """Sort key for dotted row indexes ("10" after "2")."""
    return tuple(int(p) if p.isdigit() else 0 for p in index.split("."))


def cint(row, col):
    vb = row.get(col)
    return vb.as_int() if vb is not None else None


def cfloat(row, col):
    vb = row.get(col)
    return vb.as_float() if vb is not None else None


def ctext(row, col, default=""):
    vb = row.get(col)
    return vb.as_text(default) if vb is not None else default


def unique_slug(base: str, taken: set, index: str) -> str:
    """Make ``base`` unique among ``taken`` (adds the row index on collision)."""
    s = base
    if s in taken:
        s = base + index.replace(".", "")
    taken.add(s)
    return s
