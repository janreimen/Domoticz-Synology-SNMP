"""CPU, memory, swap, load average and uptime (UCD-SNMP, with Synology fallbacks)."""
from __future__ import annotations

from typing import List, Optional

from .. import oids
from ..config import LEVEL_BASIC, LEVEL_FULL
from ..model import Reading
from ..snmpcli import SnmpClient
from ..util import clamp, pct
from .base import Collector


class ResourceCollector(Collector):
    name = "resources"
    groups = ("res",)
    min_level = LEVEL_BASIC

    def collect(self, client: SnmpClient) -> List[Reading]:
        wanted = [
            oids.UCD_CPU_USER, oids.UCD_CPU_SYSTEM, oids.UCD_CPU_IDLE,
            oids.UCD_MEM_TOTAL, oids.UCD_MEM_AVAIL, oids.UCD_MEM_BUFFER, oids.UCD_MEM_CACHED,
            oids.UCD_SWAP_TOTAL, oids.UCD_SWAP_AVAIL,
            oids.SYS_CPU_UTIL, oids.SYS_MEM_UTIL,
            oids.HR_UPTIME, oids.SYS_UPTIME,
        ] + [oids.UCD_LOAD + ".%d" % n for n in (1, 2, 3)]
        got = client.get_many(wanted)

        def num(oid: str) -> Optional[float]:
            vb = got.get(oid)
            return vb.as_float() if vb else None

        out: List[Reading] = []

        cpu = self._cpu(num)
        if cpu is not None:
            out.append(Reading.percentage("res.cpu", "CPU", cpu))

        mem_pct, free_mb = self._memory(num)
        if mem_pct is not None:
            out.append(Reading.percentage("res.mem", "Memory", mem_pct))
        if free_mb is not None:
            out.append(Reading.custom("res.memfree", "Memory Available", free_mb, "MB", 0))

        swap_total, swap_avail = num(oids.UCD_SWAP_TOTAL), num(oids.UCD_SWAP_AVAIL)
        if swap_total and swap_total > 0 and swap_avail is not None:
            swap_pct = pct(swap_total - swap_avail, swap_total)
            out.append(Reading.percentage("res.swap", "Swap", swap_pct, used=True))

        loads = [num(oids.UCD_LOAD + ".%d" % n) for n in (1, 2, 3)]
        if loads[0] is not None:
            out.append(Reading.custom("res.load1", "Load 1 min", loads[0] / 100.0, "", 2))
        if self.ctx.level >= LEVEL_FULL:
            if loads[1] is not None:
                out.append(Reading.custom("res.load5", "Load 5 min", loads[1] / 100.0, "", 2, used=False))
            if loads[2] is not None:
                out.append(Reading.custom("res.load15", "Load 15 min", loads[2] / 100.0, "", 2, used=False))

        ticks = num(oids.HR_UPTIME)
        if ticks is None:
            ticks = num(oids.SYS_UPTIME)
        if ticks is not None:
            out.append(Reading.custom("res.uptime", "Uptime", ticks / 100.0 / 86400.0, "days", 2, used=False))

        return out

    @staticmethod
    def _cpu(num) -> Optional[float]:
        user, system, idle = num(oids.UCD_CPU_USER), num(oids.UCD_CPU_SYSTEM), num(oids.UCD_CPU_IDLE)
        if user is not None and system is not None:
            return clamp(user + system)
        if idle is not None:
            return clamp(100.0 - idle)
        synology = num(oids.SYS_CPU_UTIL)
        return clamp(synology) if synology is not None else None

    @staticmethod
    def _memory(num):
        """Used memory excludes buffers and cache (what DSM calls usable)."""
        total, free = num(oids.UCD_MEM_TOTAL), num(oids.UCD_MEM_AVAIL)
        if total and total > 0 and free is not None:
            buffers = num(oids.UCD_MEM_BUFFER) or 0.0
            cached = num(oids.UCD_MEM_CACHED) or 0.0
            reclaimable = free + buffers + cached
            used = total - reclaimable
            return pct(used, total), max(0.0, reclaimable) / 1024.0
        synology = num(oids.SYS_MEM_UTIL)
        return (clamp(synology) if synology is not None else None), None
