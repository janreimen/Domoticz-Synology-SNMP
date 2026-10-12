# Contributing

Bug reports, hardware reports (also "it works on my DS...") and pull requests are welcome. The most valuable
contribution right now is **a report from a real NAS**: run `python3 -m synoSNMP NAS_IP --rates 10` and open a
"hardware report" issue (template included). A `tools/capture_mib.sh` dump, with serial numbers, host name and MAC
addresses removed, lets the model be added as a test fixture.

## Set up

```sh
git clone https://github.com/janreimen/Domoticz-Synology-SNMP.git
cd Domoticz-Synology-SNMP
make test            # python3 -m unittest discover -s tests
make check           # tests + flake8 (pyflakes checks) + shellcheck + Python 3.7 compatibility (vermin)
```

The tests need only Python 3. The end-to-end tests additionally use the net-snmp tools and `snmpd`
(`sudo apt install snmp snmpd`) and skip themselves when these are missing. Do not run the tests against a
production NAS: they use their own simulated agent.

## Rules

- **No third-party Python packages**, and nothing that needs a newer Python than 3.7 (`make compat`).
- **Only `snmpcli.py` starts processes**; only `plugin.py` imports `Domoticz`; only `devices.py` calls the Domoticz
  device API (through the object it is given). Collectors return `Reading` objects and never see Domoticz.
- **`plugin.py` must not bind the names Domoticz injects** (`Parameters`, `Devices`, `Images`, `Settings`): the
  injected globals would silently shadow them. A test enforces this. Import modules under another name
  (`from synoSNMP import config as syno_config`).
- **Collectors never raise for missing data.** A table that does not exist, an empty table or an unexpected value
  means "no readings", not an exception. Absent hardware must not create devices.
- **Device keys are stable.** A reading's `key` becomes the Domoticz `DeviceID` and must be at most 25 characters
  (`make_key` shortens long ones deterministically). Never derive a key from a row index that the NAS may renumber;
  use a name or a slug of the name.
- **Never log credentials.** Debug output may contain the serial number; it must not contain community strings or
  passphrases.
- **Read-only.** No `snmpset`.

## Adding a collector (a new hardware area)

1. Create `synoSNMP/collectors/<area>.py` with a subclass of `Collector`:

   ```python
   class FooCollector(Collector):
       name = "foo"                 # shown in log messages
       groups = ("foo",)            # first segment of every key this collector produces
       min_level = LEVEL_FULL       # lowest detail level that runs it
       interval = 0                 # minimum seconds between runs (0 = every poll)

       def collect(self, client):   # client: SnmpClient (get_many / walk / table)
           rows = client.table(oids.FOO_TABLE)
           return [Reading.percentage(make_key("foo", slug(name), "load"), "Foo %s Load" % name, value, used=False)
                   for ...]
   ```

   Implement `discover(client)` only if the collector keeps a cache of what exists (see `network.py`, `storage.py`).
   Small tables are simply re-read on every poll.
2. Add the OIDs and status tables to `oids.py`; write the Synology guide name or `unverified` next to them.
3. Register the class in `synoSNMP/collectors/__init__.py` (`COLLECTOR_CLASSES`).
4. Add rows for it to a fixture in `tests/mibdata.py` (`ds920()` / `ds218()` or a new model function) and tests in
   `tests/test_collectors.py`: normal case, hardware absent, a value the NAS does not provide.
5. `python3 tools/gen_device_catalog.py` to regenerate `docs/DEVICES.md` (a test fails if you forget), then update
   `docs/SNMP-OIDS.md`, the README device table and `CHANGELOG.md`.

## Adding a model fixture

Capture the NAS with `tools/capture_mib.sh`, remove the personal data, and turn the relevant rows into a
function in `tests/mibdata.py` (see `ds218()` for a small example). Prefer a test that asserts the *interesting*
property of that model (for example how its volumes are named) over a snapshot of every device.

## Pull requests

- One topic per pull request; keep it small enough to review.
- Add or update tests; `make check` must pass.
- Update `CHANGELOG.md` under an `Unreleased` heading. The maintainer sets the version (see `checklist.md`).
- Describe what you tested and on which NAS model / DSM version, if any.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
