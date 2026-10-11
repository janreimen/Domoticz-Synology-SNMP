"""In-memory MIB fixtures for a DS920+-like and a DS218-like NAS.

``Mib`` stores OID -> (type, value).  The same data feeds
  * ``FakeClient``  (fast, deterministic collector tests), and
  * ``pass_persist_synology.py`` (serves it through a real snmpd for end-to-end tests).
"""
from __future__ import annotations

from typing import Dict, Iterable, Tuple

SYNO = "1.3.6.1.4.1.6574"
HRS = "1.3.6.1.2.1.25.2.3.1"
IFT = "1.3.6.1.2.1.2.2.1"
IFX = "1.3.6.1.2.1.31.1.1.1"
FIXED = "1.3.6.1.2.1.25.2.1.4"
RAM = "1.3.6.1.2.1.25.2.1.2"


def okey(oid: str):
    return tuple(int(p) for p in oid.split("."))


class Mib:
    def __init__(self) -> None:
        self.data: Dict[str, Tuple[str, object]] = {}

    def add(self, oid: str, vtype: str, value) -> "Mib":
        self.data[oid] = (vtype, value)
        return self

    def row(self, base: str, index, cols: Dict[int, Tuple[str, object]]) -> "Mib":
        for col, (vtype, value) in cols.items():
            self.add("%s.%d.%s" % (base, col, index), vtype, value)
        return self

    def remove_prefix(self, prefix: str) -> "Mib":
        for oid in [o for o in self.data if o == prefix or o.startswith(prefix + ".")]:
            del self.data[oid]
        return self

    def sorted_items(self) -> Iterable[Tuple[str, Tuple[str, object]]]:
        return sorted(self.data.items(), key=lambda kv: okey(kv[0]))


I, G, C64, S, T, O, F = "integer", "gauge", "counter64", "string", "timeticks", "objectid", "float"


def _disk(m: Mib, idx, name, model, kind, status, temp, role, bad, life, health):
    m.row(SYNO + ".2.1.1", idx, {
        2: (S, name.replace("Drive ", "Disk ")), 3: (S, model), 4: (S, kind), 5: (I, status), 6: (I, temp),
        7: (S, role), 8: (I, 0), 9: (I, bad), 10: (I, 0), 11: (I, life), 12: (S, name), 13: (I, health),
    })


def _system(m: Mib, model: str, version: str, temp: int, cpu_fan: bool) -> None:
    m.add(SYNO + ".1.1.0", I, 1).add(SYNO + ".1.2.0", I, temp).add(SYNO + ".1.3.0", I, 1)
    m.add(SYNO + ".1.4.1.0", I, 1)
    if cpu_fan:
        m.add(SYNO + ".1.4.2.0", I, 1)
    m.add(SYNO + ".1.5.1.0", S, model).add(SYNO + ".1.5.2.0", S, "2060PDN123456")
    m.add(SYNO + ".1.5.3.0", S, version).add(SYNO + ".1.5.4.0", I, 2).add(SYNO + ".1.8.0", I, 1)
    m.add("1.3.6.1.2.1.1.1.0", S, "Linux %s 4.4.302+" % model.lower())


def ds920() -> Mib:
    """4 HDD + 2 NVMe cache, pool + volume, USB share, bond + 2 ports, UPS, Docker noise."""
    m = Mib()
    _system(m, "DS920+", "DSM 7.2.1-69057 Update 5", 41, cpu_fan=False)

    for i, (temp, bad) in enumerate([(34, 0), (36, 0), (37, 0), (38, 3)]):
        _disk(m, i, "Drive %d" % (i + 1), "WD40EFRX-68N32N0", "HDD", 1, temp, "data", bad, -1, 1)
    _disk(m, 4, "Cache device 1", "Samsung SSD 970 EVO 500GB", "NVMe", 1, 41, "ssd_cache", 0, 98, 1)
    _disk(m, 5, "Cache device 2", "Samsung SSD 970 EVO 500GB", "NVMe", 1, 43, "ssd_cache", 0, 97, 1)

    tib = 1024 ** 4
    m.row(SYNO + ".3.1.1", 0, {2: (S, "Storage Pool 1"), 3: (I, 1), 4: (C64, 3 * tib), 5: (C64, 21 * tib), 6: (I, 0)})
    m.row(SYNO + ".3.1.1", 1, {2: (S, "Volume 1"), 3: (I, 1), 4: (C64, 3 * tib), 5: (C64, 20 * tib), 6: (I, 0)})

    # hrStorage: RAM/virtual memory, system partition, volume, Docker sub-mounts, USB share, tmpfs
    mounts = [
        (1, RAM, "Physical memory", 1024, 8000000, 4000000),
        (3, "1.3.6.1.2.1.25.2.1.3", "Virtual memory", 1024, 10000000, 4000000),
        (31, FIXED, "/", 4096, 622000, 250000),
        (41, FIXED, "/volume1", 4096, 5368709120, 1073741824),         # 20 TiB, 20 % used... see test
        (42, FIXED, "/volume1/@docker", 4096, 5368709120, 1073741824),
        (43, FIXED, "/volume1/@docker/btrfs/subvolumes/ab12cd", 4096, 5368709120, 1073741824),
        (44, FIXED, "/volumeUSB1/usbshare", 4096, 122000000, 61000000),
        (50, FIXED, "/tmp", 4096, 1000, 10),
        (51, FIXED, "/dev/shm", 4096, 1000, 10),
    ]
    for idx, kind, descr, units, size, used in mounts:
        m.row(HRS, idx, {1: (I, idx), 2: (O, kind), 3: (S, descr), 4: (I, units), 5: (I, size), 6: (I, used)})

    # interfaces: lo, eth0, eth1, bond0, docker0 and a veth
    ifs = [(1, "lo", 24, 1, 0), (2, "eth0", 6, 1, 1000), (3, "eth1", 6, 2, 0),
           (4, "bond0", 161, 1, 2000), (5, "docker0", 6, 1, 10000), (6, "veth9a8b7c", 6, 1, 10000)]
    for idx, name, typ, oper, speed in ifs:
        m.row(IFT, idx, {2: (S, name), 3: (I, typ), 8: (I, oper), 10: (G, 1000 * idx), 16: (G, 500 * idx)})
        m.row(IFX, idx, {1: (S, name), 6: (C64, 10 ** 9 * idx), 10: (C64, 5 * 10 ** 8 * idx), 15: (G, speed)})

    # UCD-SNMP
    m.add("1.3.6.1.4.1.2021.11.9.0", I, 5).add("1.3.6.1.4.1.2021.11.10.0", I, 3).add("1.3.6.1.4.1.2021.11.11.0", I, 92)
    m.add("1.3.6.1.4.1.2021.4.5.0", I, 8000000).add("1.3.6.1.4.1.2021.4.6.0", I, 500000)
    m.add("1.3.6.1.4.1.2021.4.14.0", I, 100000).add("1.3.6.1.4.1.2021.4.15.0", I, 4000000)
    m.add("1.3.6.1.4.1.2021.4.3.0", I, 2097148).add("1.3.6.1.4.1.2021.4.4.0", I, 2000000)
    for n, v in ((1, 52), (2, 60), (3, 55)):
        m.add("1.3.6.1.4.1.2021.10.1.5.%d" % n, I, v)
    m.add("1.3.6.1.2.1.25.1.1.0", T, 8640000)

    # UPS (floats as net-snmp prints Opaque floats)
    for suffix, vtype, value in (("1.1.0", S, "CP1500EPFCLCD"), ("1.2.0", S, "CPS"), ("2.1.0", S, "OL CHRG"),
                                 ("2.12.1.0", F, 21.0), ("3.1.1.0", F, 100.0), ("3.1.4.0", F, 30.0),
                                 ("3.6.1.0", I, 3480), ("4.1.1.0", F, 230.0)):
        m.add(SYNO + ".4." + suffix, vtype, value)

    # services
    for i, (name, users) in enumerate([("HTTP", 1), ("CIFS", 2), ("AFP", 0), ("NFS", 0),
                                       ("FTP", 0), ("SFTP", 0), ("TELNET", 0), ("SSH", 1)], start=1):
        m.row(SYNO + ".6.1.1", i, {2: (S, name), 3: (I, users)})

    # storage IO: 4 SATA + 2 NVMe + md/dm devices that must be ignored
    devs = ["sata1", "sata2", "sata3", "sata4", "nvme0n1", "nvme1n1", "md0", "md1", "md2", "dm-0", "sda1"]
    for i, dev in enumerate(devs):
        m.row(SYNO + ".101.1.1", i, {2: (S, dev), 3: (G, 1000 * (i + 1)), 4: (G, 2000 * (i + 1)), 8: (I, 3 + i),
                                     12: (C64, 10 ** 10 * (i + 1)), 13: (C64, 2 * 10 ** 10 * (i + 1))})

    # SMART: sata1 healthy, sata4 with pending sectors
    row = 0
    for dev, attrs in (("/dev/sata1", [(5, "Reallocated_Sector_Ct", 100, 100, 5, 0), (9, "Power_On_Hours", 60, 60, 0, 31500),
                                       (197, "Current_Pending_Sector", 200, 200, 0, 0)]),
                       ("/dev/sata4", [(5, "Reallocated_Sector_Ct", 100, 100, 5, 0), (9, "Power_On_Hours", 70, 70, 0, 12000),
                                       (197, "Current_Pending_Sector", 198, 198, 0, 2)])):
        for attr_id, name, cur, worst_, thr, raw in attrs:
            m.row(SYNO + ".5.1.1", row, {2: (S, dev), 3: (S, name), 4: (I, attr_id), 5: (I, cur), 6: (I, worst_),
                                         7: (I, thr), 8: (I, raw), 9: (S, "OK")})
            row += 1

    # SSD cache
    m.row(SYNO + ".103.1.1", 0, {2: (S, "nvme0n1p3"), 3: (S, "cachedev_0"), 10: (I, 83), 11: (I, 12)})
    return m


def ds218() -> Mib:
    """Minimal 2-bay model: no UPS, no SMART table, no cpu fan, 32-bit counters, CPU idle only."""
    m = Mib()
    _system(m, "DS218+", "DSM 7.1.1-42962 Update 6", 38, cpu_fan=False)
    _disk(m, 0, "Drive 1", "ST4000VN008", "HDD", 1, 33, "data", 0, -1, 1)
    _disk(m, 1, "Drive 2", "ST4000VN008", "HDD", 1, 35, "data", 0, -1, 1)
    tib = 1024 ** 4
    m.row(SYNO + ".3.1.1", 0, {2: (S, "Volume 1"), 3: (I, 1), 4: (C64, 2 * tib), 5: (C64, 3 * tib)})
    m.row(HRS, 41, {1: (I, 41), 2: (O, FIXED), 3: (S, "/volume1"), 4: (I, 4096), 5: (I, 1000000), 6: (I, 250000)})
    for idx, name, speed in ((1, "lo", 0), (2, "eth0", 1000)):
        m.row(IFT, idx, {2: (S, name), 3: (I, 24 if name == "lo" else 6), 8: (I, 1), 10: (G, 4000000000 + idx), 16: (G, 100)})
        m.row(IFX, idx, {1: (S, name), 15: (G, speed)})
    m.add("1.3.6.1.4.1.2021.11.11.0", I, 80)
    m.add("1.3.6.1.4.1.2021.4.5.0", I, 2000000).add("1.3.6.1.4.1.2021.4.6.0", I, 100000)
    m.add("1.3.6.1.4.1.2021.4.14.0", I, 50000).add("1.3.6.1.4.1.2021.4.15.0", I, 700000)
    m.add("1.3.6.1.2.1.1.3.0", T, 100)
    return m
