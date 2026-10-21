import unittest

from helpers import mibdata  # noqa: F401
from synoSNMP.config import Settings
from synoSNMP.snmpcli import SnmpClient


def argv(**kw):
    s = Settings(host=kw.pop("host", "10.0.0.5"), **kw)
    return SnmpClient(s, runner=lambda a, t: (0, "", ""))._argv("snmpget", (), ["1.3.6.1.2.1.1.3.0"])


class CommandLineTests(unittest.TestCase):
    def test_get_many_deduplicates_oids_before_chunking(self):
        from synoSNMP.snmpcli import SnmpClient
        from synoSNMP.snmpparse import Varbind, INTEGER
        client = object.__new__(SnmpClient)
        batches = []
        def get_chunk(oids):
            batches.append(list(oids))
            return [Varbind(oid, INTEGER, 1, "1") for oid in oids]
        client._get_chunk = get_chunk
        client.GET_CHUNK = 2
        result = client.get_many([".1.3.6", "1.3.6", "1.3.7", "1.3.8"])
        self.assertEqual(batches, [["1.3.6", "1.3.7"], ["1.3.8"]])
        self.assertEqual(set(result), {"1.3.6", "1.3.7", "1.3.8"})

    def test_v2c(self):
        a = argv(community="secret", port=1161, timeout=3, retries=2)
        self.assertEqual(a[a.index("-v") + 1], "2c")
        self.assertNotIn("secret", a)
        self.assertNotIn("-c", a)
        self.assertEqual((a[a.index("-t") + 1], a[a.index("-r") + 1]), ("3", "2"))
        self.assertEqual(a[-2:], ["udp:10.0.0.5:1161", ".1.3.6.1.2.1.1.3.0"])
        self.assertEqual(a[a.index("-m") + 1], "")
        for flag in ("-On", "-Oe"):
            self.assertIn(flag, a)

    def test_v3_levels(self):
        a = argv(version="3", v3_user="u", v3_level="authPriv", v3_auth_pass="aaaaaaaa", v3_priv_pass="bbbbbbbb")
        self.assertNotIn("-c", a)
        self.assertEqual([a[a.index(f) + 1] for f in ("-l", "-u", "-a", "-x")],
                         ["authPriv", "u", "SHA", "AES"])
        self.assertNotIn("aaaaaaaa", a)
        self.assertNotIn("bbbbbbbb", a)
        self.assertNotIn("-A", a)
        self.assertNotIn("-X", a)
        a = argv(version="3", v3_user="u", v3_level="authNoPriv", v3_auth_pass="aaaaaaaa")
        self.assertNotIn("-A", a)
        self.assertNotIn("-X", a)
        self.assertNotIn("aaaaaaaa", a)
        a = argv(version="3", v3_user="u", v3_level="noAuthNoPriv")
        self.assertNotIn("-A", a)


    def test_trace_logs_requests_and_results_without_credentials(self):
        logs = []
        def runner(argv, timeout):
            return 0, ".1.3.6.1.2.1.1.3.0 = INTEGER: 42\n", ""
        client = SnmpClient(Settings(host="10.0.0.5", community="secret"), runner=runner, trace=logs.append)
        client.get("1.3.6.1.2.1.1.3.0")
        self.assertTrue(any("SNMP request" in line for line in logs))
        self.assertTrue(any("SNMP response" in line and "42" in line for line in logs))
        self.assertFalse(any("secret" in line for line in logs))

    def test_ipv6_and_no_shell(self):
        self.assertIn("udp6:[fe80::1]:161", argv(host="fe80::1"))
        self.assertIn("udp:nas.lan:161", argv(host="nas.lan"))
        # an address with shell metacharacters is just one argv element: no shell is ever involved
        self.assertIn("udp:x;rm -rf /:161", argv(host="x;rm -rf /"))


    def test_private_config_permissions_and_cleanup(self):
        import os
        from pathlib import Path

        settings = Settings(host="10.0.0.5", version="2c", community="private-community")
        client = SnmpClient(settings, which=lambda tool: "/usr/bin/" + tool)
        directory = client._config_dir
        self.assertIsNotNone(directory)
        self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
        config = Path(directory) / "snmp.conf"
        self.assertEqual(os.stat(str(config)).st_mode & 0o777, 0o600)
        self.assertIn('defCommunity "private-community"', config.read_text())
        self.assertNotIn("private-community", client._argv("snmpget", (), ["1.3.6.1"]))
        client.stop()
        self.assertFalse(os.path.exists(directory))

    def test_v3_private_config_keeps_both_secrets_out_of_argv(self):
        import os
        from pathlib import Path

        settings = Settings(host="nas", version="3", v3_user="monitor",
                            v3_level="authPriv", v3_auth_pass="auth-secret-123",
                            v3_priv_pass="privacy-secret-456")
        client = SnmpClient(settings, which=lambda tool: "/usr/bin/" + tool)
        try:
            argv = client._argv("snmpget", (), ["1.3.6.1"])
            self.assertNotIn(settings.v3_auth_pass, argv)
            self.assertNotIn(settings.v3_priv_pass, argv)
            config = Path(client._config_dir) / "snmp.conf"
            text = config.read_text()
            self.assertIn('defAuthPassphrase "auth-secret-123"', text)
            self.assertIn('defPrivPassphrase "privacy-secret-456"', text)
            self.assertEqual(os.stat(str(config)).st_mode & 0o777, 0o600)
        finally:
            client.stop()

    def test_config_rejects_newline_in_secret(self):
        from synoSNMP.snmpcli import SnmpError
        with self.assertRaises(SnmpError):
            SnmpClient._config_value("secret\nforgedDirective yes")


if __name__ == "__main__":
    unittest.main()
