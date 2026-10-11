"""OID constants and status tables.

Synology objects come from the "Synology DiskStation MIB Guide" (enterprise
1.3.6.1.4.1.6574).  Everything else is a standard MIB that DSM also serves
(UCD-SNMP, HOST-RESOURCES, IF-MIB).  OIDs are written without a leading dot.
"""
from __future__ import annotations

from .model import ALERT, CRIT, NONE, OK, WARN

SYNO = "1.3.6.1.4.1.6574"

# --- standard MIBs --------------------------------------------------------
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
HR_UPTIME = "1.3.6.1.2.1.25.1.1.0"

HR_STORAGE = "1.3.6.1.2.1.25.2.3.1"          # table entry: 2 type, 3 descr, 4 units, 5 size, 6 used
HR_STORAGE_FIXED_DISK = "1.3.6.1.2.1.25.2.1.4"

IF_TABLE = "1.3.6.1.2.1.2.2.1"               # 2 descr, 3 type, 8 operStatus, 10 inOctets, 16 outOctets
IF_XTABLE = "1.3.6.1.2.1.31.1.1.1"           # 1 name, 6 HCInOctets, 10 HCOutOctets, 15 highSpeed, 18 alias

UCD_LOAD = "1.3.6.1.4.1.2021.10.1.5"         # .1 / .2 / .3  = load x100 (1, 5, 15 min)
UCD_CPU_USER = "1.3.6.1.4.1.2021.11.9.0"
UCD_CPU_SYSTEM = "1.3.6.1.4.1.2021.11.10.0"
UCD_CPU_IDLE = "1.3.6.1.4.1.2021.11.11.0"
UCD_SWAP_TOTAL = "1.3.6.1.4.1.2021.4.3.0"    # kB
UCD_SWAP_AVAIL = "1.3.6.1.4.1.2021.4.4.0"
UCD_MEM_TOTAL = "1.3.6.1.4.1.2021.4.5.0"
UCD_MEM_AVAIL = "1.3.6.1.4.1.2021.4.6.0"
UCD_MEM_BUFFER = "1.3.6.1.4.1.2021.4.14.0"
UCD_MEM_CACHED = "1.3.6.1.4.1.2021.4.15.0"

# --- Synology System MIB (6574.1) ----------------------------------------------
SYS_STATUS = SYNO + ".1.1.0"
SYS_TEMP = SYNO + ".1.2.0"
SYS_POWER = SYNO + ".1.3.0"
SYS_FAN = SYNO + ".1.4.1.0"
SYS_CPU_FAN = SYNO + ".1.4.2.0"
SYS_MODEL = SYNO + ".1.5.1.0"
SYS_SERIAL = SYNO + ".1.5.2.0"
SYS_VERSION = SYNO + ".1.5.3.0"
SYS_UPGRADE = SYNO + ".1.5.4.0"
SYS_CPU_UTIL = SYNO + ".1.7.1.0"
SYS_MEM_UTIL = SYNO + ".1.7.2.0"
SYS_THERMAL = SYNO + ".1.8.0"

# --- Table entry OIDs (columns are appended as .<col>.<row>) -------------------
DISK_TABLE = SYNO + ".2.1.1"
RAID_TABLE = SYNO + ".3.1.1"
SMART_TABLE = SYNO + ".5.1.1"
SERVICES_TABLE = SYNO + ".6.1.1"
STORAGEIO_TABLE = SYNO + ".101.1.1"
FLASHCACHE_TABLE = SYNO + ".103.1.1"
EBOX_TABLE = SYNO + ".105.1.1"

# --- UPS MIB (6574.4) ----------------------------------------------------------
UPS_ROOT = SYNO + ".4"
# suffix below UPS_ROOT -> meaning.  Instances normally carry a trailing ".0".
UPS_MODEL = "1.1"            # guide: upsDeviceModel
UPS_MANUFACTURER = "1.2"     # guide: upsDeviceManufacturer
UPS_STATUS = "2.1"           # guide: upsInfoStatus (NUT style: "OL", "OB LB", ...)
UPS_LOAD = "2.12.1"          # guide: upsInfoLoadValue (%)
UPS_CHARGE = "3.1.1"         # guide: upsBatteryChargeValue (%)
UPS_CHARGE_WARN = "3.1.4"    # guide: upsBatteryChargeWarning (%)
UPS_RUNTIME = "3.6.1"        # seconds; OID taken from the previous plugin version (not in the guide table)

# --- GPU MIB (6574.108), scalars ------------------------------------------------
GPU_SUPPORTED = SYNO + ".108.1.0"    # 0 = supported, 1 = unsupported
GPU_UTIL = SYNO + ".108.2.0"
GPU_MEM_UTIL = SYNO + ".108.3.0"

# ---------------------------------------------------------------------------
# Status tables: value -> (text, alert level)
# ---------------------------------------------------------------------------
NORMAL_FAILED = {1: ("Normal", OK), 2: ("Failed", CRIT)}

DISK_STATUS = {
    1: ("Normal", OK),
    2: ("Initialized", OK),
    3: ("Not initialized", NONE),
    4: ("System partition failed", CRIT),
    5: ("Crashed", CRIT),
    6: ("Disconnected", ALERT),
}

DISK_HEALTH = {
    1: ("Healthy", OK),
    2: ("Warning", WARN),
    3: ("Critical", CRIT),
    4: ("Failing", ALERT),
}

RAID_STATUS = {
    1: ("Normal", OK),
    2: ("Repairing", WARN),
    3: ("Migrating", WARN),
    4: ("Expanding", WARN),
    5: ("Deleting", WARN),
    6: ("Creating", WARN),
    7: ("Syncing", WARN),
    8: ("Parity checking", WARN),
    9: ("Assembling", WARN),
    10: ("Canceling", WARN),
    11: ("Degraded", ALERT),
    12: ("Crashed", CRIT),
    13: ("Data scrubbing", WARN),
    14: ("Deploying", WARN),
    15: ("Undeploying", WARN),
    16: ("Mounting cache", WARN),
    17: ("Unmounting cache", WARN),
    18: ("Expanding (unfinished SHR)", WARN),
    19: ("Converting SHR to pool", WARN),
    20: ("Migrating SHR1 to SHR2", WARN),
    21: ("Unknown", NONE),
}

UPGRADE_STATUS = {
    1: ("Update available", WARN),
    2: ("Up to date", OK),
    3: ("Checking...", NONE),
    4: ("Cannot check (offline)", NONE),
    5: ("Unknown", NONE),
}

EBOX_POWER = {1: ("Normal", OK), 2: ("Poor", ALERT), 3: ("Not connected", CRIT)}

# NUT-style UPS status tokens
UPS_TOKENS = {
    "OL": ("Online", OK),
    "OB": ("On battery", ALERT),
    "LB": ("Low battery", CRIT),
    "HB": ("High battery", OK),
    "RB": ("Replace battery", WARN),
    "CHRG": ("Charging", OK),
    "DISCHRG": ("Discharging", ALERT),
    "BYPASS": ("Bypass", WARN),
    "CAL": ("Calibrating", WARN),
    "OFF": ("Off", CRIT),
    "OVER": ("Overload", ALERT),
    "TRIM": ("Trimming voltage", OK),
    "BOOST": ("Boosting voltage", OK),
    "FSD": ("Forced shutdown", CRIT),
}
