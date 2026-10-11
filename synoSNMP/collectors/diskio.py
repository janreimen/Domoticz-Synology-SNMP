"""Per-drive I/O load and throughput (Synology StorageIO MIB)."""
from __future__ import annotations

import re
from typing import List

from .. import oids
from ..config import LEVEL_FULL
from ..model import CUSTOM, Reading
from ..snmpcli import SnmpClient
from ..util import clamp, make_key, slug
from .base import Collector, cint, ctext, natural_key, unique_slug

# whole drives only: not md0/dm-0/ram or partitions such as sda1 / nvme0n1p1
_PHYSICAL = re.compile(r"^(sata\d+|sas\d+|sd[a-z]{1,3}|nvme\d+n\d+|usb[a-z]*\d*|hd[a-z]|sg\d+)$", re.I)

# StorageIO columns
C_DEV, C_READ32, C_WRITE32, C_LOAD, C_READ64, C_WRITE64 = 2, 3, 4, 8, 12, 13


class DiskIoCollector(Collector):
    name = "diskio"
    groups = ("io",)
    min_level = LEVEL_FULL

    def collect(self, client: SnmpClient) -> List[Reading]:
        rows = client.table(oids.STORAGEIO_TABLE)
        now = self.ctx.clock()
        out: List[Reading] = []
        taken: set = set()
        for index in sorted(rows, key=natural_key):
            row = rows[index]
            dev = ctext(row, C_DEV)
            if not dev or not _PHYSICAL.match(dev):
                continue
            s = unique_slug(slug(dev), taken, index)

            load = cint(row, C_LOAD)
            if load is not None:
                out.append(Reading.percentage(make_key("io", s, "load"), "IO %s Load" % dev, clamp(float(load)), used=False))

            for direction, col64, col32, label in (("read", C_READ64, C_READ32, "Read"), ("write", C_WRITE64, C_WRITE32, "Write")):
                value, bits = cint(row, col64), 64
                if value is None:
                    value, bits = cint(row, col32), 32
                key = make_key("io", s, direction)
                rate = self.ctx.rates.update(key, value, now, bits)
                title = "IO %s %s" % (dev, label)
                if rate is None:
                    out.append(Reading.waiting(key, title, CUSTOM, "MB/s", used=False))
                else:
                    out.append(Reading.custom(key, title, rate / (1024.0 * 1024.0), "MB/s", 2, used=False))
        return out
