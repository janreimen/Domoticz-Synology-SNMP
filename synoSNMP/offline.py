"""A NAS that is switched off on a schedule: its silence is expected, not an error.

``EXPECTED_OFFLINE=23:30-12:00`` in ``.env`` names the hours in which the NAS is meant to be off, as local time of the
Domoticz host.  The window may cross midnight (as in the example) and is repeated every day.  After the window ends the
NAS needs time to boot and start its SNMP service, so a grace period (``EXPECTED_OFFLINE_GRACE``, default 15 minutes)
follows in which silence is still expected.  Only a NAS that is silent after that is an outage.

This module only answers "which phase is it now?"; what the engine does about it lives in ``engine.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

OUTSIDE = "outside"      # silence is an outage
INSIDE = "inside"        # the NAS is meant to be off
GRACE = "grace"          # the window is over; the NAS is given time to boot

DEFAULT_GRACE = 900      # seconds
_WINDOW = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class OfflineWindow:
    start: int                      # minute of the day at which the NAS is switched off
    end: int                        # minute of the day at which it is switched on again
    grace: int = DEFAULT_GRACE      # seconds after ``end`` in which silence is still expected

    @classmethod
    def parse(cls, text: str, grace: int = DEFAULT_GRACE) -> "OfflineWindow":
        """``HH:MM-HH:MM``.  Raises ValueError, with a message that fits after the setting name."""
        match = _WINDOW.match(text.strip())
        if not match:
            raise ValueError("must look like 23:30-12:00 (start and end as HH:MM, 24-hour clock), got %r" % text)
        h1, m1, h2, m2 = (int(g) for g in match.groups())
        if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
            raise ValueError("has an impossible time (hours 0-23, minutes 0-59), got %r" % text)
        if (h1, m1) == (h2, m2):
            raise ValueError("start and end must differ, got %r" % text)
        return cls(h1 * 60 + m1, h2 * 60 + m2, grace)

    # -- labels ------------------------------------------------------------------------------
    @property
    def label(self) -> str:
        return "%s-%s" % (_hhmm(self.start), _hhmm(self.end))

    @property
    def back_label(self) -> str:
        """When the NAS is meant to be on again (HH:MM)."""
        return _hhmm(self.end)

    @property
    def deadline_label(self) -> str:
        """When silence becomes an outage: the end of the window plus the grace period (HH:MM)."""
        return _hhmm((self.end + (self.grace + 59) // 60) % (24 * 60))

    # -- the question the engine asks ---------------------------------------------------------
    def phase(self, now: datetime) -> str:
        second = now.hour * 3600 + now.minute * 60 + now.second
        start, end = self.start * 60, self.end * 60
        inside = (start <= second < end) if start < end else (second >= start or second < end)
        if inside:
            return INSIDE
        if self.grace > 0 and (now - _last_at(now, self.end)).total_seconds() < self.grace:
            return GRACE
        return OUTSIDE

    def seconds_until_end(self, now: datetime) -> float:
        """Time until the window next ends (the NAS is meant to be on again)."""
        return (_next_at(now, self.end) - now).total_seconds()


def _hhmm(minute: int) -> str:
    return "%02d:%02d" % divmod(minute, 60)


def _at(now: datetime, minute: int) -> datetime:
    return now.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0)


def _last_at(now: datetime, minute: int) -> datetime:
    """The most recent moment, not later than ``now``, at which the clock showed this minute of the day."""
    moment = _at(now, minute)
    return moment - timedelta(days=1) if moment > now else moment


def _next_at(now: datetime, minute: int) -> datetime:
    """The next moment, later than ``now``, at which the clock shows this minute of the day."""
    moment = _at(now, minute)
    return moment + timedelta(days=1) if moment <= now else moment
