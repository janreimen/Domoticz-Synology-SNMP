"""System health: overall status, power, fans, thermal state, temperature, versions."""
from __future__ import annotations

from typing import List

from .. import oids
from ..config import LEVEL_BASIC
from ..model import Reading
from ..snmpcli import SnmpClient
from .base import Collector


class SystemCollector(Collector):
    name = "system"
    groups = ("sys",)
    min_level = LEVEL_BASIC

    #: (key, device name, OID) for the Normal/Failed style status objects.
    STATUS_OBJECTS = (
        ("sys.status", "System Status", oids.SYS_STATUS),
        ("sys.power", "Power Supply", oids.SYS_POWER),
        ("sys.fan", "System Fan", oids.SYS_FAN),
        ("sys.cpufan", "CPU Fan", oids.SYS_CPU_FAN),
        ("sys.thermal", "Thermal Status", oids.SYS_THERMAL),
    )

    def collect(self, client: SnmpClient) -> List[Reading]:
        wanted = [oid for _, _, oid in self.STATUS_OBJECTS]
        wanted += [oids.SYS_TEMP, oids.SYS_MODEL, oids.SYS_VERSION, oids.SYS_UPGRADE]
        got = client.get_many(wanted)
        out: List[Reading] = []

        # Each object only exists if the hardware has it (fanless models have no fan OID).
        for key, name, oid in self.STATUS_OBJECTS:
            vb = got.get(oid)
            code = vb.as_int() if vb else None
            if code is None:
                continue
            text, level = oids.NORMAL_FAILED.get(code, ("Unknown (%d)" % code, 0))
            out.append(Reading.alert(key, name, level, text))

        temp = got.get(oids.SYS_TEMP)
        if temp and temp.as_float() is not None:
            out.append(Reading.temperature("sys.temp", "System Temperature", temp.as_float()))

        model = got.get(oids.SYS_MODEL)
        version = got.get(oids.SYS_VERSION)
        if model or version:
            parts = [p for p in (model.as_text() if model else "", version.as_text() if version else "") if p]
            out.append(Reading.text("sys.info", "System Info", "  |  ".join(parts)))

        upgrade = got.get(oids.SYS_UPGRADE)
        if upgrade and upgrade.as_int() is not None:
            text, level = oids.UPGRADE_STATUS.get(upgrade.as_int(), ("Unknown", 0))
            out.append(Reading.alert("sys.update", "DSM Update", level, text))

        return out
