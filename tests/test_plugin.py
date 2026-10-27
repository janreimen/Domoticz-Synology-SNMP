"""plugin.py itself, with the Domoticz stub: logging levels, start-up failures and the manifest."""
import importlib
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import domoticz_stub as dz
import mibdata
from fakeclient import FakeClient
from synoSNMP.engine import CycleResult
from synoSNMP.snmpcli import SnmpUnavailable

HERE = os.path.dirname(os.path.abspath(__file__))
NO_ENV = os.path.join(tempfile.gettempdir(), "domoticz-syno-snmp-no-such-dir", ".env")


def load_plugin(**params):
    """A fresh plugin module, as Domoticz would load it, that never reads a real .env file."""
    sys.modules["Domoticz"] = dz
    dz.reset()
    sys.modules.pop("plugin", None)
    plugin = importlib.import_module("plugin")
    real = plugin.syno_config._load_env
    patcher = mock.patch.object(plugin.syno_config, "_load_env", lambda path=None: real(NO_ENV))
    patcher.start()
    plugin._patcher = patcher
    plugin.Parameters = dict({"Address": "fake", "Port": "161", "Mode1": "public", "Mode2": "2c",
                              "Mode5": "3", "Mode6": "Normal"}, **params)
    plugin.Devices = dz.Devices
    plugin.Settings = {"Language": "en"}              # what Domoticz injects under that name
    plugin.Images = {}
    return plugin


def fake_client_factory(made):
    def factory(settings, trace=None):
        client = FakeClient(mibdata.ds920(), settings)
        made.append(client)
        return client
    return factory


def logs(kind):
    return [t for k, t in dz.logs if k == kind]


class PluginTestCase(unittest.TestCase):
    def load(self, **params):
        plugin = load_plugin(**params)
        self.addCleanup(plugin._patcher.stop)
        return plugin

    def started(self, plugin, **_):
        made = []
        plugin.SnmpClient = fake_client_factory(made)
        plugin.onStart()
        self.addCleanup(plugin.onStop)
        return made


class LogLevelTests(PluginTestCase):
    def result(self):
        return CycleResult(messages=[("error", "boom"), ("notice", "disks: working again"),
                                     ("info", "Connected to Synology DS920+"), ("debug", "Serial number: X")],
                           duration=2.0, snmp_calls=7)

    def log(self, level):
        plugin = self.load(Mode6=level)
        plugin.Parameters["Mode6"] = level
        plugin._plugin.settings = plugin.syno_config.Settings.from_parameters(plugin.Parameters)
        dz.logs.clear()
        plugin._plugin._log(self.result())
        return dz.logs

    def test_normal_shows_errors_and_recoveries_only(self):
        out = self.log("Normal")
        self.assertEqual(out, [("error", "boom"), ("log", "disks: working again")])

    def test_debug_adds_identity_and_detection_notes_and_the_poll_summary(self):
        out = self.log("Debug")
        self.assertEqual([k for k, _ in out], ["error", "log", "log", "log", "log"])
        texts = [t for _, t in out]
        self.assertIn("Connected to Synology DS920+", texts)
        self.assertIn("Serial number: X", texts)
        self.assertTrue(any(t.startswith("Poll complete: 0 readings, 7 SNMP calls, 2.00s") for t in texts))

    def test_trace_is_a_superset_of_debug(self):
        debug = [t for _, t in self.log("Debug")]
        trace = [t for _, t in self.log("Trace")]
        self.assertEqual(trace[:len(debug)], debug)
        self.assertTrue(any(t.startswith("TRACE:") for t in trace))


class StartUpTests(PluginTestCase):
    def test_configuration_error_is_reported_not_raised(self):
        plugin = self.load(Address="")
        plugin.onStart()
        self.assertTrue(any("Configuration error" in t for t in logs("error")))
        self.assertIsNone(plugin._plugin.poller)

    def test_configuration_warnings_are_logged_at_start(self):
        plugin = self.load(Mode2="3", Username="u", Password="longpassphrase", Mode3="authPriv;SHA;AES;otherprivphrase")
        self.started(plugin)
        self.assertTrue(any(t.startswith("Configuration warning:") and "deprecated" in t for t in logs("log")), logs("log"))

    def test_missing_net_snmp_tools_are_reported(self):
        plugin = self.load()
        plugin.SnmpClient = mock.Mock(side_effect=SnmpUnavailable("snmpget/snmpwalk not found in PATH. Install ..."))
        plugin.onStart()
        self.assertEqual(logs("error"), ["snmpget/snmpwalk not found in PATH. Install ..."])
        self.assertIsNone(plugin._plugin.poller)
        plugin.onStop()                                                  # must not raise

    @unittest.skipUnless(shutil.which("snmpget") and shutil.which("snmpwalk"), "net-snmp client tools not installed")
    def test_a_credential_that_cannot_be_written_to_the_private_config_is_reported_not_raised(self):
        plugin = self.load(Mode1="bad\ncommunity")
        plugin.onStart()                                                 # the real SnmpClient refuses the line break
        self.assertEqual(len(logs("error")), 1)
        self.assertIn("Cannot prepare the SNMP client", logs("error")[0])
        self.assertIsNone(plugin._plugin.poller)
        plugin.onStop()

    def test_a_failure_while_building_the_poller_removes_the_private_configuration(self):
        plugin = self.load()
        made = []
        plugin.SnmpClient = fake_client_factory(made)
        plugin.Engine = mock.Mock(side_effect=RuntimeError("no engine today"))
        plugin.onStart()
        self.assertIn("Could not start polling: RuntimeError: no engine today", logs("error"))
        self.assertTrue(made[0]._stopped)                                # client.stop() ran: credentials are gone
        self.assertIsNone(plugin._plugin._client)
        self.assertIsNone(plugin._plugin.poller)
        plugin.onStop()

    def test_the_poller_uses_the_engine_tick(self):
        plugin = self.load()
        self.started(plugin)
        self.assertEqual(plugin._plugin.poller.interval, plugin._plugin.poller.engine.tick)
        self.assertEqual(plugin._plugin._tick, plugin._plugin.poller.engine.tick)

    def test_start_poll_heartbeat_stop_with_a_fake_nas(self):
        plugin = self.load()
        made = self.started(plugin)
        deadline = time.time() + 20
        while time.time() < deadline and plugin._plugin.poller.results.qsize() == 0:
            time.sleep(0.05)
        plugin.onHeartbeat()
        ids = {d.DeviceID: d for d in dz.Devices.values()}
        self.assertIn("res.cpu", ids)
        self.assertEqual(ids["plugin.health"].nValue, 1)                 # OK
        self.assertIn("collectors OK", ids["plugin.health"].sValue)
        self.assertEqual(logs("error"), [])
        plugin.onStop()
        self.assertTrue(made[0]._stopped)
        self.assertIsNone(plugin._plugin.poller)

    def test_the_debug_start_line_names_where_the_schedule_came_from(self):
        plugin = self.load(Mode6="Debug")
        self.started(plugin)
        line = [t for t in logs("log") if "scheduler tick=" in t]
        self.assertEqual(len(line), 1)
        self.assertIn("built-in defaults (no .env found)", line[0])


class ManifestTests(unittest.TestCase):
    def manifest(self):
        with open(os.path.join(HERE, "..", "plugin.py"), encoding="utf-8") as fh:
            text = fh.read()
        return ET.fromstring(re.search(r'"""\s*(<plugin .*?</plugin>)\s*"""', text, re.S).group(1))

    def params(self):
        return {p.get("field"): p for p in self.manifest().iter("param")}

    def test_secrets_are_password_fields(self):
        params = self.params()
        for field in ("Password", "Mode4"):
            self.assertEqual(params[field].get("password"), "true", field)
        self.assertEqual(params["Mode4"].get("label"), "SNMPv3 privacy passphrase")
        self.assertIsNone(params["Mode1"].get("password"))               # community stays visible (v1/v2c)

    def test_no_field_still_claims_to_be_a_polling_interval(self):
        for p in self.manifest().iter("param"):
            self.assertNotIn("interval", p.get("label", "").lower())

    def test_default_v3_options_name_both_protocols_and_no_passphrase(self):
        default = self.params()["Mode3"].get("default")
        self.assertEqual(default, "authPriv;SHA;AES")


if __name__ == "__main__":
    unittest.main()
