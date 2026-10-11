"""Optional hardware found only on some models: expansion units, SSD cache, GPU."""
from __future__ import annotations

from typing import List

from .. import oids
from ..config import LEVEL_FULL, LEVEL_STANDARD
from ..model import NONE, Reading
from ..snmpcli import SnmpClient
from ..util import make_key, slug
from .base import Collector, cint, ctext, natural_key, unique_slug


class EboxCollector(Collector):
    """Power state of attached expansion units (DX/RX series)."""

    name = "ebox"
    groups = ("ebox",)
    min_level = LEVEL_STANDARD

    def collect(self, client: SnmpClient) -> List[Reading]:
        rows = client.table(oids.EBOX_TABLE)
        out: List[Reading] = []
        taken: set = set()
        for index in sorted(rows, key=natural_key):
            row = rows[index]
            model = ctext(row, 2) or "Expansion %s" % index
            s = unique_slug(slug(model), taken, index)
            for col, suffix, label in ((3, "pwr", "Power"), (4, "rpwr", "Redundant Power")):
                code = cint(row, col)
                if code is None:
                    continue                          # no redundant supply on this unit
                text, level = oids.EBOX_POWER.get(code, ("Unknown (%d)" % code, NONE))
                out.append(Reading.alert(make_key("ebox", s, suffix), "%s %s" % (model, label), level, text))
        return out


class FlashCacheCollector(Collector):
    """SSD cache hit rates (only models with SSD cache report rows)."""

    name = "flashcache"
    groups = ("fc",)
    min_level = LEVEL_FULL

    def collect(self, client: SnmpClient) -> List[Reading]:
        rows = client.table(oids.FLASHCACHE_TABLE)
        out: List[Reading] = []
        taken: set = set()
        for index in sorted(rows, key=natural_key):
            row = rows[index]
            space = ctext(row, 3)
            if not space or space == "0":
                continue                              # unsupported models answer with zeros
            s = unique_slug(slug(space), taken, index)
            for col, suffix, label in ((10, "rhit", "Read Hit Rate"), (11, "whit", "Write Hit Rate")):
                value = cint(row, col)
                if value is not None:
                    out.append(Reading.percentage(make_key("fc", s, suffix), "SSD Cache %s %s" % (space, label),
                                                  float(value), used=False))
        return out


class GpuCollector(Collector):
    """GPU load (only models with a GPU card, e.g. DVA3221)."""

    name = "gpu"
    groups = ("gpu",)
    min_level = LEVEL_FULL

    def collect(self, client: SnmpClient) -> List[Reading]:
        got = client.get_many([oids.GPU_SUPPORTED, oids.GPU_UTIL, oids.GPU_MEM_UTIL])
        supported = got.get(oids.GPU_SUPPORTED)
        if supported is None or supported.as_int() != 0:        # 0 = supported, 1 = unsupported
            return []
        out: List[Reading] = []
        for oid, key, name in ((oids.GPU_UTIL, "gpu.util", "GPU Utilization"),
                               (oids.GPU_MEM_UTIL, "gpu.mem", "GPU Memory")):
            vb = got.get(oid)
            if vb is not None and vb.as_float() is not None:
                out.append(Reading.percentage(key, name, vb.as_float(), used=False))
        return out
