import unittest

from helpers import mibdata  # noqa: F401  (sets sys.path)
from synoSNMP.config import Settings, SettingsError
from synoSNMP.util import RateCalc, make_key, slug, pct, human_bytes


class SettingsTests(unittest.TestCase):
    def params(self, **kw):
        p = {"Address": "192.168.1.10", "Port": "161", "Mode1": "public", "Mode2": "2c",
             "Mode4": "60", "Mode5": "3", "Mode6": "Normal"}
        p.update(kw)
        return p

    def test_defaults_from_manifest(self):
        s = Settings.from_parameters(self.params())
        self.assertEqual((s.host, s.port, s.version, s.community, s.interval, s.level, s.debug),
                         ("192.168.1.10", 161, "2c", "public", 60, 3, False))

    def test_debug_and_level(self):
        s = Settings.from_parameters(self.params(Mode6="Debug", Mode5="1"))
        self.assertTrue(s.debug)
        self.assertEqual(s.level, 1)

    def test_rejects_bad_values(self):
        for bad in ({"Address": ""}, {"Port": "99999"}, {"Mode4": "2"}, {"Mode4": "abc"}, {"Mode5": "7"}, {"Mode2": "4"}):
            with self.assertRaises(SettingsError, msg=str(bad)):
                Settings.from_parameters(self.params(**bad))

    def test_snmpv3(self):
        s = Settings.from_parameters(self.params(Mode2="3", Username="monitor", Password="longpassphrase",
                                                 Mode3="authPriv;SHA-256;AES-256;otherprivphrase"))
        self.assertEqual((s.v3_user, s.v3_level, s.v3_auth_proto, s.v3_priv_proto, s.v3_priv_pass),
                         ("monitor", "authPriv", "SHA-256", "AES-256", "otherprivphrase"))

    def test_snmpv3_priv_passphrase_defaults_to_auth_passphrase(self):
        s = Settings.from_parameters(self.params(Mode2="3", Username="u", Password="longpassphrase", Mode3="authPriv;SHA;AES;"))
        self.assertEqual(s.v3_priv_pass, "longpassphrase")

    def test_snmpv3_validation(self):
        with self.assertRaises(SettingsError):
            Settings.from_parameters(self.params(Mode2="3", Password="longpassphrase"))        # no user
        with self.assertRaises(SettingsError):
            Settings.from_parameters(self.params(Mode2="3", Username="u", Password="short"))   # too short
        with self.assertRaises(SettingsError):
            Settings.from_parameters(self.params(Mode2="3", Username="u", Password="longpassphrase", Mode3="bogus"))
        s = Settings.from_parameters(self.params(Mode2="3", Username="u", Mode3="noAuthNoPriv"))
        self.assertEqual(s.v3_level, "noAuthNoPriv")


class UtilTests(unittest.TestCase):
    def test_keys_are_short_stable_and_distinct(self):
        short = make_key("disk", "drive1", "temp")
        self.assertEqual(short, "disk.drive1.temp")
        a = make_key("vol", "volumeusb1usbshare", "used")
        b = make_key("vol", "volumeusb1usbshare", "free")
        self.assertLessEqual(len(a), 25)
        self.assertNotEqual(a, b)
        self.assertEqual(a, make_key("vol", "volumeusb1usbshare", "used"))

    def test_slug(self):
        self.assertEqual(slug("Cache device 1"), "cachedevice1")
        self.assertEqual(slug("///"), "x")

    def test_pct_and_human(self):
        self.assertEqual(pct(25, 100), 25.0)
        self.assertIsNone(pct(1, 0))
        self.assertEqual(human_bytes(1024 ** 4 * 7.3), "7.3 TB")

    def test_rate_calc(self):
        r = RateCalc()
        self.assertIsNone(r.update("k", 100, 0.0))
        self.assertEqual(r.update("k", 600, 10.0), 50.0)
        self.assertIsNone(r.update("k", 10, 20.0))                 # reset
        self.assertEqual(r.update("k", 110, 30.0), 10.0)
        self.assertEqual(r.update("w", 4294967290, 0.0, 32) or 0, 0)
        self.assertEqual(r.update("w", 6, 2.0, 32), 6.0)           # wrapped: 12 bytes in 2 s


if __name__ == "__main__":
    unittest.main()
