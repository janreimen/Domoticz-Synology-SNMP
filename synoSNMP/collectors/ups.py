"""UPS attached to the NAS (Synology UPS MIB).  Absent UPS = no devices."""
from __future__ import annotations

from typing import Dict, List, Optional

from .. import oids
from ..config import LEVEL_FULL, LEVEL_STANDARD
from ..model import NONE, OK, Reading, worst
from ..snmpcli import SnmpClient
from ..snmpparse import Varbind
from .base import Collector

_KNOWN = (oids.UPS_MODEL, oids.UPS_MANUFACTURER, oids.UPS_STATUS, oids.UPS_LOAD,
          oids.UPS_CHARGE, oids.UPS_CHARGE_WARN, oids.UPS_RUNTIME)


class UpsCollector(Collector):
    name = "ups"
    groups = ("ups",)
    min_level = LEVEL_STANDARD

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.present: Optional[bool] = None
        self._logged_extra = False

    def discover(self, client: SnmpClient) -> None:
        self.present = bool(client.walk(oids.UPS_ROOT))
        self.ctx.debug("UPS %s" % ("detected" if self.present else "not present"))

    def collect(self, client: SnmpClient) -> List[Reading]:
        if self.present is None:
            self.discover(client)
        if not self.present:
            return []
        data: Dict[str, Varbind] = {vb.oid[len(oids.UPS_ROOT) + 1:]: vb for vb in client.walk(oids.UPS_ROOT)}
        if not data:
            return []

        def val(suffix: str) -> Optional[Varbind]:
            return data.get(suffix + ".0") or data.get(suffix)

        if not self._logged_extra:
            self._logged_extra = True
            extra = sorted(k for k in data if k.rsplit(".0", 1)[0] not in _KNOWN and k not in _KNOWN)
            if extra:
                self.ctx.debug("UPS OIDs present but not used: %s" % ", ".join(extra))

        out: List[Reading] = []
        status = val(oids.UPS_STATUS)
        if status is not None and status.as_text():
            out.append(self._status(status.as_text()))

        charge = val(oids.UPS_CHARGE)
        if charge is not None and charge.as_float() is not None:
            out.append(Reading.percentage("ups.charge", "UPS Battery Charge", charge.as_float()))
        load = val(oids.UPS_LOAD)
        if load is not None and load.as_float() is not None:
            out.append(Reading.percentage("ups.load", "UPS Load", load.as_float()))
        runtime = val(oids.UPS_RUNTIME)
        if runtime is not None and runtime.as_float() is not None:
            out.append(Reading.custom("ups.runtime", "UPS Runtime", runtime.as_float() / 60.0, "min", 0))

        if self.ctx.level >= LEVEL_FULL:
            model, maker = val(oids.UPS_MODEL), val(oids.UPS_MANUFACTURER)
            name = " ".join(p for p in (maker.as_text() if maker else "", model.as_text() if model else "") if p)
            if name:
                out.append(Reading.text("ups.info", "UPS Info", name, used=False))
        return out

    @staticmethod
    def _status(raw: str) -> Reading:
        tokens = raw.replace(",", " ").split()
        texts, levels = [], []
        for token in tokens:
            text, level = oids.UPS_TOKENS.get(token.upper(), (token, NONE))
            texts.append(text)
            levels.append(level)
        level = worst(*levels) if levels else NONE
        if level == NONE and any(l == OK for l in levels):
            level = OK
        return Reading.alert("ups.status", "UPS Status", level, " + ".join(texts) or raw)
