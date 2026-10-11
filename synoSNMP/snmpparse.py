"""Parser for the text output of net-snmp's snmpget / snmpwalk / snmpbulkwalk.

Pure functions only (no subprocess, no Domoticz), so it can be unit-tested
with captured output.  The client always runs the tools with ``-On`` so that
every line looks like::

    .1.3.6.1.4.1.6574.1.5.1.0 = STRING: "DS920+"
    .1.3.6.1.4.1.6574.2.1.1.6.0 = INTEGER: 34
    .1.3.6.1.2.1.25.1.1.0 = Timeticks: (29222) 0:04:52.22
    .1.3.6.1.4.1.6574.101.1.1.12.1 = Counter64: 18446744073709551000
    .1.3.6.1.4.1.6574.4.3.1.1.0 = Opaque: Float: 100.000000
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

# Normalised value types
INTEGER = "INTEGER"
GAUGE = "GAUGE"
COUNTER = "COUNTER"
COUNTER64 = "COUNTER64"
TIMETICKS = "TIMETICKS"
STRING = "STRING"
HEXSTRING = "HEXSTRING"
OID = "OID"
IPADDRESS = "IPADDRESS"
OPAQUE = "OPAQUE"
FLOAT = "FLOAT"
OTHER = "OTHER"
NOSUCH = "NOSUCH"        # "No Such Object" / "No Such Instance"
ENDOFMIB = "ENDOFMIB"    # "No more variables left in this MIB View"

_INT_TYPES = (INTEGER, GAUGE, COUNTER, COUNTER64, TIMETICKS)

_LINE = re.compile(r"^\.?(\d+(?:\.\d+)*)\s+=\s+(.*)$")
_TYPED = re.compile(r"^([A-Za-z][A-Za-z0-9\- ]*?):\s?(.*)$", re.S)
_LEADING_INT = re.compile(r"^\s*(-?\d+)")
_PAREN_INT = re.compile(r"\((-?\d+)\)")
_NUMBER = re.compile(r"^\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")

# Lines printed by the net-snmp tools that are errors, not (continuation of) values.
_ERROR_LINE = re.compile(
    r"^(Timeout:|Error\b|Reason:|Failed object|snmp\w*:|.*Unknown host|.*Authentication failure|"
    r"No Response|.*Cannot find module|.*Too long)",
    re.I,
)

_TYPE_MAP = {
    "integer": INTEGER, "integer32": INTEGER, "int": INTEGER,
    "gauge32": GAUGE, "unsigned32": GAUGE, "uinteger": GAUGE, "uinteger32": GAUGE, "uint32": GAUGE,
    "counter32": COUNTER, "counter": COUNTER,
    "counter64": COUNTER64,
    "timeticks": TIMETICKS,
    "string": STRING,
    "hex-string": HEXSTRING,
    "oid": OID,
    "ipaddress": IPADDRESS,
    "opaque": OPAQUE,
}


@dataclass(frozen=True)
class Varbind:
    """One ``OID = value`` pair, with the value already converted."""

    oid: str          # numeric, without leading dot
    type: str         # one of the constants above
    value: Any = None
    raw: str = ""

    @property
    def missing(self) -> bool:
        return self.type in (NOSUCH, ENDOFMIB)

    def as_int(self, default: Optional[int] = None) -> Optional[int]:
        v = self.value
        if self.type in _INT_TYPES and isinstance(v, int):
            return v
        if self.type == FLOAT and isinstance(v, float):
            return int(v)
        if self.type in (STRING, HEXSTRING, OPAQUE) and isinstance(v, str):
            f = decode_opaque_float(v) if self.type != STRING else None
            if f is not None:
                return int(f)
            m = _NUMBER.match(v)
            if m and self.type == STRING:
                return int(float(m.group(1)))
        return default

    def as_float(self, default: Optional[float] = None) -> Optional[float]:
        v = self.value
        if self.type == FLOAT and isinstance(v, float):
            return v
        if self.type in _INT_TYPES and isinstance(v, int):
            return float(v)
        if self.type in (STRING, HEXSTRING, OPAQUE) and isinstance(v, str):
            f = decode_opaque_float(v)
            if f is not None:
                return f
            if self.type == STRING:
                m = _NUMBER.match(v)
                if m:
                    return float(m.group(1))
        return default

    def as_text(self, default: str = "") -> str:
        v = self.value
        if v is None:
            return default
        if self.type == FLOAT:
            return "%g" % v
        return str(v).strip()


def decode_opaque_float(text: str) -> Optional[float]:
    """Decode a BER-wrapped float/double given as hex ("9f 78 04 42 c8 00 00").

    Some agents (Synology's UPS MIB among them) return floats as Opaque values.
    net-snmp normally prints them as ``Opaque: Float: 100.0``; this handles the
    case where they arrive as raw hex instead.
    """
    tokens = re.findall(r"[0-9a-fA-F]{2}", text.replace("0x", " "))
    if len(tokens) < 7:
        return None
    try:
        data = bytes(int(t, 16) for t in tokens)
    except ValueError:
        return None
    if data[0] == 0x9F and data[1] == 0x78 and data[2] == 4 and len(data) >= 7:
        return struct.unpack(">f", data[3:7])[0]
    if data[0] == 0x9F and data[1] == 0x79 and data[2] == 8 and len(data) >= 11:
        return struct.unpack(">d", data[3:11])[0]
    return None


def _convert(raw: str) -> Tuple[str, Any]:
    """Turn the text after ``=`` into (type, python value)."""
    text = raw.strip()
    low = text.lower()

    if low.startswith("no such object") or low.startswith("no such instance"):
        return NOSUCH, None
    if low.startswith("no more variables"):
        return ENDOFMIB, None

    # Bare quoted string (net-snmp prints empty strings without a type label).
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return STRING, text[1:-1]
    # Bare number (only when a tool is asked for raw timeticks, -Ot).
    if re.fullmatch(r"-?\d+", text):
        return INTEGER, int(text)

    m = _TYPED.match(text)
    if not m:
        return OTHER, text
    label, rest = m.group(1).strip(), m.group(2)
    kind = _TYPE_MAP.get(label.lower(), OTHER)

    if kind == OPAQUE:
        sub = re.match(r"^\s*(Float|Double):\s*(.*)$", rest, re.S | re.I)
        if sub:
            num = _NUMBER.match(sub.group(2))
            if num:
                return FLOAT, float(num.group(1))
        sub64 = re.match(r"^\s*(?:Counter64|UInteger64|Integer64):\s*(-?\d+)", rest, re.I)
        if sub64:
            return COUNTER64, int(sub64.group(1))
        f = decode_opaque_float(rest)
        if f is not None:
            return FLOAT, f
        return OPAQUE, rest.strip()

    if kind == TIMETICKS:
        p = _PAREN_INT.search(rest)
        if p:
            return TIMETICKS, int(p.group(1))
        n = _LEADING_INT.match(rest)
        return (TIMETICKS, int(n.group(1))) if n else (OTHER, rest)

    if kind in (INTEGER, GAUGE, COUNTER, COUNTER64):
        n = _LEADING_INT.match(rest)
        if n:
            return kind, int(n.group(1))
        p = _PAREN_INT.search(rest)          # enum style: up(1)
        if p:
            return kind, int(p.group(1))
        return OTHER, rest.strip()

    if kind == STRING:
        s = rest.strip()
        if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        return STRING, s

    if kind == OID:
        return OID, rest.strip().lstrip(".")

    if kind in (HEXSTRING, IPADDRESS):
        return kind, rest.strip()

    return OTHER, rest.strip()


def is_error_line(line: str) -> bool:
    return bool(_ERROR_LINE.match(line.strip()))


def parse_output(text: str) -> Tuple[List[Varbind], List[str]]:
    """Parse tool output into (varbinds, other_lines).

    ``other_lines`` are error/diagnostic lines (timeouts, "Error in packet", ...).
    Lines that follow a varbind and are not diagnostics are treated as the
    continuation of a multi-line string value.
    """
    entries: List[List[str]] = []   # [oid, raw]
    others: List[str] = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            entries.append([m.group(1), m.group(2)])
        elif not line.strip():
            continue
        elif is_error_line(line) or not entries:
            others.append(line.strip())
        else:
            entries[-1][1] += "\n" + line
    varbinds = []
    for oid, raw in entries:
        vtype, value = _convert(raw)
        varbinds.append(Varbind(oid=oid, type=vtype, value=value, raw=raw))
    return varbinds, others
