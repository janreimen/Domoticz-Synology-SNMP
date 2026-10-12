"""Domoticz-Synology-SNMP: Synology NAS monitoring through the native net-snmp tools.

Package layout (each module has exactly one job):

    config.py       plugin settings (parsed from Domoticz ``Parameters``)
    snmpparse.py    parser for snmpget/snmpwalk output (pure functions)
    snmpcli.py      runs snmpget / snmpwalk / snmpbulkwalk (subprocess only)
    oids.py         OID constants and status tables
    model.py        ``Reading``: what a collector hands to the device layer
    util.py         small helpers (slugs, stable device keys, counter rates)
    engine.py       discovery scheduling, collector isolation, error policy
    poller.py       background thread that runs the engine
    devices.py      the ONLY module that talks to the Domoticz device API
    collectors/     one module per hardware area (system, disks, network, ...)
"""

__version__ = "0.2.0"
