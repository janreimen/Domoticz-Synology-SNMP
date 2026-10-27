import unittest

import domoticz_stub as dz
import mibdata
from helpers import make
from synoSNMP.devices import DeviceManager
from synoSNMP.engine import CycleResult
from synoSNMP.model import Reading


class T:
    t = 0.0

    def __call__(self):
        return self.t


def manager(refresh=300):
    dz.reset()
    clock = T()
    return DeviceManager(dz, lambda: dz.Devices, refresh, clock), clock


def result(*readings, ran=("x",)):
    return CycleResult(readings=list(readings), ran_groups=set(ran), present_keys={r.key for r in readings})


class CreationTests(unittest.TestCase):
    def test_devices_are_created_with_the_right_types(self):
        m, _ = manager()
        m.apply(result(
            Reading.temperature("x.temp", "Temp", 41.0),
            Reading.percentage("x.pct", "Pct", 12.5),
            Reading.text("x.txt", "Txt", "hello"),
            Reading.alert("x.alert", "Alert", 3, "careful"),
            Reading.custom("x.rate", "Rate", 3.14159, "Mbit/s", 2, used=False),
        ))
        by_id = {d.DeviceID: d for d in dz.Devices.values()}
        self.assertEqual([by_id[k].TypeName for k in ("x.temp", "x.pct", "x.txt", "x.alert", "x.rate")],
                         ["Temperature", "Percentage", "Text", "Alert", "Custom"])
        self.assertEqual(by_id["x.rate"].Options, {"Custom": "1;Mbit/s"})
        self.assertEqual((by_id["x.rate"].Used, by_id["x.temp"].Used), (0, 1))
        self.assertEqual((by_id["x.alert"].nValue, by_id["x.alert"].sValue), (3, "careful"))
        self.assertEqual(by_id["x.rate"].sValue, "3.14")
        self.assertEqual(sorted(dz.Devices), [1, 2, 3, 4, 5])

    def test_pending_readings_create_nothing(self):
        m, _ = manager()
        m.apply(result(Reading.waiting("x.rate", "Rate", "custom", "Mbit/s")))
        self.assertEqual(dz.Devices, {})

    def test_existing_devices_are_found_again_after_restart(self):
        m, _ = manager()
        m.apply(result(Reading.percentage("x.a", "A", 1.0), Reading.percentage("x.b", "B", 2.0)))
        fresh = DeviceManager(dz, lambda: dz.Devices, 300, T())          # "Domoticz restarted"
        fresh.sync()
        fresh.apply(result(Reading.percentage("x.b", "B", 5.0), Reading.percentage("x.c", "C", 3.0)))
        self.assertEqual(len(dz.Devices), 3)                             # B reused, only C is new
        self.assertEqual({d.DeviceID: d.Unit for d in dz.Devices.values()}, {"x.a": 1, "x.b": 2, "x.c": 3})
        self.assertEqual(dz.Devices[2].sValue, "5.0")

    def test_foreign_devices_keep_their_units(self):
        m, _ = manager()
        dz.Devices[1] = dz.StubDevice(Name="old", Unit=1, DeviceID="00000001")
        m.sync()
        m.apply(result(Reading.percentage("x.a", "A", 1.0)))
        self.assertEqual(dz.Devices[2].DeviceID, "x.a")
        self.assertEqual(dz.Devices[1].updates, 0)

    def test_unit_exhaustion_is_reported_once(self):
        m, _ = manager()
        for i in range(1, 256):
            dz.Devices[i] = dz.StubDevice(Name="n%d" % i, Unit=i, DeviceID="")
        m.apply(result(Reading.percentage("x.a", "A", 1.0)))
        m.apply(result(Reading.percentage("x.b", "B", 1.0)))
        self.assertEqual(len([l for l in dz.logs if l[0] == "error"]), 1)


class UpdateTests(unittest.TestCase):
    def test_writes_on_change_and_when_stale_only(self):
        m, clock = manager(refresh=300)
        m.apply(result(Reading.percentage("x.a", "A", 1.0)))
        dev = dz.Devices[1]
        base = dev.updates
        clock.t = 10
        m.apply(result(Reading.percentage("x.a", "A", 1.0)))             # unchanged, fresh
        self.assertEqual(dev.updates, base)
        m.apply(result(Reading.percentage("x.a", "A", 2.0)))             # changed
        self.assertEqual(dev.updates, base + 1)
        clock.t = 400
        m.apply(result(Reading.percentage("x.a", "A", 2.0)))             # stale: refresh "last seen"
        self.assertEqual(dev.updates, base + 2)

    def test_missing_hardware_times_out_and_recovers(self):
        m, _ = manager()
        m.apply(result(Reading.percentage("disk.a", "A", 1.0), Reading.percentage("net.b", "B", 1.0), ran=("disk", "net")))
        m.apply(result(Reading.percentage("net.b", "B", 1.0), ran=("disk", "net")))   # disk.a gone
        self.assertEqual((dz.Devices[1].TimedOut, dz.Devices[2].TimedOut), (1, 0))
        m.apply(result(Reading.percentage("disk.a", "A", 1.0), Reading.percentage("net.b", "B", 1.0), ran=("disk", "net")))
        self.assertEqual(dz.Devices[1].TimedOut, 0)                      # came back

    def test_devices_of_a_stale_collector_are_flagged_and_recover_with_its_next_reading(self):
        m, _ = manager()
        m.apply(result(Reading.percentage("disk.a", "A", 1.0), Reading.percentage("net.b", "B", 1.0), ran=("disk", "net")))
        stale = result(Reading.percentage("net.b", "B", 1.0), ran=("net",))
        stale.stale_groups = {"disk"}
        m.apply(stale)
        self.assertEqual((dz.Devices[1].TimedOut, dz.Devices[2].TimedOut), (1, 0))
        self.assertEqual(dz.Devices[1].sValue, "1.0")                    # the value itself is left alone
        m.apply(result(Reading.percentage("disk.a", "A", 1.0), Reading.percentage("net.b", "B", 1.0), ran=("disk", "net")))
        self.assertEqual(dz.Devices[1].TimedOut, 0)

    def test_failed_collector_does_not_time_out_its_devices(self):
        m, _ = manager()
        m.apply(result(Reading.percentage("disk.a", "A", 1.0), ran=("disk",)))
        m.apply(result(ran=("net",)))                                    # disk collector did not run this cycle
        self.assertEqual(dz.Devices[1].TimedOut, 0)

    def test_unreachable_nas_flags_everything(self):
        m, _ = manager()
        m.apply(result(Reading.percentage("x.a", "A", 1.0), Reading.percentage("x.b", "B", 1.0)))
        m.mark_all_timed_out()
        self.assertEqual([d.TimedOut for d in dz.Devices.values()], [1, 1])


class DeletedDeviceTests(unittest.TestCase):
    """A device deleted in the Domoticz UI while the plugin runs must come back, not be updated into the void."""

    def test_a_deleted_device_is_created_again_and_the_user_is_told_once(self):
        m, clock = manager()
        m.apply(result(Reading.percentage("x.a", "A", 1.0), Reading.percentage("x.b", "B", 2.0)))
        del dz.Devices[1]                                                 # x.a deleted in the UI
        dz.logs.clear()
        clock.t = 60
        m.apply(result(Reading.percentage("x.a", "A", 5.0), Reading.percentage("x.b", "B", 2.0)))
        by_id = {d.DeviceID: d for d in dz.Devices.values()}
        self.assertEqual(sorted(by_id), ["x.a", "x.b"])
        self.assertEqual(by_id["x.a"].sValue, "5.0")                      # and it received the current value
        messages = [t for kind, t in dz.logs if kind == "log"]
        self.assertEqual(len(messages), 1)
        self.assertIn("'A' [x.a] was removed in Domoticz; creating it again", messages[0])
        self.assertIn("'Used' flag", messages[0])                         # the supported way to hide a device

        dz.logs.clear()
        clock.t = 120
        m.apply(result(Reading.percentage("x.a", "A", 6.0), Reading.percentage("x.b", "B", 2.0)))
        self.assertEqual([t for kind, t in dz.logs if kind == "log"], [])  # nothing further to say

    def test_a_unit_that_now_belongs_to_another_device_is_not_overwritten(self):
        m, _ = manager()
        m.apply(result(Reading.percentage("x.a", "A", 1.0)))
        dz.Devices[1] = dz.StubDevice(Name="someone else's", Unit=1, DeviceID="00000009")
        m.apply(result(Reading.percentage("x.a", "A", 3.0)))
        self.assertEqual(dz.Devices[1].updates, 0)
        self.assertEqual({d.DeviceID for d in dz.Devices.values()}, {"00000009", "x.a"})

    def test_a_device_without_a_matching_unit_after_a_restart_is_created_by_the_first_apply(self):
        m, _ = manager()
        m.apply(result(Reading.percentage("x.a", "A", 1.0)))
        dz.Devices.clear()                                                # everything deleted while Domoticz was down
        fresh = DeviceManager(dz, lambda: dz.Devices, 300, T())
        fresh.sync()
        fresh.apply(result(Reading.percentage("x.a", "A", 2.0)))
        self.assertEqual([d.DeviceID for d in dz.Devices.values()], ["x.a"])


class AxisLabelSyncTests(unittest.TestCase):
    """The unit shown on a Custom sensor's axis is owned by the plugin; names, visibility and history are not."""

    def existing(self, label):
        m, clock = manager()
        dz.Devices[1] = dz.StubDevice(Name="my own name", Unit=1, DeviceID="x.rate", TypeName="Custom",
                                      Options={"Custom": "1;%s" % label}, Used=1)
        dz.Devices[1].sValue = "1.00"
        m.sync()
        return m, clock

    def test_an_outdated_label_is_updated_once_per_start(self):
        m, clock = self.existing("MB/s")
        m.apply(result(Reading.custom("x.rate", "Rate", 2.0, "Mbit/s", 2)))
        dev = dz.Devices[1]
        self.assertEqual(dev.Options, {"Custom": "1;Mbit/s"})
        self.assertEqual(dev.Name, "my own name")                         # the user's name stays
        self.assertEqual(dev.Used, 1)
        self.assertEqual(len([t for k, t in dz.logs if k == "log" and "axis label updated" in t]), 1)
        updates = dev.updates
        clock.t = 10
        m.apply(result(Reading.custom("x.rate", "Rate", 3.0, "Mbit/s", 2)))
        self.assertEqual(dev.Options, {"Custom": "1;Mbit/s"})
        self.assertEqual(len([t for k, t in dz.logs if k == "log" and "axis label updated" in t]), 1)   # not again
        self.assertEqual(dev.updates, updates + 1)                        # only the new value was written

    def test_a_correct_label_is_left_alone(self):
        m, _ = self.existing("Mbit/s")
        m.apply(result(Reading.custom("x.rate", "Rate", 1.0, "Mbit/s", 2)))
        self.assertEqual([t for k, t in dz.logs if k == "log"], [])

    def test_other_options_of_the_device_are_kept(self):
        m, _ = self.existing("MB/s")
        dz.Devices[1].Options["Something"] = "else"
        m.apply(result(Reading.custom("x.rate", "Rate", 1.0, "Mbit/s", 2)))
        self.assertEqual(dz.Devices[1].Options, {"Custom": "1;Mbit/s", "Something": "else"})

    def test_devices_that_are_not_custom_sensors_are_not_touched(self):
        m, _ = manager()
        m.apply(result(Reading.percentage("x.a", "A", 1.0)))
        dz.Devices[1].Options = {"Anything": "1"}
        fresh = DeviceManager(dz, lambda: dz.Devices, 300, T())
        fresh.sync()
        fresh.apply(result(Reading.percentage("x.a", "A", 2.0)))
        self.assertEqual(dz.Devices[1].Options, {"Anything": "1"})


class PluginHealthDeviceTests(unittest.TestCase):
    def health(self, level=1, text="13 collectors OK"):
        return [Reading.alert("plugin.health", "Plugin Health", level, text),
                Reading.custom("plugin.pollsecs", "Poll Duration", 1.5, "s", 2, used=False)]

    def test_health_devices_are_written_even_when_there_are_no_readings(self):
        m, _ = manager()
        down = CycleResult(reachable=False, health=self.health(4, "NAS not answering"))
        m.apply(down)
        by_id = {d.DeviceID: d for d in dz.Devices.values()}
        self.assertEqual((by_id["plugin.health"].nValue, by_id["plugin.health"].sValue), (4, "NAS not answering"))
        self.assertEqual(by_id["plugin.pollsecs"].Used, 0)
        self.assertEqual(by_id["plugin.pollsecs"].sValue, "1.50")

    def test_an_unreachable_nas_flags_everything_except_the_health_devices(self):
        m, _ = manager()
        m.apply(CycleResult(readings=[Reading.percentage("x.a", "A", 1.0)], ran_groups={"x"},
                            present_keys={"x.a"}, health=self.health()))
        m.mark_all_timed_out()
        by_id = {d.DeviceID: d for d in dz.Devices.values()}
        self.assertEqual(by_id["x.a"].TimedOut, 1)
        self.assertEqual(by_id["plugin.health"].TimedOut, 0)
        self.assertEqual(by_id["plugin.pollsecs"].TimedOut, 0)

    def test_health_changes_are_written_and_unchanged_values_are_not(self):
        m, clock = manager()
        m.apply(CycleResult(health=self.health(1, "13 collectors OK")))
        dev = [d for d in dz.Devices.values() if d.DeviceID == "plugin.health"][0]
        base = dev.updates
        clock.t = 10
        m.apply(CycleResult(health=self.health(1, "13 collectors OK")))
        self.assertEqual(dev.updates, base)
        m.apply(CycleResult(health=self.health(2, "1 of 13 collectors failing: smart")))
        self.assertEqual((dev.nValue, dev.updates), (2, base + 1))


class FromEngineTests(unittest.TestCase):
    def test_full_ds920_fits_into_domoticz_limits(self):
        m, _ = manager()
        engine, _, _ = make(mibdata.ds920())
        m.apply(engine.cycle())
        self.assertLessEqual(len(dz.Devices), 255)
        ids = [d.DeviceID for d in dz.Devices.values()]
        self.assertEqual(len(ids), len(set(ids)))                        # unique DeviceIDs
        self.assertTrue(all(len(i) <= 25 for i in ids))
        self.assertTrue(all(d.Name for d in dz.Devices.values()))
        print("\n    DS920+ full detail level -> %d Domoticz devices (%d visible by default)" % (
            len(dz.Devices), sum(d.Used for d in dz.Devices.values())))


if __name__ == "__main__":
    unittest.main()
