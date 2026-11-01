"""A NAS that is switched off on a schedule (EXPECTED_OFFLINE*): silence in its off periods is no error.

The schedule is a week: per weekday zero to six slots, a slot belongs to the day it starts and may run into the next
day.  The engine gets a fake wall clock, so a test says "it is Wednesday 23:45" instead of waiting for it.
Day 0 of ``at()`` is Wednesday 2026-10-28; day 1 is Thursday, 3 Saturday, 4 Sunday, 5 Monday, 7 the next Wednesday.
"""
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import domoticz_stub as dz
import mibdata
from fakeclient import FakeClient
from helpers import Clock, by_key, messages
from synoSNMP.cli import main
from synoSNMP.config import Settings, SettingsError
from synoSNMP.engine import CycleResult, Engine
from synoSNMP.model import CRIT, NONE, OK
from synoSNMP.offline import (DAYS, GRACE, INSIDE, OUTSIDE, OfflineSchedule, Slot, parse_slots, when)
from synoSNMP.poller import Poller

NO_ENV = os.path.join(tempfile.gettempdir(), "domoticz-syno-snmp-no-such-dir", ".env")
BASE = datetime(2026, 10, 28)         # a Wednesday


def at(hhmm, day=0, second=0):
    hour, minute = (int(p) for p in hhmm.split(":"))
    return BASE + timedelta(days=day, hours=hour, minutes=minute, seconds=second)


def daily(text, grace=900):
    """The same slots on every weekday."""
    return OfflineSchedule((parse_slots(text),) * 7, grace)


def week(grace=900, **slots):
    """``week(WED="23:30-12:00")``: the slots per weekday, the other days have none."""
    return OfflineSchedule(tuple(parse_slots(slots.get(day, "")) for day in DAYS), grace)


def settings_from(env_text):
    handle = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8")
    handle.write(env_text)
    handle.close()
    try:
        return Settings.from_parameters({"Address": "fake", "Mode5": "3"}, env_path=handle.name)
    finally:
        os.unlink(handle.name)


class WallClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def nas(env_text="EXPECTED_OFFLINE=23:30-12:00\n", now=None):
    """Engine on a fake DS920+ with a fake wall clock.  Returns engine, client, wall, clock."""
    clock, wall = Clock(), WallClock(now or at("18:00"))
    client = FakeClient(mibdata.ds920())
    return Engine(settings_from(env_text), client, clock=clock, wall=wall), client, wall, clock


def health(result):
    return {r.key: r for r in result.health}["plugin.health"]


# ---------------------------------------------------------------------------------------------------------------
class SlotParsingTests(unittest.TestCase):
    def test_a_slot_across_midnight(self):
        (slot,) = parse_slots("23:30-12:00")
        self.assertEqual((slot.start, slot.end, slot.minutes, slot.label), (23 * 60 + 30, 12 * 60, 12 * 60 + 30, "23:30-12:00"))

    def test_spaces_and_single_digit_hours(self):
        self.assertEqual(parse_slots(" 7:05 - 08:00 "), (Slot(7 * 60 + 5, 8 * 60),))

    def test_several_slots_come_back_sorted(self):
        self.assertEqual([s.label for s in parse_slots("23:00-23:30, 02:00-04:00,12:00-13:00")],
                         ["02:00-04:00", "12:00-13:00", "23:00-23:30"])

    def test_empty_or_none_is_no_slot(self):
        for text in ("", "  ", "none", "None", " NONE "):
            self.assertEqual(parse_slots(text), (), repr(text))

    def test_six_slots_are_allowed_and_seven_are_not(self):
        six = ",".join("%02d:00-%02d:30" % (h, h) for h in (0, 4, 8, 12, 16, 20))
        self.assertEqual(len(parse_slots(six)), 6)
        with self.assertRaisesRegex(ValueError, r"7 slots, at most 6"):
            parse_slots(six + ",22:00-22:30")

    def test_nonsense_is_rejected(self):
        for bad in ("23:30", "23:30-", "abc", "24:00-12:00", "23:60-12:00", "12:00-12:00", "23.30-12.00", "23:30 12:00",
                    "08:00-09:00,", ",08:00-09:00", "08:00-09:00,,10:00-11:00", "08:00-09:00;10:00-11:00"):
            with self.assertRaises(ValueError, msg=bad):
                parse_slots(bad)

    def test_slots_of_one_day_must_not_overlap(self):
        for bad in ("08:00-12:00,11:00-13:00",
                    "08:00-09:00,08:00-10:00",              # the same start
                    "23:30-12:00,23:45-23:50"):             # a slot that crosses midnight covers the rest of its day
            with self.assertRaisesRegex(ValueError, "overlap", msg=bad):
                parse_slots(bad)

    def test_slots_that_touch_or_follow_each_other_are_fine(self):
        self.assertEqual(len(parse_slots("06:00-08:00,08:00-10:00")), 2)
        self.assertEqual(len(parse_slots("22:00-23:00,23:30-12:00")), 2)
        self.assertEqual(len(parse_slots("08:00-09:00,23:30-12:00")), 2)


# ---------------------------------------------------------------------------------------------------------------
class DailyScheduleTests(unittest.TestCase):
    """The same slots every day: what a single EXPECTED_OFFLINE line means."""

    def setUp(self):
        self.night = daily("23:30-12:00")

    def test_phases_around_the_night(self):
        phase = self.night.phase
        self.assertEqual(phase(at("18:00")), OUTSIDE)
        self.assertEqual(phase(at("23:29", second=59)), OUTSIDE)
        self.assertEqual(phase(at("23:30")), INSIDE)             # the start belongs to the off period
        self.assertEqual(phase(at("00:00", day=1)), INSIDE)
        self.assertEqual(phase(at("03:00", day=1)), INSIDE)
        self.assertEqual(phase(at("11:59", day=1, second=59)), INSIDE)
        self.assertEqual(phase(at("12:00", day=1)), GRACE)       # the end does not
        self.assertEqual(phase(at("12:14", day=1, second=59)), GRACE)
        self.assertEqual(phase(at("12:15", day=1)), OUTSIDE)
        self.assertEqual(phase(at("12:15", day=1, second=1)), OUTSIDE)

    def test_a_slot_inside_one_day(self):
        w = daily("01:00-05:00")
        self.assertEqual([w.phase(at(t)) for t in ("00:59", "01:00", "04:59", "05:00", "05:14", "05:15")],
                         [OUTSIDE, INSIDE, INSIDE, GRACE, GRACE, OUTSIDE])

    def test_no_grace_means_no_grace(self):
        self.assertEqual(daily("23:30-12:00", grace=0).phase(at("12:00", day=1)), OUTSIDE)

    def test_a_grace_period_longer_than_the_gap_before_the_next_off_period_never_hides_it(self):
        w = daily("23:30-12:00", grace=86400)
        self.assertEqual(w.phase(at("20:00")), GRACE)
        self.assertEqual(w.phase(at("23:30")), INSIDE)

    def test_the_period_and_the_time_until_it_ends(self):
        phase, period = self.night.state(at("23:45"))
        self.assertEqual((phase, period), (INSIDE, (at("23:30"), at("12:00", day=1))))
        self.assertEqual(self.night.seconds_until_end(at("11:00", day=1)), 3600)
        self.assertEqual(self.night.seconds_until_end(at("23:45")), 12 * 3600 + 15 * 60)
        self.assertEqual(self.night.seconds_until_end(at("11:59", day=1, second=30)), 30)
        self.assertEqual(self.night.seconds_until_end(at("12:00", day=1)), 0.0)    # not inside any more
        self.assertEqual(self.night.seconds_until_end(at("18:00")), 0.0)

    def test_the_period_in_the_grace_phase_is_the_one_that_has_just_ended(self):
        self.assertEqual(self.night.state(at("12:05", day=1)), (GRACE, (at("23:30"), at("12:00", day=1))))
        self.assertEqual(self.night.state(at("18:00")), (OUTSIDE, None))

    def test_the_deadline_is_the_end_plus_the_grace_period_rounded_up_to_the_minute(self):
        end = at("12:00", day=1)
        self.assertEqual(daily("23:30-12:00").deadline(end), at("12:15", day=1))
        self.assertEqual(daily("23:30-12:00", grace=90).deadline(end), at("12:02", day=1))
        self.assertEqual(daily("23:30-12:00", grace=0).deadline(end), end)
        self.assertEqual(daily("20:00-23:50").deadline(at("23:50")), at("00:05", day=1))


class WeeklyScheduleTests(unittest.TestCase):
    def test_a_slot_belongs_to_the_day_on_which_it_starts(self):
        w = week(WED="23:30-12:00")
        self.assertEqual(w.phase(at("22:00")), OUTSIDE)
        self.assertEqual(w.phase(at("23:45")), INSIDE)                     # Wednesday night ...
        self.assertEqual(w.phase(at("03:00", day=1)), INSIDE)              # ... lasts into Thursday
        self.assertEqual(w.phase(at("12:05", day=1)), GRACE)
        self.assertEqual(w.phase(at("23:45", day=1)), OUTSIDE)             # Thursday night has no slot
        self.assertEqual(w.phase(at("23:45", day=-1)), OUTSIDE)            # neither has Tuesday night
        self.assertEqual(w.phase(at("23:45", day=7)), INSIDE)              # and it repeats every week

    def test_a_day_without_slots_is_never_off(self):
        w = week(SAT="01:00-05:00")                                         # 2026-10-31 is a Saturday
        self.assertEqual([w.phase(at("01:30", day=d)) for d in (2, 3, 4)], [OUTSIDE, INSIDE, OUTSIDE])

    def test_several_slots_in_one_day_each_with_its_own_grace_period(self):
        w = week(WED="02:00-04:00,12:00-13:00,23:00-23:30")
        phases = [(t, w.phase(at(t))) for t in ("01:59", "02:00", "03:59", "04:00", "04:14", "04:15", "11:59",
                                                "12:30", "13:10", "13:15", "23:15", "23:30", "23:44", "23:45")]
        self.assertEqual(phases, [("01:59", OUTSIDE), ("02:00", INSIDE), ("03:59", INSIDE), ("04:00", GRACE),
                                  ("04:14", GRACE), ("04:15", OUTSIDE), ("11:59", OUTSIDE), ("12:30", INSIDE),
                                  ("13:10", GRACE), ("13:15", OUTSIDE), ("23:15", INSIDE), ("23:30", GRACE),
                                  ("23:44", GRACE), ("23:45", OUTSIDE)])
        self.assertEqual(w.seconds_until_end(at("03:00")), 3600)           # the end of that slot, not of the day

    def test_six_slots_in_one_day(self):
        w = week(WED="00:00-01:00,04:00-05:00,08:00-09:00,12:00-13:00,16:00-17:00,20:00-21:00")
        self.assertEqual(len(w.days[2]), 6)
        self.assertEqual([w.phase(at(t)) for t in ("00:15", "03:00", "04:30", "13:20", "20:30", "22:00")],
                         [INSIDE, OUTSIDE, INSIDE, OUTSIDE, INSIDE, OUTSIDE])

    def test_slots_that_follow_each_other_are_one_off_period(self):
        w = week(WED="06:00-08:00,08:00-10:00")
        self.assertEqual(w.state(at("08:00")), (INSIDE, (at("06:00"), at("10:00"))))   # no grace period in between
        self.assertEqual(w.seconds_until_end(at("07:00")), 3 * 3600)
        self.assertEqual(w.phase(at("10:00")), GRACE)

    def test_slots_that_follow_each_other_across_midnight_are_one_off_period(self):
        w = week(WED="22:00-00:00", THU="00:00-06:00")
        self.assertEqual(w.state(at("23:00")), (INSIDE, (at("22:00"), at("06:00", day=1))))
        self.assertEqual(w.state(at("00:00", day=1)), (INSIDE, (at("22:00"), at("06:00", day=1))))
        self.assertEqual(w.phase(at("06:00", day=1)), GRACE)

    def test_the_end_of_one_day_overlapping_the_slot_of_the_next_is_one_off_period(self):
        w = week(WED="23:00-10:00", THU="08:00-12:00")
        self.assertEqual(w.state(at("11:00", day=1)), (INSIDE, (at("23:00"), at("12:00", day=1))))

    def test_a_slot_from_sunday_night_runs_into_monday(self):
        w = week(SUN="23:30-12:00")                                         # 2026-11-01 is a Sunday
        self.assertEqual(w.phase(at("22:00", day=4)), OUTSIDE)
        self.assertEqual(w.phase(at("23:45", day=4)), INSIDE)
        self.assertEqual(w.phase(at("03:00", day=5)), INSIDE)
        self.assertEqual(w.phase(at("12:05", day=5)), GRACE)

    def test_a_slot_almost_as_long_as_a_day(self):
        w = week(WED="23:59-23:58")
        self.assertEqual([w.phase(at(t, day=1)) for t in ("12:00", "23:57", "23:58")], [INSIDE, INSIDE, GRACE])

    def test_a_schedule_has_seven_days(self):
        with self.assertRaises(ValueError):
            OfflineSchedule(((),) * 6)

    def test_the_summary_for_the_log(self):
        self.assertEqual(daily("23:30-12:00").summary(), "every day 23:30-12:00")
        self.assertEqual(daily("02:00-03:00,23:30-12:00").summary(), "every day 02:00-03:00, 23:30-12:00")
        self.assertEqual(week(MON="23:30-12:00", WED="02:00-04:00,13:00-14:00").summary(),
                         "Mon 23:30-12:00; Tue -; Wed 02:00-04:00, 13:00-14:00; Thu -; Fri -; Sat -; Sun -")

    def test_clock_labels_name_the_weekday_only_when_it_is_not_today(self):
        self.assertEqual(when(at("12:00"), at("09:00")), "12:00")
        self.assertEqual(when(at("12:00", day=1), at("23:45")), "Thu 12:00")
        self.assertEqual(when(at("06:05", day=5), at("23:45")), "Mon 06:05")


# ---------------------------------------------------------------------------------------------------------------
class SettingsTests(unittest.TestCase):
    def test_disabled_by_default(self):
        s = settings_from("")
        self.assertIsNone(s.offline_schedule)
        self.assertEqual(s.warnings, [])

    def test_one_entry_for_every_day_and_the_default_grace(self):
        s = settings_from("EXPECTED_OFFLINE=23:30-12:00\n")
        self.assertEqual(s.offline_schedule.days, (parse_slots("23:30-12:00"),) * 7)
        self.assertEqual(s.offline_schedule.grace, 900)
        self.assertEqual(s.warnings, [])

    def test_several_slots_in_the_every_day_entry(self):
        s = settings_from("EXPECTED_OFFLINE=02:00-03:00,23:30-12:00\n")
        self.assertEqual([len(d) for d in s.offline_schedule.days], [2] * 7)

    def test_the_grace_period_is_configurable(self):
        self.assertEqual(settings_from("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_GRACE=600\n").offline_schedule.grace, 600)
        self.assertEqual(settings_from("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_GRACE=0\n").offline_schedule.grace, 0)

    def test_quotes_and_comments_are_understood(self):
        s = settings_from('EXPECTED_OFFLINE="23:30-12:00"  # the NAS sleeps\nEXPECTED_OFFLINE_SAT=\'\'\n')
        self.assertEqual(s.offline_schedule.days[0], parse_slots("23:30-12:00"))
        self.assertEqual(s.offline_schedule.days[5], ())

    def test_an_empty_value_means_off(self):
        self.assertIsNone(settings_from("EXPECTED_OFFLINE=\n").offline_schedule)

    def test_one_line_per_weekday_with_none_to_six_slots(self):
        values = ("23:30-12:00", "", "02:00-04:00,13:00-14:00", "none", "23:00-23:30", "",
                  "08:00-09:00,10:00-11:00,12:00-13:00,14:00-15:00,16:00-17:00,18:00-19:00")
        s = settings_from("".join("EXPECTED_OFFLINE_%s=%s\n" % (d, v) for d, v in zip(DAYS, values)))
        self.assertEqual([len(d) for d in s.offline_schedule.days], [1, 0, 2, 0, 1, 0, 6])
        self.assertEqual(s.warnings, [])

    def test_a_weekday_line_replaces_the_every_day_entry_for_that_day(self):
        s = settings_from("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_SAT=01:00-05:00\nEXPECTED_OFFLINE_SUN=\n")
        days = s.offline_schedule.days
        self.assertEqual(days[5], parse_slots("01:00-05:00"))          # Saturday: its own line
        self.assertEqual(days[6], ())                                   # Sunday: present but empty = no off period
        self.assertEqual(days[0], parse_slots("23:30-12:00"))          # Monday: no line, the every-day entry
        self.assertEqual(settings_from("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_SUN=none\n").offline_schedule.days[6], ())

    def test_weekday_lines_alone_leave_the_other_days_without_slots(self):
        s = settings_from("EXPECTED_OFFLINE_SAT=01:00-05:00\n")
        self.assertEqual([len(d) for d in s.offline_schedule.days], [0, 0, 0, 0, 0, 1, 0])

    def test_empty_weekday_lines_alone_are_no_schedule_and_no_complaint(self):
        s = settings_from("EXPECTED_OFFLINE_SAT=\nEXPECTED_OFFLINE_SUN=none\n")
        self.assertIsNone(s.offline_schedule)
        self.assertEqual(s.warnings, [])

    def test_a_bad_entry_is_a_configuration_error_that_names_the_setting(self):
        for key, bad in (("EXPECTED_OFFLINE", "23:30"), ("EXPECTED_OFFLINE_TUE", "25:00-12:00"),
                         ("EXPECTED_OFFLINE_FRI", "12:00-12:00"), ("EXPECTED_OFFLINE_SUN", "soon"),
                         ("EXPECTED_OFFLINE_MON", "08:00-09:00,"), ("EXPECTED_OFFLINE_THU", "08:00-12:00,11:00-13:00")):
            with self.assertRaises(SettingsError, msg=key + "=" + bad) as ctx:
                settings_from("%s=%s\n" % (key, bad))
            self.assertTrue(str(ctx.exception).startswith(key + " "), str(ctx.exception))

    def test_more_than_six_slots_in_a_day_are_refused(self):
        seven = ",".join("%02d:00-%02d:30" % (h, h) for h in (0, 3, 6, 9, 12, 15, 18))
        with self.assertRaisesRegex(SettingsError, r"EXPECTED_OFFLINE_WED has 7 slots, at most 6"):
            settings_from("EXPECTED_OFFLINE_WED=%s\n" % seven)

    def test_a_bad_grace_period_is_a_configuration_error(self):
        for bad in ("-1", "86401", "soon"):
            with self.assertRaises(SettingsError, msg=bad):
                settings_from("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_GRACE=%s\n" % bad)

    def test_a_grace_period_without_any_slot_is_pointed_out(self):
        for text in ("EXPECTED_OFFLINE_GRACE=600\n", "EXPECTED_OFFLINE_GRACE=600\nEXPECTED_OFFLINE_SAT=\n"):
            s = settings_from(text)
            self.assertIsNone(s.offline_schedule)
            self.assertEqual(len(s.warnings), 1)
            self.assertIn("EXPECTED_OFFLINE_GRACE has no effect without a slot", s.warnings[0])

    def test_typos_are_reported_with_a_suggestion(self):
        s = settings_from("EXPECTED_OFLINE=23:30-12:00\n")
        self.assertIsNone(s.offline_schedule)
        self.assertIn("did you mean EXPECTED_OFFLINE?", s.warnings[0])
        s = settings_from("EXPECTED_OFFLINE_MONDAY=23:30-12:00\n")
        self.assertIsNone(s.offline_schedule)
        self.assertIn("did you mean EXPECTED_OFFLINE_MON?", s.warnings[0])

    def test_the_process_environment_wins_over_the_file(self):
        with mock.patch.dict(os.environ, {"EXPECTED_OFFLINE": "01:00-05:00", "EXPECTED_OFFLINE_THU": "06:00-07:00"}):
            s = settings_from("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_THU=08:00-09:00\n")
        self.assertEqual(s.offline_schedule.days[0], parse_slots("01:00-05:00"))
        self.assertEqual(s.offline_schedule.days[3], parse_slots("06:00-07:00"))


# ---------------------------------------------------------------------------------------------------------------
class SilenceInsideAnOffPeriodTests(unittest.TestCase):
    def test_it_is_announced_once_and_is_not_an_error(self):
        engine, client, wall, clock = nas()
        engine.cycle()                                              # the NAS was up until now
        clock.advance(60)                                           # the next poll: the 60 s collectors are due
        client.down = True
        wall.now = at("23:45")
        result = engine.cycle()
        self.assertEqual(messages(result, "error"), [])
        notices = messages(result, "notice")
        self.assertEqual(len(notices), 1)
        self.assertIn("as expected", notices[0])
        self.assertIn("switched off on schedule until Thu 12:00", notices[0])
        self.assertIn("still silent at Thu 12:15", notices[0])      # when silence turns into an outage
        self.assertFalse(result.reachable)
        self.assertTrue(result.expected_off)
        self.assertEqual(result.readings, [])
        for minutes in (5, 10, 15):                                  # and not again, however long the night is
            clock.advance(300)
            wall.now = at("23:45") + timedelta(minutes=minutes)
            again = engine.cycle()
            self.assertEqual((messages(again, "error"), messages(again, "notice"), messages(again, "info")), ([], [], []))
            self.assertTrue(again.expected_off)

    def test_plugin_health_is_grey_and_says_why(self):
        engine, client, wall, _ = nas(now=at("23:45"))
        client.down = True
        h = health(engine.cycle())
        self.assertEqual(h.nvalue, NONE)
        self.assertTrue(h.svalue.startswith("NAS switched off on schedule until Thu 12:00, probing every 5 min"), h.svalue)

    def test_the_health_text_names_no_weekday_for_an_end_that_is_today(self):
        engine, client, wall, _ = nas(now=at("03:00", day=1))
        client.down = True
        self.assertTrue(health(engine.cycle()).svalue.startswith("NAS switched off on schedule until 12:00,"))

    def test_polling_slows_down_to_a_probe_every_five_minutes(self):
        engine, client, wall, _ = nas(now=at("23:45"))
        client.down = True
        result = engine.cycle()
        self.assertEqual(result.wait, 300.0)
        self.assertLessEqual(result.snmp_calls, 1)                  # one cheap probe, no discovery, no collectors

    def test_but_it_wakes_up_when_the_off_period_ends(self):
        engine, client, wall, _ = nas(now=at("11:58", day=1))
        client.down = True
        self.assertEqual(engine.cycle().wait, 120.0)
        wall.now = at("11:59", day=1, second=59)
        self.assertEqual(engine.cycle().wait, 1.0)

    def test_the_failure_time_decides_not_the_start_of_the_cycle(self):
        """The NAS shuts down in the middle of a cycle that began before the off period: the failure is inside it."""
        engine, client, wall, clock = nas(now=at("23:29", second=50))
        original = client.walk

        def walk(oid):
            wall.now = at("23:30", second=20)                      # the shutdown happens, and time moves on
            client.down = True
            return original(oid)
        client.walk = walk
        result = engine.cycle()
        self.assertFalse(result.reachable)
        self.assertEqual(messages(result, "error"), [])
        self.assertTrue(result.expected_off)

    def test_a_dead_nas_before_the_off_period_starts_is_still_an_error(self):
        engine, client, wall, _ = nas(now=at("23:29", second=59))
        client.down = True
        result = engine.cycle()
        self.assertEqual(len(messages(result, "error")), 1)
        self.assertFalse(result.expected_off)


class WeeklyScheduleEngineTests(unittest.TestCase):
    def test_a_day_without_a_slot_is_an_outage_on_that_night(self):
        env = "EXPECTED_OFFLINE_WED=23:30-12:00\n"
        engine, client, wall, _ = nas(env, now=at("23:45"))                 # Wednesday night: off as scheduled
        client.down = True
        self.assertEqual(messages(engine.cycle(), "error"), [])
        engine, client, wall, _ = nas(env, now=at("23:45", day=1))          # Thursday night: nothing scheduled
        client.down = True
        result = engine.cycle()
        self.assertEqual(len(messages(result, "error")), 1)
        self.assertFalse(result.expected_off)
        self.assertEqual(health(result).nvalue, CRIT)

    def test_two_slots_in_one_day(self):
        engine, client, wall, clock = nas("EXPECTED_OFFLINE_WED=02:00-04:00,13:00-14:00\n", now=at("03:00"))
        client.down = True
        first = engine.cycle()                                              # the first slot
        self.assertEqual(messages(first, "error"), [])
        self.assertIn("switched off on schedule until 04:00", messages(first, "notice")[0])
        self.assertIn("still silent at 04:15", messages(first, "notice")[0])
        clock.advance(3600)
        wall.now = at("09:00")                                              # between the slots: a real outage
        outage = engine.cycle()
        self.assertEqual(len(messages(outage, "error")), 1)
        self.assertFalse(outage.expected_off)
        clock.advance(3600)
        wall.now = at("13:30")                                              # the second slot: silence is expected again
        second = engine.cycle()
        self.assertEqual(messages(second, "error"), [])
        self.assertTrue(second.expected_off)
        self.assertTrue(health(second).svalue.startswith("NAS switched off on schedule until 14:00,"))

    def test_every_slot_is_announced_and_its_return_too(self):
        engine, client, wall, clock = nas("EXPECTED_OFFLINE_WED=02:00-04:00,13:00-14:00\n", now=at("03:00"))
        client.down = True
        self.assertEqual(len(messages(engine.cycle(), "notice")), 1)        # off at 02:00
        client.down = False
        wall.now = at("04:10")
        clock.advance(3600)
        self.assertEqual(messages(engine.cycle(), "notice"),
                         ["SNMP connection restored: the NAS is back after its scheduled off period"])
        client.down = True
        wall.now = at("13:30")
        clock.advance(3600)
        again = engine.cycle()                                              # off again at 13:00
        self.assertEqual(messages(again, "error"), [])
        self.assertEqual(len(messages(again, "notice")), 1)
        self.assertIn("until 14:00", messages(again, "notice")[0])

    def test_slots_that_follow_each_other_across_midnight_are_one_night(self):
        engine, client, wall, clock = nas("EXPECTED_OFFLINE_WED=22:00-00:00\nEXPECTED_OFFLINE_THU=00:00-06:00\n", now=at("23:00"))
        client.down = True
        evening = engine.cycle()
        self.assertTrue(health(evening).svalue.startswith("NAS switched off on schedule until Thu 06:00,"))
        clock.advance(3600)
        wall.now = at("00:30", day=1)
        night = engine.cycle()
        self.assertEqual((messages(night, "error"), messages(night, "notice")), ([], []))
        self.assertTrue(health(night).svalue.startswith("NAS switched off on schedule until 06:00,"))

    def test_the_wake_up_time_follows_the_end_of_the_slot_that_is_running(self):
        engine, client, wall, _ = nas("EXPECTED_OFFLINE_WED=02:00-04:00,13:00-14:00\n", now=at("03:58"))
        client.down = True
        self.assertEqual(engine.cycle().wait, 120.0)                        # 04:00, not 14:00
        wall.now = at("13:20")
        self.assertEqual(engine.cycle().wait, 300.0)                        # 14:00 is more than a probe away

    def test_a_night_that_starts_on_sunday_ends_on_monday(self):
        engine, client, wall, _ = nas("EXPECTED_OFFLINE_SUN=23:30-12:00\n", now=at("11:58", day=5))
        client.down = True
        result = engine.cycle()
        self.assertEqual((messages(result, "error"), result.wait), ([], 120.0))

    def test_the_grace_period_is_announced_when_the_plugin_starts_inside_it(self):
        engine, client, wall, _ = nas(now=at("12:05", day=1))
        client.down = True
        notices = messages(engine.cycle(), "notice")
        self.assertEqual(len(notices), 1)
        self.assertIn("the scheduled off period ended at 12:00 and the NAS is given time to start", notices[0])
        self.assertIn("still silent at 12:15", notices[0])


class SilenceAfterTheOffPeriodTests(unittest.TestCase):
    def test_the_grace_period_gives_the_nas_time_to_boot(self):
        engine, client, wall, _ = nas(now=at("12:05", day=1))
        client.down = True
        result = engine.cycle()
        self.assertEqual(messages(result, "error"), [])
        self.assertTrue(result.expected_off)
        self.assertIsNone(result.wait)                              # in the grace period the normal tick applies again
        h = health(result)
        self.assertEqual(h.nvalue, NONE)
        self.assertTrue(h.svalue.startswith("Waiting for the NAS to start (expected back at 12:00, outage from 12:15)"), h.svalue)

    def test_after_the_grace_period_silence_is_an_outage_reported_once(self):
        engine, client, wall, clock = nas(now=at("23:45"))
        client.down = True
        engine.cycle()                                              # the quiet night
        wall.now = at("12:15", day=1)
        result = engine.cycle()
        self.assertEqual(len(messages(result, "error")), 1)
        self.assertIn("No SNMP answer", messages(result, "error")[0])
        self.assertFalse(result.expected_off)
        self.assertIsNone(result.wait)
        self.assertEqual(health(result).nvalue, CRIT)
        clock.advance(60)
        wall.now = at("12:16", day=1)
        self.assertEqual(messages(engine.cycle(), "error"), [])     # the usual once-only rule

    def test_without_a_grace_period_the_end_of_the_off_period_is_the_deadline(self):
        engine, client, wall, _ = nas("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_GRACE=0\n", now=at("12:00", day=1))
        client.down = True
        self.assertEqual(len(messages(engine.cycle(), "error")), 1)

    def test_every_slot_has_its_own_grace_period(self):
        engine, client, wall, _ = nas("EXPECTED_OFFLINE_WED=02:00-04:00,13:00-14:00\n", now=at("14:10"))
        client.down = True
        self.assertEqual(messages(engine.cycle(), "error"), [])             # 10 minutes after the second slot
        engine, client, wall, _ = nas("EXPECTED_OFFLINE_WED=02:00-04:00,13:00-14:00\n", now=at("14:20"))
        client.down = True
        self.assertEqual(len(messages(engine.cycle(), "error")), 1)

    def test_a_silent_nas_outside_the_off_periods_is_an_outage_as_before(self):
        engine, client, wall, _ = nas(now=at("18:00"))
        client.down = True
        result = engine.cycle()
        self.assertEqual(len(messages(result, "error")), 1)
        self.assertIn("No SNMP answer", messages(result, "error")[0])
        self.assertFalse(result.expected_off)
        self.assertIsNone(result.wait)
        self.assertEqual(health(result).nvalue, CRIT)

    def test_without_a_schedule_nothing_changes_at_any_hour(self):
        engine, client, wall, _ = nas(env_text="", now=at("23:45"))
        client.down = True
        result = engine.cycle()
        self.assertEqual(len(messages(result, "error")), 1)
        self.assertFalse(result.expected_off)
        self.assertIsNone(result.wait)


class TheNasComesBackTests(unittest.TestCase):
    def test_a_nas_that_is_up_inside_an_off_period_is_polled_normally(self):
        """Switched on early, or not switched off tonight."""
        engine, client, wall, _ = nas(now=at("02:00", day=1))
        result = engine.cycle()
        self.assertTrue(result.reachable)
        self.assertFalse(result.expected_off)
        self.assertIsNone(result.wait)
        self.assertEqual(health(result).nvalue, OK)
        self.assertEqual((messages(result, "error"), messages(result, "notice")), ([], []))
        self.assertIn("res.cpu", by_key(result))

    def test_the_return_is_announced_once_and_polling_resumes_in_full(self):
        engine, client, wall, clock = nas(now=at("23:45"))
        client.down = True
        engine.cycle()
        client.down = False
        wall.now = at("12:03", day=1)
        clock.advance(3600)
        result = engine.cycle()
        self.assertTrue(result.reachable)
        self.assertEqual(messages(result, "notice"),
                         ["SNMP connection restored: the NAS is back after its scheduled off period"])
        self.assertEqual(messages(result, "error"), [])
        self.assertIn("res.cpu", by_key(result))
        self.assertEqual(health(result).nvalue, OK)
        clock.advance(60)
        self.assertEqual(messages(engine.cycle(), "notice"), [])

    def test_the_next_night_is_announced_again(self):
        engine, client, wall, clock = nas(now=at("23:45"))
        client.down = True
        engine.cycle()
        client.down = False
        wall.now = at("12:03", day=1)
        engine.cycle()
        client.down = True
        wall.now = at("23:45", day=1)
        clock.advance(40000)
        result = engine.cycle()
        self.assertEqual(messages(result, "error"), [])
        self.assertEqual(len(messages(result, "notice")), 1)

    def test_after_a_reported_outage_only_the_usual_restored_notice_appears(self):
        engine, client, wall, clock = nas(now=at("23:45"))
        client.down = True
        engine.cycle()
        wall.now = at("13:00", day=1)                               # past the grace period: an outage
        self.assertEqual(len(messages(engine.cycle(), "error")), 1)
        client.down = False
        clock.advance(60)
        self.assertEqual(messages(engine.cycle(), "notice"), ["SNMP connection restored"])

    def test_traffic_rates_start_again_after_the_night(self):
        engine, client, wall, clock = nas(now=at("23:00"))
        engine.cycle()
        clock.advance(60)
        self.assertFalse(by_key(engine.cycle())["net.eth0.rx"].pending)
        clock.advance(60)
        client.down = True
        wall.now = at("23:45")
        engine.cycle()
        client.down = False
        wall.now = at("12:30", day=1)
        clock.advance(60)
        self.assertTrue(by_key(engine.cycle())["net.eth0.rx"].pending)      # a counter from last night is no sample


# ---------------------------------------------------------------------------------------------------------------
class PollerTests(unittest.TestCase):
    class Recorder:
        """Stands in for the stop event: records how long the poller wants to sleep, stops after ``limit`` sleeps."""
        def __init__(self, limit):
            self.waits, self.limit = [], limit

        def is_set(self):
            return len(self.waits) >= self.limit

        def wait(self, seconds):
            self.waits.append(seconds)

        def set(self):
            self.limit = 0

    class Engine:
        def __init__(self, waits):
            self.waits = list(waits)
            self.client = mock.Mock()

        def cycle(self):
            return CycleResult(wait=self.waits.pop(0))

    def sleeps(self, waits, interval=60):
        poller = Poller(self.Engine(waits), interval)
        poller._halt = self.Recorder(len(waits))
        poller.run()
        return [round(w) for w in poller._halt.waits]

    def test_a_cycle_can_ask_for_a_different_pause(self):
        self.assertEqual(self.sleeps([300.0, None, 120.0]), [300, 60, 120])

    def test_the_pause_is_never_shorter_than_a_second(self):
        self.assertEqual(self.sleeps([0.2]), [1])


class PluginTests(unittest.TestCase):
    """plugin.py: what an expected silence does (and does not do) to the Domoticz devices."""

    def setUp(self):
        import test_plugin
        self.helpers = test_plugin
        self.plugin = test_plugin.load_plugin()
        self.addCleanup(self.plugin._patcher.stop)
        dz.logs.clear()

    def start(self, env=None):
        made = []
        self.plugin.SnmpClient = self.helpers.fake_client_factory(made)
        with mock.patch.dict(os.environ, env or {}):
            self.plugin.onStart()
        self.addCleanup(self.plugin.onStop)
        poller = self.plugin._plugin.poller
        if poller is None:                                          # a configuration error: the hardware did not start
            return {}
        import time
        deadline = time.time() + 20
        while time.time() < deadline and poller.results.qsize() == 0:
            time.sleep(0.05)
        self.plugin.onHeartbeat()                                   # the first, healthy result creates the devices
        return {d.DeviceID: d for d in dz.Devices.values()}

    def push(self, **fields):
        self.plugin._plugin.poller.results.put(CycleResult(**fields))
        self.plugin.onHeartbeat()

    def test_a_scheduled_silence_leaves_the_devices_alone_and_updates_the_health_device(self):
        ids = self.start()
        grey = ids["plugin.health"]
        self.push(reachable=False, expected_off=True, wait=300.0,
                  health=[type(self).reading(0, "NAS switched off on schedule until Thu 12:00, probing every 5 min")])
        self.assertEqual(ids["res.cpu"].TimedOut, 0)
        self.assertEqual(grey.nValue, 0)
        self.assertTrue(grey.sValue.startswith("NAS switched off on schedule"), grey.sValue)

    def test_a_real_outage_still_flags_every_device(self):
        ids = self.start()
        self.push(reachable=False, health=[type(self).reading(4, "NAS not answering (no SNMP response)")])
        self.assertEqual(ids["res.cpu"].TimedOut, 1)
        self.assertEqual(ids["plugin.health"].TimedOut, 0)

    @staticmethod
    def reading(level, text):
        from synoSNMP.model import Reading
        return Reading.alert("plugin.health", "Plugin Health", level, text)

    def schedule_lines(self):
        return [t for k, t in dz.logs if k == "log" and "switched off on schedule" in t]

    def test_the_schedule_is_confirmed_in_the_log_at_start_up(self):
        self.start(env={"EXPECTED_OFFLINE": "23:30-12:00"})
        (line,) = self.schedule_lines()
        self.assertIn("every day 23:30-12:00", line)
        self.assertIn("15 min", line)

    def test_a_weekly_schedule_is_listed_day_by_day(self):
        self.start(env={"EXPECTED_OFFLINE_MON": "23:30-12:00,02:00-03:00", "EXPECTED_OFFLINE_GRACE": "600"})
        (line,) = self.schedule_lines()
        self.assertIn("Mon 02:00-03:00, 23:30-12:00; Tue -; Wed -", line)
        self.assertIn("10 min", line)

    def test_no_schedule_no_line(self):
        self.start()
        self.assertEqual(self.schedule_lines(), [])

    def test_a_bad_schedule_is_a_configuration_error_and_the_hardware_does_not_start(self):
        self.start(env={"EXPECTED_OFFLINE_TUE": "08:00-12:00,11:00-13:00"})
        errors = [t for k, t in dz.logs if k == "error"]
        self.assertTrue(any("Configuration error: EXPECTED_OFFLINE_TUE" in t and "overlap" in t for t in errors), errors)

    def test_the_expected_silence_notice_is_logged_at_the_normal_level(self):
        self.start()
        dz.logs.clear()
        self.plugin._plugin._log(CycleResult(messages=[("notice", "fake does not answer, as expected: ...")]))
        self.assertEqual(dz.logs, [("log", "fake does not answer, as expected: ...")])

    def test_the_next_poll_is_announced_with_the_requested_pause(self):
        self.start()
        self.plugin._plugin.settings.log_level = 2
        dz.logs.clear()
        self.plugin._plugin._log(CycleResult(duration=10.0, wait=300.0))
        self.assertIn("next poll in about 290s", [t for _, t in dz.logs][-1])


class CommandLineTests(unittest.TestCase):
    def test_a_diagnostic_run_reports_a_silent_nas_as_silent_whatever_the_schedule_says(self):
        out, err = io.StringIO(), io.StringIO()

        def factory(settings):
            self.assertIsNone(settings.offline_schedule)
            c = FakeClient(mibdata.ds920(), settings)
            c.down = True
            return c
        every_day = {"EXPECTED_OFFLINE_" + d: "00:00-23:59" for d in DAYS}
        with mock.patch.dict(os.environ, every_day):
            rc = main(["fake"], out=out, err=err, client_factory=factory)
        self.assertEqual(rc, 2)
        self.assertIn("No SNMP answer", err.getvalue())


if __name__ == "__main__":
    unittest.main()
