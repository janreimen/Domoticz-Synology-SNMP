"""Plugin settings, parsed and validated from the Domoticz ``Parameters`` dict.

Hardware-page fields (see the manifest in plugin.py):

    Address   NAS host name / IP
    Port      SNMP port (161)
    Mode1     community (SNMP v1/v2c)
    Mode2     SNMP version: 2c | 1 | 3
    Username  SNMPv3 user
    Password  SNMPv3 authentication passphrase
    Mode3     SNMPv3 options  "level;authProto;privProto;privPassphrase"
    Mode5     detail level: 1 basic | 2 standard | 3 full
    Mode6     logging: Normal | Debug | Trace

Polling intervals are loaded from the plugin-local .env file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

LEVEL_BASIC = 1
LEVEL_STANDARD = 2
LEVEL_FULL = 3

MIN_INTERVAL = 10

_V3_LEVELS = {"noauthnopriv": "noAuthNoPriv", "authnopriv": "authNoPriv", "authpriv": "authPriv"}
_AUTH_PROTOS = {"MD5", "SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512"}
_PRIV_PROTOS = {"DES", "AES", "AES-192", "AES-256"}


class SettingsError(ValueError):
    """Raised for an invalid hardware configuration."""


@dataclass
class Settings:
    host: str
    port: int = 161
    version: str = "2c"              # "1", "2c" or "3"
    community: str = "public"
    v3_user: str = ""
    v3_level: str = "authPriv"
    v3_auth_proto: str = "SHA"
    v3_auth_pass: str = ""
    v3_priv_proto: str = "AES"
    v3_priv_pass: str = ""
    interval: int = 60               # scheduler tick: minimum configured collector interval
    slow_interval: int = 600         # legacy compatibility for directly-created Settings objects
    collector_intervals: Optional[Dict[str, int]] = None
    level: int = LEVEL_FULL          # detail level, see LEVEL_*
    debug: bool = False              # backwards-compatible: true for Debug or Trace
    log_level: int = 1               # 1 normal, 2 debug, 3 trace
    timeout: int = 5                 # seconds per SNMP try
    retries: int = 1
    discovery_interval: int = 3600   # re-detect hardware this often (seconds)
    refresh_interval: int = 300      # rewrite unchanged values this often (seconds)

    @classmethod
    def from_parameters(cls, params: Mapping[str, Any], env_path: Optional[str] = None) -> "Settings":
        def get(name: str, default: str = "") -> str:
            value = params.get(name, default)
            return default if value is None else str(value).strip()

        env_values = _load_schedule_env(env_path)
        host = get("Address")
        if not host:
            raise SettingsError("No NAS address configured")

        port = _to_int(get("Port", "161") or "161", "Port", 1, 65535)

        version = get("Mode2", "2c").lower().replace("v", "") or "2c"
        if version in ("2", "2c"):
            version = "2c"
        if version not in ("1", "2c", "3"):
            raise SettingsError("Unsupported SNMP version %r (use 1, 2c or 3)" % version)

        s = cls(
            host=host,
            port=port,
            version=version,
            community=get("Mode1", "public") or "public",
            collector_intervals=_parse_collector_intervals(env_values),
            level=_to_int(get("Mode5", "3") or "3", "Detail level", 1, 3),
            debug=get("Mode6", "Normal").lower() in ("debug", "trace", "true", "1", "3", "2"),
            log_level={"normal": 1, "false": 1, "0": 1, "1": 1, "debug": 2, "true": 2, "2": 2, "trace": 3, "3": 3}.get(get("Mode6", "Normal").lower(), 1),
        )

        s.interval = min(s.collector_intervals.values()) if s.collector_intervals else 60
        s.slow_interval = max(s.collector_intervals.values()) if s.collector_intervals else 600

        if version == "3":
            s.v3_user = get("Username")
            s.v3_auth_pass = get("Password")
            if not s.v3_user:
                raise SettingsError("SNMPv3 needs a user name (Username field)")
            _apply_v3_options(s, get("Mode3"))
            if s.v3_level != "noAuthNoPriv" and len(s.v3_auth_pass) < 8:
                raise SettingsError("SNMPv3 authentication passphrase must be at least 8 characters")
            if s.v3_level == "authPriv":
                if not s.v3_priv_pass:
                    s.v3_priv_pass = s.v3_auth_pass   # common setup: same passphrase for both
                if len(s.v3_priv_pass) < 8:
                    raise SettingsError("SNMPv3 privacy passphrase must be at least 8 characters")
        return s


# Environment keys are intentionally limited to polling schedules. Credentials remain
# in Domoticz hardware parameters and are never loaded from or written to .env.
SCHEDULE_DEFAULTS = {
    "SYSTEM_INTERVAL": ("system", 60),
    "RESOURCES_INTERVAL": ("resources", 60),
    "DISKS_INTERVAL": ("disks", 300),
    "RAID_INTERVAL": ("raid", 300),
    "VOLUMES_INTERVAL": ("mounts", 600),
    "NETWORK_INTERVAL": ("network", 60),
    "UPS_INTERVAL": ("ups", 60),
    "EXPANSION_UNITS_INTERVAL": ("ebox", 300),
    "DISK_IO_INTERVAL": ("diskio", 60),
    "SMART_INTERVAL": ("smart", 3600),
    "SERVICES_INTERVAL": ("services", 300),
    "SSD_CACHE_INTERVAL": ("flashcache", 60),
    "GPU_INTERVAL": ("gpu", 60),
}


def _load_schedule_env(env_path: Optional[str] = None) -> Dict[str, str]:
    """Read schedule-only .env settings; process environment takes precedence."""
    path = Path(env_path) if env_path else Path(__file__).resolve().parent.parent / ".env"
    values: Dict[str, str] = {}
    if path.is_file():
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if value and value[0:1] in ("\"", "'") and value[-1:] == value[0:1]:
                    value = value[1:-1]
                if key in SCHEDULE_DEFAULTS:
                    values[key] = value
        except OSError as exc:
            raise SettingsError("Cannot read schedule configuration %s: %s" % (path, exc))
    for key in SCHEDULE_DEFAULTS:
        if key in os.environ:
            values[key] = os.environ[key].strip()
    return values


def _parse_collector_intervals(env_values: Mapping[str, str]) -> Dict[str, int]:
    intervals: Dict[str, int] = {}
    for env_key, (collector, default) in SCHEDULE_DEFAULTS.items():
        raw = env_values.get(env_key, str(default)) or str(default)
        intervals[collector] = _to_int(raw, env_key, MIN_INTERVAL, 86400)
    return intervals


def _to_int(text: str, label: str, low: int, high: int) -> int:
    try:
        value = int(float(text))
    except ValueError:
        raise SettingsError("%s must be a number, got %r" % (label, text))
    if not low <= value <= high:
        raise SettingsError("%s must be between %d and %d, got %d" % (label, low, high, value))
    return value


def _apply_v3_options(s: Settings, text: str) -> None:
    """Parse ``level;authProto;privProto;privPassphrase`` (all parts optional)."""
    if not text:
        return
    parts = [p.strip() for p in re.split(r"[;,]", text)]
    if parts and parts[0]:
        level = _V3_LEVELS.get(parts[0].lower())
        if not level:
            raise SettingsError("SNMPv3 security level must be noAuthNoPriv, authNoPriv or authPriv")
        s.v3_level = level
    if len(parts) > 1 and parts[1]:
        proto = parts[1].upper()
        if proto not in _AUTH_PROTOS:
            raise SettingsError("SNMPv3 auth protocol must be one of %s" % ", ".join(sorted(_AUTH_PROTOS)))
        s.v3_auth_proto = proto
    if len(parts) > 2 and parts[2]:
        proto = parts[2].upper()
        if proto not in _PRIV_PROTOS:
            raise SettingsError("SNMPv3 privacy protocol must be one of %s" % ", ".join(sorted(_PRIV_PROTOS)))
        s.v3_priv_proto = proto
    if len(parts) > 3 and parts[3]:
        s.v3_priv_pass = parts[3]
