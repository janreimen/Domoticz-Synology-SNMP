import unittest

from helpers import mibdata  # noqa: F401
from synoSNMP.config import Settings
from synoSNMP.snmpcli import SnmpClient


def argv(**kw):
    s = Settings(host=kw.pop("host", "10.0.0.5"), **kw)
    return SnmpClient(s, runner=lambda a, t: (0, "", ""))._argv("snmpget", (), ["1.3.6.1.2.1.1.3.0"])


class CommandLineTests(unittest.TestCase):
    def test_v2c(self):
        a = argv(community="secret", port=1161, timeout=3, retries=2)
        self.assertEqual(a[a.index("-v") + 1], "2c")
        self.assertEqual(a[a.index("-c") + 1], "secret")
        self.assertEqual((a[a.index("-t") + 1], a[a.index("-r") + 1]), ("3", "2"))
        self.assertEqual(a[-2:], ["udp:10.0.0.5:1161", ".1.3.6.1.2.1.1.3.0"])
        self.assertEqual(a[a.index("-m") + 1], "")
        for flag in ("-On", "-Oe"):
            self.assertIn(flag, a)

    def test_v3_levels(self):
        a = argv(version="3", v3_user="u", v3_level="authPriv", v3_auth_pass="aaaaaaaa", v3_priv_pass="bbbbbbbb")
        self.assertNotIn("-c", a)
        self.assertEqual([a[a.index(f) + 1] for f in ("-l", "-u", "-a", "-A", "-x", "-X")],
                         ["authPriv", "u", "SHA", "aaaaaaaa", "AES", "bbbbbbbb"])
        a = argv(version="3", v3_user="u", v3_level="authNoPriv", v3_auth_pass="aaaaaaaa")
        self.assertIn("-A", a)
        self.assertNotIn("-X", a)
        a = argv(version="3", v3_user="u", v3_level="noAuthNoPriv")
        self.assertNotIn("-A", a)

    def test_ipv6_and_no_shell(self):
        self.assertIn("udp6:[fe80::1]:161", argv(host="fe80::1"))
        self.assertIn("udp:nas.lan:161", argv(host="nas.lan"))
        # an address with shell metacharacters is just one argv element: no shell is ever involved
        self.assertIn("udp:x;rm -rf /:161", argv(host="x;rm -rf /"))


if __name__ == "__main__":
    unittest.main()
