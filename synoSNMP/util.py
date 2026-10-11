"""Small, dependency-free helpers shared by all modules."""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Optional, Tuple

#: Domoticz limits ``DeviceID`` to 25 characters.
MAX_KEY_LEN = 25


def slug(text: object, fallback: str = "x") -> str:
    """Lower-case alphanumeric-only form of ``text`` ("Drive 1" -> "drive1")."""
    s = re.sub(r"[^a-z0-9]+", "", str(text).lower())
    return s or fallback


def make_key(*parts: object) -> str:
    """Build a stable device key like ``disk.drive1.temp`` (max 25 chars).

    Over-long keys are shortened deterministically: a readable prefix plus a
    hash of the full key, so the same hardware always maps to the same key.
    """
    key = ".".join(str(p) for p in parts)
    if len(key) <= MAX_KEY_LEN:
        return key
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return key[: MAX_KEY_LEN - 9] + "~" + digest


def group_of(key: str) -> str:
    """The collector group a key belongs to (its first dotted segment)."""
    return key.split(".", 1)[0]


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def pct(part: float, whole: float) -> Optional[float]:
    """``part`` as a percentage of ``whole`` (None if ``whole`` is not positive)."""
    if whole is None or whole <= 0 or part is None:
        return None
    return clamp(100.0 * part / whole)


def human_bytes(n: float) -> str:
    """1024-based human readable size ("7.3 TB")."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0 or unit == "PB":
            return ("%.0f %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024.0
    return "%.1f PB" % n  # pragma: no cover


class RateCalc:
    """Turns ever-increasing SNMP counters into per-second rates.

    Handles 32-bit wrap-around and counter resets (reboot): a reset yields
    ``None`` for that one sample instead of a bogus spike.
    """

    def __init__(self) -> None:
        self._prev: Dict[str, Tuple[int, float]] = {}

    def update(self, key: str, value: Optional[int], now: float, bits: int = 64) -> Optional[float]:
        if value is None:
            self._prev.pop(key, None)
            return None
        prev = self._prev.get(key)
        self._prev[key] = (value, now)
        if prev is None:
            return None
        prev_value, prev_time = prev
        dt = now - prev_time
        if dt <= 0:
            return None
        if value >= prev_value:
            delta = value - prev_value
        elif bits == 32:
            delta = value + (1 << 32) - prev_value
        else:
            return None  # counter reset
        return delta / dt

    def forget_all(self) -> None:
        self._prev.clear()
