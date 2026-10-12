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
