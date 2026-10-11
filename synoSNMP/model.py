"""What collectors hand to the device layer.

A ``Reading`` is Domoticz-agnostic: it names a device (stable ``key``), says
what kind of sensor it is, and carries the current value.  Collectors never
import Domoticz; the device layer never speaks SNMP.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Alert levels (Domoticz "Alert" device: 0 grey, 1 green, 2 yellow, 3 orange, 4 red)
NONE = 0
OK = 1
WARN = 2
ALERT = 3
CRIT = 4

# Sensor kinds
TEMPERATURE = "temperature"
PERCENTAGE = "percentage"
TEXT = "text"
ALERT_DEV = "alert"
CUSTOM = "custom"


@dataclass(frozen=True)
class Reading:
    key: str                     # stable, <= 25 chars, e.g. "disk.drive1.temp"
    name: str                    # device name shown in Domoticz
    kind: str                    # one of the kinds above
    nvalue: int = 0
    svalue: str = ""
    unit: str = ""               # CUSTOM only
    used: bool = True            # visible by default in the Domoticz UI
    pending: bool = False        # device exists/should exist but has no value yet

    # -- constructors --------------------------------------------------
    @classmethod
    def temperature(cls, key: str, name: str, celsius: float, used: bool = True) -> "Reading":
        return cls(key, name, TEMPERATURE, 0, "%.1f" % celsius, used=used)

    @classmethod
    def percentage(cls, key: str, name: str, value: float, used: bool = True) -> "Reading":
        return cls(key, name, PERCENTAGE, 0, "%.1f" % value, used=used)

    @classmethod
    def text(cls, key: str, name: str, text: str, used: bool = True) -> "Reading":
        return cls(key, name, TEXT, 0, text[:250], used=used)

    @classmethod
    def alert(cls, key: str, name: str, level: int, text: str, used: bool = True) -> "Reading":
        return cls(key, name, ALERT_DEV, int(level), text[:250], used=used)

    @classmethod
    def custom(cls, key: str, name: str, value: float, unit: str, decimals: int = 1,
               used: bool = True) -> "Reading":
        return cls(key, name, CUSTOM, 0, ("%." + str(decimals) + "f") % value, unit=unit, used=used)

    @classmethod
    def waiting(cls, key: str, name: str, kind: str, unit: str = "", used: bool = True) -> "Reading":
        """Placeholder: the device is known but has no value yet (e.g. first rate sample)."""
        return cls(key, name, kind, unit=unit, used=used, pending=True)


def worst(*levels: Optional[int]) -> int:
    """The most severe alert level (NONE counts as least severe, ignored if others exist)."""
    present = [l for l in levels if l is not None]
    if not present:
        return NONE
    return max(present)
