# Changelog

## [0.2.4] - 2026-10-29

Baseline: 0.2.3. No behaviour changes for a NAS that runs all the time.

### Added
- **A NAS that is switched off on a schedule.** `EXPECTED_OFFLINE=23:30-12:00` in `.env` names the hours in which the
  NAS is meant to be off (local time of the Domoticz host, may cross midnight). Until now the plugin logged "No SNMP
  answer" as an error every night. While a silent NAS is inside that window, or inside the grace period after it
  (`EXPECTED_OFFLINE_GRACE`, default 900 s, 0-86400, so that DSM can boot and start SNMP):
  - no error is logged; one *notice* (shown at every logging level) says that the silence is expected and when it
    would become an outage, and one notice says that the NAS is back;
  - the *Plugin Health* device is grey and says why (*NAS switched off on schedule*, *Waiting for the NAS to start*);
  - the devices keep their last values and are not flagged timed out;
  - polling slows down to one probe every 5 minutes and wakes up when the window ends (in the grace period the normal
    tick applies again).

  A NAS that answers inside the window is polled normally. A NAS that is still silent after the grace period is an
  outage as before: one error, red health device, devices flagged timed out. The failure time decides, not the start
  of the poll that failed, so a shutdown in the middle of a poll is judged correctly. The command line tool ignores
  the schedule. An invalid window is a configuration error; `EXPECTED_OFFLINE_GRACE` without `EXPECTED_OFFLINE` is
  reported as a configuration warning.
- `CycleResult.expected_off` and `CycleResult.wait` (a cycle may ask the poller for a different pause than the tick),
  and an optional `wall` clock for `Engine`.
- New module `synoSNMP/offline.py`; tests in `tests/test_offline.py` (45).

### Changed
- The Debug "next poll in about ..." line uses the requested pause.

## [0.2.3] - 2026-10-25

Every item under *Fixed* was reproduced against 0.2.2 by running the code (scheduler timelines on the fixture NAS,
the CLI, the plugin's logging, a deleted device, `.env` edge cases) before it was changed, and has a regression test
that fails on 0.2.2.

### Fixed
- **Collectors on the tick were skipped every second cycle.** A collector's last run was stamped when it *finished*, so
  the next cycle, exactly one tick later, found its interval 0.1-3 s short and skipped it. The 60 s families therefore
  ran every 120 s, the first network and disk-I/O rates took two minutes instead of one, and the cycle that follows the
  hourly re-detection ran nothing at all. The schedule is now measured from the start of a cycle (with 0.25 s of timing
  tolerance), so a collector with a 60 s interval runs in every 60 s cycle.
- **The scheduler tick ignored the detail level.** It was the shortest interval of every configured collector, also of
  collectors that are not built (for example `GPU_INTERVAL=10` at *Basic* made the whole plugin poll every 10 s). It is
  now the shortest interval of the collectors that exist (`Engine.tick`).
- **`python3 -m synoSNMP NAS --rates N` printed 18 rates as "no value yet".** The second sample honoured the
  collectors' schedules, so nothing was due. It now polls every collector again.
- **Errors were invisible at the default *Normal* logging level.** Collector, configuration and SNMP errors reached the
  Domoticz log only at *Debug* or higher; only "no SNMP answer" was shown. Every error is now logged at every level (once
  per problem, as before), recoveries are logged at *Normal* too, and the identity, serial and detection notes that the
  documentation placed at *Debug* now really appear there (they needed *Trace*).
- **One collector that timed out took the whole NAS down.** A table that never answered (SMART, for example) was
  treated as "NAS unreachable": all collectors' readings were discarded and every device was flagged timed out. After a
  timeout the engine now asks for the model OID; if the NAS answers, only that collector is marked as failed. The same
  rule applies to hardware detection (first cycle and hourly), where a single table that never answered also made the
  whole NAS look unreachable, again on every cycle.
- **A collector that had stopped answering looked healthy.** 0.2.2 re-sent the cached readings of a failing collector
  on every cycle, so Domoticz kept refreshing "last seen" and never flagged them: after 40 minutes of a dead drive table
  the drive temperature was still green and "just updated". The old values are no longer re-sent (the devices keep their
  last value and an honest age), and the devices are flagged timed out when a retry fails after that value has
  become more than two of the collector's intervals old; the next good reading clears the flag.
- **A device deleted in Domoticz stayed gone** until the next restart, and the plugin silently wrote to nothing. It is
  created again on the next poll, with a log line that says how to hide a device properly (clear *Used*).
- **`.env` parsing:** `SYSTEM_INTERVAL=inf` crashed with an uncaught `OverflowError`; `60  # a comment` was rejected;
  `export KEY=value` and a UTF-8 byte-order mark made the line vanish silently; and a misspelled key (`DISK_INTERVAL`)
  was ignored without a word. Values are now cleaned as described in the README, and unknown keys and lines that are not
  `KEY=value` are reported at start-up (with a "did you mean ...?" for near misses). Text after a closing quote (`"60"abc`) is an error instead of being dropped.
- **Drive health `Failing` (DSM 7.1+ `diskHealthStatus` 4) was only orange** while `Critical` (3) was red. Both are red now;
  a code the plugin does not know is shown as `Unknown (n)` and yellow instead of an unnamed state.
- **Start-up failures leaked the private SNMP configuration directory**, and a credential the private file cannot hold
  (a line break in the community) raised out of `onStart`. Both are cleaned up and reported.
- Leftover credential directories of a Domoticz that was killed before it could clean up (power cut, OOM, `kill -9`)
  are removed at the next start (see *Security*). Directories that 0.2.2 and older left behind carry no process id, so
  nobody can tell whether their owner still runs; they are never touched automatically (remove them by hand while
  no plugin is running: `rm -r /tmp/domoticz-syno-snmp-*`).
- Documentation that disagreed with the code: log-level table, "slow schedule" wording, the call-count table.

### Added
- **Plugin Health device** (Alert) and a hidden **Poll Duration** device (seconds). Health is green when every collector
  works, yellow when some fail (named in the text), red when the NAS does not answer, is not a Synology or polling has
  stopped, and shows the SNMP calls and seconds of the last poll. Both are written while the NAS is unreachable, so a
  broken poller no longer looks like a quiet NAS; they are the only devices that are not flagged timed out then.
- **Back-off for failing collectors.** A collector that fails is retried after its own interval (at most 5 minutes for
  the first retry), then with doubling delays up to the larger of its interval and 5 minutes, instead of every cycle.
  Its devices keep their last value meanwhile (see *Fixed*: it is not sent again) and are flagged timed out once it is
  two intervals old when a retry fails. The command line tool ignores back-off.
- **SNMPv3 privacy passphrase field** (password field, hardware setting `Mode4`). The default of *SNMPv3 options* is now
  `authPriv;SHA;AES`. The old fourth part (`;passphrase`, shown in clear text on the hardware page) keeps working with a
  warning. A value shorter than 8 characters in the new field is ignored, because versions up to 0.2.1 stored a polling
  interval in `Mode4`, and an upgraded installation still has `60` or similar there.
- **`.env` transport settings:** `SNMP_TIMEOUT` (5 s), `SNMP_RETRIES` (1), `DISCOVERY_INTERVAL` (3600 s) and
  `REFRESH_INTERVAL` (300 s), validated like the schedules. The file that was read is named in the Debug log.
- **UPS battery alert** device: yellow when the charge is at or below the UPS's own warning threshold (OID `3.1.4`).
- **Command line tool:** `-c -`, `-A -` and `-X -` ask for the secret instead of taking it as an argument;
  `SYNO_SNMP_COMMUNITY`, `SYNO_SNMP_AUTH_PASS` and `SYNO_SNMP_PRIV_PASS` are read from the environment; the output ends
  with a *Plugin health* line (`health` in `--json`); configuration warnings are printed to stderr; a cancelled prompt
  is a clean error (exit 1).
- The axis label of Custom sensors (for example `Mbit/s`) is brought up to date once per start on existing devices.
- `deploy.sh` warns when `--verify-args` carries a passphrase or a community other than `public` (visible in the process
  list of both machines and in the shell history) and masks it in the `--dry-run` output; the error for an unknown option
  never repeats its value.
- CI: the full suite, including the end-to-end tests against a real `snmpd`, now also runs on Python 3.7 and 3.8 (the
  minimum the plugin claims) in containers; workflows run with read-only permissions; a tag `vX.Y.Z` builds the package,
  checks that tag, manifest, `__version__` and CHANGELOG agree, and publishes the release with this text.

### Changed
- Private SNMP configuration directories are named `domoticz-syno-snmp-<pid>-<random>` so that orphans can be recognised.
- `Settings.interval` is only the fallback of `Engine.tick`; the poller always uses the tick.
- The fixture DS920+ at *Full* creates 73 devices (was 70): Plugin Health, Poll Duration and UPS Battery.
- The tests no longer read a `.env` that happens to sit in the checkout; there are 242 of them (was 118).

### Security
- The SNMPv3 privacy passphrase can be entered in a password field instead of the clear-text options field.
- The command line tool no longer needs secrets in its arguments (prompt or environment).
- Orphaned private configuration directories are removed at start. Only real directories owned by the current user that
  contain nothing but `snmp.conf`, whose name carries the id of a process that no longer exists, are touched; symlinks,
  directories without a process id (0.2.2 and older) and anything doubtful are left alone.

### Notes
- **Investigated and not changed:** SNMPv1 GETs that mix present and absent objects work as they are (the `snmpget` tool
  retries without the rejected object; a regression test against a real `snmpd` pins that). `snmpbulkwalk` cannot walk
  several subtrees in one call (it walks only the first OID given), so "batch the walks" is not possible.
- **Deferred:** changing device keys (`DeviceID`) to make them more stable. The Domoticz plugin API cannot rename the
  `DeviceID` of an existing device, so a change would leave every installed device orphaned with its history.
- Real DS1525+ hardware validation remains outstanding.

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

### Fixed
- Trace logging now includes the successful Synology identity check, including the detected model and DSM version.
- Updated the real-net-snmp chunking test to use more than the configured maximum number of distinct OIDs. Duplicate OIDs are intentionally deduplicated before chunking and must not be used to force extra subprocess calls.
- The plugin end-to-end test explicitly enables Trace logging before asserting that the connection identity appears in Trace output.

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

[0.2.4]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.2.4
[0.2.3]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.2.3
[0.2.2]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.2.2
[0.2.1]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.2.1
[0.2.0]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.2.0
[0.1.1]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.1.1
[0.1.0]: https://github.com/janreimen/Domoticz-Synology-SNMP/releases/tag/v0.1.0
