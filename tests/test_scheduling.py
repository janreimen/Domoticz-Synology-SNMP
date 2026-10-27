"""The scheduler and the failure policy, driven the way production drives them.

The production path is ``Settings.from_parameters()`` (per-collector intervals from the .env defaults)
-> ``Engine`` -> a ``Poller`` that starts a cycle every ``tick`` seconds *measured from the start of the
previous one*.  The fake clock below advances with every SNMP call, so discovery and slow collectors
make a cycle take real (fake) time, exactly what hid the scheduling bugs of 0.2.2.
"""
import collections
import os
import tempfile
import unittest

import mibdata
from fakeclient import FakeClient
from helpers import Clock, by_key, messages
from synoSNMP import oids
from synoSNMP.config import Settings
from synoSNMP.engine import Engine
from synoSNMP.model import CRIT, OK, WARN
from synoSNMP.snmpcli import SnmpError

NO_ENV = os.path.join(tempfile.gettempdir(), "domoticz-syno-snmp-no-such-dir", ".env")
PARAMS = {"Address": "fake", "Mode5": "3", "Mode6": "Normal"}


class TimedClient(FakeClient):
    """A FakeClient whose every request costs ``per_call`` seconds of fake time."""

    def __init__(self, mib, clock, per_call=0.05):
        super().__init__(mib)
        self.clock, self.per_call = clock, per_call

    def get_many(self, oids):
        self.clock.advance(self.per_call)
        return super().get_many(oids)

    def walk(self, oid):
        self.clock.advance(self.per_call)
        return super().walk(oid)


def production(level="3", env_text=None, per_call=0.05, mib=None):
    """Engine + client + clock built through the same path as the plugin."""
    clock = Clock()
    client = TimedClient(mib or mibdata.ds920(), clock, per_call)
    path = NO_ENV
    tmp = None
    if env_text is not None:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8")
        tmp.write(env_text)
        tmp.close()
        path = tmp.name
    try:
        settings = Settings.from_parameters(dict(PARAMS, Mode5=level), env_path=path)
    finally:
        if tmp is not None:
            os.unlink(tmp.name)
    return Engine(settings, client, clock=clock), client, clock


Cycle = collections.namedtuple("Cycle", "t result discovered")


def drive(engine, clock, cycles, jitter=0.0):
    """What Poller.run() does: a new cycle every ``tick`` seconds counted from the previous cycle's start."""
    out = []
    for _ in range(cycles):
        started = clock.t
        result = engine.cycle()
        out.append(Cycle(started, result, engine._last_discovery == started))
        clock.advance(max(1.0, engine.tick - (clock.t - started)) + jitter)
    return out


def ran(engine, result, name):
    collector = [c for c in engine.collectors if c.name == name][0]
    return set(collector.groups) <= result.ran_groups


class ProductionScheduleTests(unittest.TestCase):
    def fast(self, engine):
        return [c.name for c in engine.collectors if c.interval == engine.tick]

    def test_collectors_on_the_tick_run_in_every_single_cycle(self):
        engine, _, clock = production()
        self.assertEqual(engine.tick, 60)
        fast = self.fast(engine)
        self.assertGreaterEqual(len(fast), 5)
        for cycle in drive(engine, clock, 8):
            for name in fast:
                self.assertTrue(ran(engine, cycle.result, name), "%s skipped at t=%.0f" % (name, cycle.t))

    def test_network_rates_exist_from_the_second_cycle_on(self):
        engine, _, clock = production()
        cycles = drive(engine, clock, 3)
        self.assertTrue(by_key(cycles[0].result)["net.eth0.rx"].pending)
        self.assertFalse(by_key(cycles[1].result)["net.eth0.rx"].pending)
        self.assertFalse(by_key(cycles[2].result)["net.eth0.rx"].pending)

    def test_a_cycle_that_follows_the_hourly_discovery_is_not_short_changed(self):
        engine, _, clock = production()
        cycles = drive(engine, clock, 64)                               # a little over an hour
        discoveries = [c for c in cycles if c.discovered]
        self.assertEqual(len(discoveries), 2)                           # the first cycle, then ~3600 s later
        self.assertGreaterEqual(discoveries[1].t - discoveries[0].t, engine.settings.discovery_interval - 1)
        for cycle in cycles:                                            # including the two that did the discovery
            for name in self.fast(engine):
                self.assertTrue(ran(engine, cycle.result, name), "%s skipped at t=%.0f" % (name, cycle.t))

    def test_a_poller_that_wakes_slightly_early_is_tolerated(self):
        engine, _, clock = production()
        for cycle in drive(engine, clock, 12, jitter=-0.2):
            for name in self.fast(engine):
                self.assertTrue(ran(engine, cycle.result, name), "%s skipped at t=%.1f" % (name, cycle.t))

    def test_slow_collectors_keep_their_own_interval(self):
        engine, _, clock = production()
        cycles = drive(engine, clock, 21)                               # 20 minutes
        runs = lambda name: [i for i, c in enumerate(cycles) if ran(engine, c.result, name)]
        self.assertEqual(runs("disks"), [0, 5, 10, 15, 20])             # 300 s
        self.assertEqual(runs("mounts"), [0, 10, 20])                   # 600 s
        self.assertEqual(runs("smart"), [0])                            # 3600 s
        self.assertEqual(runs("system"), list(range(21)))               # 60 s

    def test_the_tick_only_counts_collectors_that_exist_at_this_detail_level(self):
        env = "GPU_INTERVAL=10\n"
        basic, _, _ = production(level="1", env_text=env)
        self.assertEqual(basic.tick, 60)                                # no GPU collector at Basic
        full, _, _ = production(level="3", env_text=env)
        self.assertEqual(full.tick, 10)

    def test_a_forced_cycle_ignores_schedules(self):
        engine, _, clock = production()
        first = engine.cycle()
        self.assertTrue(first.ran_groups)
        again = engine.cycle()                                          # same instant: nothing is due
        self.assertEqual(again.ran_groups, set())
        forced = engine.cycle(force=True)
        self.assertEqual(forced.ran_groups, first.ran_groups)


class BackoffTests(unittest.TestCase):
    def test_delays_double_from_the_interval_up_to_a_cap(self):
        engine, _, _ = production()
        by_name = {c.name: c for c in engine.collectors}
        delays = lambda name: [engine._backoff(by_name[name], n) for n in range(1, 7)]
        self.assertEqual(delays("resources"), [60, 120, 240, 300, 300, 300])
        self.assertEqual(delays("disks"), [300] * 6)
        self.assertEqual(delays("smart"), [300, 600, 1200, 2400, 3600, 3600])

    def test_a_failing_collector_is_retried_with_back_off_and_reported_once(self):
        engine, client, clock = production()
        engine.cycle()                                                   # healthy start
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]                 # drive table breaks
        failures = []
        succeeded = []
        for cycle in drive(engine, clock, 12):                          # 12 minutes
            failures += messages(cycle.result, "error")
            if ran(engine, cycle.result, "disks"):
                succeeded.append(cycle.t)
        self.assertEqual(len(failures), 1)                               # the same problem is logged once
        self.assertEqual(succeeded, [])
        # attempted at ~300 s and ~600 s after the last good run, not once per minute
        self.assertEqual(engine._fails["disks"], 2)

    def test_recovery_is_announced_at_the_normal_log_level(self):
        engine, client, clock = production()
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]
        drive(engine, clock, 2)
        client.fail_prefixes = []
        notices = []
        for cycle in drive(engine, clock, 6):
            notices += messages(cycle.result, "notice")
        self.assertEqual(notices, ["disks: working again"])
        self.assertNotIn("disks", engine._fails)

    def test_a_failing_collector_does_not_replay_old_values_as_fresh(self):
        engine, client, clock = production()
        engine.cycle()
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]
        clock.advance(300)
        result = engine.cycle()                                          # disks is due and fails
        self.assertNotIn("disk", result.ran_groups)                      # so its devices are not flagged as removed ...
        self.assertNotIn("disk.drive1.temp", by_key(result))             # ... and the old value is not sent again
        self.assertNotIn("disk.drive1.temp", result.present_keys)
        self.assertEqual(result.stale_groups, set())                     # one missed run is tolerated
        self.assertIn("vol.root.used", by_key(result))                   # mounts is merely not due: its values still replay

    def test_devices_of_a_collector_that_keeps_failing_are_flagged_when_a_retry_fails_after_two_intervals(self):
        engine, client, clock = production()
        t0 = clock.t
        drive(engine, clock, 1)                                          # one healthy cycle
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]
        stale_at = [round(c.t - t0) for c in drive(engine, clock, 21) if "disk" in c.result.stale_groups]
        # DISKS_INTERVAL=300 and a 300 s back-off: attempts at 300, 600, 900, 1200.  The data is 600 s old at the second
        # attempt, which is not yet "more than two intervals"; the third attempt flags it.  No flag between attempts.
        self.assertEqual(stale_at, [900, 1200])
        self.assertEqual(engine._fails["disks"], 4)

    def test_a_collector_that_fails_twice_and_then_recovers_is_never_flagged(self):
        """A 60 s collector fails at 60 s and 120 s, then waits out a 120 s back-off and works again: no red flicker."""
        engine, client, clock = production()
        resources = [c for c in engine.collectors if c.name == "resources"][0]
        real, broken = resources.collect, [False]

        def collect(client_):
            if broken[0]:
                raise SnmpError("simulated failure")
            return real(client_)
        resources.collect = collect
        flagged = []
        for step in range(8):
            broken[0] = step in (1, 2)                                   # the cycles at t=60 and t=120
            cycle = drive(engine, clock, 1)[0]
            flagged.append("res" in cycle.result.stale_groups)
        self.assertFalse(any(flagged), flagged)
        self.assertNotIn("resources", engine._fails)                     # and it is healthy again at the end

    def test_a_failing_collector_that_never_worked_is_flagged_too(self):
        """After a Domoticz restart the devices exist with old values, but the collector has not delivered yet."""
        engine, client, clock = production()
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]
        t0 = clock.t
        stale_at = [round(c.t - t0) for c in drive(engine, clock, 17) if "disk" in c.result.stale_groups]
        self.assertEqual(stale_at[0], 900)

    def test_a_forced_cycle_retries_a_collector_that_is_backing_off(self):
        engine, client, clock = production()
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]
        engine.cycle()
        client.fail_prefixes = []
        self.assertNotIn("disk", engine.cycle().ran_groups)              # still backing off
        self.assertIn("disk", engine.cycle(force=True).ran_groups)


class StaleDeviceTests(unittest.TestCase):
    """Engine and device layer together: a table stops answering, the NAS does not."""

    def test_domoticz_sees_stale_values_as_stale_and_recovers_with_the_collector(self):
        import domoticz_stub as dz
        from synoSNMP.devices import DeviceManager
        engine, client, clock = production()
        dz.reset()
        manager = DeviceManager(dz, lambda: dz.Devices, 300, clock)
        t0 = clock.t
        manager.apply(engine.cycle())
        dev = [d for d in dz.Devices.values() if d.DeviceID == "disk.drive1.temp"][0]
        good = dev.sValue
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]
        timeline = []
        for _ in range(40):
            clock.advance(60)
            updates = dev.updates
            manager.apply(engine.cycle())
            timeline.append((round(clock.t - t0), dev.TimedOut, dev.updates - updates))
        self.assertEqual(dev.sValue, good)                               # the last value is never overwritten
        self.assertLess([m for m, timed_out, _ in timeline if timed_out == 0][-1], 900)     # not red before the third failed retry
        self.assertEqual(timeline[-1][1], 1)                             # after 40 minutes it is red
        self.assertEqual(sum(1 for *_, n in timeline if n), 1)           # one write only: the time-out flag itself, no refreshes
        client.fail_prefixes = []
        clock.advance(300)                                               # back-off is over
        manager.apply(engine.cycle())
        self.assertEqual((dev.TimedOut, dev.sValue), (0, good))          # recovered with the next good reading


class TimeoutIsolationTests(unittest.TestCase):
    def test_a_collector_that_times_out_while_the_nas_answers_does_not_take_everything_down(self):
        engine, client, clock = production()
        client.timeout_prefixes = [oids.SMART_TABLE]
        result = engine.cycle()
        self.assertTrue(result.reachable)
        self.assertIn("res.cpu", by_key(result))
        self.assertIn("net.eth0.link", by_key(result))
        self.assertNotIn("smart", result.ran_groups)
        errors = messages(result, "error")
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("smart:"), errors)
        self.assertIn("answers other requests", errors[0])
        self.assertEqual(by_key_health(result)["plugin.health"].nvalue, WARN)
        self.assertIn("1 of 13 collectors failing: smart", by_key_health(result)["plugin.health"].svalue)

    def test_a_table_that_times_out_during_hardware_detection_is_not_an_outage_either(self):
        """Discovery (first cycle, then hourly) asks some collectors to walk a table before the first poll.  The same rule
        as for polling applies: if the NAS still answers a trivial request, only that collector is affected."""
        engine, client, clock = production()
        client.timeout_prefixes = [mibdata.IFT]                          # the interface table never answers
        result = engine.cycle()
        self.assertTrue(result.reachable)
        self.assertIn("res.cpu", by_key(result))
        self.assertIn("disk.drive1.temp", by_key(result))
        self.assertNotIn("net", result.ran_groups)
        errors = messages(result, "error")
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("network:"), errors)
        self.assertEqual(by_key_health(result)["plugin.health"].nvalue, WARN)
        client.timeout_prefixes = []                                     # the table comes back: hardware is detected again
        clock.advance(60)
        self.assertIn("net.eth0.link", by_key(engine.cycle(force=True)))

    def test_a_discovery_timeout_while_the_nas_is_really_silent_is_an_outage(self):
        """The probe after a timeout decides in hardware detection too: if the NAS does not answer it, the NAS is down."""
        engine, client, clock = production()
        original, before_silence = client.walk, []

        def walk(oid):
            if oid.lstrip(".").startswith(mibdata.IFT):                  # the first table discovery asks for ...
                before_silence.append(client.calls)
                client.down = True                                       # ... and the NAS drops off the network right there
            return original(oid)
        client.walk = walk
        result = engine.cycle()
        self.assertFalse(result.reachable)
        self.assertEqual(result.readings, [])
        self.assertIn("No SNMP answer", messages(result, "error")[0])
        self.assertEqual(by_key_health(result)["plugin.health"].nvalue, CRIT)
        # The outcome would be the same without the probe, but not the cost: each further request is a full SNMP
        # timeout on a real NAS.  After the NAS went silent the cycle may send the request that timed out and one probe.
        self.assertLessEqual(result.snmp_calls - before_silence[0], 2)

    def test_a_timeout_while_the_nas_is_really_silent_still_means_unreachable(self):
        engine, client, clock = production()
        engine.cycle()
        client.down = True
        clock.advance(60)
        result = engine.cycle()
        self.assertFalse(result.reachable)
        self.assertEqual(result.readings, [])
        self.assertEqual(len(messages(result, "error")), 1)
        self.assertIn("No SNMP answer", messages(result, "error")[0])

    def test_the_probe_decides_after_the_collector_timed_out(self):
        """The SMART walk times out and the NAS then does not answer the probe either: the NAS is down."""
        engine, client, clock = production()
        engine.cycle()
        clock.advance(3600)                                              # everything is due again
        original_walk = client.walk

        def walk(oid):
            if oid.lstrip(".").startswith(oids.SMART_TABLE):
                client.down = True                                       # the NAS drops off the network right here
            return original_walk(oid)
        client.walk = walk
        result = engine.cycle()
        self.assertFalse(result.reachable)
        self.assertEqual(result.readings, [])
        self.assertIn("No SNMP answer", messages(result, "error")[0])


def by_key_health(result):
    return {r.key: r for r in result.health}


class HealthDeviceTests(unittest.TestCase):
    def test_healthy_cycle_reports_ok_with_the_cost_of_the_cycle(self):
        engine, _, _ = production()
        health = by_key_health(engine.cycle())
        self.assertEqual(health["plugin.health"].nvalue, OK)
        self.assertTrue(health["plugin.health"].svalue.startswith("13 collectors OK | "), health["plugin.health"].svalue)
        self.assertIn(" SNMP calls | ", health["plugin.health"].svalue)
        self.assertEqual(health["plugin.pollsecs"].kind, "custom")
        self.assertFalse(health["plugin.pollsecs"].used)                 # hidden by default

    def test_an_unreachable_nas_is_critical(self):
        engine, client, _ = production()
        client.down = True
        health = by_key_health(engine.cycle())
        self.assertEqual(health["plugin.health"].nvalue, CRIT)
        self.assertTrue(health["plugin.health"].svalue.startswith("NAS not answering"))

    def test_a_device_that_is_not_a_synology_is_critical(self):
        engine, _, _ = production(mib=mibdata.ds920().remove_prefix(mibdata.SYNO))
        health = by_key_health(engine.cycle())
        self.assertEqual(health["plugin.health"].nvalue, CRIT)
        self.assertTrue(health["plugin.health"].svalue.startswith("Not a Synology"))

    def test_missing_tools_are_critical_and_say_why(self):
        from synoSNMP.snmpcli import SnmpUnavailable
        engine, client, _ = production()

        def boom(oids):
            raise SnmpUnavailable("snmpget not found")
        client.get_many = boom
        result = engine.cycle()
        self.assertTrue(result.fatal)
        health = by_key_health(result)["plugin.health"]
        self.assertEqual(health.nvalue, CRIT)
        self.assertIn("snmpget not found", health.svalue)

    def test_a_failing_collector_is_a_warning_that_names_it(self):
        engine, client, _ = production()
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]
        health = by_key_health(engine.cycle())["plugin.health"]
        self.assertEqual(health.nvalue, WARN)
        self.assertIn("1 of 13 collectors failing: disks", health.svalue)

    def test_health_returns_to_ok_after_recovery(self):
        engine, client, clock = production()
        client.fail_prefixes = [mibdata.SYNO + ".2.1.1"]
        engine.cycle()
        client.fail_prefixes = []
        clock.advance(300)
        self.assertEqual(by_key_health(engine.cycle())["plugin.health"].nvalue, OK)


if __name__ == "__main__":
    unittest.main()
