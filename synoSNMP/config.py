"""Plugin settings, parsed and validated from the Domoticz ``Parameters`` dict.

Hardware-page fields (see the manifest in plugin.py):

    Address   NAS host name / IP
    Port      SNMP port (161)
    Mode1     community (SNMP v1/v2c)
    Mode2     SNMP version: 2c | 1 | 3
    Username  SNMPv3 user
    Password  SNMPv3 authentication passphrase
    Mode3     SNMPv3 options  "level;authProto;privProto;privPassphrase"
    Mode4     poll interval in seconds
    Mode5     detail level: 1 basic | 2 standard | 3 full
    Mode6     debug: Normal | Debug
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

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
    interval: int = 60               # seconds between polls
    level: int = LEVEL_FULL          # detail level, see LEVEL_*
    debug: bool = False
    timeout: int = 5                 # seconds per SNMP try
    retries: int = 1
    discovery_interval: int = 3600   # re-detect hardware this often (seconds)
    refresh_interval: int = 300      # rewrite unchanged values this often (seconds)

    @classmethod
    def from_parameters(cls, params: Mapping[str, Any]) -> "Settings":
        def get(name: str, default: str = "") -> str:
            value = params.get(name, default)
            return default if value is None else str(value).strip()

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
            interval=_to_int(get("Mode4", "60") or "60", "Poll interval", MIN_INTERVAL, 86400),
            level=_to_int(get("Mode5", "3") or "3", "Detail level", 1, 3),
            debug=get("Mode6", "Normal").lower() in ("debug", "true", "1"),
        )

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
