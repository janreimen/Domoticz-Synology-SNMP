"""Plugin settings, parsed and validated from the Domoticz ``Parameters`` dict.

Hardware-page fields (see the manifest in plugin.py):

    Address   NAS host name / IP
    Port      SNMP port (161)
    Mode1     community (SNMP v1/v2c)
    Mode2     SNMP version: 2c | 1 | 3
    Username  SNMPv3 user
    Password  SNMPv3 authentication passphrase
    Mode3     SNMPv3 options  "level;authProto;privProto"
    Mode4     SNMPv3 privacy passphrase (password field).  Versions before 0.2.2 stored a polling
              interval here, so a value shorter than 8 characters is never taken as a passphrase.
    Mode5     detail level: 1 basic | 2 standard | 3 full
    Mode6     logging: Normal | Debug | Trace

Polling intervals and transport tuning are loaded from the plugin-local .env file.
"""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional

LEVEL_BASIC = 1
LEVEL_STANDARD = 2
LEVEL_FULL = 3

MIN_INTERVAL = 10
MIN_PASSPHRASE = 8

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
    interval: int = 60               # fallback scheduler tick; Engine.tick is authoritative (built collectors only)
    slow_interval: int = 600         # legacy compatibility for directly-created Settings objects
    collector_intervals: Optional[Dict[str, int]] = None
    level: int = LEVEL_FULL          # detail level, see LEVEL_*
    debug: bool = False              # backwards-compatible: true for Debug or Trace
    log_level: int = 1               # 1 normal, 2 debug, 3 trace
    timeout: int = 5                 # seconds per SNMP try                       (.env SNMP_TIMEOUT)
    retries: int = 1                 # SNMP retries per request                   (.env SNMP_RETRIES)
    discovery_interval: int = 3600   # re-detect hardware this often (seconds)    (.env DISCOVERY_INTERVAL)
    refresh_interval: int = 300      # rewrite unchanged values this often (s)    (.env REFRESH_INTERVAL)
    env_file: Optional[str] = None   # the .env that was read, None = built-in defaults
    warnings: List[str] = field(default_factory=list)   # non-fatal configuration problems, logged at start-up

    @classmethod
    def from_parameters(cls, params: Mapping[str, Any], env_path: Optional[str] = None,
                        mode4_may_be_stale: bool = True) -> "Settings":
        def get(name: str, default: str = "") -> str:
            value = params.get(name, default)
            return default if value is None else str(value).strip()

        env = _load_env(env_path)
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
            collector_intervals=_parse_collector_intervals(env.values),
            level=_to_int(get("Mode5", "3") or "3", "Detail level", 1, 3),
            debug=get("Mode6", "Normal").lower() in ("debug", "trace", "true", "1", "3", "2"),
            log_level={"normal": 1, "false": 1, "0": 1, "1": 1, "debug": 2, "true": 2, "2": 2, "trace": 3, "3": 3}.get(get("Mode6", "Normal").lower(), 1),
        )
        s.env_file = env.path
        s.warnings.extend(env.warnings)
        _apply_transport(s, env.values)

        s.interval = min(s.collector_intervals.values()) if s.collector_intervals else 60
        s.slow_interval = max(s.collector_intervals.values()) if s.collector_intervals else 600

        if version == "3":
            s.v3_user = get("Username")
            s.v3_auth_pass = get("Password")
            if not s.v3_user:
                raise SettingsError("SNMPv3 needs a user name (Username field)")
            _apply_v3_options(s, get("Mode3"))
            if s.v3_priv_pass:
                s.warnings.append("The privacy passphrase inside 'SNMPv3 options' is deprecated and will stop working "
                                  "in a later release: enter it in the 'SNMPv3 privacy passphrase' field instead.")
            priv_field = get("Mode4")
            if priv_field:
                if len(priv_field) >= MIN_PASSPHRASE or not mode4_may_be_stale:
                    s.v3_priv_pass = priv_field
                elif not priv_field.isdigit():
                    # all-digit short values are the polling interval that 0.2.1 and older kept here
                    s.warnings.append("'SNMPv3 privacy passphrase' is shorter than %d characters and was ignored."
                                      % MIN_PASSPHRASE)
            if s.v3_level != "noAuthNoPriv" and len(s.v3_auth_pass) < MIN_PASSPHRASE:
                raise SettingsError("SNMPv3 authentication passphrase must be at least 8 characters")
            if s.v3_level == "authPriv":
                if not s.v3_priv_pass:
                    s.v3_priv_pass = s.v3_auth_pass   # common setup: same passphrase for both
                if len(s.v3_priv_pass) < MIN_PASSPHRASE:
                    raise SettingsError("SNMPv3 privacy passphrase must be at least 8 characters")
        return s


# Environment keys are intentionally limited to polling schedules and transport tuning. Credentials
# remain in Domoticz hardware parameters and are never loaded from or written to .env.
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

#: env key -> (Settings attribute, minimum, maximum).  The default is the dataclass default.
TRANSPORT_SETTINGS = {
    "SNMP_TIMEOUT": ("timeout", 1, 60),
    "SNMP_RETRIES": ("retries", 0, 5),
    "DISCOVERY_INTERVAL": ("discovery_interval", 60, 86400),
    "REFRESH_INTERVAL": ("refresh_interval", 30, 86400),
}

KNOWN_KEYS = tuple(SCHEDULE_DEFAULTS) + tuple(TRANSPORT_SETTINGS)

_ASSIGN = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class _Env(NamedTuple):
    values: Dict[str, str]
    path: Optional[str]            # the file that was read (None: no .env, built-in defaults)
    warnings: List[str]


def _clean_value(text: str) -> str:
    """The value of a KEY=VALUE line: quotes removed, a trailing ' # comment' dropped."""
    text = text.strip()
    if text[:1] in ("'", '"'):
        end = text.find(text[0], 1)
        if end != -1:
            rest = text[end + 1:].strip()
            if rest and not rest.startswith("#"):
                return text                  # text after the closing quote: keep it all, so the value is rejected
            return text[1:end]
        return text
    return re.split(r"\s+#", text, maxsplit=1)[0].strip()


def _load_env(env_path: Optional[str] = None) -> _Env:
    """Read the plugin-local .env; process environment takes precedence.

    Problems that do not stop the plugin (unknown or malformed lines) are returned as warnings,
    because a typo in a key name would otherwise leave the default silently in force.
    """
    path = Path(env_path) if env_path else Path(__file__).resolve().parent.parent / ".env"
    values: Dict[str, str] = {}
    warnings: List[str] = []
    found = None
    if path.is_file():
        found = str(path)
        try:
            content = path.read_text(encoding="utf-8-sig")        # tolerate a BOM from Windows editors
        except (OSError, UnicodeDecodeError) as exc:
            raise SettingsError("Cannot read schedule configuration %s: %s" % (path, exc))
        for lineno, raw in enumerate(content.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = _ASSIGN.match(line)
            if not match:
                warnings.append("%s line %d ignored: expected KEY=VALUE" % (path.name, lineno))
                continue
            key, value = match.group(1), _clean_value(match.group(2))
            if key not in KNOWN_KEYS:
                close = difflib.get_close_matches(key.upper(), KNOWN_KEYS, n=1, cutoff=0.6)
                warnings.append("%s line %d: unknown setting %s ignored%s" % (
                    path.name, lineno, key, " (did you mean %s?)" % close[0] if close else ""))
                continue
            values[key] = value
    for key in KNOWN_KEYS:
        if key in os.environ:
            values[key] = os.environ[key].strip()
    return _Env(values, found, warnings)


def _parse_collector_intervals(env_values: Mapping[str, str]) -> Dict[str, int]:
    intervals: Dict[str, int] = {}
    for env_key, (collector, default) in SCHEDULE_DEFAULTS.items():
        raw = env_values.get(env_key, str(default)) or str(default)
        intervals[collector] = _to_int(raw, env_key, MIN_INTERVAL, 86400)
    return intervals


def _apply_transport(s: Settings, env_values: Mapping[str, str]) -> None:
    for env_key, (attr, low, high) in TRANSPORT_SETTINGS.items():
        raw = env_values.get(env_key, "")
        if raw:
            setattr(s, attr, _to_int(raw, env_key, low, high))


def _to_int(text: str, label: str, low: int, high: int) -> int:
    try:
        value = int(float(text))
    except (ValueError, OverflowError):                 # "abc", "nan" and "inf" alike
        raise SettingsError("%s must be a number, got %r" % (label, text))
    if not low <= value <= high:
        raise SettingsError("%s must be between %d and %d, got %d" % (label, low, high, value))
    return value


def _apply_v3_options(s: Settings, text: str) -> None:
    """Parse ``level;authProto;privProto[;privPassphrase]`` (all parts optional).

    The fourth part (a privacy passphrase) is deprecated: it is shown in clear text on the hardware
    page.  It still works, with a warning; use the dedicated password field instead.
    """
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
