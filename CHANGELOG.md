# Changelog

## [0.2.2] - 2026-10-21

### Added
- Per-collector polling intervals loaded from a documented plugin-local `.env` file; `.env.sample` provides defaults and descriptions.
- Independent intervals for system, resources, physical disks, RAID/storage pools, volumes, network, UPS, expansion units, disk I/O, SMART, services, SSD cache, and GPU metrics.
- Environment variables override `.env` values; accepted intervals are 10–86400 seconds. SNMP credentials remain in Domoticz hardware parameters.
- Regression tests for schedule-file loading, defaults, overrides and interval validation.

### Changed
- Removed the single fast/slow interval fields from the Domoticz hardware configuration. The poller wakes at the shortest configured interval and each collector runs only when due.
- Cached readings remain available between scheduled collections, without being counted as fresh hardware-presence evidence.
- Updated the hardware description and README with the schedule variables and collector-to-sensor mapping.

### Notes
- Settings follow existing collector boundaries. Readings bundled by a collector (for example uptime with CPU/memory, and DSM update status with system health) share that collector's interval.
- Real DS1525+ hardware validation remains outstanding.

## [0.2.1] - 2026-10-20

### Security
- Removed SNMP community strings and SNMPv3 authentication/privacy passphrases from net-snmp process arguments.
- Store credentials in a temporary, isolated Net-SNMP configuration directory with directory mode `0700` and config-file mode `0600`.
- Clean up the private configuration when the client stops; reject newline, carriage-return and NUL characters in config values to prevent directive injection.
- Preserve SNMPv1, v2c, and v3 security-level/protocol selection.

### Changed
- Added three logging levels: Normal (start/stop, timeouts and critical errors), Debug (operational status and concise poll/next-poll summary), and Trace (full execution path: poll-cycle steps, discovery and collector start/skip/result/error details, SNMP requests/responses, reading/device decisions, heartbeat callbacks, and poll scheduling).
- Trace now logs each collected sensor's name, stable key, type, nValue, sValue, unit, visibility and pending status, plus whether the value was sent to Domoticz, refreshed, skipped as unchanged, or timed out/recovered.
- Poller-thread trace events are queued and emitted from Domoticz heartbeat callbacks rather than calling Domoticz logging APIs from the worker thread. This also makes slow SNMP requests visible before a poll cycle completes.
- Trace logging never prints SNMP community strings or SNMPv3 passphrases.

### Tests
- Added regression coverage for secret-free command arguments, private configuration permissions, cleanup, config-value injection, logging-level parsing and SNMP trace output.

## [0.2.0] - 2026-10-11

### Added
- **Command line tool** `python3 -m synoSNMP NAS_IP`: runs the plugin's engine without Domoticz and prints every
  device it would create (`--rates SECONDS` for network / drive I/O rates, `--json`, `--level`, `--debug`,
  SNMP v1 / v2c / v3 options). Exit status 0 ok, 1 usage or missing tools, 2 NAS does not answer, 3 not a Synology.
- **`deploy.sh`**: install locally or over SSH (`--host`), `--dry-run`, backup of the installed copy outside the
  plugins directory (newest 3 kept), `--rollback`, owner restore, Domoticz restart, `--install-deps`, `--verify NAS`.
- `Makefile` (`test`, `lint`, `compat`, `package`, `deploy`, `deploy-dry`, `rollback`).
- Documentation: full `README.md`, `DEPLOY.md`, `docs/DEVICES.md` (generated from the test fixtures by
  `tools/gen_device_catalog.py`), `docs/SNMP-OIDS.md`, `ROADMAP.md`, `SECURITY.md`, `CONTRIBUTING.md`,
  `checklist.md` (release checklist).
- Repository files: `LICENSE` (MIT), `.gitignore`, GitHub Actions workflow (tests on Python 3.9 - 3.13 with a real
  `snmpd`, lint, Python 3.7 compatibility check), issue templates (bug report, hardware report).
- `tools/capture_mib.sh` supports SNMP v3 through `SNMP_OPTS` and warns about the personal data in a dump.
- Tests: CLI (8), `deploy.sh` (10) and repository checks (9): version agreement between `plugin.py`,
  `synoSNMP/__init__.py` and this file, `docs/DEVICES.md` up to date, required files present, Markdown links resolve.

### Changed
- `CycleResult` has a `not_synology` flag (an agent that answers without the Synology MIB). `reachable` keeps its
  meaning, so the plugin behaves as before.
- Install instructions use the directory name `Domoticz-Synology-SNMP`.

### Fixed
- Test suite: unused imports removed, files closed (no `ResourceWarning`s).

## [0.1.1] - 2026-10-11

### Fixed
- `onStart` failed with `AttributeError: 'dict' object has no attribute 'from_parameters'`. Domoticz injects a
  global dict called `Settings` into the plugin module, which shadowed the plugin's own `Settings` class.
  `plugin.py` no longer binds that name. The test harness now injects `Settings` / `Images` like Domoticz does, and
  a test checks that `plugin.py` never binds a name Domoticz injects (`Parameters`, `Devices`, `Images`, `Settings`).

## [0.1.0] - 2026-10-10

Complete rewrite of the old pysnmp-based *Synology Monitor* plugin.

### Added
- All queries run through the native `snmpget` / `snmpbulkwalk` / `snmpwalk`; no Python SNMP library.
- Dynamic hardware detection: drives (health, temperature, remaining life, bad sectors), storage pools and
  volumes, mounted volumes, network ports, UPS, fans, thermal status, SMART, drive I/O, SSD cache, expansion
  units, GPU, services, CPU / memory / swap / load, DSM version and update state. No hard-coded disk count, DSM OID
  suffix or interface index.
- SNMP v1, v2c and v3 (noAuthNoPriv / authNoPriv / authPriv).
- Detail levels Basic / Standard / Full; rarely used devices are created hidden.
- Background polling thread; Domoticz' plugin thread never waits for the network.
- Devices keyed by `DeviceID`; removed hardware is flagged "timed out" instead of being deleted; problems are
  logged once, and their recovery once.
- Modular code: parser, client, collectors, engine, device layer and plugin glue are separate modules.

[0.2.2]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.2.2
[0.2.1]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.2.1
[0.2.0]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.2.0
[0.1.1]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.1.1
[0.1.0]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.1.0
