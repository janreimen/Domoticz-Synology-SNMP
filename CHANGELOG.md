# Changelog

## 0.1.1 (first release pushed in github)
- Fix: `onStart` failed with `AttributeError: 'dict' object has no attribute 'from_parameters'`. Domoticz injects a
  global dict called `Settings` into the plugin module, which shadowed the plugin's own `Settings` class.
  plugin.py no longer binds that name. The test harness now injects `Settings` / `Images` like Domoticz does,
  so this class of bug is covered.

## 0.1.0 (pure local development)
Complete rewrite of the old pysnmp-based "Synology Monitor" plugin.

- No Python SNMP library: all queries run through the native `snmpget` / `snmpbulkwalk` / `snmpwalk`
- Hardware is detected dynamically (drives, RAID/pools/volumes, mounts, network ports, UPS, fans, SMART,
  SSD cache, expansion units, GPU) - no hard-coded disk count, DSM OID suffix or interface index
- SNMP v1, v2c and v3 (noAuthNoPriv / authNoPriv / authPriv)
- Polling runs in a background thread; Domoticz' plugin thread never waits for the network
- Devices are keyed by `DeviceID`, so new hardware gets new devices and removed hardware is flagged "timed out"
- Modular code: parser, client, collectors, engine, device layer and plugin glue are separate modules
