"""Start a real snmpd (net-snmp) serving the DS920+ fixture; used for end-to-end tests."""
import os
import shutil
import socket
import subprocess
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def available():
    return all(shutil.which(t) for t in ("snmpd", "snmpget", "snmpwalk", "snmpbulkwalk"))


def free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Snmpd:
    def __init__(self):
        self.port = free_udp_port()
        self.dir = tempfile.mkdtemp(prefix="synosnmp-test-")
        conf = os.path.join(self.dir, "snmpd.conf")
        with open(conf, "w") as f:
            f.write("agentAddress udp:127.0.0.1:%d\nrocommunity public 127.0.0.1\n" % self.port)
            f.write("createUser synov3 SHA longpassphrase1 AES longpassphrase2\nrouser synov3 priv\n")
            f.write("pass_persist .1.3.6.1.4.1.6574 %s\n" % os.path.join(HERE, "pass_persist_synology.py"))
        self.proc = subprocess.Popen(["snmpd", "-f", "-Lo", "-C", "-c", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 10
        while time.time() < deadline:
            r = subprocess.run(["snmpget", "-v2c", "-c", "public", "-t", "1", "-r", "0", "-m", "", "-On",
                                "udp:127.0.0.1:%d" % self.port, ".1.3.6.1.4.1.6574.1.5.1.0"], capture_output=True)
            if b"DS920+" in r.stdout:
                return
            time.sleep(0.3)
        self.stop()
        raise RuntimeError("snmpd did not start")

    def stop(self):
        self.proc.kill()
        self.proc.wait()
        shutil.rmtree(self.dir, ignore_errors=True)
