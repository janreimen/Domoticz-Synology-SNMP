import unittest

from helpers import by_key, make, mibdata, messages
from synoSNMP.snmpcli import SnmpUnavailable


class ConnectionTests(unittest.TestCase):
    def test_unreachable_is_reported_once_then_recovers(self):
        engine, client, clock = make(mibdata.ds920())
        client.down = True
        first = engine.cycle()
        self.assertFalse(first.reachable)
        self.assertEqual(first.readings, [])
        self.assertEqual(len(messages(first, "error")), 1)
        self.assertIn("No SNMP answer", messages(first, "error")[0])

        second = engine.cycle()                                   # still down: no log spam
        self.assertEqual(messages(second, "error"), [])

        client.down = False
        third = engine.cycle()
        self.assertTrue(third.reachable)
        self.assertGreater(len(third.readings), 50)
        self.assertTrue(any("restored" in t for t in messages(third, "info")))
        self.assertTrue(any("Connected to Synology DS920+" in t for t in messages(third, "info")))

    def test_hardware_is_rediscovered_after_an_outage(self):
        engine, client, clock = make(mibdata.ds920())
        engine.cycle()
        client.down = True
        engine.cycle()
        client.down = False
        client.mib.row(mibdata.IFT, 9, {2: ("string", "eth2"), 3: ("integer", 6), 8: ("integer", 1)})
        client.mib.row(mibdata.IFX, 9, {1: ("string", "eth2"), 6: ("counter64", 1), 10: ("counter64", 1), 15: ("gauge", 10000)})
        self.assertIn("net.eth2.link", by_key(engine.cycle()))      # new 10GbE card appears

    def test_not_a_synology(self):
        mib = mibdata.ds920().remove_prefix(mibdata.SYNO)
        engine, client, clock = make(mib)
        result = engine.cycle()
        self.assertEqual(result.readings, [])
        self.assertIn("Synology MIB", messages(result, "error")[0])
        self.assertEqual(messages(engine.cycle(), "error"), [])    # reported once

    def test_missing_tools_are_fatal(self):
        engine, client, clock = make(mibdata.ds920())
        def boom(oids):
            raise SnmpUnavailable("snmpget not found")
        client.get_many = boom
        result = engine.cycle()
        self.assertTrue(result.fatal)
        self.assertIn("snmpget not found", messages(result, "error")[0])


class IsolationTests(unittest.TestCase):
    def test_a_failing_collector_does_not_stop_the_others(self):
        engine, client, clock = make(mibdata.ds920())
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]            # drive table broken
        result = engine.cycle()
        r = by_key(result)
        self.assertIn("res.cpu", r)
        self.assertIn("net.eth0.link", r)
        self.assertFalse([k for k in r if k.startswith("disk.")])
        self.assertNotIn("disk", result.ran_groups)                 # so its devices are NOT flagged as removed...
        self.assertIn("res", result.ran_groups)
        self.assertEqual(len(messages(result, "error")), 1)

        again = engine.cycle()
        self.assertEqual(messages(again, "error"), [])              # same failure: no repeat

        client.fail_prefixes = []
        fixed = engine.cycle()
        self.assertIn("disk.drive1.temp", by_key(fixed))
        self.assertTrue(any("disks: working again" in t for t in messages(fixed, "info")))

    def test_a_crashing_collector_is_contained(self):
        engine, client, clock = make(mibdata.ds920())
        engine.collectors[1].collect = lambda c: 1 / 0
        result = engine.cycle()
        self.assertIn("sys.status", by_key(result))
        self.assertTrue(any("ZeroDivisionError" in t for t in messages(result, "error")))


class DynamicHardwareTests(unittest.TestCase):
    def test_removed_drive_disappears_from_present_keys(self):
        engine, client, clock = make(mibdata.ds920())
        before = engine.cycle()
        self.assertIn("disk.drive4.temp", before.present_keys)
        for col in range(2, 14):
            client.mib.data.pop(mibdata.SYNO + ".2.1.1.%d.3" % col, None)
        clock.advance(engine.settings.slow_interval)
        after = engine.cycle()
        self.assertNotIn("disk.drive4.temp", after.present_keys)
        self.assertIn("disk", after.ran_groups)                     # -> device layer flags it timed out
        self.assertIn("6 drives", by_key(before)["disk.summary"].svalue)
        self.assertIn("5 drives", by_key(after)["disk.summary"].svalue)

    def test_new_drive_appears_without_restart(self):
        engine, client, clock = make(mibdata.ds218())
        engine.cycle()
        mibdata._disk(client.mib, 2, "Drive 3", "ST4000VN008", "HDD", 1, 31, "hotspare", 0, -1, 1)
        clock.advance(engine.settings.slow_interval)
        self.assertEqual(by_key(engine.cycle())["disk.drive3.temp"].svalue, "31.0")

    def test_mount_index_shift_is_detected(self):
        """hrStorage indexes are dynamic: when /volume1 moves, we must not report another mount's numbers."""
        engine, client, clock = make(mibdata.ds920())
        engine.cycle()
        clock.advance(engine.settings.slow_interval)
        for col in range(1, 7):                                     # index 41 now describes /tmp, /volume1 moved to 45
            row41 = client.mib.data.pop(mibdata.HRS + ".%d.41" % col)
            client.mib.data[mibdata.HRS + ".%d.45" % col] = row41
        mibdata.Mib.row(client.mib, mibdata.HRS, 41, {1: ("integer", 41), 2: ("objectid", mibdata.FIXED), 3: ("string", "/tmp"),
                                                     4: ("integer", 4096), 5: ("integer", 1000), 6: ("integer", 999)})
        r = by_key(engine.cycle())
        self.assertEqual(r["vol.volume1.used"].svalue, "20.0")
        self.assertNotIn("/tmp Used", [x.name for x in r.values()])

    def test_mounts_and_ports_are_cached_between_polls(self):
        engine, client, clock = make(mibdata.ds920())
        first = engine.cycle()
        second = engine.cycle()
        self.assertLess(second.snmp_calls, first.snmp_calls)        # discovery walks only happen once


class SlowScheduleTests(unittest.TestCase):
    def test_slow_collectors_are_skipped_then_run_when_due(self):
        engine, client, clock = make(mibdata.ds920(), slow_interval=120)
        first = engine.cycle()
        self.assertIn("disk", first.ran_groups)
        self.assertIn("vol", first.ran_groups)
        first_calls = first.snmp_calls
        clock.advance(60)
        second = engine.cycle()
        self.assertNotIn("disk", second.ran_groups)
        self.assertNotIn("vol", second.ran_groups)
        self.assertIn("disk.drive1.temp", by_key(second))  # cached values remain available
        self.assertLess(second.snmp_calls, first_calls)
        self.assertNotIn("disk.drive1.temp", second.present_keys)  # cache is not fresh presence evidence
        clock.advance(60)
        third = engine.cycle()
        self.assertIn("disk", third.ran_groups)
        self.assertIn("vol", third.ran_groups)
        self.assertIn("disk.drive1.temp", third.present_keys)

    def test_failed_slow_collector_retains_cached_values_without_marking_fresh(self):
        engine, client, clock = make(mibdata.ds920(), slow_interval=120)
        first = engine.cycle()
        old = by_key(first)["disk.drive1.temp"].svalue
        clock.advance(120)
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]
        second = engine.cycle()
        self.assertNotIn("disk", second.ran_groups)
        self.assertNotIn("disk.drive1.temp", second.present_keys)
        self.assertEqual(by_key(second)["disk.drive1.temp"].svalue, old)

    def test_unreachable_cycle_does_not_return_cached_readings(self):
        engine, client, clock = make(mibdata.ds920(), slow_interval=120)
        engine.cycle()
        clock.advance(60)
        client.down = True
        result = engine.cycle()
        self.assertFalse(result.reachable)
        self.assertEqual(result.readings, [])
        self.assertEqual(result.present_keys, set())
        self.assertEqual(result.ran_groups, set())

    def test_fast_metrics_continue_every_cycle(self):
        engine, client, clock = make(mibdata.ds920(), slow_interval=300)
        first = engine.cycle()
        clock.advance(60)
        second = engine.cycle()
        self.assertIn("res", second.ran_groups)
        self.assertIn("sys", second.ran_groups)
        self.assertIn("disk", first.ran_groups)
        self.assertNotIn("disk", second.ran_groups)


if __name__ == "__main__":
    unittest.main()


class TraceLoggingTests(unittest.TestCase):
    def test_trace_reports_cycle_steps_and_each_collector(self):
        from synoSNMP.config import Settings
        from synoSNMP.engine import Engine
        from fakeclient import FakeClient
        events = []
        client = FakeClient(mibdata.ds218())
        engine = Engine(Settings(host="fake", level=3), client, trace=events.append)
        result = engine.cycle()
        joined = "\n".join(events)
        self.assertTrue(result.readings)
        self.assertIn("poll cycle started", joined)
        self.assertIn("Connected to Synology DS218", joined)
        self.assertIn("hardware discovery", joined)
        self.assertIn("collector start:", joined)
        self.assertIn("collector complete:", joined)
        self.assertIn("sensor collected:", joined)
        self.assertIn("sValue=", joined)
        self.assertIn("poll cycle finished", joined)
