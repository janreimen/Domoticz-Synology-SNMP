import io
import json
import os
import sys
import time
import unittest
from unittest import mock

from helpers import mibdata
from fakeclient import FakeClient
from synoSNMP import config
from synoSNMP.cli import main


def run(mib, *argv, sleeps=None, seen=None, prompt=None, env=None):
    """Run the CLI against a fake NAS.  ``seen`` collects the Settings the client was built from."""
    out, err = io.StringIO(), io.StringIO()

    def factory(settings):
        if seen is not None:
            seen.append(settings)
        return FakeClient(mib, settings)

    def forbidden(label):
        raise AssertionError("unexpected passphrase prompt: %s" % label)

    with mock.patch.dict(os.environ, env or {}):
        rc = main(["fake"] + list(argv), out=out, err=err, client_factory=factory,
                  sleep=(sleeps.append if sleeps is not None else (lambda s: None)), prompt=prompt or forbidden)
    return rc, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_table_output(self):
        rc, out, err = run(mibdata.ds920())
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("Synology DS920+  DSM 7.2.1-69057 Update 5", out)
        self.assertIn("disk.drive4.health", out)
        self.assertIn("[WARN]", out)
        self.assertIn("(no value yet - needs a second sample", out)
        self.assertIn("(hidden by default)", out)

    def test_rates_flag_takes_a_second_sample(self):
        sleeps = []
        rc, out, _ = run(mibdata.ds920(), "--rates", "7", sleeps=sleeps)
        self.assertEqual((rc, sleeps), (0, [7]))

    def test_the_second_sample_fills_every_rate_even_though_the_collectors_are_not_due_again(self):
        """Regression: the second cycle used to honour the 60 s .. 1 h schedules and returned 18 empty rates."""
        def sleep(seconds):
            time.sleep(0.01)                                           # a measurable interval between the samples
        out, err = io.StringIO(), io.StringIO()
        rc = main(["fake", "--rates", "1", "--json"], out=out, err=err, sleep=sleep, prompt=None,
                  client_factory=lambda s: FakeClient(mibdata.ds920(), s))
        self.assertEqual(rc, 0)
        devices = json.loads(out.getvalue())["devices"]
        self.assertEqual([d["key"] for d in devices if d["pending"]], [])
        self.assertTrue([d for d in devices if d["key"] == "net.eth0.rx"])
        self.assertTrue([d for d in devices if d["key"].startswith("io.")])

    def test_without_rates_the_rates_are_pending(self):
        _, out, _ = run(mibdata.ds920(), "--json")
        pending = [d["key"] for d in json.loads(out)["devices"] if d["pending"]]
        self.assertIn("net.eth0.rx", pending)

    def test_plugin_health_is_printed_and_in_the_json(self):
        rc, out, _ = run(mibdata.ds920())
        self.assertIn("Plugin health: [OK] 13 collectors OK | ", out)
        _, js, _ = run(mibdata.ds920(), "--json")
        health = json.loads(js)["health"]
        self.assertEqual([h["key"] for h in health], ["plugin.health"])
        self.assertEqual(health[0]["level"], 1)

    def test_json_output(self):
        rc, out, _ = run(mibdata.ds920(), "--json", "--level", "1")
        data = json.loads(out)
        self.assertEqual(data["model"], "DS920+")
        keys = {d["key"] for d in data["devices"]}
        self.assertIn("sys.status", keys)
        self.assertNotIn("net.eth0.link", keys)             # level 1 = basic

    def test_not_a_synology_gives_exit_3(self):
        rc, out, err = run(mibdata.ds920().remove_prefix(mibdata.SYNO))
        self.assertEqual(rc, 3)
        self.assertIn("Synology MIB", err)

    def test_unreachable_gives_exit_2(self):
        out, err = io.StringIO(), io.StringIO()

        def factory(s):
            c = FakeClient(mibdata.ds920(), s)
            c.down = True
            return c
        rc = main(["fake"], out=out, err=err, client_factory=factory)
        self.assertEqual(rc, 2)
        self.assertIn("No SNMP answer", err.getvalue())

    def test_bad_settings_give_exit_1(self):
        rc, _, err = run(mibdata.ds920(), "-v", "3")          # v3 without a user
        self.assertEqual(rc, 1)
        self.assertIn("user", err)

    def test_unknown_option_gives_exit_1_not_argparses_2(self):
        err = io.StringIO()
        real_stderr, sys.stderr = sys.stderr, err
        try:
            rc = main(["fake", "--no-such-option"], out=io.StringIO(), err=io.StringIO())
        finally:
            sys.stderr = real_stderr
        self.assertEqual(rc, 1)                                   # 2 is reserved for "NAS does not answer"
        self.assertIn("--no-such-option", err.getvalue())

    def test_configuration_warnings_are_printed_to_stderr(self):
        env = config._Env({}, "/x/.env", ["/x/.env line 1: unknown setting DISK_INTERVAL ignored (did you mean DISKS_INTERVAL?)"])
        with mock.patch("synoSNMP.config._load_env", return_value=env):
            rc, out, err = run(mibdata.ds920())
        self.assertEqual(rc, 0)
        self.assertIn("WARNING: /x/.env line 1: unknown setting DISK_INTERVAL ignored", err)

    def test_help_exits_0(self):
        real, sys.stdout = sys.stdout, io.StringIO()
        try:
            rc = main(["--help"], out=io.StringIO(), err=io.StringIO())
        finally:
            sys.stdout = real
        self.assertEqual(rc, 0)




class SecretHandlingTests(unittest.TestCase):
    def test_community_from_the_environment(self):
        seen = []
        rc, out, err = run(mibdata.ds920(), seen=seen, env={"SYNO_SNMP_COMMUNITY": "from-env"})
        self.assertEqual((rc, seen[0].community), (0, "from-env"))
        self.assertNotIn("from-env", out + err)

    def test_the_command_line_beats_the_environment(self):
        seen = []
        run(mibdata.ds920(), "-c", "from-cli", seen=seen, env={"SYNO_SNMP_COMMUNITY": "from-env"})
        self.assertEqual(seen[0].community, "from-cli")

    def test_a_dash_prompts_for_the_community(self):
        seen, asked = [], []

        def prompt(label):
            asked.append(label)
            return "typed-secret"
        rc, out, err = run(mibdata.ds920(), "-c", "-", seen=seen, prompt=prompt)
        self.assertEqual((rc, asked, seen[0].community), (0, ["SNMP community: "], "typed-secret"))
        self.assertNotIn("typed-secret", out + err)

    def test_snmpv3_passphrases_come_from_prompts_or_the_environment(self):
        seen, asked = [], []
        answers = {"SNMPv3 authentication passphrase: ": "authpassphrase1", "SNMPv3 privacy passphrase: ": "privpassphrase2"}

        def prompt(label):
            asked.append(label)
            return answers[label]
        rc, out, err = run(mibdata.ds920(), "-v", "3", "-u", "monitor", "-A", "-", "-X", "-", seen=seen, prompt=prompt)
        self.assertEqual(rc, 0, err)
        self.assertEqual(asked, ["SNMPv3 authentication passphrase: ", "SNMPv3 privacy passphrase: "])
        self.assertEqual((seen[0].v3_auth_pass, seen[0].v3_priv_pass), ("authpassphrase1", "privpassphrase2"))

        seen = []
        rc, _, err = run(mibdata.ds920(), "-v", "3", "-u", "monitor", seen=seen,
                         env={"SYNO_SNMP_AUTH_PASS": "envauthphrase", "SYNO_SNMP_PRIV_PASS": "envprivphrase"})
        self.assertEqual(rc, 0, err)
        self.assertEqual((seen[0].v3_auth_pass, seen[0].v3_priv_pass), ("envauthphrase", "envprivphrase"))

    def test_without_a_privacy_passphrase_the_authentication_passphrase_is_used(self):
        seen = []
        run(mibdata.ds920(), "-v", "3", "-u", "u", "-A", "authpassphrase1", seen=seen)
        self.assertEqual(seen[0].v3_priv_pass, "authpassphrase1")

    def test_a_short_privacy_passphrase_is_an_error_not_a_stale_interval(self):
        rc, _, err = run(mibdata.ds920(), "-v", "3", "-u", "u", "-A", "authpassphrase1", "-X", "10")
        self.assertEqual(rc, 1)
        self.assertIn("at least 8", err)

    def test_a_prompt_without_a_terminal_is_a_clean_error(self):
        def no_terminal(label):
            raise EOFError()
        rc, out, err = run(mibdata.ds920(), "-c", "-", prompt=no_terminal)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("cancelled", err)

    def test_ctrl_c_at_the_prompt_is_a_clean_error(self):
        def interrupted(label):
            raise KeyboardInterrupt()
        rc, _, err = run(mibdata.ds920(), "-c", "-", prompt=interrupted)
        self.assertEqual(rc, 1)
        self.assertIn("cancelled", err)


if __name__ == "__main__":
    unittest.main()
