# Domoticz-Synology-SNMP

A [Domoticz](https://www.domoticz.com) plugin that monitors a **Synology NAS** through the native
**net-snmp command line tools** (`snmpget`, `snmpbulkwalk`, `snmpwalk`).

* **No `pysnmp`, no Python packages at all.** The only dependency is the `snmp` package of your distribution.
* **The hardware is detected, not configured.** Number of drives, storage pools, volumes, network ports, UPS, fans,
  SSD cache, expansion units: read from the NAS, turned into Domoticz devices. No "DSM OID" setting, no interface
  numbers, no disk count.
* **Never blocks Domoticz.** Polling runs in a background thread; a slow or dead NAS cannot stall the plugin thread.
* **Modular.** Parser, SNMP client, one collector per hardware area, engine and Domoticz glue are separate modules.
  A new area is one new file.

> **Status:** version 0.2.3. Developed and tested against simulated DS920+ / DS218+ data and a real `snmpd`
> serving it, **not yet against a real DSM**. Please read [what is verified and what is not](#what-is-verified-and-what-is-not)
> and run the check in [First run](#first-run-check-what-your-nas-reports) on your NAS before relying on it.

Contents: [Requirements](#requirements) · [Enable SNMP on the NAS](#enable-snmp-on-the-nas) · [Install](#install) ·
[Settings](#settings) · [Devices](#what-is-detected-and-created) · [How it works](#how-it-works) ·
[Command line tool](#first-run-check-what-your-nas-reports) · [Troubleshooting](#troubleshooting) · [FAQ](#faq) ·
[Migrating](#migrating-from-the-old-pysnmp-plugin) · [Development](#development) · [License](#license)

## Requirements

| Where | What |
|---|---|
| Domoticz host | Domoticz with Python plugin support, **Python 3.7+** |
| Domoticz host | `snmpget`, `snmpwalk` (and preferably `snmpbulkwalk`): `sudo apt install snmp` |
| NAS | Synology DSM with the SNMP service enabled |

Without `snmpbulkwalk` (and with SNMP v1) the plugin falls back to plain `snmpwalk`, which is slower.
If Domoticz runs in a container, the tools must be installed inside the container.

## Enable SNMP on the NAS

DSM: *Control Panel > Terminal & SNMP > SNMP*, tick **Enable SNMP service**, then either

* **SNMPv1/v2c:** set a *Community*. Simple, but the community is sent in clear text; `public` is guessable.
* **SNMPv3 (recommended):** tick *Enable SNMPv3 service*, set a user name and a password (at least 8 characters)
  and choose the authentication / privacy protocols. Use the same protocols in the plugin's *SNMPv3 options*.

Then restrict who may ask: *Control Panel > Security > Firewall*, allow UDP 161 from the Domoticz host only.

Check from the Domoticz host (prints the model):

```sh
snmpget -v2c -c public -On -m "" NAS_IP 1.3.6.1.4.1.6574.1.5.1.0
# .1.3.6.1.4.1.6574.1.5.1.0 = STRING: "DS920+"
```

A wrong community or a blocked port both end in `Timeout: No Response`. SNMP cannot tell them apart.

## Install

```sh
cd /srv/domoticz/plugins
git clone https://github.com/janreimen/Domoticz-Synology-SNMP.git      # or unpack the release archive here
sudo systemctl restart domoticz
```

`plugin.py` and the `synoSNMP/` directory must stay together. Or use the bundled
[`deploy.sh`](DEPLOY.md), which installs locally or over SSH, makes a backup, restores ownership, restarts Domoticz
and can verify the NAS afterwards:

```sh
./deploy.sh --host jan@rpi4office --verify 192.168.1.20
```

Then *Setup > Hardware*, type **Synology NAS (net-snmp)**, enter the NAS address and credentials, *Add*.
Full instructions, updates, rollback and removal: **[DEPLOY.md](DEPLOY.md)**.

## Settings

| Field | Meaning |
|---|---|
| NAS address / SNMP port | host name or IP (IPv6 works), port default 161 |
| SNMP version | v2c (default), v1 or v3 |
| Community (v1/v2c) | the community string |
| SNMPv3 user / passphrase | v3 user name and authentication passphrase (min. 8 characters) |
| SNMPv3 options | `level;authProtocol;privProtocol`, for example `authPriv;SHA;AES` (the default). Level: `noAuthNoPriv`, `authNoPriv`, `authPriv`. Auth: MD5, SHA, SHA-224/256/384/512. Privacy: DES, AES, AES-192, AES-256. A fourth `;privPassphrase` part still works but is deprecated: this field is shown in clear text |
| SNMPv3 privacy passphrase | password field, min. 8 characters. Leave it empty to re-use the authentication passphrase. A value shorter than 8 characters is ignored, because versions up to 0.2.1 kept a polling interval (such as `60`) in this field; after an update, clear the field before typing a passphrase into it, as an old number is invisible there and would become the start of it |
| Intervals and tuning | Configured in the plugin-root `.env` file, see [Schedule configuration](#how-it-works): one interval per sensor family (10–86400 seconds) plus SNMP timeout, retries, re-detection and refresh. Copy `.env.sample` to `.env` and edit the values |
| Detail level | Basic / Standard / Full, see below. Default Full |
| Logging level | **Normal**: start/stop, every error (each problem once) and its recovery, and configuration warnings. **Debug**: also the NAS identity and serial number, what each collector detected, where the schedule came from, and a poll summary with the time until the next poll. **Trace**: full internal execution path, including poll-cycle steps, collector discovery/start/skip/result/error decisions, every SNMP request/response, reading and device decisions, heartbeat callbacks, and poll scheduling. SNMP credentials are never logged. |

Run several NAS units by adding the hardware once per NAS. Each gets its own set of devices.

## What is detected and created

| Detail level | Area | Devices |
|---|---|---|
| **Basic** | Plugin | **Plugin Health** (green: all collectors work; yellow: some collector fails, named in the text; red: NAS not answering, not a Synology, or polling stopped; the text also shows the SNMP calls and seconds of the last poll) and a hidden **Poll Duration** in seconds. Both keep updating while the NAS is down |
| | System | system / power / fan / CPU-fan / thermal status (only those the model reports), system temperature, model + DSM version, "DSM update available" |
| | CPU, memory | CPU %, memory % (without cache and buffers), memory available, swap %, load 1 min, uptime |
| | Drives | per drive and per cache drive: **health** (DSM status, DSM health, bad sectors and, for SSD/NVMe, remaining life in the text), **temperature**, remaining life; one "Disks Overall" summary. The number of drives comes from the NAS |
| | RAID | status of every storage pool and volume (degraded, repairing, scrubbing, crashed, ...) with free/total space |
| | Volumes | used % and free space of every volume, USB / eSATA shares and the DSM system partition |
| **Standard** | Network | per hardware port (eth, bond, ovs_eth, wlan): link state + speed, received and sent Mbit/s. Docker / veth / loopback are ignored |
| | UPS | status, battery charge, **battery alert** (yellow at or below the UPS's own charge-warning threshold), load, runtime (only if a UPS is attached) |
| | Expansion units | power state of DX/RX units (only if attached) |
| **Full** | Drive I/O | per drive: load %, read and write MB/s |
| | SMART | per drive: health from reallocated / pending / uncorrectable sectors, power-on hours (every `SMART_INTERVAL`, default one hour) |
| | Services | logged-in users per service (HTTP, CIFS, NFS, SSH, ...) |
| | Extras | bad-sector counts, 5/15 min load, UPS info, SSD cache hit rates, GPU load (only on models that have them) |

Devices that are rarely looked at (drive I/O, SMART, services, extra load averages, uptime, bad sectors, cache hit
rates) are created **not visible by default**: add them under *Setup > Devices*. They still count towards Domoticz'
limit of 255 devices per hardware; a very large NAS with expansion units may want the *Standard* level.

The full list for a DS920+ and a DS218+ (device IDs, names, types, levels, example values) is in
**[docs/DEVICES.md](docs/DEVICES.md)**. Rate devices (network, drive I/O) need two samples and appear with the
second poll.

**Alert colors** (for all status devices): grey = not available, green = OK, yellow = warning or busy (for example
scrubbing, a few bad sectors), orange = degraded or on battery, red = critical (crashed pool or volume, failed system partition, drive reported critical or failing, low battery).

**Devices are matched by `DeviceID`, never by unit number.** You may rename a device or change its visibility,
history and notifications stay; the plugin only writes values. A drive, port or volume that disappears is flagged
**timed out** (not deleted); new hardware gets new devices on the next detection. Detection repeats hourly, and
immediately when the NAS renumbers its interfaces or mounts (`DISCOVERY_INTERVAL` changes the hour).

If you **delete** a device in Domoticz, the plugin creates it again on the next poll and says so in the log. To get rid
of a device for good, clear its *Used* flag instead of deleting it. The axis label of Custom sensors (for example
`Mbit/s`) belongs to the plugin and is brought up to date once per start; names, visibility, history and notifications
are never touched.

## How it works

```
plugin.py               Domoticz glue only: manifest + callbacks, starts the poller thread
synoSNMP/
  config.py             settings from Domoticz Parameters (validated)
  snmpcli.py            runs snmpget / snmpbulkwalk / snmpwalk: the only module that starts processes
  snmpparse.py          parses their output (pure functions, incl. Opaque floats)
  oids.py               OIDs and status tables
  engine.py             connection probe, discovery and collector schedules, collector isolation and back-off,
                        plugin health, de-duplicated error log
  poller.py             background thread
  devices.py            the only module that touches the Domoticz device API
  model.py  util.py     Reading type; key / slug / rate helpers
  cli.py  __main__.py   python3 -m synoSNMP: the engine without Domoticz
  collectors/           system  resources  disks  storage(raid, mounts)  network  ups
                        diskio  smart  services  extras(expansion units, ssd cache, gpu)
```

A collector returns `Reading` objects and knows nothing about Domoticz; the device layer knows nothing about SNMP.
Adding an area = one new file in `collectors/` plus one line in `collectors/__init__.py` (see [CONTRIBUTING](CONTRIBUTING.md)).

**Detection.** On start and then hourly each collector walks the relevant tables (Synology MIB under
`1.3.6.1.4.1.6574`, UCD-SNMP, HOST-RESOURCES, IF-MIB), decides what exists and builds a device list. Row numbers are
never trusted: volumes are re-identified by name on their scheduled collection and ports on the next scheduled network poll, so a renumbering after a firmware
update or a new volume does not mix up devices.

**Schedule configuration (`.env`).** Copy `.env.sample` to `.env` beside `plugin.py`. All values are seconds. The file is intentionally limited to polling intervals and transport tuning; SNMP credentials remain in Domoticz hardware parameters and are never read from it. The process environment overrides matching `.env` entries. `KEY=value`, `export KEY=value`, quotes, `# comments` after a value and a UTF-8 byte-order mark are all accepted. A line the plugin cannot use (a misspelled key such as `DISK_INTERVAL`, which suggests `DISKS_INTERVAL`, or a line that is not `KEY=value`) is ignored with a *Configuration warning* in the log; a value that is not a number or out of range stops the hardware with a configuration error. Debug logging names the file that was read. Intervals per collector family (10–86400):

| `.env` variable | Readings covered | Default |
|---|---|---:|
| `SYSTEM_INTERVAL` | System status, power/fans, thermal status, NAS temperature, system info and DSM update status | 60 s |
| `RESOURCES_INTERVAL` | CPU, memory, swap, load averages and uptime | 60 s |
| `DISKS_INTERVAL` | Physical disk health, temperature, remaining life and bad-sector counts | 300 s |
| `RAID_INTERVAL` | RAID / storage-pool status | 300 s |
| `VOLUMES_INTERVAL` | Volume capacity and free space | 600 s |
| `NETWORK_INTERVAL` | Network interface status and traffic rates | 60 s |
| `UPS_INTERVAL` | UPS status, charge, load, runtime and identifying information | 60 s |
| `EXPANSION_UNITS_INTERVAL` | Expansion-unit and redundant-power status | 300 s |
| `DISK_IO_INTERVAL` | Physical disk load and read/write throughput | 60 s |
| `SMART_INTERVAL` | SMART health and detailed drive statistics | 3600 s |
| `SERVICES_INTERVAL` | Connected users per supported service | 300 s |
| `SSD_CACHE_INTERVAL` | SSD cache read/write hit rates | 60 s |
| `GPU_INTERVAL` | GPU and GPU-memory utilization | 60 s |

Transport and housekeeping:

| `.env` variable | Meaning | Default | Range |
|---|---|---:|---|
| `SNMP_TIMEOUT` | seconds to wait for one SNMP answer | 5 | 1–60 |
| `SNMP_RETRIES` | retries per request | 1 | 0–5 |
| `DISCOVERY_INTERVAL` | how often the hardware is detected again | 3600 | 60–86400 |
| `REFRESH_INTERVAL` | unchanged values are written again this often, so Domoticz' "last seen" stays current | 300 | 30–86400 |

These settings follow the existing collector boundaries. Consequently, readings inside one collector share its interval—for example, uptime shares `RESOURCES_INTERVAL`, and DSM update status shares `SYSTEM_INTERVAL`. Separate intervals inside those collectors would require splitting the collector architecture and are not implied by this configuration.

**Polling.** The background thread starts a cycle every *tick* seconds, where the tick is the shortest interval of the
collectors that exist at the chosen detail level (60 s with the defaults; an interval set for a collector that is not
built, such as `GPU_INTERVAL` at *Basic*, does not make anything faster). In each cycle only the collectors whose own
interval has elapsed run; the others reuse their last readings. A collector runs in the first cycle at or after its
interval, so with a 30 s tick an interval of 45 s means every 60 s. A cached reading is not treated as proof that a
device still exists, so missing hardware is noticed at the next successful collection. Domoticz' heartbeat (every 5 s) drains
the queue and updates devices; unchanged values are rewritten every `REFRESH_INTERVAL`. SNMP calls per cycle, counted on
the simulated DS920+ with the default schedule (one call = one `snmpget` or walk; a `snmpget` of more than 20 OIDs
uses more than one):

| Detail level | First poll (includes detection) | Fast cycle (60 s collectors only) | Cycle in which the 5-minute collectors are due too |
|---|---|---|---|
| Basic | 8 | 2 | 4 |
| Standard | 14 | 4 | 7 |
| Full | 19 | 7 | 11 |

Every call is one short-lived process: `snmpget` batches up to 20 scalar OIDs, walks use `snmpbulkwalk` when the agent
supports it.

**Failure handling.** Each collector is isolated: if one fails the others still deliver, and its devices keep their last
value untouched (it is not sent again, so Domoticz' "last seen" shows how old it is). When a retry fails and that
value is more than two of the collector's intervals old, its devices are flagged timed out (red); the next good
reading clears the flag. A
collector that fails is tried again after its own interval (at most five minutes for
the first retry), then with doubling delays up to the larger of its interval and five minutes. If a request times out,
the plugin asks the NAS a trivial question: when that is answered the NAS is up and only that collector is broken,
otherwise the NAS is down. A problem is logged **once** and its recovery once, not every poll, and every error is shown
at every logging level. When the NAS stops answering, all its devices (except the plugin's own health devices) are
flagged timed out and values resume by themselves when it is back. The **Plugin Health** device shows the current state
in Domoticz itself, so a broken poller no longer looks like a quiet NAS.

## First run: check what your NAS reports

The plugin ships a command line tool that runs the same engine without Domoticz and prints every device it would
create. Use it before adding the hardware, and whenever a device is missing:

```sh
cd /srv/domoticz/plugins/Domoticz-Synology-SNMP
python3 -m synoSNMP NAS_IP -c public                    # SNMPv2c
python3 -m synoSNMP NAS_IP --rates 10                   # a second sample after 10 s: shows network / disk I/O rates
python3 -m synoSNMP NAS_IP -v 3 -u monitor -A - -X -   # SNMPv3: asks for both passphrases (not shown, not in the history)
python3 -m synoSNMP NAS_IP --level 1 --json             # machine readable, basic level
python3 -m synoSNMP NAS_IP --debug                      # also print what each collector detected
```

A passphrase or community given as a value (`-A secret`) is visible in the process list and the shell history. Pass `-`
to be asked instead, or set `SYNO_SNMP_COMMUNITY`, `SYNO_SNMP_AUTH_PASS` and `SYNO_SNMP_PRIV_PASS` in the environment.
Unlike the plugin, the tool takes `-X` literally (it never mistakes it for an old polling interval). The output ends with
the **Plugin health** line, `--json` has a `health` entry, and configuration warnings from `.env` go to stderr.

Options: `-p PORT`, `-v 1|2c|3`, `-c COMMUNITY`, `-u USER`, `-A AUTHPASS`, `-X PRIVPASS`, `-l LEVEL`, `-a AUTHPROTO`,
`-x PRIVPROTO`, `--level 1|2|3`, `--rates SECONDS`, `--json`, `--debug`, `--version-info`.
`--rates` always polls every collector again, whatever its schedule, so every rate has a value after the second sample.
Exit status: 0 ok, 1 bad options, tools missing or a passphrase prompt that was cancelled, 2 NAS does not answer,
3 answers but is not a Synology.

If something looks wrong or missing, `tools/capture_mib.sh NAS_IP [community] > dump.txt` records everything the plugin
reads, which is all that is needed to reproduce the problem offline. Remove serial numbers before sharing it.

## Troubleshooting

| Log message / symptom | Meaning and fix |
|---|---|
| `No SNMP answer from ...` | wrong address, port or community, SNMP not enabled in DSM, or the NAS firewall blocks the Domoticz host. A wrong community looks exactly like a timeout |
| `does not serve the Synology MIB` | the agent answers but `1.3.6.1.4.1.6574` is not visible: restricted SNMP view, or not a Synology |
| `the agent answered but returned no data` | the SNMP view is too restrictive (v3 user without access to the tree) |
| `Cannot run snmpget` / `net-snmp client tools` | `sudo apt install snmp` (inside the container if containerised) and restart Domoticz |
| `SNMPv3 ... passphrase must be at least 8 characters` | DSM and net-snmp both require 8+ characters. For *authPriv* the privacy passphrase is its own field; an empty field re-uses the authentication passphrase, which only works if DSM uses the same one |
| `Configuration warning: ... unknown setting X ignored (did you mean Y?)` | a typo in `.env`; the default stays in force until you fix the key |
| `Configuration error: ... must be a number` / `must be between` | a value in `.env` is not a number or out of range; the hardware does not start until it is fixed |
| `disks: SNMP error ...` (or another collector name) and the *Plugin Health* device is yellow | that one collector fails (a MIB table the NAS does not serve, a restricted SNMP view, a table that times out); everything else keeps working and the collector is retried with growing delays. `... : working again` follows when it recovers |
| *Plugin Health* is red | `NAS not answering`: see the first row. `Not a Synology`, or `Polling stopped` with the reason (net-snmp tools missing): fix it and disable / enable the hardware |
| `Device '...' was removed in Domoticz; creating it again` | you deleted a plugin device; clear *Used* instead of deleting to hide it |
| `All 255 Domoticz units of this hardware are in use` | lower the detail level or delete unused devices |
| A device you expect is missing | run the command line tool with `--debug`, or set *Debug* and look for the `mounts: ...`, `network ports: ...`, `UPS ...` lines. Compare with `snmpwalk -v2c -c public -On -m "" NAS_IP 1.3.6.1.4.1.6574.2` |
| A rate device shows nothing | rates need two samples: wait one poll interval |
| Everything is "timed out" after a NAS reboot | expected while it is down; values resume on their own |

More on a specific symptom: set *Logging level* to *Debug* for a concise poll summary, or *Trace* to inspect individual SNMP requests/responses and device updates.

## FAQ

**Which models and DSM versions work?** No model is special-cased: everything comes from the tables the NAS
returns, so the same code serves a DS218+, a DS720+, a DS920+, a DS1525+ or a rack unit. Synology documents the MIB
objects used here for DSM 6.2.4 and newer; DSM 7.x is what the fixtures imitate. Models released since 2025 use the same
MIB as far as is known; that is an expectation, not something tested on hardware.

**Does it need DSM packages or root access on the NAS?** No. It only reads over SNMP.

**Why do I see fewer devices than your catalogue?** The catalogue is for simulated hardware, and hidden devices are
not shown until you add them under *Setup > Devices*.

**Can it write or change anything on the NAS?** No. Only `snmpget` and walks are used, never `snmpset`.

**Why not SNMP traps?** Polling is simpler, works through any firewall setup and needs nothing on the Domoticz host
beyond the client tools.

**Why command line tools instead of a Python library?** One external dependency that every distribution packages and
patches, no `pip`, no wheels to build on a Raspberry Pi, and the exact same query can be reproduced by hand when
something is wrong.

## Migrating from the old pysnmp plugin

This is a **new hardware type** (key `SynologySNMP`), separate from the old *Synology Monitor* (`SynoMonitor`). Both can
run side by side, and the old devices are **not** converted.

1. Install `snmp` (`sudo apt install snmp`), deploy this plugin, add *Synology NAS (net-snmp)* as new hardware.
2. Compare for a few days. Rebuild dashboards, scenes and notifications on the new devices.
3. Delete the old hardware (this removes its devices and history) and the old plugin directory. `pysnmp` is no longer needed.

The *DSM OID* field is gone: drive table positions are detected. The check interval is in seconds now (the old plugin
used minutes).

## What is verified and what is not

**Verified** (automated tests, run on every change): the parser and SNMP client against the real net-snmp tools and a
real `snmpd` (v1, v2c, v3 including the privacy-passphrase field through the real `plugin.py`, timeouts, wrong
credentials, aborting a request), the scheduler as the poller runs it (per-collector intervals, hourly detection, back-off,
timeout isolation), every collector against simulated DS920+
and DS218+ data (also DSM-6-style tables, expansion units, renumbered interfaces and mounts), the device layer
against a Domoticz stub, the real `plugin.py` loaded the way Domoticz loads it (including the injected globals), and
`deploy.sh`.

**Not verified on a real DSM**, so please report what your NAS shows (a `capture_mib.sh` dump helps):

* the exact output format and the OID for UPS runtime and battery values (taken from the old plugin);
* the text of `hrStorageDescr` for volumes, USB shares and the system partition on your DSM version;
* the numbering of cache drives and expansion-unit drives, and whether a drive in an expansion unit shows up twice;
* how DSM 7.3 reports drives that are not on Synology's compatibility list;
* any SNMP changes in DSM 7.3 and later.

**Not covered:** iSCSI LUNs, NFS / SMB latency, high-availability clusters, per-share quotas, Docker container
statistics.

**Security notes:** the plugin does not pass the community string or SNMPv3 passphrases in net-snmp process arguments
(0.2.1). It writes them to a private temporary `snmp.conf` under a `0700` directory, with the file set to `0600`, and
removes the temporary configuration when the client stops; since 0.2.3 a directory left behind by a Domoticz that was
killed is removed at the next start (directories of 0.2.2 and older carry no process id and are never removed
automatically: `rm -r /tmp/domoticz-syno-snmp-*` while no plugin is running). The SNMPv3 privacy passphrase has its own password field and the command line tool
can ask for secrets instead of taking them as arguments. This reduces exposure in process listings; it does not protect
credentials stored in Domoticz settings or encrypt SNMPv1/v2c traffic. Prefer SNMPv3 `authPriv` and restrict UDP 161 to
the Domoticz host. See [SECURITY.md](SECURITY.md).

## Development

```sh
make test          # python3 -m unittest discover -s tests
make lint          # flake8 (pyflakes checks) + shellcheck
make compat        # vermin: code must run on Python 3.7+
make package       # dist/Domoticz-Synology-SNMP-<version>.tar.gz
```

The tests need nothing but Python. CI runs them on Python 3.7 to 3.13. The end-to-end tests additionally use a **real `snmpd`** (package `snmpd`) serving a
DS920+-like data set and are skipped automatically when it is not installed. `docs/DEVICES.md` is generated
(`python3 tools/gen_device_catalog.py`) and checked by the tests.

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a collector or a model fixture, [ROADMAP.md](ROADMAP.md) for what is
planned, [CHANGELOG.md](CHANGELOG.md) for what changed and [docs/SNMP-OIDS.md](docs/SNMP-OIDS.md) for the OID reference.

## License

[MIT](LICENSE). The plugin idea and the device concept follow the earlier *Synology Monitor* plugin by
bramvreugd and febalci; this is an independent rewrite and shares no code with it.
