"""A drop-in for SnmpClient that answers from an in-memory Mib (no processes, no network)."""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from synoSNMP import snmpparse as P
from synoSNMP.config import Settings
from synoSNMP.snmpcli import SnmpClient, SnmpTimeout

from mibdata import Mib, okey

_TYPES = {"integer": P.INTEGER, "gauge": P.GAUGE, "counter": P.COUNTER, "counter64": P.COUNTER64,
          "string": P.STRING, "timeticks": P.TIMETICKS, "objectid": P.OID, "float": P.FLOAT}


class FakeClient(SnmpClient):
    def __init__(self, mib: Mib, settings: Settings = None) -> None:
        super().__init__(settings or Settings(host="fake"), runner=lambda argv, t: (0, "", ""))
        self.mib = mib
        self.down = False          # simulate an unreachable agent
        self.fail_prefixes = []    # OID prefixes that raise a plain SnmpError

    def _vb(self, oid: str) -> P.Varbind:
        vtype, value = self.mib.data[oid]
        return P.Varbind(oid=oid, type=_TYPES[vtype], value=value, raw=str(value))

    def _check(self, oid: str) -> None:
        if self.down:
            raise SnmpTimeout("Timeout: No Response from fake")
        from synoSNMP.snmpcli import SnmpError
        for p in self.fail_prefixes:
            if oid.startswith(p):
                raise SnmpError("simulated failure for %s" % oid)

    def get_many(self, oids):
        self.calls += 1
        result = {}
        for oid in oids:
            oid = oid.lstrip(".")
            self._check(oid)
            if oid in self.mib.data:
                result[oid] = self._vb(oid)
        return result

    def walk(self, oid):
        self.calls += 1
        base = oid.lstrip(".")
        self._check(base)
        prefix = base + "."
        return [self._vb(o) for o, _ in self.mib.sorted_items() if o.startswith(prefix)]
