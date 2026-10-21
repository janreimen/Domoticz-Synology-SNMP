"""Physical drives (Synology Disk MIB): temperature, health, remaining life, bad sectors.

The drive table is small, so it is re-walked whenever the configured slow schedule runs: inserted, removed
or swapped drives show up (or time out) on their own, with no separate discovery step.
"""
from __future__ import annotations

from typing import List

from .. import oids
from ..config import LEVEL_BASIC, LEVEL_FULL
from ..model import NONE, WARN, Reading, worst
from ..snmpcli import SnmpClient
from ..util import make_key, slug
from .base import Collector, cint, ctext, natural_key, unique_slug

# Disk MIB columns
C_ID, C_MODEL, C_TYPE, C_STATUS, C_TEMP, C_ROLE = 2, 3, 4, 5, 6, 7
C_BADSECTOR, C_LIFE, C_NAME, C_HEALTH = 9, 11, 12, 13


class DiskCollector(Collector):
    name = "disks"
    groups = ("disk",)
    min_level = LEVEL_BASIC

    def collect(self, client: SnmpClient) -> List[Reading]:
        rows = client.table(oids.DISK_TABLE)
        out: List[Reading] = []
        levels: List[int] = []
        temps: List[float] = []
        taken: set = set()

        for index in sorted(rows, key=natural_key):
            row = rows[index]
            label = ctext(row, C_NAME) or ctext(row, C_ID) or "Disk %s" % index
            s = unique_slug(slug(label), taken, index)

            status = cint(row, C_STATUS)
            health = cint(row, C_HEALTH)
            status_text, status_level = oids.DISK_STATUS.get(status, ("Unknown", NONE)) if status is not None else ("", NONE)
            health_text, health_level = oids.DISK_HEALTH.get(health, ("", NONE)) if health is not None else ("", NONE)
            level = worst(status_level, health_level)
            levels.append(level)

            bad = cint(row, C_BADSECTOR)
            life = cint(row, C_LIFE)
            if bad is not None and bad > 0:
                level = worst(level, WARN)
                levels[-1] = level

            parts = [p for p in (
                ctext(row, C_MODEL), ctext(row, C_TYPE), ctext(row, C_ROLE),
                status_text, ("health: " + health_text) if health_text else "",
                ("bad sectors: %d" % bad) if bad is not None and bad >= 0 else "",
                ("life: %d%%" % life) if life is not None and 0 <= life <= 100 else "",
            ) if p]
            out.append(Reading.alert(make_key("disk", s, "health"), label + " Health", level, " | ".join(parts)))

            temp = cint(row, C_TEMP)
            if temp is not None and 0 < temp < 200:
                temps.append(float(temp))
                out.append(Reading.temperature(make_key("disk", s, "temp"), label + " Temperature", float(temp)))

            if life is not None and 0 <= life <= 100:      # -1 on spinning disks
                out.append(Reading.percentage(make_key("disk", s, "life"), label + " Remaining Life", float(life)))

            if self.ctx.level >= LEVEL_FULL and bad is not None and bad >= 0:
                out.append(Reading.custom(make_key("disk", s, "bad"), label + " Bad Sectors", float(bad), "", 0, used=False))

        if rows:
            problems = sum(1 for l in levels if l >= WARN)
            text = "%d drive%s" % (len(rows), "" if len(rows) == 1 else "s")
            if temps:
                text += " | hottest %.0f C" % max(temps)
            text += " | %s" % ("%d with problems" % problems if problems else "no problems")
            out.append(Reading.alert("disk.summary", "Disks Overall", worst(*levels), text))
        return out
