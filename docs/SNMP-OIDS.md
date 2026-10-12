# SNMP objects used

Everything the plugin reads. OIDs are numeric; the plugin runs the net-snmp tools with `-On -Oe -m ""`, so **no MIB
files are needed** on the Domoticz host. Table columns are written `<table entry OID>.<column>.<row index>`.

Source column: **guide** = Synology's *DiskStation MIB Guide* (enterprise `1.3.6.1.4.1.6574`); **std** = standard MIB
that DSM also serves; **old plugin** = taken from the previous plugin version and **not verified** against a guide or a
real NAS. Nothing here has been checked against a real DSM yet; see the README.

Use `tools/capture_mib.sh NAS_IP` to record what your NAS returns for all of these.

## Synology System MIB `1.3.6.1.4.1.6574.1`

| OID | Object | Used for | Source |
|---|---|---|---|
| `.1.1.0` | systemStatus (1 normal, 2 failed) | `sys.status` | guide |
| `.1.2.0` | temperature (°C) | `sys.temp` | guide |
| `.1.3.0` | powerStatus | `sys.power` | guide |
| `.1.4.1.0` / `.1.4.2.0` | systemFanStatus / cpuFanStatus | `sys.fan`, `sys.cpufan` (only if the model answers) | guide |
| `.1.5.1.0` | modelName | connection probe, `sys.info`. **No answer = "not a Synology"** | guide |
| `.1.5.2.0` | serialNumber | debug log only | guide |
| `.1.5.3.0` | version (DSM) | `sys.info` | guide |
| `.1.5.4.0` | upgradeAvailable (1 available, 2 up to date, 3 connecting, 4 disconnected, 5 others) | `sys.update` | guide |
| `.1.7.1.0` / `.1.7.2.0` | cpuUtilization / memUtilization | fallback for `res.cpu` / `res.mem` when UCD-SNMP is not served | guide |
| `.1.8.0` | thermalStatus | `sys.thermal` | guide |

## Tables

| Table entry OID | Columns read | Collector, devices | Source |
|---|---|---|---|
| Disk `.6574.2.1.1` | 2 id, 3 model, 4 type, 5 status, 6 temperature, 7 role, 9 bad sectors, 11 remaining life, 12 name, 13 health | `disks`: per drive and cache drive health / temperature / remaining life / bad sectors. The drive count is whatever rows exist | guide |
| RAID `.6574.3.1.1` | 2 name, 3 status, 4 free, 5 total, 6 hot spare | `raid`: pool / volume status with free and total space | guide |
| UPS `.6574.4` | see below | `ups` | guide + old plugin |
| SMART `.6574.5.1.1` | 2 device, 3 attribute name, 4 attribute id, 5 current, 6 worst, 7 threshold, 8 raw, 9 status, 10 raw 64-bit | `smart`: ids 5, 9, 187, 196, 197, 198, 199 | guide |
| Services `.6574.6.1.1` | 2 service name, 3 users | `services` | guide |
| StorageIO `.6574.101.1.1` | 2 device, 3/4 read/write bytes (32-bit), 8 load, 12/13 read/write bytes (64-bit) | `diskio`: whole drives only (sata, sas, sd, nvme; not partitions, md, dm) | guide |
| FlashCache `.6574.103.1.1` | 3 space, 10 read hit rate, 11 write hit rate | `flashcache` (models without SSD cache answer with zeros: ignored) | guide |
| Ebox `.6574.105.1.1` | 2 model, 3 power, 4 redundant power | `ebox`: expansion units | guide |
| GPU `.6574.108` scalars | `.1.0` supported (0 yes, 1 no), `.2.0` utilisation, `.3.0` memory utilisation | `gpu` | guide |

### UPS `1.3.6.1.4.1.6574.4`

Instances normally carry a trailing `.0`. The collector walks the whole subtree once per poll and picks these suffixes:

| Suffix | Object | Source |
|---|---|---|
| `1.1` | upsDeviceModel | guide |
| `1.2` | upsDeviceManufacturer | guide |
| `2.1` | upsInfoStatus (NUT style tokens: `OL`, `OB`, `LB`, `CHRG`, ...) | guide |
| `2.12.1` | upsInfoLoadValue (%) | guide |
| `3.1.1` | upsBatteryChargeValue (%) | guide |
| `3.1.4` | upsBatteryChargeWarning (%) | guide |
| `3.6.1` | runtime in seconds | **old plugin, unverified** |

Some firmware returns these values as Opaque floats (`Opaque: Float: 100.000000`, or raw BER hex `9f 78 04 ...`);
`snmpparse.py` decodes both. Which one real DSM versions use is not verified.

## Standard MIBs

| OID | MIB / object | Used for | Source |
|---|---|---|---|
| `1.3.6.1.2.1.1.1.0` | sysDescr | connection probe | std |
| `1.3.6.1.2.1.25.1.1.0` | hrSystemUptime (`1.3.6.1.2.1.1.3.0` sysUpTime as fallback) | `res.uptime` | std |
| `1.3.6.1.2.1.25.2.3.1` | hrStorageTable: 2 type, 3 descr, 4 allocation units, 5 size, 6 used | `mounts`: volumes (`/volumeN`, `/volumeUSBn/usbshare`, `/`) used % and free space | std |
| `1.3.6.1.2.1.2.2.1` | ifTable: 2 descr, 3 type, 8 operStatus, 10 inOctets, 16 outOctets | `network`: port detection, link state, 32-bit counters | std |
| `1.3.6.1.2.1.31.1.1.1` | ifXTable: 1 name, 6 HC in, 10 HC out, 15 high speed, 18 alias | `network`: 64-bit counters (preferred), speed | std |
| `1.3.6.1.4.1.2021.4.3.0` / `.4.0` | UCD-SNMP memory: swap total / available (kB) | `res.swap` | std |
| `1.3.6.1.4.1.2021.4.5.0` / `.6.0` / `.14.0` / `.15.0` | total / available real memory, buffers, cached (kB) | `res.mem`, `res.memfree` (cache and buffers excluded) | std |
| `1.3.6.1.4.1.2021.10.1.5.{1,2,3}` | laLoadInt (load x 100) | `res.load1/5/15` | std |
| `1.3.6.1.4.1.2021.11.{9,10,11}.0` | ssCpuUser / ssCpuSystem / ssCpuIdle | `res.cpu` (Synology `.1.7.1.0` is the fallback) | std |

## Detection rules worth knowing

* **Row indexes are never trusted.** Interfaces are identified by their name, mounts by their description, drives
  by their id / name in the table; a renumbering after a DSM update re-runs detection instead of mixing up devices.
* **Network ports** are the interfaces of ifType Ethernet / bond / VLAN / Wi-Fi whose names do not look virtual
  (`lo`, `docker*`, `veth*`, `br-*`, `virbr*`, `tun*`, `tap*`, `ovs-system`, ...). Open vSwitch ports `ovs_eth*` are kept.
* **Volumes** are the hrStorage rows whose description is `/` or starts with `/volume` and has at most one more
  path level (`/volume1`, `/volumeUSB1/usbshare`); sub-mounts containing `@` (`/volume1/@docker/...`) are ignored.
* **Counters** (network, drive I/O) become rates between two polls. A 32-bit counter that wrapped is corrected; a
  64-bit counter that went backwards (reboot) yields no value for that one sample instead of a spike. 64-bit
  counters are preferred, 32-bit ones are the fallback.
* **GET size.** Scalar reads are sent in groups of at most 20 OIDs per `snmpget` to stay below UDP size limits.
* **SNMP v1** has no GETBULK: the plugin falls back to `snmpwalk` and treats `noSuchName` as "absent".
