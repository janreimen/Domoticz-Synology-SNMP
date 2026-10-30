"""A NAS that is switched off on a schedule (EXPECTED_OFFLINE): silence in that window is no error.

The engine gets a fake wall clock, so a test says "it is 23:45" instead of waiting for it.
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
from synoSNMP.offline import GRACE, INSIDE, OUTSIDE, OfflineWindow
from synoSNMP.poller import Poller

NO_ENV = os.path.join(tempfile.gettempdir(), "domoticz-syno-snmp-no-such-dir", ".env")
DAY = (2026, 10, 28)                  # a Wednesday; the NAS goes off on Wednesday 23:30 and is back on Thursday 12:00


def at(hhmm, day=0, second=0):
    hour, minute = (int(p) for p in hhmm.split(":"))
    return datetime(DAY[0], DAY[1], DAY[2] + day, hour, minute, second)


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


class WindowParsingTests(unittest.TestCase):
    def test_a_window_across_midnight(self):
        w = OfflineWindow.parse("23:30-12:00")
        self.assertEqual((w.start, w.end, w.grace), (23 * 60 + 30, 12 * 60, 900))
        self.assertEqual((w.label, w.back_label, w.deadline_label), ("23:30-12:00", "12:00", "12:15"))

    def test_spaces_and_single_digit_hours(self):
        w = OfflineWindow.parse(" 7:05 - 08:00 ")
        self.assertEqual((w.start, w.end), (7 * 60 + 5, 8 * 60))

    def test_the_grace_period_is_rounded_up_to_the_minute_in_labels(self):
        self.assertEqual(OfflineWindow.parse("23:30-12:00", grace=90).deadline_label, "12:02")
        self.assertEqual(OfflineWindow.parse("23:30-12:00", grace=0).deadline_label, "12:00")

    def test_the_deadline_can_pass_midnight(self):
        self.assertEqual(OfflineWindow.parse("20:00-23:50", grace=900).deadline_label, "00:05")

    def test_nonsense_is_rejected(self):
        for bad in ("23:30", "23:30-", "abc", "24:00-12:00", "23:60-12:00", "12:00-12:00", "23.30-12.00", "23:30 12:00"):
            with self.assertRaises(ValueError, msg=bad):
                OfflineWindow.parse(bad)


class WindowPhaseTests(unittest.TestCase):
    def setUp(self):
        self.night = OfflineWindow.parse("23:30-12:00")

    def test_phases_around_the_night(self):
        phase = self.night.phase
        self.assertEqual(phase(at("18:00")), OUTSIDE)
        self.assertEqual(phase(at("23:29", second=59)), OUTSIDE)
        self.assertEqual(phase(at("23:30")), INSIDE)             # the start belongs to the window
        self.assertEqual(phase(at("00:00", day=1)), INSIDE)
        self.assertEqual(phase(at("03:00", day=1)), INSIDE)
        self.assertEqual(phase(at("11:59", day=1, second=59)), INSIDE)
        self.assertEqual(phase(at("12:00", day=1)), GRACE)       # the end does not
        self.assertEqual(phase(at("12:14", day=1, second=59)), GRACE)
        self.assertEqual(phase(at("12:15", day=1)), OUTSIDE)
        self.assertEqual(phase(at("12:15", day=1, second=1)), OUTSIDE)

    def test_a_window_inside_one_day(self):
        w = OfflineWindow.parse("01:00-05:00")
        self.assertEqual([w.phase(at(t)) for t in ("00:59", "01:00", "04:59", "05:00", "05:14", "05:15")],
                         [OUTSIDE, INSIDE, INSIDE, GRACE, GRACE, OUTSIDE])

    def test_no_grace_means_no_grace(self):
        w = OfflineWindow.parse("23:30-12:00", grace=0)
        self.assertEqual(w.phase(at("12:00", day=1)), OUTSIDE)

    def test_a_grace_period_longer_than_the_gap_before_the_next_window_never_hides_the_window(self):
        w = OfflineWindow.parse("23:30-12:00", grace=86400)
        self.assertEqual(w.phase(at("20:00")), GRACE)
        self.assertEqual(w.phase(at("23:30")), INSIDE)

    def test_the_time_until_the_window_ends(self):
        w = self.night
        self.assertEqual(w.seconds_until_end(at("11:00", day=1)), 3600)
        self.assertEqual(w.seconds_until_end(at("23:45")), 12 * 3600 + 15 * 60)
        self.assertEqual(w.seconds_until_end(at("12:00", day=1)), 24 * 3600)          # that end has just passed
        self.assertEqual(w.seconds_until_end(at("11:59", day=1, second=30)), 30)


class SettingsTests(unittest.TestCase):
    def test_disabled_by_default(self):
        s = settings_from("")
        self.assertIsNone(s.offline_window)
        self.assertEqual(s.warnings, [])

    def test_the_window_and_its_default_grace(self):
        s = settings_from("EXPECTED_OFFLINE=23:30-12:00\n")
        self.assertEqual((s.offline_window.label, s.offline_window.grace), ("23:30-12:00", 900))
        self.assertEqual(s.warnings, [])

    def test_the_grace_period_is_configurable(self):
        self.assertEqual(settings_from("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_GRACE=600\n").offline_window.grace, 600)
        self.assertEqual(settings_from("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_GRACE=0\n").offline_window.grace, 0)

    def test_quotes_and_comments_are_understood(self):
        s = settings_from('EXPECTED_OFFLINE="23:30-12:00"  # the NAS sleeps\n')
        self.assertEqual(s.offline_window.label, "23:30-12:00")

    def test_an_empty_value_means_off(self):
        self.assertIsNone(settings_from("EXPECTED_OFFLINE=\n").offline_window)

    def test_a_bad_window_is_a_configuration_error_that_names_the_setting(self):
        for bad in ("23:30", "25:00-12:00", "12:00-12:00", "soon"):
            with self.assertRaises(SettingsError, msg=bad) as ctx:
                settings_from("EXPECTED_OFFLINE=%s\n" % bad)
            self.assertIn("EXPECTED_OFFLINE", str(ctx.exception))

    def test_a_bad_grace_period_is_a_configuration_error(self):
        for bad in ("-1", "86401", "soon"):
            with self.assertRaises(SettingsError, msg=bad):
                settings_from("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_GRACE=%s\n" % bad)

    def test_a_grace_period_without_a_window_is_pointed_out(self):
        s = settings_from("EXPECTED_OFFLINE_GRACE=600\n")
        self.assertIsNone(s.offline_window)
        self.assertEqual(s.warnings, ["EXPECTED_OFFLINE_GRACE has no effect without EXPECTED_OFFLINE"])

    def test_a_typo_is_reported_with_a_suggestion(self):
        s = settings_from("EXPECTED_OFLINE=23:30-12:00\n")
        self.assertIsNone(s.offline_window)
        self.assertIn("did you mean EXPECTED_OFFLINE?", s.warnings[0])

    def test_the_process_environment_wins_over_the_file(self):
        with mock.patch.dict(os.environ, {"EXPECTED_OFFLINE": "01:00-05:00"}):
            self.assertEqual(settings_from("EXPECTED_OFFLINE=23:30-12:00\n").offline_window.label, "01:00-05:00")


class SilenceInsideTheWindowTests(unittest.TestCase):
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
        self.assertIn("23:30-12:00", notices[0])
        self.assertIn("12:15", notices[0])                          # when silence turns into an outage
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
        self.assertTrue(h.svalue.startswith("NAS switched off on schedule (23:30-12:00), probing every 5 min"), h.svalue)

    def test_polling_slows_down_to_a_probe_every_five_minutes(self):
        engine, client, wall, _ = nas(now=at("23:45"))
        client.down = True
        result = engine.cycle()
        self.assertEqual(result.wait, 300.0)
        self.assertLessEqual(result.snmp_calls, 1)                  # one cheap probe, no discovery, no collectors

    def test_but_it_wakes_up_when_the_window_ends(self):
        engine, client, wall, _ = nas(now=at("11:58", day=1))
        client.down = True
        self.assertEqual(engine.cycle().wait, 120.0)
        wall.now = at("11:59", day=1, second=59)
        self.assertEqual(engine.cycle().wait, 1.0)

    def test_the_failure_time_decides_not_the_start_of_the_cycle(self):
        """The NAS shuts down in the middle of a cycle that began before the window: the failure is in the window."""
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

    def test_a_dead_nas_before_the_window_starts_is_still_an_error(self):
        engine, client, wall, _ = nas(now=at("23:29", second=59))
        client.down = True
        result = engine.cycle()
        self.assertEqual(len(messages(result, "error")), 1)
        self.assertFalse(result.expected_off)


class SilenceAfterTheWindowTests(unittest.TestCase):
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

    def test_without_a_grace_period_the_end_of_the_window_is_the_deadline(self):
        engine, client, wall, _ = nas("EXPECTED_OFFLINE=23:30-12:00\nEXPECTED_OFFLINE_GRACE=0\n", now=at("12:00", day=1))
        client.down = True
        self.assertEqual(len(messages(engine.cycle(), "error")), 1)

    def test_a_silent_nas_outside_the_window_is_an_outage_as_before(self):
        engine, client, wall, _ = nas(now=at("18:00"))
        client.down = True
        result = engine.cycle()
        self.assertEqual(len(messages(result, "error")), 1)
        self.assertIn("No SNMP answer", messages(result, "error")[0])
        self.assertFalse(result.expected_off)
        self.assertIsNone(result.wait)
        self.assertEqual(health(result).nvalue, CRIT)

    def test_without_a_window_nothing_changes_at_any_hour(self):
        engine, client, wall, _ = nas(env_text="", now=at("23:45"))
        client.down = True
        result = engine.cycle()
        self.assertEqual(len(messages(result, "error")), 1)
        self.assertFalse(result.expected_off)
        self.assertIsNone(result.wait)


class TheNasComesBackTests(unittest.TestCase):
    def test_a_nas_that_is_up_inside_the_window_is_polled_normally(self):
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
                  health=[type(self).reading(0, "NAS switched off on schedule (23:30-12:00), probing every 5 min")])
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

    def test_the_window_is_confirmed_in_the_log_at_start_up(self):
        self.start(env={"EXPECTED_OFFLINE": "23:30-12:00"})
        lines = [t for k, t in dz.logs if k == "log" and "switched off on schedule" in t]
        self.assertEqual(len(lines), 1)
        self.assertIn("23:30-12:00", lines[0])
        self.assertIn("12:15", lines[0])

    def test_no_window_no_line(self):
        self.start()
        self.assertEqual([t for k, t in dz.logs if k == "log" and "switched off on schedule" in t], [])

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
            self.assertIsNone(settings.offline_window)
            c = FakeClient(mibdata.ds920(), settings)
            c.down = True
            return c
        with mock.patch.dict(os.environ, {"EXPECTED_OFFLINE": "00:00-23:59"}):
            rc = main(["fake"], out=out, err=err, client_factory=factory)
        self.assertEqual(rc, 2)
        self.assertIn("No SNMP answer", err.getvalue())


if __name__ == "__main__":
    unittest.main()
