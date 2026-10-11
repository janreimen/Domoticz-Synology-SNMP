#!/usr/bin/env python3
"""snmpd ``pass_persist`` backend that serves the Synology subtree of the DS920+ fixture."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mibdata  # noqa: E402

ROOT = mibdata.SYNO
TYPES = {"integer": "integer", "gauge": "gauge", "counter64": "counter64", "string": "string",
         "timeticks": "timeticks", "objectid": "objectid", "float": "string"}   # floats travel as text here
DATA = {o: (TYPES[t], str(v)) for o, (t, v) in mibdata.ds920().sorted_items() if o.startswith(ROOT + ".")}
KEYS = sorted(DATA, key=mibdata.okey)


def reply(oid, vtype, value):
    sys.stdout.write("%s\n%s\n%s\n" % ("." + oid, vtype, value))


while True:
    cmd = sys.stdin.readline()
    if not cmd:
        break
    cmd = cmd.strip()
    if cmd == "PING":
        sys.stdout.write("PONG\n")
    elif cmd in ("get", "getnext"):
        oid = sys.stdin.readline().strip().strip(".")
        if cmd == "get":
            reply(oid, *DATA[oid]) if oid in DATA else sys.stdout.write("NONE\n")
        else:
            nxt = [k for k in KEYS if mibdata.okey(k) > mibdata.okey(oid)]
            reply(nxt[0], *DATA[nxt[0]]) if nxt else sys.stdout.write("NONE\n")
    else:
        sys.stdin.readline()
        sys.stdout.write("NONE\n")
    sys.stdout.flush()
