import os
import tempfile
import unittest
from unittest import mock

from helpers import mibdata  # noqa: F401  (sets sys.path)
from synoSNMP.config import Settings, SettingsError
from synoSNMP.util import RateCalc, make_key, slug, pct, human_bytes


NO_ENV = os.path.join(tempfile.gettempdir(), "domoticz-syno-snmp-no-such-dir", ".env")


def env_file(text):
    """A temporary .env with this content; returns its path (removed again by the test's addCleanup)."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8", newline="")
    handle.write(text)
    handle.close()
    return handle.name


class SettingsTests(unittest.TestCase):
    def parse(self, params, env_path=NO_ENV, **kw):
        """from_parameters() that never picks up a .env that happens to sit in the checkout."""
        return Settings.from_parameters(params, env_path=env_path, **kw)

    def params(self, **kw):
        p = {"Address": "192.168.1.10", "Port": "161", "Mode1": "public", "Mode2": "2c",
             "Mode5": "3", "Mode6": "Normal"}
        p.update(kw)
        return p

    def test_defaults_from_manifest(self):
        s = self.parse(self.params())
        self.assertEqual((s.host, s.port, s.version, s.community, s.interval, s.slow_interval, s.level, s.debug),
                         ("192.168.1.10", 161, "2c", "public", 60, 3600, 3, False))
        self.assertEqual(s.collector_intervals["disks"], 300)
        self.assertEqual(s.collector_intervals["mounts"], 600)
        self.assertEqual(s.collector_intervals["smart"], 3600)

    def test_debug_and_level(self):
        s = self.parse(self.params(Mode6="Debug", Mode5="1"))
        self.assertTrue(s.debug)
        self.assertEqual(s.level, 1)

    def test_logging_levels(self):
        self.assertEqual(self.parse(self.params(Mode6="Normal")).log_level, 1)
        self.assertEqual(self.parse(self.params(Mode6="Debug")).log_level, 2)
        trace = self.parse(self.params(Mode6="Trace"))
        self.assertEqual(trace.log_level, 3)
        self.assertTrue(trace.debug)

    def test_rejects_bad_values(self):
        for bad in ({"Address": ""}, {"Port": "99999"}, {"Mode5": "7"}, {"Mode2": "4"}):
            with self.assertRaises(SettingsError, msg=str(bad)):
                self.parse(self.params(**bad))


    def test_env_schedule_overrides_defaults_and_validates_values(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as env:
            env.write("SYSTEM_INTERVAL=45\nSMART_INTERVAL=7200\nUNKNOWN_SETTING=123\n")
            env.flush()
            s = self.parse(self.params(), env_path=env.name)
        self.assertEqual(s.collector_intervals["system"], 45)
        self.assertEqual(s.collector_intervals["smart"], 7200)
        self.assertEqual(s.interval, 45)
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("unknown setting UNKNOWN_SETTING ignored", s.warnings[0])

        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as env:
            env.write("GPU_INTERVAL=2\n")
            env.flush()
            with self.assertRaises(SettingsError):
                self.parse(self.params(), env_path=env.name)

    def test_snmpv3(self):
        s = self.parse(self.params(Mode2="3", Username="monitor", Password="longpassphrase",
                                                 Mode3="authPriv;SHA-256;AES-256;otherprivphrase"))
        self.assertEqual((s.v3_user, s.v3_level, s.v3_auth_proto, s.v3_priv_proto, s.v3_priv_pass),
                         ("monitor", "authPriv", "SHA-256", "AES-256", "otherprivphrase"))

    def test_snmpv3_priv_passphrase_defaults_to_auth_passphrase(self):
        s = self.parse(self.params(Mode2="3", Username="u", Password="longpassphrase", Mode3="authPriv;SHA;AES;"))
        self.assertEqual(s.v3_priv_pass, "longpassphrase")

    def test_snmpv3_validation(self):
        with self.assertRaises(SettingsError):
            self.parse(self.params(Mode2="3", Password="longpassphrase"))        # no user
        with self.assertRaises(SettingsError):
            self.parse(self.params(Mode2="3", Username="u", Password="short"))   # too short
        with self.assertRaises(SettingsError):
            self.parse(self.params(Mode2="3", Username="u", Password="longpassphrase", Mode3="bogus"))
        s = self.parse(self.params(Mode2="3", Username="u", Mode3="noAuthNoPriv"))
        self.assertEqual(s.v3_level, "noAuthNoPriv")


class EnvFileTests(unittest.TestCase):
    def load(self, text, **params):
        path = env_file(text)
        self.addCleanup(os.unlink, path)
        return Settings.from_parameters(dict({"Address": "h", "Mode5": "3"}, **params), env_path=path)

    def test_no_file_means_the_built_in_defaults_and_no_warning(self):
        s = Settings.from_parameters({"Address": "h"}, env_path=NO_ENV)
        self.assertIsNone(s.env_file)
        self.assertEqual(s.warnings, [])
        self.assertEqual(s.collector_intervals["system"], 60)

    def test_the_file_that_was_read_is_remembered(self):
        path = env_file("SYSTEM_INTERVAL=30\n")
        self.addCleanup(os.unlink, path)
        self.assertEqual(Settings.from_parameters({"Address": "h"}, env_path=path).env_file, path)

    def test_inline_comments_quotes_and_export_are_understood(self):
        s = self.load("# a comment\n\nexport SYSTEM_INTERVAL=30  # half a minute\n"
                      "DISKS_INTERVAL = \"120\"\nRAID_INTERVAL='600' # quoted\n")
        self.assertEqual([s.collector_intervals[k] for k in ("system", "disks", "raid")], [30, 120, 600])
        self.assertEqual(s.warnings, [])

    def test_text_after_a_closing_quote_is_an_error_not_silently_dropped(self):
        with self.assertRaises(SettingsError):
            self.load('SYSTEM_INTERVAL="60"abc\n')
        self.assertEqual(self.load('SYSTEM_INTERVAL="60"   # fine\n').collector_intervals["system"], 60)

    def test_a_byte_order_mark_from_a_windows_editor_is_ignored(self):
        s = self.load("\ufeffSYSTEM_INTERVAL=30\n")
        self.assertEqual(s.collector_intervals["system"], 30)
        self.assertEqual(s.warnings, [])

    def test_crlf_line_endings(self):
        s = self.load("SYSTEM_INTERVAL=30\r\nDISKS_INTERVAL=120\r\n")
        self.assertEqual((s.collector_intervals["system"], s.collector_intervals["disks"]), (30, 120))

    def test_numbers_that_are_not_numbers_are_configuration_errors_not_crashes(self):
        for bad in ("inf", "-inf", "nan", "abc", "1e999"):
            with self.assertRaises(SettingsError, msg=bad):
                self.load("SYSTEM_INTERVAL=%s\n" % bad)
        self.assertEqual(self.load("SYSTEM_INTERVAL=\n").collector_intervals["system"], 60)    # empty: the default

    def test_a_misspelled_key_is_reported_with_a_suggestion_instead_of_being_ignored_silently(self):
        s = self.load("DISK_INTERVAL=30\n")
        self.assertEqual(s.collector_intervals["disks"], 300)             # the default stays in force...
        self.assertEqual(len(s.warnings), 1)                              # ...but the user is told why
        self.assertIn("unknown setting DISK_INTERVAL ignored (did you mean DISKS_INTERVAL?)", s.warnings[0])

    def test_an_unrelated_unknown_key_is_reported_without_a_suggestion(self):
        s = self.load("COMMUNITY=public\n")
        self.assertEqual(len(s.warnings), 1)
        self.assertNotIn("did you mean", s.warnings[0])

    def test_a_line_that_is_not_an_assignment_is_reported_with_its_line_number(self):
        s = self.load("SYSTEM_INTERVAL=30\nthis is not valid\n")
        self.assertEqual(s.collector_intervals["system"], 30)
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("line 2 ignored", s.warnings[0])

    def test_credentials_are_never_taken_from_the_file(self):
        s = self.load("SNMP_COMMUNITY=leaked\nCOMMUNITY=leaked\nPASSWORD=leakedleaked\n")
        self.assertEqual(s.community, "public")
        self.assertEqual(len(s.warnings), 3)

    def test_the_process_environment_wins_over_the_file(self):
        with mock.patch.dict(os.environ, {"SYSTEM_INTERVAL": "20"}):
            self.assertEqual(self.load("SYSTEM_INTERVAL=45\n").collector_intervals["system"], 20)

    def test_a_file_that_cannot_be_decoded_is_a_configuration_error(self):
        handle = tempfile.NamedTemporaryFile("wb", suffix=".env", delete=False)
        handle.write(b"SYSTEM_INTERVAL=\x80\x81\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        with self.assertRaises(SettingsError):
            Settings.from_parameters({"Address": "h"}, env_path=handle.name)


class TransportSettingsTests(unittest.TestCase):
    def load(self, text):
        path = env_file(text)
        self.addCleanup(os.unlink, path)
        return Settings.from_parameters({"Address": "h"}, env_path=path)

    def test_defaults(self):
        s = Settings.from_parameters({"Address": "h"}, env_path=NO_ENV)
        self.assertEqual((s.timeout, s.retries, s.discovery_interval, s.refresh_interval), (5, 1, 3600, 300))

    def test_values_from_the_env_file(self):
        s = self.load("SNMP_TIMEOUT=10\nSNMP_RETRIES=0\nDISCOVERY_INTERVAL=120\nREFRESH_INTERVAL=60\n")
        self.assertEqual((s.timeout, s.retries, s.discovery_interval, s.refresh_interval), (10, 0, 120, 60))
        self.assertEqual(s.warnings, [])

    def test_they_do_not_change_the_polling_schedule(self):
        s = self.load("SNMP_TIMEOUT=10\n")
        self.assertEqual(s.interval, 60)

    def test_out_of_range_values_are_rejected(self):
        for text in ("SNMP_TIMEOUT=0", "SNMP_TIMEOUT=61", "SNMP_RETRIES=6", "SNMP_RETRIES=-1",
                     "DISCOVERY_INTERVAL=59", "REFRESH_INTERVAL=29", "SNMP_TIMEOUT=fast"):
            with self.assertRaises(SettingsError, msg=text):
                self.load(text + "\n")


class SnmpV3PrivacyFieldTests(unittest.TestCase):
    def parse(self, **kw):
        params = {"Address": "h", "Mode2": "3", "Username": "u", "Password": "longpassphrase1", "Mode3": "authPriv;SHA;AES"}
        params.update(kw)
        mode4_may_be_stale = params.pop("_stale", True)
        return Settings.from_parameters(params, env_path=NO_ENV, mode4_may_be_stale=mode4_may_be_stale)

    def test_the_privacy_passphrase_has_its_own_field(self):
        s = self.parse(Mode4="longpassphrase2")
        self.assertEqual((s.v3_auth_pass, s.v3_priv_pass), ("longpassphrase1", "longpassphrase2"))
        self.assertEqual(s.warnings, [])

    def test_an_empty_field_re_uses_the_authentication_passphrase(self):
        self.assertEqual(self.parse(Mode4="").v3_priv_pass, "longpassphrase1")
        self.assertEqual(self.parse().v3_priv_pass, "longpassphrase1")

    def test_a_polling_interval_that_an_older_version_left_in_the_field_is_not_a_passphrase(self):
        """0.2.1 and older kept the polling interval (10, 60, 300, 3600, ...) in Mode4."""
        for stale in ("10", "30", "60", "300", "3600", "86400"):
            s = self.parse(Mode4=stale)
            self.assertEqual(s.v3_priv_pass, "longpassphrase1", stale)
            self.assertEqual(s.warnings, [], stale)

    def test_a_short_value_that_is_not_a_number_is_ignored_with_a_warning(self):
        s = self.parse(Mode4="abc")
        self.assertEqual(s.v3_priv_pass, "longpassphrase1")
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("shorter than 8 characters", s.warnings[0])

    def test_a_long_number_is_a_passphrase(self):
        self.assertEqual(self.parse(Mode4="123456789012").v3_priv_pass, "123456789012")

    def test_the_command_line_takes_the_value_literally(self):
        with self.assertRaises(SettingsError):
            self.parse(Mode4="10", _stale=False)                           # a real, too short, passphrase: not a stale interval
        self.assertEqual(self.parse(Mode4="longpassphrase2", _stale=False).v3_priv_pass, "longpassphrase2")

    def test_the_old_fourth_part_of_the_options_still_works_but_is_deprecated(self):
        s = self.parse(Mode3="authPriv;SHA;AES;oldprivphrase")
        self.assertEqual(s.v3_priv_pass, "oldprivphrase")
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("deprecated", s.warnings[0])

    def test_the_field_wins_over_the_deprecated_fourth_part(self):
        s = self.parse(Mode3="authPriv;SHA;AES;oldprivphrase", Mode4="newprivphrase")
        self.assertEqual(s.v3_priv_pass, "newprivphrase")

    def test_versions_1_and_2c_do_not_look_at_the_field(self):
        s = Settings.from_parameters({"Address": "h", "Mode2": "2c", "Mode4": "abc"}, env_path=NO_ENV)
        self.assertEqual((s.v3_priv_pass, s.warnings), ("", []))

    def test_authnopriv_needs_no_privacy_passphrase(self):
        s = self.parse(Mode3="authNoPriv;SHA")
        self.assertEqual(s.v3_level, "authNoPriv")


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
