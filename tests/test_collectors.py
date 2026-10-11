import unittest

from helpers import by_key, make, mibdata
from synoSNMP import model


class Ds920Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine, cls.client, cls.clock = make(mibdata.ds920())
        cls.result = cls.engine.cycle()
        cls.r = by_key(cls.result)

    def test_system(self):
        r = self.r
        self.assertEqual(r["sys.status"].nvalue, model.OK)
        self.assertEqual(r["sys.temp"].svalue, "41.0")
        self.assertEqual(r["sys.info"].svalue, "DS920+  |  DSM 7.2.1-69057 Update 5")
        self.assertEqual(r["sys.update"].svalue, "Up to date")
        self.assertNotIn("sys.cpufan", r)            # this model has no CPU fan object -> no device

    def test_cpu_memory_swap_load_uptime(self):
        r = self.r
        self.assertEqual(r["res.cpu"].svalue, "8.0")                  # user 5 + system 3
        self.assertEqual(r["res.mem"].svalue, "42.5")                 # (8.0M - (0.5M+0.1M+4.0M)) / 8.0M
        self.assertEqual(r["res.memfree"].svalue, "4492")             # MB incl. buffers and cache
        self.assertEqual(r["res.swap"].svalue, "4.6")
        self.assertEqual(r["res.load1"].svalue, "0.52")
        self.assertEqual(r["res.uptime"].svalue, "1.00")              # 8 640 000 ticks = 1 day

    def test_every_drive_is_detected(self):
        r = self.r
        health = [k for k in r if k.startswith("disk.") and k.endswith(".health")]
        self.assertEqual(len(health), 6)                              # 4 HDD + 2 NVMe cache
        self.assertEqual(r["disk.drive1.temp"].svalue, "34.0")
        self.assertEqual(r["disk.cachedevice2.temp"].svalue, "43.0")

    def test_disk_health_and_summary(self):
        r = self.r
        self.assertEqual(r["disk.drive1.health"].nvalue, model.OK)
        self.assertEqual(r["disk.drive4.health"].nvalue, model.WARN)  # 3 bad sectors
        self.assertIn("bad sectors: 3", r["disk.drive4.health"].svalue)
        self.assertEqual(r["disk.summary"].nvalue, model.WARN)
        self.assertIn("6 drives", r["disk.summary"].svalue)
        self.assertIn("hottest 43", r["disk.summary"].svalue)

    def test_remaining_life_only_for_ssd(self):
        r = self.r
        self.assertEqual(r["disk.cachedevice1.life"].svalue, "98.0")
        self.assertNotIn("disk.drive1.life", r)                       # HDDs report -1

    def test_raid_pool_and_volume_status(self):
        r = self.r
        self.assertEqual(r["raid.storagepool1.status"].nvalue, model.OK)
        self.assertIn("free of 20.0 TB", r["raid.volume1.status"].svalue)

    def test_only_real_mounts_are_tracked(self):
        r = self.r
        used = {x.name for x in r.values() if x.key.startswith("vol.") and x.name.endswith(" Used")}
        self.assertEqual(used, {"/ Used", "/volume1 Used", "/volumeUSB1/usbshare Used"})
        self.assertEqual(r["vol.volume1.used"].svalue, "20.0")
        self.assertEqual(r["vol.volume1.free"].svalue, "16384.0")     # GB
        for key in r:
            self.assertLessEqual(len(key), 25, key)

    def test_only_hardware_network_ports(self):
        r = self.r
        ports = {k.split(".")[1] for k in r if k.startswith("net.")}
        self.assertEqual(ports, {"eth0", "eth1", "bond0"})            # no lo, docker0, veth
        self.assertEqual(r["net.eth0.link"].svalue, "Up | 1 Gbit/s")
        self.assertEqual(r["net.eth1.link"].nvalue, model.WARN)
        self.assertEqual(r["net.bond0.link"].svalue, "Up | 2 Gbit/s")

    def test_ups(self):
        r = self.r
        self.assertEqual(r["ups.status"].svalue, "Online + Charging")
        self.assertEqual(r["ups.status"].nvalue, model.OK)
        self.assertEqual(r["ups.charge"].svalue, "100.0")
        self.assertEqual(r["ups.load"].svalue, "21.0")
        self.assertEqual(r["ups.runtime"].svalue, "58")               # 3480 s

    def test_storage_io_whole_drives_only(self):
        devs = {k.split(".")[1] for k in self.r if k.startswith("io.")}
        self.assertEqual(devs, {"sata1", "sata2", "sata3", "sata4", "nvme0n1", "nvme1n1"})   # no md*, dm-0, sda1

    def test_smart(self):
        r = self.r
        self.assertEqual(r["smart.sata1.health"].nvalue, model.OK)
        self.assertEqual(r["smart.sata4.health"].nvalue, model.WARN)
        self.assertIn("pending 2", r["smart.sata4.health"].svalue)
        self.assertIn("power-on 31500 h", r["smart.sata1.health"].svalue)

    def test_services_and_ssd_cache(self):
        r = self.r
        self.assertEqual(r["svc.cifs.users"].svalue, "2")
        self.assertEqual(r["fc.cachedev0.rhit"].svalue, "83.0")

    def test_extras_are_not_visible_by_default(self):
        r = self.r
        self.assertTrue(r["res.cpu"].used and r["disk.drive1.temp"].used and r["ups.status"].used)
        self.assertFalse(r["smart.sata1.health"].used or r["svc.http.users"].used or r["io.sata1.load"].used)

    def test_first_poll_has_no_rates_yet_but_devices_are_announced(self):
        rx = self.r["net.eth0.rx"]
        self.assertTrue(rx.pending)
        self.assertIn("net.eth0.rx", self.result.present_keys)        # so a restart does not flag it as gone


class RateTests(unittest.TestCase):
    def test_64bit_rate_in_mbit(self):
        engine, client, clock = make(mibdata.ds920())
        engine.cycle()
        client.mib.data[mibdata.IFX + ".6.2"] = ("counter64", 10 ** 9 * 2 + 125_000_000)   # +125 MB
        client.mib.data[mibdata.IFX + ".10.2"] = ("counter64", 5 * 10 ** 8 * 2 + 12_500_000)
        clock.advance(10)
        r = by_key(engine.cycle())
        self.assertEqual(r["net.eth0.rx"].svalue, "100.00")             # 125 MB in 10 s = 100 Mbit/s
        self.assertEqual(r["net.eth0.tx"].svalue, "10.00")
        self.assertEqual(r["net.eth0.rx"].unit, "Mbit/s")

    def test_counter_reset_after_reboot_gives_no_spike(self):
        engine, client, clock = make(mibdata.ds920())
        engine.cycle()
        client.mib.data[mibdata.IFX + ".6.2"] = ("counter64", 5)       # went backwards
        clock.advance(10)
        self.assertTrue(by_key(engine.cycle())["net.eth0.rx"].pending)

    def test_disk_io_rates_use_64bit_counters(self):
        engine, client, clock = make(mibdata.ds920())
        engine.cycle()
        base = mibdata.SYNO + ".101.1.1.%d.0"                          # row 0 = sata1
        client.mib.data[base % 12] = ("counter64", 10 ** 10 + 10 * 1024 * 1024 * 5)    # +50 MiB read
        clock.advance(10)
        r = by_key(engine.cycle())
        self.assertEqual(r["io.sata1.read"].svalue, "5.00")             # MB/s
        self.assertEqual(r["io.sata1.write"].svalue, "0.00")

    def test_smart_is_only_polled_every_ten_minutes(self):
        engine, client, clock = make(mibdata.ds920())
        self.assertIn("smart", engine.cycle().ran_groups)
        clock.advance(60)
        self.assertNotIn("smart", engine.cycle().ran_groups)
        clock.advance(600)
        self.assertIn("smart", engine.cycle().ran_groups)


class Ds218Tests(unittest.TestCase):
    """A small model: whatever is absent must simply not appear."""

    @classmethod
    def setUpClass(cls):
        cls.engine, cls.client, cls.clock = make(mibdata.ds218())
        cls.result = cls.engine.cycle()
        cls.r = by_key(cls.result)

    def test_detected_hardware_matches_the_model(self):
        r = self.r
        self.assertEqual(len([k for k in r if k.startswith("disk.") and k.endswith(".health")]), 2)
        self.assertEqual({k.split(".")[1] for k in r if k.startswith("net.")}, {"eth0"})
        for absent in ("ups.status", "sys.cpufan", "res.swap", "smart.sata1.health", "svc.http.users"):
            self.assertNotIn(absent, r)
        self.assertEqual(self.engine.info["model"], "DS218+")

    def test_cpu_falls_back_to_idle_and_uptime_to_sysuptime(self):
        self.assertEqual(self.r["res.cpu"].svalue, "20.0")
        self.assertEqual(self.r["res.uptime"].svalue, "0.00")

    def test_memory_with_less_ram(self):
        self.assertEqual(self.r["res.mem"].svalue, "57.5")

    def test_32bit_counters_wrap_around(self):
        engine, client, clock = make(mibdata.ds218())
        engine.cycle()
        client.mib.data[mibdata.IFT + ".10.2"] = ("gauge", 105032706)   # 4000000002 + 400 000 000 - 2**32
        clock.advance(10)
        self.assertEqual(by_key(engine.cycle())["net.eth0.rx"].svalue, "320.00")


class DetailLevelTests(unittest.TestCase):
    def groups(self, level):
        engine, _, _ = make(mibdata.ds920(), level=level)
        return {k.split(".")[0] for k in by_key(engine.cycle())}

    def test_basic(self):
        self.assertEqual(self.groups(1), {"sys", "res", "disk", "raid", "vol"})

    def test_standard_adds_network_and_ups(self):
        g = self.groups(2)
        self.assertTrue({"net", "ups"} <= g)
        self.assertFalse({"io", "smart", "svc", "fc"} & g)

    def test_full_has_everything(self):
        self.assertTrue({"io", "smart", "svc", "fc"} <= self.groups(3))


if __name__ == "__main__":
    unittest.main()
