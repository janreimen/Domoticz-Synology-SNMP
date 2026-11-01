"""A NAS that is switched off on a schedule: its silence is expected, not an error.

The schedule is a week.  For every weekday there are zero to six *slots* ``HH:MM-HH:MM`` in which the NAS is meant to be
off, as local time of the Domoticz host (``EXPECTED_OFFLINE_MON`` ... ``EXPECTED_OFFLINE_SUN`` in ``.env``;
``EXPECTED_OFFLINE`` is the entry of every day that has none of its own).

* A slot belongs to the day on which it starts.  When its end is not later than its start it runs into the next day:
  ``23:30-12:00`` on Monday is Monday 23:30 to Tuesday 12:00.
* Slots that touch or overlap, also across midnight, are one off period.
* After the end of an off period the NAS needs time to boot and start its SNMP service, so a grace period
  (``EXPECTED_OFFLINE_GRACE``, default 15 minutes) follows in which silence is still expected.  Only a NAS that is
  silent after that is an outage.

This module only answers "which phase is it now, and which off period is it?"; what the engine does about it lives in
``engine.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, NamedTuple, Optional, Sequence, Tuple

OUTSIDE = "outside"      # silence is an outage
INSIDE = "inside"        # the NAS is meant to be off
GRACE = "grace"          # the off period is over; the NAS is given time to boot

DEFAULT_GRACE = 900      # seconds
MAX_SLOTS = 6            # per day
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")      # the order of datetime.weekday(): Monday is 0
_DAY = 24 * 60           # minutes
_SLOT = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")

Period = Tuple[datetime, datetime]        # an off period: switched off at, switched on again at


class Slot(NamedTuple):
    start: int      # minute of the day at which the NAS is switched off
    end: int        # minute of the day at which it is switched on again; not later than ``start``: the next day

    @property
    def minutes(self) -> int:
        return (self.end - self.start) % _DAY

    @property
    def label(self) -> str:
        return "%s-%s" % (_hhmm(self.start), _hhmm(self.end))


def parse_slots(text: str) -> Tuple[Slot, ...]:
    """``HH:MM-HH:MM[,HH:MM-HH:MM ...]``, at most six, none of them overlapping; empty or ``none``: no slot.

    The result is sorted by start.  Raises ValueError, with a message that fits after the name of the setting.
    """
    text = text.strip()
    if not text or text.lower() == "none":
        return ()
    slots = [_parse_slot(part) for part in text.split(",")]
    if len(slots) > MAX_SLOTS:
        raise ValueError("has %d slots, at most %d are allowed per day" % (len(slots), MAX_SLOTS))
    ordered = sorted(slots)
    for first, second in zip(ordered, ordered[1:]):
        # a slot that crosses midnight runs past the start of every later slot of its day
        if first.start + first.minutes > second.start:
            raise ValueError("slots %s and %s overlap" % (first.label, second.label))
    return tuple(ordered)


def _parse_slot(part: str) -> Slot:
    part = part.strip()
    if not part:
        raise ValueError("has an empty slot (a stray comma?)")
    match = _SLOT.match(part)
    if not match:
        raise ValueError("slot %r must look like 23:30-12:00 (start and end as HH:MM, 24-hour clock)" % part)
    h1, m1, h2, m2 = (int(g) for g in match.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        raise ValueError("slot %r has an impossible time (hours 0-23, minutes 0-59)" % part)
    if (h1, m1) == (h2, m2):
        raise ValueError("slot %r: start and end must differ" % part)
    return Slot(h1 * 60 + m1, h2 * 60 + m2)


@dataclass(frozen=True)
class OfflineSchedule:
    days: Tuple[Tuple[Slot, ...], ...]      # the slots that START on each weekday, Monday first: always seven entries
    grace: int = DEFAULT_GRACE              # seconds after the end of an off period in which silence is still expected

    def __post_init__(self) -> None:
        if len(self.days) != len(DAYS):
            raise ValueError("a schedule has one entry per weekday (%d), got %d" % (len(DAYS), len(self.days)))

    # -- the questions the engine asks ---------------------------------------------------------
    def state(self, now: datetime) -> Tuple[str, Optional[Period]]:
        """``(phase, off period)``: INSIDE the period that contains ``now``; GRACE after the period that has just
        ended; OUTSIDE (no period) otherwise."""
        periods = self.periods(now)
        for period in periods:
            if period[0] <= now < period[1]:
                return INSIDE, period
        if self.grace > 0:
            ended = [p for p in periods if p[1] <= now]
            if ended and (now - ended[-1][1]).total_seconds() < self.grace:
                return GRACE, ended[-1]
        return OUTSIDE, None

    def phase(self, now: datetime) -> str:
        return self.state(now)[0]

    def seconds_until_end(self, now: datetime) -> float:
        """Time until the off period that contains ``now`` ends; 0 when ``now`` is not inside one."""
        phase, period = self.state(now)
        return (period[1] - now).total_seconds() if phase == INSIDE and period else 0.0

    def deadline(self, end: datetime) -> datetime:
        """When silence after an off period that ended at ``end`` becomes an outage (rounded up to the minute)."""
        return end + timedelta(minutes=(self.grace + 59) // 60)

    # -- the off periods around a moment -----------------------------------------------------
    def periods(self, now: datetime) -> List[Period]:
        """The merged off periods from two days before ``now`` to a week after it, in order.

        Two days back catches a period that began on an earlier day (a slot is shorter than a day) and the one that a
        long grace period still refers to; a week ahead catches the next period of any weekly schedule.
        """
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        raw: List[Period] = []
        for offset in range(-2, 8):
            day = midnight + timedelta(days=offset)
            for slot in self.days[day.weekday()]:
                begin = day + timedelta(minutes=slot.start)
                raw.append((begin, begin + timedelta(minutes=slot.minutes)))
        raw.sort()
        merged: List[Period] = []
        for begin, end in raw:
            if merged and begin <= merged[-1][1]:                  # touches or overlaps the previous one
                if end > merged[-1][1]:
                    merged[-1] = (merged[-1][0], end)
            else:
                merged.append((begin, end))
        return merged

    # -- labels ------------------------------------------------------------------------------
    def summary(self) -> str:
        """One line for the log, for example ``every day 23:30-12:00`` or ``Mon 23:30-12:00; Tue -; ...``."""
        if len(set(self.days)) == 1:
            return "every day " + _slots_label(self.days[0])
        return "; ".join("%s %s" % (name.title(), _slots_label(slots) or "-") for name, slots in zip(DAYS, self.days))


def when(moment: datetime, now: datetime) -> str:
    """``HH:MM``; with the weekday in front when it is not today (``Thu 12:00``)."""
    text = "%02d:%02d" % (moment.hour, moment.minute)
    return text if moment.date() == now.date() else "%s %s" % (DAYS[moment.weekday()].title(), text)


def _slots_label(slots: Sequence[Slot]) -> str:
    return ", ".join(slot.label for slot in slots)


def _hhmm(minute: int) -> str:
    return "%02d:%02d" % divmod(minute, 60)
