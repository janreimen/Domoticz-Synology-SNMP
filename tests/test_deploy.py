"""deploy.sh in local mode, into a temporary plugins directory (no Domoticz, no restart)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "deploy.sh")
sys.path.insert(0, ROOT)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@unittest.skipUnless(shutil.which("bash") and shutil.which("tar") and os.name == "posix", "needs bash and tar")
class DeployScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="synosnmp-deploy-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plugins = os.path.join(self.tmp, "plugins")
        os.makedirs(self.plugins)
        self.dir = os.path.join(self.plugins, "Domoticz-Synology-SNMP")
        self.backups = os.path.join(self.tmp, "backups")

    def deploy(self, *args, expect=0):
        cmd = ["bash", SCRIPT, "--dir", self.dir, "--backup-dir", self.backups, "--no-restart"] + list(args)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                              cwd=self.tmp, timeout=60)
        self.assertEqual(proc.returncode, expect, proc.stdout)
        return proc.stdout

    def backup_files(self):
        return sorted(os.listdir(self.backups)) if os.path.isdir(self.backups) else []

    def test_dry_run_changes_nothing(self):
        out = self.deploy("--dry-run")
        self.assertIn("plugin.py", out)
        self.assertIn("[dry-run]", out)
        self.assertEqual(os.listdir(self.plugins), [])
        self.assertEqual(self.backup_files(), [])

    def test_fresh_install_copies_only_runtime_files(self):
        out = self.deploy()
        self.assertIn("new install", out)
        for path in ("plugin.py", "synoSNMP/__init__.py", "synoSNMP/collectors/disks.py", "README.md", ".env.sample"):
            self.assertTrue(os.path.exists(os.path.join(self.dir, path)), path)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "tests")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "dist")))
        for _, dirs, _ in os.walk(self.dir):
            self.assertNotIn("__pycache__", dirs)
        self.assertEqual(self.backup_files(), [])

    def test_update_backs_up_outside_the_plugin_directory_and_keeps_foreign_files(self):
        self.deploy()
        with open(os.path.join(self.dir, "local-notes.txt"), "w") as fh:
            fh.write("mine")
        self.deploy()
        self.assertEqual(len(self.backup_files()), 1)
        self.assertEqual(os.listdir(self.plugins), ["Domoticz-Synology-SNMP"])     # nothing else is loaded as a plugin
        self.assertTrue(os.path.exists(os.path.join(self.dir, "local-notes.txt")))

    def test_only_the_newest_backups_are_kept(self):
        self.deploy()
        for _ in range(4):
            self.deploy("--keep", "2")
        self.assertEqual(len(self.backup_files()), 2)

    def test_rollback_restores_the_previous_copy(self):
        self.deploy()
        marker = os.path.join(self.dir, "plugin.py")
        with open(marker, "a") as fh:
            fh.write("\n# local edit\n")
        self.deploy()                                                  # replaces the edited copy, backs it up
        self.assertNotIn("# local edit", read(marker))
        self.deploy("--rollback")
        self.assertIn("# local edit", read(marker))

    def test_rollback_without_backup_fails_cleanly(self):
        self.deploy()
        out = self.deploy("--rollback", expect=1)
        self.assertIn("no backup found", out)

    def test_unusable_backup_directory_aborts_before_touching_the_install(self):
        self.deploy()
        marker = os.path.join(self.dir, "plugin.py")
        with open(marker, "a") as fh:
            fh.write("\n# keep me\n")
        blocker = os.path.join(self.tmp, "blocker")
        open(blocker, "w").close()
        self.backups = os.path.join(blocker, "sub")                     # cannot be created: parent is a file
        out = self.deploy(expect=1)
        self.assertIn("nothing was changed", out)
        self.assertIn("# keep me", read(marker))

    def test_restart_command_is_run_and_a_failing_one_does_not_fail_the_deployment(self):
        marker = os.path.join(self.tmp, "restarted")
        cmd = ["bash", SCRIPT, "--dir", self.dir, "--backup-dir", self.backups, "--restart-cmd", "touch " + marker]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertTrue(os.path.exists(marker))
        cmd[-1] = "exit 3"
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("restart command failed", proc.stdout)

    def test_bad_arguments(self):
        proc = subprocess.run(["bash", SCRIPT, "--nope"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=30)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unknown option", proc.stdout)
        proc = subprocess.run(["bash", SCRIPT, "--keep", "0"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=30)
        self.assertEqual(proc.returncode, 1)

    def test_version_matches_the_package(self):
        proc = subprocess.run(["bash", SCRIPT, "--version"], stdout=subprocess.PIPE, universal_newlines=True, timeout=30)
        from synoSNMP import __version__
        self.assertEqual(proc.stdout.strip(), __version__)


if __name__ == "__main__":
    unittest.main()
