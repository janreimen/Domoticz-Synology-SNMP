"""The private net-snmp configuration directory: naming, permissions, and removal of orphans.

Credentials never go on a command line; they live in ``<tmp>/domoticz-syno-snmp-<pid>-<random>/snmp.conf``.
A Domoticz that is killed (power cut, ``kill -9``, OOM) never reaches ``onStop()``, so the next start removes
the directories of processes that no longer exist.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import mibdata  # noqa: F401  (sets sys.path)
from synoSNMP.config import Settings
from synoSNMP.snmpcli import SnmpClient, cleanup_stale_private_configs

HOUR = 3600
OTHER_UID = os.geteuid() + 1 if hasattr(os, "geteuid") else 1


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="cleanup-test-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.now = 1_000_000.0

    def make(self, name, files=("snmp.conf",), age=0):
        path = os.path.join(self.base, name)
        os.mkdir(path)
        for f in files:
            with open(os.path.join(path, f), "w") as fh:
                fh.write("defCommunity \"secret\"\n")
        os.utime(path, (self.now - age, self.now - age))
        return path

    def clean(self, alive=()):
        return cleanup_stale_private_configs(self.base, pid_alive=lambda pid: pid in alive)

    def test_the_directory_of_a_process_that_no_longer_exists_is_removed(self):
        path = self.make("domoticz-syno-snmp-4242-abcd1234")
        self.assertEqual(self.clean(), [path])
        self.assertFalse(os.path.exists(path))

    def test_the_directory_of_a_running_process_is_kept(self):
        path = self.make("domoticz-syno-snmp-4242-abcd1234")
        self.assertEqual(self.clean(alive={4242}), [])
        self.assertTrue(os.path.isdir(path))

    def test_our_own_directory_is_never_removed_even_if_the_liveness_check_says_otherwise(self):
        path = self.make("domoticz-syno-snmp-%d-abcd1234" % os.getpid())
        self.assertEqual(self.clean(), [])
        self.assertTrue(os.path.isdir(path))

    def test_directories_without_a_pid_are_never_removed_however_old(self):
        """0.2.2 and older name them without a pid; the owner may be a plugin that has been running for weeks."""
        old = self.make("domoticz-syno-snmp-ab12cd_x", age=HOUR * 24 * 30)
        digits = self.make("domoticz-syno-snmp-12345678", age=HOUR * 24 * 30)
        self.assertEqual(self.clean(), [])
        self.assertTrue(os.path.isdir(old) and os.path.isdir(digits))

    def test_a_directory_owned_by_somebody_else_is_left_alone(self):
        path = self.make("domoticz-syno-snmp-4242-abcd1234")
        self.assertEqual(cleanup_stale_private_configs(self.base, pid_alive=lambda pid: False, uid=OTHER_UID), [])
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(cleanup_stale_private_configs(self.base, pid_alive=lambda pid: False), [path])

    def test_a_pid_that_cannot_exist_does_not_abort_the_sweep(self):
        """A planted name with an impossible pid used to raise OverflowError out of os.kill and stop the cleanup."""
        planted = self.make("domoticz-syno-snmp-9999999999-zz")
        orphan = self.make("domoticz-syno-snmp-4242-abcd1234")
        removed = cleanup_stale_private_configs(self.base, pid_alive=lambda pid: pid == 9999999999)
        self.assertEqual(removed, [orphan])
        self.assertTrue(os.path.isdir(planted))
        with mock.patch("os.kill", side_effect=OverflowError):                  # the real liveness check
            self.assertEqual(cleanup_stale_private_configs(self.base), [])      # unknown: kept
        self.assertTrue(os.path.isdir(planted))

    def test_a_directory_with_anything_but_snmp_conf_is_left_alone(self):
        path = self.make("domoticz-syno-snmp-4242-abcd1234", files=("snmp.conf", "notes.txt"))
        self.assertEqual(self.clean(), [])
        self.assertTrue(os.path.isdir(path))

    def test_names_that_only_look_similar_are_left_alone(self):
        for name in ("domoticz-syno-snmp", "domoticz-syno-snmp-", "domoticz-syno-snmp-1-a b", "domoticz-other-4242-abcd",
                     "xdomoticz-syno-snmp-4242-abcd1234", "domoticz-syno-snmp-4242-abcd1234.bak"):
            self.make(name, age=HOUR * 24)
        self.assertEqual(self.clean(), [])
        self.assertEqual(len(os.listdir(self.base)), 6)

    def test_a_plain_file_with_a_matching_name_is_left_alone(self):
        path = os.path.join(self.base, "domoticz-syno-snmp-4242-abcd1234")
        with open(path, "w") as fh:
            fh.write("not a directory")
        self.assertEqual(self.clean(), [])
        self.assertTrue(os.path.isfile(path))

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks")
    def test_a_symlink_is_never_followed_or_removed(self):
        target = os.path.join(self.base, "precious")
        os.mkdir(target)
        with open(os.path.join(target, "snmp.conf"), "w") as fh:
            fh.write("keep me\n")
        link = os.path.join(self.base, "domoticz-syno-snmp-4242-abcd1234")
        os.symlink(target, link)
        self.assertEqual(self.clean(), [])
        self.assertTrue(os.path.islink(link))
        self.assertTrue(os.path.isfile(os.path.join(target, "snmp.conf")))

    def test_a_missing_or_unreadable_temp_directory_is_not_an_error(self):
        self.assertEqual(cleanup_stale_private_configs(os.path.join(self.base, "nope")), [])


class ClientPrivateConfigTests(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="client-test-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        patcher = mock.patch.object(tempfile, "tempdir", self.base)
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, **kw):
        # runner=None: the real code path that writes the credentials; `which` stands in for installed tools
        return SnmpClient(Settings(host="127.0.0.1", **kw), which=lambda tool: "/usr/bin/" + tool)

    def private_dirs(self):
        return sorted(os.listdir(self.base))

    def test_directory_name_permissions_and_content(self):
        client = self.client(community='sec"ret')
        self.addCleanup(client.stop)
        (name,) = self.private_dirs()
        self.assertRegex(name, r"^domoticz-syno-snmp-%d-[A-Za-z0-9_]+$" % os.getpid())
        directory = os.path.join(self.base, name)
        self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
        conf = os.path.join(directory, "snmp.conf")
        self.assertEqual(stat.S_IMODE(os.stat(conf).st_mode), 0o600)
        with open(conf, encoding="utf-8") as fh:
            self.assertIn('defCommunity "sec\\"ret"', fh.read())

    def test_stop_removes_it_and_is_idempotent(self):
        client = self.client()
        self.assertEqual(len(self.private_dirs()), 1)
        client.stop()
        client.stop()
        self.assertEqual(self.private_dirs(), [])

    def test_starting_removes_what_a_killed_process_left_behind(self):
        orphan = os.path.join(self.base, "domoticz-syno-snmp-%d-deadbeef" % dead_pid())
        os.mkdir(orphan)
        with open(os.path.join(orphan, "snmp.conf"), "w") as fh:
            fh.write("defCommunity \"left behind\"\n")
        client = self.client()
        self.addCleanup(client.stop)
        self.assertFalse(os.path.exists(orphan))
        self.assertEqual(len(self.private_dirs()), 1)

    def test_a_second_hardware_in_the_same_process_does_not_delete_the_first_ones_directory(self):
        first = self.client(community="one")
        second = self.client(community="two")
        self.addCleanup(first.stop)
        self.addCleanup(second.stop)
        self.assertEqual(len(self.private_dirs()), 2)
        with open(os.path.join(first._config_dir, "snmp.conf"), encoding="utf-8") as fh:
            self.assertIn('"one"', fh.read())

    def test_the_credentials_never_appear_on_a_command_line(self):
        client = self.client(version="3", v3_user="u", v3_level="authPriv",
                             v3_auth_pass="authpassphrase1", v3_priv_pass="privpassphrase2")
        self.addCleanup(client.stop)
        argv = client._argv("snmpget", (), ["1.3.6.1.2.1.1.3.0"])
        self.assertFalse([a for a in argv if "passphrase" in a])
        self.assertEqual(client._subprocess_env["SNMPCONFPATH"], client._config_dir)


if __name__ == "__main__":
    unittest.main()
