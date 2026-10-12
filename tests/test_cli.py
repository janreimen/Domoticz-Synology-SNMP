import io
import json
import sys
import unittest

from helpers import mibdata
from fakeclient import FakeClient
from synoSNMP.cli import main


def run(mib, *argv, sleeps=None):
    out, err = io.StringIO(), io.StringIO()
    rc = main(["fake"] + list(argv), out=out, err=err, client_factory=lambda s: FakeClient(mib, s),
              sleep=(sleeps.append if sleeps is not None else (lambda s: None)))
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

    def test_help_exits_0(self):
        real, sys.stdout = sys.stdout, io.StringIO()
        try:
            rc = main(["--help"], out=io.StringIO(), err=io.StringIO())
        finally:
            sys.stdout = real
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
