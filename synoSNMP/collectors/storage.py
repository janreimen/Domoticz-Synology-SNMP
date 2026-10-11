"""Storage: RAID / storage pool / volume status and mounted-volume capacity."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .. import oids
from ..config import LEVEL_BASIC
from ..model import NONE, WARN, Reading
from ..snmpcli import SnmpClient
from ..util import human_bytes, make_key, pct, slug
from .base import Collector, cint, ctext, natural_key, unique_slug

# RAID MIB columns
R_NAME, R_STATUS, R_FREE, R_TOTAL, R_HOTSPARE = 2, 3, 4, 5, 6


class RaidCollector(Collector):
    """Status of every storage pool and volume listed in Storage Manager."""

    name = "raid"
    groups = ("raid",)
    min_level = LEVEL_BASIC

    def collect(self, client: SnmpClient) -> List[Reading]:
        rows = client.table(oids.RAID_TABLE)
        out: List[Reading] = []
        taken: set = set()
        for index in sorted(rows, key=natural_key):
            row = rows[index]
            name = ctext(row, R_NAME) or "Storage %s" % index
            s = unique_slug(slug(name), taken, index)
            code = cint(row, R_STATUS)
            text, level = oids.RAID_STATUS.get(code, ("Unknown (%s)" % code, NONE)) if code is not None else ("Unknown", NONE)

            free, total = cint(row, R_FREE), cint(row, R_TOTAL)
            parts = [text]
            if total:
                parts.append("%s free of %s" % (human_bytes(free or 0), human_bytes(total)))
            spares = cint(row, R_HOTSPARE)
            if spares is not None:
                if spares < 0:
                    parts.append("hot spare error")
                    level = max(level, WARN)
                elif spares > 0:
                    parts.append("%d hot spare%s" % (spares, "" if spares == 1 else "s"))
            out.append(Reading.alert(make_key("raid", s, "status"), name + " Status", level, " | ".join(parts)))
        return out


_MOUNT = re.compile(r"^/volume[^/@]*(/[^/@]+)?$")


def interesting_mount(descr: str) -> bool:
    """Volumes, external (USB/eSATA) shares and the DSM system partition.

    Excludes Docker/btrfs sub-mounts ("/volume1/@docker/..."), tmpfs and friends.
    """
    d = descr.strip()
    return d == "/" or bool(_MOUNT.match(d))


class MountCollector(Collector):
    """Used percentage and free space of mounted volumes (HOST-RESOURCES-MIB).

    hrStorage row indexes are assigned dynamically and can shift (for example
    when Docker mounts something), so every poll also reads the description of
    each tracked row and re-runs discovery immediately if one no longer matches.
    """

    name = "mounts"
    groups = ("vol",)
    min_level = LEVEL_BASIC

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.mounts: Dict[str, str] = {}   # row index -> mount point

    def discover(self, client: SnmpClient) -> None:
        types = {vb.oid.rsplit(".", 1)[-1]: vb for vb in client.walk(oids.HR_STORAGE + ".2")}
        mounts: Dict[str, str] = {}
        for vb in client.walk(oids.HR_STORAGE + ".3"):
            idx = vb.oid.rsplit(".", 1)[-1]
            descr = vb.as_text()
            kind = types.get(idx)
            if kind is not None and kind.value != oids.HR_STORAGE_FIXED_DISK:
                continue
            if interesting_mount(descr):
                mounts[idx] = descr
        self.mounts = mounts
        self.ctx.debug("mounts: %s" % ", ".join("%s=%s" % kv for kv in sorted(mounts.items(), key=lambda kv: natural_key(kv[0]))))

    def collect(self, client: SnmpClient) -> List[Reading]:
        if not self.mounts:
            self.discover(client)
        got = self._fetch(client)
        if got is None:                      # a tracked row now describes something else
            self.discover(client)
            got = self._fetch(client) or {}
        out: List[Reading] = []
        taken: set = set()
        for idx in sorted(self.mounts, key=natural_key):
            descr = self.mounts[idx]
            units, size, used = (got.get(oids.HR_STORAGE + ".%d.%s" % (c, idx)) for c in (4, 5, 6))
            if not (units and size and used):
                continue
            u, sz, us = units.as_int(), size.as_int(), used.as_int()
            percent = pct(us, sz)
            if percent is None:
                continue
            s = unique_slug(slug(descr, "root"), taken, idx)
            out.append(Reading.percentage(make_key("vol", s, "used"), "%s Used" % descr, percent))
            free_gb = max(0, sz - us) * (u or 1) / (1024.0 ** 3)
            out.append(Reading.custom(make_key("vol", s, "free"), "%s Free" % descr, free_gb, "GB", 1))
        return out

    def _fetch(self, client: SnmpClient) -> Optional[dict]:
        wanted = []
        for idx in self.mounts:
            wanted += [oids.HR_STORAGE + ".%d.%s" % (c, idx) for c in (3, 4, 5, 6)]
        got = client.get_many(wanted)
        for idx, descr in self.mounts.items():
            vb = got.get(oids.HR_STORAGE + ".3.%s" % idx)
            if vb is None or vb.as_text() != descr:
                return None
        return got
