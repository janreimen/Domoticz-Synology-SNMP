---
name: Bug report
about: Something does not work, a value is wrong, or the plugin logs an error
title: ""
labels: bug
---

**What happens, and what did you expect?**

**Environment**

- Plugin version (Setup > Hardware, or `python3 -m synoSNMP --version-info`):
- NAS model and DSM version (e.g. DS920+, DSM 7.2.1-69057):
- SNMP version used (v1 / v2c / v3):
- Domoticz version:
- Python version (`python3 -V`) and net-snmp version (`snmpget -V`):
- Domoticz runs on (Raspberry Pi OS, Docker, ...):

**Output of the command line tool** (run from the plugin directory; remove the serial numbers)

```
python3 -m synoSNMP NAS_IP --debug
```

**Domoticz log** (Setup > Log, with the hardware's *Debug* option set to *Debug*, a few polls)

```

```

**Raw SNMP data (if a device is missing or wrong)**

`tools/capture_mib.sh NAS_IP > dump.txt` records everything the plugin reads. The dump contains the NAS and drive
serial numbers, the host name and MAC addresses. Remove them, then attach the file. **Never post a community string
or SNMPv3 passphrase.**
