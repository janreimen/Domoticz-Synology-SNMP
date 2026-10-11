"""SMART health per drive (Synology SMART MIB), read every 10 minutes (it changes slowly)."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .. import oids
from ..config import LEVEL_FULL
from ..model import CRIT, OK, WARN, Reading
from ..snmpcli import SnmpClient
from ..util import make_key, slug
from .base import Collector, cint, ctext, natural_key

# SMART MIB columns
C_DEV, C_NAME, C_ID, C_CURRENT, C_WORST, C_THRESH, C_RAW, C_STATUS, C_RAW64 = 2, 3, 4, 5, 6, 7, 8, 9, 10

REALLOCATED, POWER_ON_HOURS, REPORTED_UNCORRECT, REALLOC_EVENT = 5, 9, 187, 196
PENDING, OFFLINE_UNCORRECT, CRC_ERRORS = 197, 198, 199
_WARN_IDS = (REALLOCATED, REPORTED_UNCORRECT, REALLOC_EVENT, PENDING, OFFLINE_UNCORRECT)


class SmartCollector(Collector):
    name = "smart"
    groups = ("smart",)
    min_level = LEVEL_FULL
    interval = 600

    def collect(self, client: SnmpClient) -> List[Reading]:
        rows = client.table(oids.SMART_TABLE)
        drives: Dict[str, List[dict]] = {}
        for index in sorted(rows, key=natural_key):
            row = rows[index]
            dev = ctext(row, C_DEV)
            if not dev:
                continue
            raw = cint(row, C_RAW64)
            if raw is None:
                raw = cint(row, C_RAW)
            drives.setdefault(dev, []).append({
                "id": cint(row, C_ID), "name": ctext(row, C_NAME), "current": cint(row, C_CURRENT),
                "thresh": cint(row, C_THRESH), "raw": raw, "status": ctext(row, C_STATUS),
            })

        out: List[Reading] = []
        for dev in sorted(drives):
            base = dev.rstrip("/").rsplit("/", 1)[-1]
            level, text = self._assess(drives[dev])
            out.append(Reading.alert(make_key("smart", slug(base), "health"), "SMART %s" % base, level, text, used=False))
        return out

    @staticmethod
    def _assess(attrs: List[dict]) -> Tuple[int, str]:
        by_id = {a["id"]: a for a in attrs if a["id"] is not None}

        def raw(attr_id: int) -> Optional[int]:
            a = by_id.get(attr_id)
            return (a["raw"] & 0xFFFFFFFF) if a and a["raw"] is not None else None

        level = OK
        problems = []
        for a in attrs:
            if a["thresh"] and a["current"] is not None and a["thresh"] > 0 and a["current"] <= a["thresh"]:
                level = CRIT
                problems.append("%s failing" % (a["name"] or a["id"]))
            elif "fail" in a["status"].lower():
                level = CRIT
                problems.append("%s: %s" % (a["name"] or a["id"], a["status"]))
        if level != CRIT and any((raw(i) or 0) > 0 for i in _WARN_IDS):
            level = WARN

        parts = []
        for label, attr_id in (("realloc", REALLOCATED), ("pending", PENDING),
                               ("uncorrectable", OFFLINE_UNCORRECT), ("CRC", CRC_ERRORS)):
            v = raw(attr_id)
            if v is not None:
                parts.append("%s %d" % (label, v))
        hours = raw(POWER_ON_HOURS)
        if hours is not None:
            parts.append("power-on %d h" % hours)
        text = " | ".join(problems + parts) or "no SMART attributes"
        return level, text
