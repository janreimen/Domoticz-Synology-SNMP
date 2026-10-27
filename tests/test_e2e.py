"""End to end: real net-snmp binaries <-> real snmpd; then the actual plugin.py with a Domoticz stub."""
import importlib
import sys
import time
import unittest

import domoticz_stub as dz
import snmpd_fixture
from helpers import by_key, messages
from synoSNMP.config import Settings
from synoSNMP.engine import Engine
from synoSNMP.snmpcli import SnmpClient, SnmpError, SnmpTimeout


@unittest.skipUnless(snmpd_fixture.available(), "net-snmp (snmpd, snmpget, snmpbulkwalk) not installed")
class RealNetSnmpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snmpd = snmpd_fixture.Snmpd()

    @classmethod
    def tearDownClass(cls):
        cls.snmpd.stop()

    def settings(self, **kw):
        base = dict(host="127.0.0.1", port=self.snmpd.port, timeout=2, retries=0)
        base.update(kw)
        return Settings(**base)

    def test_get_walk_and_table_with_the_real_binaries(self):
        c = SnmpClient(self.settings())
        self.assertEqual(c.get("1.3.6.1.4.1.6574.1.5.1.0").as_text(), "DS920+")
        got = c.get_many(["1.3.6.1.4.1.6574.1.1.0", "1.3.6.1.4.1.6574.1.9.9.0", "1.3.6.1.4.1.6574.3.1.1.5.0"])
        self.assertEqual(sorted(got), ["1.3.6.1.4.1.6574.1.1.0", "1.3.6.1.4.1.6574.3.1.1.5.0"])    # absent OID omitted
        self.assertEqual(got["1.3.6.1.4.1.6574.3.1.1.5.0"].as_int(), 21 * 1024 ** 4)               # Counter64
        table = c.table("1.3.6.1.4.1.6574.2.1.1")
        self.assertEqual(len(table), 6)
        self.assertEqual(table["3"][12].as_text(), "Drive 4")
        self.assertEqual(c.walk("1.3.6.1.4.1.6574.9"), [])                                          # empty subtree

    def test_snmpv1_fallback_path(self):
        c = SnmpClient(self.settings(version="1"))
        self.assertEqual(c.get("1.3.6.1.4.1.6574.1.5.1.0").as_text(), "DS920+")
        self.assertIsNone(c.get("1.3.6.1.4.1.6574.1.9.9.0"))
        self.assertEqual(len(c.table("1.3.6.1.4.1.6574.2.1.1")), 6)

    def test_snmpv1_get_with_present_and_absent_objects_in_one_request(self):
        """SNMPv1 rejects a whole GET that names a missing object (noSuchName).  The net-snmp tool retries without
        the offending object itself, so the present ones still come back, in one call.  (Checked against a real agent:
        no client-side workaround is needed.)"""
        c = SnmpClient(self.settings(version="1"))
        present = ["1.3.6.1.4.1.6574.1.1.0", "1.3.6.1.4.1.6574.1.5.1.0"]
        before = c.calls
        got = c.get_many([present[0], "1.3.6.1.4.1.6574.1.9.9.0", present[1]])
        self.assertEqual(sorted(got), sorted(present))
        self.assertEqual(c.calls - before, 1)

    def test_snmpv3_authpriv(self):
        c = SnmpClient(self.settings(version="3", v3_user="synov3", v3_level="authPriv", v3_auth_proto="SHA",
                                     v3_auth_pass="longpassphrase1", v3_priv_proto="AES", v3_priv_pass="longpassphrase2"))
        self.assertEqual(c.get("1.3.6.1.4.1.6574.1.5.1.0").as_text(), "DS920+")
        self.assertEqual(len(c.table("1.3.6.1.4.1.6574.2.1.1")), 6)
        bad = SnmpClient(self.settings(version="3", v3_user="synov3", v3_level="authPriv", v3_auth_proto="SHA",
                                       v3_auth_pass="wrongpassphrase", v3_priv_proto="AES", v3_priv_pass="longpassphrase2", timeout=1))
        with self.assertRaises(Exception) as ctx:
            bad.get("1.3.6.1.4.1.6574.1.5.1.0")
        self.assertTrue(isinstance(ctx.exception, SnmpError))

    def test_many_oids_are_chunked(self):
        c = SnmpClient(self.settings())
        # Use more than GET_CHUNK distinct OIDs. Repeating the six drive OIDs
        # would now be deduplicated before chunking and correctly need only one
        # subprocess, so pad with distinct absent OIDs to exercise chunking.
        oids = ["1.3.6.1.4.1.6574.1.5.1.0"] + [
            "1.3.6.1.4.1.6574.2.1.1.6.%d" % i for i in range(6)
        ] + ["1.3.6.1.4.1.6574.99.1.%d.0" % i for i in range(14)]
        self.assertGreater(len(set(oids)), c.GET_CHUNK)
        before = c.calls
        got = c.get_many(oids)
        self.assertEqual(len(got), 7)
        self.assertGreaterEqual(c.calls - before, 2)

    def test_timeout_and_wrong_community(self):
        with self.assertRaises(SnmpTimeout):
            SnmpClient(self.settings(community="wrong", timeout=1)).get("1.3.6.1.2.1.1.3.0")
        with self.assertRaises(SnmpTimeout):
            SnmpClient(self.settings(port=self.snmpd.port + 1, timeout=1)).get("1.3.6.1.2.1.1.3.0")

    def test_stop_aborts_a_running_request(self):
        c = SnmpClient(self.settings(port=self.snmpd.port + 1, timeout=5, retries=3))      # would take 20 s
        import threading
        t0 = time.time()
        threading.Timer(0.5, c.stop).start()
        with self.assertRaises(Exception):
            c.get("1.3.6.1.2.1.1.3.0")
        self.assertLess(time.time() - t0, 5)

    def test_engine_with_real_client(self):
        engine = Engine(self.settings(), SnmpClient(self.settings()))
        result = engine.cycle()
        r = by_key(result)
        self.assertTrue(result.reachable, messages(result, "error"))
        self.assertEqual(messages(result, "error"), [])                    # no collector blew up on real output
        self.assertEqual(engine.info["model"], "DS920+")
        self.assertEqual(r["disk.drive4.health"].nvalue, 2)
        self.assertEqual(r["ups.charge"].svalue, "100.0")                  # float delivered as text still parses
        self.assertEqual(r["raid.volume1.status"].svalue, "Normal | 3.0 TB free of 20.0 TB")
        self.assertEqual(len([k for k in r if k.startswith("io.") and k.endswith(".load")]), 6)
        self.assertIn("smart.sata4.health", r)
        time.sleep(1.1)
        second = by_key(engine.cycle())
        self.assertFalse(second["io.sata1.read"].pending)                  # rates from real counters


@unittest.skipUnless(snmpd_fixture.available(), "net-snmp not installed")
class PluginTests(unittest.TestCase):
    """Run the real plugin.py (manifest, glue, thread) against the real snmpd."""

    @classmethod
    def setUpClass(cls):
        cls.snmpd = snmpd_fixture.Snmpd()
        sys.modules["Domoticz"] = dz

    @classmethod
    def tearDownClass(cls):
        cls.snmpd.stop()

    def load_plugin(self, **params):
        dz.reset()
        sys.modules.pop("plugin", None)
        plugin = importlib.import_module("plugin")
        plugin.Parameters = {"Address": "127.0.0.1", "Port": str(self.snmpd.port), "Mode1": "public", "Mode2": "2c",
                             "Mode5": "3", "Mode6": "Trace"}
        plugin.Parameters.update(params)
        plugin.Devices = dz.Devices
        # Domoticz injects these four globals into the plugin module; the real `Settings` is a dict of
        # Domoticz' own settings and silently replaces anything the plugin imported under that name.
        plugin.Settings = {"Language": "en"}
        plugin.Images = {}
        return plugin

    def wait_for_results(self, plugin, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline and plugin._plugin.poller.results.qsize() == 0:
            time.sleep(0.1)
        self.assertGreater(plugin._plugin.poller.results.qsize(), 0, "poller produced nothing")

    def test_plugin_creates_devices_from_a_real_nas_and_stops_cleanly(self):
        plugin = self.load_plugin()
        plugin.onStart()
        try:
            self.wait_for_results(plugin)
            plugin.onHeartbeat()
        finally:
            plugin.onStop()
        errors = [t for l, t in dz.logs if l == "error"]
        self.assertEqual(errors, [])
        ids = {d.DeviceID: d for d in dz.Devices.values()}
        self.assertEqual(ids["sys.info"].sValue, "DS920+  |  DSM 7.2.1-69057 Update 5")
        self.assertEqual(ids["disk.drive2.temp"].sValue, "36.0")
        self.assertEqual(ids["disk.drive2.temp"].TypeName, "Temperature")
        self.assertGreater(len(dz.Devices), 30)
        self.assertTrue(any(l.lower() in ("debug", "trace") and "Connected to Synology DS920+" in t
                            for l, t in dz.logs))
        self.assertTrue(any(l == "heartbeat" for l, _ in dz.logs))
        self.assertFalse(plugin._plugin.poller)                             # poller thread joined

    V3 = dict(Mode2="3", Username="synov3", Password="longpassphrase1", Mode3="authPriv;SHA;AES")

    def test_snmpv3_privacy_passphrase_field_is_used(self):
        """The agent's privacy passphrase differs from its authentication passphrase, so this only works when the
        'SNMPv3 privacy passphrase' field (Mode4) really reaches net-snmp."""
        plugin = self.load_plugin(Mode4="longpassphrase2", **self.V3)
        plugin.onStart()
        try:
            self.wait_for_results(plugin)
            plugin.onHeartbeat()
        finally:
            plugin.onStop()
        self.assertEqual([t for l, t in dz.logs if l == "error"], [])
        ids = {d.DeviceID: d for d in dz.Devices.values()}
        self.assertEqual(ids["sys.info"].sValue, "DS920+  |  DSM 7.2.1-69057 Update 5")
        self.assertEqual(ids["plugin.health"].nValue, 1)

    def test_snmpv3_without_the_field_falls_back_to_the_authentication_passphrase(self):
        plugin = self.load_plugin(**self.V3)                                 # no Mode4: priv passphrase := auth passphrase
        plugin.onStart()
        try:
            plugin._plugin.poller.engine.client.s.timeout = 1
            self.wait_for_results(plugin, timeout=40)
            plugin.onHeartbeat()
        finally:
            plugin.onStop()
        self.assertTrue([t for l, t in dz.logs if l == "error"])
        health = [d for d in dz.Devices.values() if d.DeviceID == "plugin.health"][0]
        self.assertEqual(health.nValue, 4)

    def test_bad_configuration_is_reported_not_raised(self):
        plugin = self.load_plugin(Address="")
        plugin.onStart()
        self.assertTrue(any(l == "error" and "Configuration error" in t for l, t in dz.logs))
        plugin.onHeartbeat()
        plugin.onStop()

    def test_unreachable_nas_is_reported_and_devices_flagged(self):
        plugin = self.load_plugin(Port=str(self.snmpd.port + 1))
        plugin.Parameters["Mode2"] = "2c"
        plugin.onStart()
        try:
            plugin._plugin.poller.engine.client.s.timeout = 1
            self.wait_for_results(plugin, timeout=40)
            plugin.onHeartbeat()
        finally:
            plugin.onStop()
        self.assertTrue(any(l == "error" and "No SNMP answer" in t for l, t in dz.logs))
        # Only the plugin's own health devices exist, and they say what is wrong.
        self.assertEqual(sorted(d.DeviceID for d in dz.Devices.values()), ["plugin.health", "plugin.pollsecs"])
        health = [d for d in dz.Devices.values() if d.DeviceID == "plugin.health"][0]
        self.assertEqual(health.nValue, 4)                                  # CRIT -> red alert
        self.assertIn("NAS not answering", health.sValue)
        self.assertEqual(health.TimedOut, 0)


if __name__ == "__main__":
    unittest.main()
