"""Network interfaces: link state, negotiated speed and traffic rates (IF-MIB)."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .. import oids
from ..config import LEVEL_STANDARD
from ..model import NONE, OK, WARN, CUSTOM, Reading
from ..snmpcli import SnmpClient
from ..util import make_key, slug
from .base import Collector, natural_key, unique_slug

# ifType values that are real (or bonded / VLAN) Ethernet and Wi-Fi ports
_WANTED_TYPES = {6, 71, 117, 135, 161}
# Software interfaces that are not hardware ports
_VIRTUAL = re.compile(
    r"^(lo|docker\d*|veth|br-|virbr|sit\d|tun|tap|dummy|vnet|ifb\d|gre|ip6|ovs[_-]system|macvlan|tunl|teql|bonding_masters)",
    re.I,
)

_OPER = {1: ("Up", OK), 2: ("Down", WARN), 3: ("Testing", WARN), 5: ("Dormant", NONE), 6: ("Not present", NONE)}


def is_hardware_port(name: str, if_type: Optional[int]) -> bool:
    if not name or _VIRTUAL.match(name):
        return False
    return if_type is None or if_type in _WANTED_TYPES


class NetworkCollector(Collector):
    name = "network"
    groups = ("net",)
    min_level = LEVEL_STANDARD

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.ports: Dict[str, str] = {}     # ifIndex -> ifName

    # -- discovery -------------------------------------------------------------
    def discover(self, client: SnmpClient) -> None:
        names = {vb.oid.rsplit(".", 1)[-1]: vb.as_text() for vb in client.walk(oids.IF_XTABLE + ".1")}
        if not names:                                    # agent without IF-MIB ifXTable
            names = {vb.oid.rsplit(".", 1)[-1]: vb.as_text() for vb in client.walk(oids.IF_TABLE + ".2")}
        types = {vb.oid.rsplit(".", 1)[-1]: vb.as_int() for vb in client.walk(oids.IF_TABLE + ".3")}
        self.ports = {idx: name for idx, name in names.items() if is_hardware_port(name, types.get(idx))}
        self.ctx.rates.forget_all()
        self.ctx.debug("network ports: %s" % ", ".join("%s=%s" % kv for kv in sorted(self.ports.items(), key=lambda kv: natural_key(kv[0]))))

    # -- polling -----------------------------------------------------------------
    def collect(self, client: SnmpClient) -> List[Reading]:
        if not self.ports:
            self.discover(client)
        got = self._fetch(client)
        if got is None:                                  # ifIndex numbering changed
            self.discover(client)
            got = self._fetch(client) or {}
        now = self.ctx.clock()
        out: List[Reading] = []
        taken: set = set()
        for idx in sorted(self.ports, key=natural_key):
            name = self.ports[idx]
            s = unique_slug(slug(name), taken, idx)

            oper = got.get(oids.IF_TABLE + ".8." + idx)
            speed = got.get(oids.IF_XTABLE + ".15." + idx)
            code = oper.as_int() if oper else None
            if code is not None:
                text, level = _OPER.get(code, ("Unknown (%d)" % code, NONE))
                mbit = speed.as_int() if speed else None
                if code == 1 and mbit:
                    text += " | %s" % _speed(mbit)
                out.append(Reading.alert(make_key("net", s, "link"), name + " Link", level, text))

            for direction, hc_col, lo_col, label in (("rx", 6, 10, "Received"), ("tx", 10, 16, "Sent")):
                value, bits = self._counter(got, idx, hc_col, lo_col)
                key = make_key("net", s, direction)
                rate = self.ctx.rates.update(key, value, now, bits)
                title = "%s %s" % (name, label)
                if rate is None:
                    out.append(Reading.waiting(key, title, CUSTOM, "Mbit/s"))
                else:
                    out.append(Reading.custom(key, title, rate * 8.0 / 1e6, "Mbit/s", 2))
        return out

    def _fetch(self, client: SnmpClient) -> Optional[dict]:
        wanted = []
        for idx in self.ports:
            wanted += [
                oids.IF_XTABLE + ".1." + idx, oids.IF_TABLE + ".8." + idx, oids.IF_XTABLE + ".15." + idx,
                oids.IF_XTABLE + ".6." + idx, oids.IF_XTABLE + ".10." + idx,
            ]
        got = client.get_many(wanted)
        # 32-bit counters only for ports that have no 64-bit ones
        fallback = []
        for idx in self.ports:
            if oids.IF_XTABLE + ".6." + idx not in got:
                fallback += [oids.IF_TABLE + ".10." + idx, oids.IF_TABLE + ".16." + idx]
        if fallback:
            got.update(client.get_many(fallback))
        for idx, name in self.ports.items():
            vb = got.get(oids.IF_XTABLE + ".1." + idx)
            if vb is not None and vb.as_text() != name:
                return None
        return got

    def _counter(self, got: dict, idx: str, hc_col: int, lo_col: int):
        hc = got.get(oids.IF_XTABLE + ".%d.%s" % (hc_col, idx))
        if hc is not None:
            return hc.as_int(), 64
        lo = got.get(oids.IF_TABLE + ".%d.%s" % (lo_col, idx))
        return (lo.as_int() if lo is not None else None), 32


def _speed(mbit: int) -> str:
    return "%g Gbit/s" % (mbit / 1000.0) if mbit >= 1000 else "%d Mbit/s" % mbit
