"""Command line front end: detect a NAS and print what the plugin would create.

    python3 -m synoSNMP NAS_IP -c public
    python3 -m synoSNMP NAS_IP -v 3 -u monitor -A - -X -           # prompts for the passphrases

Runs exactly the engine the Domoticz plugin uses, without Domoticz.  Useful after
a deployment, and to see why a device is (not) created.
Secrets: a passphrase or community given on the command line is visible in the process list and the shell history.
Pass ``-`` as the value to be prompted instead, or set SYNO_SNMP_COMMUNITY / SYNO_SNMP_AUTH_PASS /
SYNO_SNMP_PRIV_PASS in the environment.

Exit status: 0 ok, 2 NAS unreachable, 3 not a Synology, 1 other error.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from typing import Callable, List, Optional, TextIO

from . import __version__
from .config import Settings, SettingsError
from .engine import CycleResult, Engine
from .model import ALERT_DEV, CUSTOM, Reading
from .snmpcli import SnmpClient, SnmpUnavailable

LEVEL_NAMES = {0: "n/a", 1: "OK", 2: "WARN", 3: "ALERT", 4: "CRIT"}


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    p = argparse.ArgumentParser(prog="python3 -m synoSNMP", description=__doc__.split("\n\n")[0])
    p.add_argument("host", help="NAS address")
    p.add_argument("-p", "--port", default="161")
    p.add_argument("-v", "--version", default="2c", choices=["1", "2c", "3"], help="SNMP version (default 2c)")
    p.add_argument("-c", "--community", default=env("SYNO_SNMP_COMMUNITY", "public"),
                   help="v1/v2c community; '-' prompts (default: $SYNO_SNMP_COMMUNITY or public)")
    p.add_argument("-u", "--user", default="", help="SNMPv3 user")
    p.add_argument("-A", "--auth-pass", default=env("SYNO_SNMP_AUTH_PASS", ""),
                   help="SNMPv3 authentication passphrase; '-' prompts (default: $SYNO_SNMP_AUTH_PASS)")
    p.add_argument("-X", "--priv-pass", default=env("SYNO_SNMP_PRIV_PASS", ""),
                   help="SNMPv3 privacy passphrase; '-' prompts (default: $SYNO_SNMP_PRIV_PASS, else same as -A)")
    p.add_argument("-l", "--v3-level", default="authPriv", help="noAuthNoPriv | authNoPriv | authPriv")
    p.add_argument("-a", "--auth-proto", default="SHA")
    p.add_argument("-x", "--priv-proto", default="AES")
    p.add_argument("--level", type=int, default=3, choices=[1, 2, 3], help="detail level (1 basic, 2 standard, 3 full)")
    p.add_argument("--rates", type=int, default=0, metavar="SECONDS",
                   help="wait SECONDS and poll again so traffic/IO rates are shown")
    p.add_argument("--json", action="store_true", help="machine readable output")
    p.add_argument("--debug", action="store_true", help="also print detection notes")
    p.add_argument("--version-info", action="version", version="synoSNMP " + __version__)
    return p


def _secret(value: str, label: str, prompt: Callable[[str], str]) -> str:
    """``-`` means "ask me": the value then never appears in the process list or the shell history."""
    return prompt("%s: " % label) if value == "-" else value


def settings_from_args(a: argparse.Namespace, prompt: Callable[[str], str] = getpass.getpass) -> Settings:
    community = _secret(a.community, "SNMP community", prompt)
    auth = _secret(a.auth_pass, "SNMPv3 authentication passphrase", prompt)
    priv = _secret(a.priv_pass, "SNMPv3 privacy passphrase", prompt)
    params = {"Address": a.host, "Port": a.port, "Mode1": community, "Mode2": a.version,
              "Mode3": "%s;%s;%s" % (a.v3_level, a.auth_proto, a.priv_proto), "Mode4": priv,
              "Mode5": str(a.level), "Username": a.user, "Password": auth,
              "Mode6": "Debug" if a.debug else "Normal"}
    return Settings.from_parameters(params, mode4_may_be_stale=False)


def describe(r: Reading) -> str:
    if r.pending:
        return "(no value yet - needs a second sample, use --rates 10)"
    if r.kind == ALERT_DEV:
        return "[%s] %s" % (LEVEL_NAMES.get(r.nvalue, r.nvalue), r.svalue)
    if r.kind == CUSTOM:
        return ("%s %s" % (r.svalue, r.unit)).strip()
    return {"temperature": "%s C", "percentage": "%s %%"}.get(r.kind, "%s") % r.svalue


def render(result: CycleResult, info: dict, out: TextIO) -> None:
    out.write("Synology %s  %s\n" % (info.get("model", "?"), info.get("version", "")))
    out.write("%d devices (%d SNMP calls, %.1fs)\n\n" % (
        len([r for r in result.readings]), result.snmp_calls, result.duration))
    width = max([len(r.key) for r in result.readings] + [8])
    name_width = max([len(r.name) for r in result.readings] + [8])
    for r in result.readings:
        out.write("%-*s  %-*s  %-11s %s%s\n" % (width, r.key, name_width, r.name, r.kind, describe(r),
                                                 "" if r.used else "   (hidden by default)"))
    for r in result.health[:1]:
        out.write("\nPlugin health: %s\n" % describe(r))


def main(argv: Optional[List[str]] = None, out: TextIO = sys.stdout, err: TextIO = sys.stderr,
         client_factory: Optional[Callable[[Settings], SnmpClient]] = None,
         sleep: Callable[[float], None] = time.sleep,
         prompt: Callable[[str], str] = getpass.getpass) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:                       # argparse exits with 2 for bad options; 2 means "NAS silent" here
        return 0 if exc.code in (0, None) else 1
    try:
        settings = settings_from_args(args, prompt)
        settings.timeout, settings.retries = 3, 1
        settings.offline_window = None              # a diagnostic run reports a silent NAS as silent, whatever the schedule
        client = (client_factory or SnmpClient)(settings)
    except SettingsError as exc:
        err.write("Configuration error: %s\n" % exc)
        return 1
    except SnmpUnavailable as exc:
        err.write("%s\n" % exc)
        return 1
    except (EOFError, KeyboardInterrupt):           # a passphrase prompt without a terminal, or Ctrl-C at the prompt
        err.write("Passphrase input cancelled.\n")
        return 1

    try:
        for warning in settings.warnings:
            err.write("WARNING: %s\n" % warning)
        engine = Engine(settings, client)
        result = engine.cycle()
        if args.rates and result.reachable and not result.fatal:
            sleep(args.rates)
            # forced: the second sample must not be skipped by the collectors' own (longer) schedules
            again = engine.cycle(force=True)
            again.messages = result.messages + again.messages
            result = again

        for level, text in result.messages:
            if level == "error" or (level == "debug" and args.debug):
                err.write("%s: %s\n" % (level.upper(), text))
        if result.fatal or not result.readings:
            if not result.messages:
                err.write("No data.\n")
            if result.not_synology:
                return 3
            if result.fatal:
                return 1
            return 2 if not result.reachable else 1

        if args.json:
            json.dump({"model": engine.info.get("model"), "version": engine.info.get("version"),
                       "health": [{"key": r.key, "level": r.nvalue, "value": r.svalue} for r in result.health[:1]],
                       "devices": [{"key": r.key, "name": r.name, "kind": r.kind, "value": r.svalue, "level": r.nvalue,
                                    "unit": r.unit, "visible": r.used, "pending": r.pending} for r in result.readings]},
                      out, indent=2)
            out.write("\n")
        else:
            render(result, engine.info, out)
        return 0
    finally:
        client.stop()