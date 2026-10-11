# Domoticz-Synology-SNMP

Domoticz plugin that monitors a **Synology NAS** through the native **net-snmp command line tools**
(`snmpget`, `snmpbulkwalk`, `snmpwalk`). No `pysnmp`, no Python packages at all. The installed hardware is
**detected automatically** and turned into Domoticz devices.

## Requirements

On the Domoticz host (for example `rpi4office`):

```sh
sudo apt install snmp          # provides snmpget / snmpwalk / snmpbulkwalk
```

On the NAS: *Control Panel > Terminal & SNMP > SNMP service*: enable SNMP and set a v1/v2c community
or an SNMPv3 user. Python 3.7 or newer.

Check that it works before adding the hardware:

```sh
snmpget -v2c -c public -On -m "" NAS_IP 1.3.6.1.4.1.6574.1.5.1.0      # -> STRING: "DS920+"
```

## Install

```sh
cd /srv/domoticz/plugins
cp -r /path/to/Domoticz-Synology-SNMP Domoticz-Synology-Plugin       # plugin.py + synoSNMP/ must stay together
sudo systemctl restart domoticz
```

Then *Setup > Hardware*, type **Synology NAS (net-snmp)**. This is a **new hardware type** (key `SynologySNMP`),
separate from the old plugin; the old devices are not migrated.

## Settings

| Field | Meaning |
|---|---|
| NAS address / SNMP port | host name or IP, default 161 |
| SNMP version | v2c (default), v1 or v3 |
| Community | v1/v2c community |
| SNMPv3 user / passphrase | v3 user and authentication passphrase (min. 8 characters) |
| SNMPv3 options | `level;authProtocol;privProtocol;privPassphrase`, e.g. `authPriv;SHA;AES;`. Empty privacy passphrase re-uses the authentication passphrase |
| Poll interval | seconds, minimum 10, default 60 |
| Detail level | Basic / Standard / Full (below) |
| Debug | verbose log: discovered hardware, SNMP call count and duration per poll |

Note: the community / passphrases are passed on the `snmpget` command line, so they are visible to local users
in `ps` while a request runs. Use an SNMPv3 user with a read-only view on a shared host, and restrict SNMP on the
NAS to the Domoticz host's IP (DSM firewall).

## What is detected and created

| Detail level | Area | Devices |
|---|---|---|
| Basic | System | system / power / fan / CPU-fan / thermal status (only those the model has), temperature, model + DSM version, DSM update available |
| | CPU, memory | CPU %, memory % (excluding cache/buffers), memory available (MB), swap %, load 1/5/15 min, uptime |
| | Drives | per drive: health (status + DSM health + bad sectors), temperature, remaining life (SSD/NVMe), bad sectors; one "Disks Overall" summary. **Number of drives is read from the NAS** |
| | RAID | status of every storage pool and volume (degraded, repairing, scrubbing, crashed ...) with free/total space |
| | Volumes | used % and free GB of every volume, USB/eSATA share and the DSM system partition |
| Standard | Network | per hardware port (eth*, bond*, ovs_eth*, wlan*): link state + speed, received and sent Mbit/s. Docker/veth/loopback are ignored |
| | UPS | status, battery charge, load, runtime (only if a UPS is attached) |
| | Expansion units | power state (DX/RX series, only if attached) |
| Full | Drive I/O | per drive: utilization %, read and write MB/s |
| | SMART | per drive: health from reallocated / pending / uncorrectable sectors, power-on hours |
| | Services | logged-in users per service (HTTP, CIFS, NFS, SSH ...) |
| | SSD cache, GPU | cache hit rates / GPU load, only on models that have them |

Less important devices (drive I/O, SMART, services, 5/15-minute load, uptime) are created **not visible by default**
(Domoticz "Used" off); add them under *Setup > Devices*. Alert devices use Domoticz' colors:
grey = n/a, green = OK, yellow = warning or busy (for example scrubbing), orange = degraded, red = critical.

Devices are matched by `DeviceID`, never by unit number. A drive or port that disappears is flagged **timed out**
(not deleted); a new one gets a new device on the next poll. Network interfaces and mounts are re-detected hourly
and immediately if the NAS renumbers them.

## How it works

```
plugin.py            Domoticz glue only (manifest + callbacks)
synoSNMP/
  config.py          settings from Domoticz Parameters (validated)
  snmpcli.py         runs snmpget / snmpbulkwalk / snmpwalk (the only module that starts processes)
  snmpparse.py       parses their output (pure functions)
  oids.py            OIDs and status tables (Synology MIB guide)
  engine.py          probe, discovery schedule, collector isolation, de-duplicated error logging
  poller.py          background thread
  devices.py         the only module that touches the Domoticz device API
  model.py util.py   Reading type, key/slug/rate helpers
  collectors/        system  resources  disks  storage(raid, mounts)  network  ups
                     diskio  smart  services  extras(ebox, ssd cache, gpu)
```

A collector returns `Reading` objects and knows nothing about Domoticz; the device layer knows nothing about SNMP.
Adding a new area = one new file in `collectors/` plus one line in `collectors/__init__.py`.

## Tests

```sh
python3 -m unittest discover -s tests          # 79 tests
```

The suite includes end-to-end tests against a **real `snmpd`** serving a DS920+-like data set (skipped
automatically if `snmpd` is not installed): real binaries, SNMP v1/v2c/v3, timeouts, wrong credentials, aborting a
request, and the actual `plugin.py` with a Domoticz stub.

## Troubleshooting

* *"No SNMP answer"*: wrong address/community, SNMP disabled in DSM, or the NAS firewall blocks the Domoticz host.
  A wrong community looks exactly like a timeout in SNMP v1/v2c.
* *"does not serve the Synology MIB"*: the agent answers but 1.3.6.1.4.1.6574 is not visible (restricted view, or not a Synology).
* A device you expect is missing: set Debug and look at the "mounts: ..." / "network ports: ..." / "UPS ..." lines,
  or compare with `snmpwalk -v2c -c public -On -m "" NAS_IP 1.3.6.1.4.1.6574.2`.
* `tools/capture_mib.sh NAS_IP > dump.txt` captures everything the plugin reads (redact serial numbers before sharing).
