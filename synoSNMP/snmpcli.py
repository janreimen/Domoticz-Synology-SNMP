"""SNMP access through the native net-snmp command line tools.

This is the only module that starts processes.  It builds the command line,
runs ``snmpget`` / ``snmpbulkwalk`` / ``snmpwalk``, and hands the text to
``snmpparse``.  No Python SNMP library is involved.

Options used on every call:

    -On       numeric OIDs, so no MIB files are needed
    -Oe       enumerations as numbers
    -m ""     do not load any MIB (faster start-up, no "Cannot find module" noise)
    -Cc       (walks) tolerate agents that return non-increasing OIDs
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import snmpparse
from .config import Settings
from .snmpparse import Varbind

Runner = Callable[[List[str], float], Tuple[int, str, str]]

INSTALL_HINT = "Install the net-snmp client tools, e.g.: sudo apt install snmp"


class SnmpError(Exception):
    """An SNMP request failed."""


class SnmpTimeout(SnmpError):
    """The agent did not answer (down, wrong community/credentials, SNMP disabled, firewall)."""


class SnmpUnavailable(SnmpError):
    """The net-snmp command line tools are not installed."""


def _subprocess_runner(owner: "SnmpClient") -> Runner:
    def run(argv: List[str], wall_timeout: float) -> Tuple[int, str, str]:
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=owner._subprocess_env)
        except OSError as exc:
            raise SnmpUnavailable("Cannot run %s: %s. %s" % (argv[0], exc, INSTALL_HINT))
        owner._set_proc(proc)
        try:
            out, err = proc.communicate(timeout=wall_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise SnmpTimeout("%s did not finish within %ds" % (argv[0], wall_timeout))
        finally:
            owner._set_proc(None)
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    return run


class SnmpClient:
    """Thin, synchronous wrapper around the net-snmp tools."""

    GET_CHUNK = 20          # OIDs per snmpget (keeps replies below the UDP size limit)
    BULK_REPETITIONS = 20
    WALK_WALL_TIMEOUT = 90  # a whole table walk may legitimately take a while

    def __init__(self, settings: Settings, runner: Optional[Runner] = None,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 trace: Optional[Callable[[str], None]] = None) -> None:
        self.s = settings
        self._trace = trace
        self.calls = 0
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._stopped = False
        self._bulk_ok = settings.version != "1"
        self._run = runner or _subprocess_runner(self)
        self._bin: Dict[str, str] = {}
        self._config_dir: Optional[str] = None
        self._subprocess_env = None
        if runner is None:
            for tool in ("snmpget", "snmpwalk", "snmpbulkwalk"):
                path = which(tool)
                if path:
                    self._bin[tool] = path
            if "snmpget" not in self._bin or "snmpwalk" not in self._bin:
                raise SnmpUnavailable("snmpget/snmpwalk not found in PATH. " + INSTALL_HINT)
            if "snmpbulkwalk" not in self._bin:
                self._bulk_ok = False
            self._prepare_private_config()

    # -- process control -------------------------------------------------
    def _set_proc(self, proc: Optional[subprocess.Popen]) -> None:
        with self._lock:
            self._proc = proc

    def stop(self) -> None:
        """Abort the running request and remove private credentials on shutdown."""
        with self._lock:
            self._stopped = True
            proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        self._cleanup_private_config()

    @staticmethod
    def _config_value(value: str) -> str:
        """Quote one value for a Net-SNMP config directive, rejecting line injection."""
        if any(ch in value for ch in ("\r", "\n", "\0")):
            raise SnmpError("SNMP credential contains an unsupported control character")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _prepare_private_config(self) -> None:
        """Write credentials to a private Net-SNMP config, never to process arguments."""
        s = self.s
        directives = []
        if s.version in ("1", "2c"):
            directives.append("defCommunity %s" % self._config_value(s.community))
        else:
            # Net-SNMP reads these directives from snmp.conf. Username, security
            # level and protocol names are non-secret and remain argv options.
            if s.v3_level != "noAuthNoPriv":
                directives.append("defAuthPassphrase %s" % self._config_value(s.v3_auth_pass))
            if s.v3_level == "authPriv":
                directives.append("defPrivPassphrase %s" % self._config_value(s.v3_priv_pass))

        directory = tempfile.mkdtemp(prefix="domoticz-syno-snmp-")
        try:
            os.chmod(directory, 0o700)
            config_path = os.path.join(directory, "snmp.conf")
            fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write("# Private credentials for Domoticz-Synology-SNMP; do not share.\n")
                stream.write("\n".join(directives) + "\n")
            os.chmod(config_path, 0o600)
            env = os.environ.copy()
            env["SNMPCONFPATH"] = directory
            self._config_dir = directory
            self._subprocess_env = env
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def _cleanup_private_config(self) -> None:
        directory = self._config_dir
        self._config_dir = None
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        self._subprocess_env = None

    # -- command line ----------------------------------------------------
    def transport(self) -> str:
        host = self.s.host
        if ":" in host and not host.startswith("["):
            return "udp6:[%s]:%d" % (host, self.s.port)
        return "udp:%s:%d" % (host, self.s.port)

    def _argv(self, tool: str, extra: Sequence[str], oids: Iterable[str]) -> List[str]:
        s = self.s
        argv = [self._bin.get(tool, tool), "-v", s.version]
        if s.version == "3":
            argv += ["-l", s.v3_level, "-u", s.v3_user]
            if s.v3_level != "noAuthNoPriv":
                argv += ["-a", s.v3_auth_proto]
            if s.v3_level == "authPriv":
                argv += ["-x", s.v3_priv_proto]
        argv += ["-t", str(s.timeout), "-r", str(s.retries), "-On", "-Oe", "-m", ""]
        argv += list(extra)
        argv.append(self.transport())
        argv += ["." + o.lstrip(".") for o in oids]
        return argv

    # -- request execution -----------------------------------------------
    def _exec(self, tool: str, extra: Sequence[str], oids: Sequence[str],
              wall_timeout: float) -> Tuple[List[Varbind], int, str]:
        if self._stopped:
            raise SnmpError("client stopped")
        argv = self._argv(tool, extra, oids)
        self.calls += 1
        if self._trace:
            self._trace("SNMP request #%d: %s host=%s OIDs=%s" % (
                self.calls, tool, self.s.host, ",".join(oids)))
        try:
            rc, out, err = self._run(argv, wall_timeout)
        except Exception as exc:
            if self._trace:
                self._trace("SNMP request #%d failed: %s: %s" % (self.calls, type(exc).__name__, exc))
            raise
        varbinds, others = snmpparse.parse_output(out)
        message = " | ".join([l for l in (err.strip().splitlines() + others) if l.strip()])
        if self._trace:
            if varbinds:
                for vb in varbinds:
                    self._trace("SNMP response #%d: %s %s=%s" % (self.calls, vb.type, vb.oid, vb.raw or vb.value))
            if message:
                self._trace("SNMP response #%d diagnostic: %s" % (self.calls, message))
            if not varbinds and not message:
                self._trace("SNMP response #%d: empty (exit=%d)" % (self.calls, rc))
        return varbinds, rc, message

    @staticmethod
    def _raise_for(message: str, rc: int) -> None:
        low = message.lower()
        if "timeout" in low or "no response" in low:
            raise SnmpTimeout(message or "no response")
        raise SnmpError(message or "snmp tool exited with status %d" % rc)

    def _get_chunk(self, oids: Sequence[str]) -> List[Varbind]:
        wall = (self.s.timeout * (self.s.retries + 1)) + 10
        varbinds, rc, message = self._exec("snmpget", (), oids, wall)
        if rc != 0 and not varbinds:
            # SNMPv1 answers a missing object with noSuchName; that just means "absent".
            if "nosuchname" in message.lower():
                return []
            self._raise_for(message, rc)
        return varbinds

    # -- public API ---------------------------------------------------------
    def get_many(self, oids: Sequence[str]) -> Dict[str, Varbind]:
        """GET several scalars.  Absent objects are simply not in the result."""
        result: Dict[str, Varbind] = {}
        # Keep request order but remove duplicates before chunking. Repeated OIDs
        # waste request slots and can push otherwise compatible reads into another
        # snmpget subprocess. Normalise leading dots to match parsed response keys.
        unique_oids = []
        seen = set()
        for oid in oids:
            normalized = oid.lstrip(".")
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_oids.append(normalized)
        for i in range(0, len(unique_oids), self.GET_CHUNK):
            for vb in self._get_chunk(unique_oids[i:i + self.GET_CHUNK]):
                if not vb.missing:
                    result[vb.oid] = vb
        return result

    def get(self, oid: str) -> Optional[Varbind]:
        return self.get_many([oid]).get(oid.lstrip("."))

    def walk(self, oid: str) -> List[Varbind]:
        """Walk a subtree.  An empty/absent subtree gives an empty list."""
        base = oid.lstrip(".")
        use_bulk = self._bulk_ok
        tool = "snmpbulkwalk" if use_bulk else "snmpwalk"
        extra = ["-Cc"] + (["-Cr%d" % self.BULK_REPETITIONS] if use_bulk else [])
        varbinds, rc, message = self._exec(tool, extra, [base], self.WALK_WALL_TIMEOUT)
        if rc != 0 and use_bulk and "timeout" not in message.lower():
            # Agent without GETBULK support: fall back to plain walk, and remember.
            self._bulk_ok = False
            return self.walk(oid)
        if rc != 0 and not varbinds:
            self._raise_for(message, rc)
        prefix = base + "."
        return [vb for vb in varbinds if not vb.missing and vb.oid.startswith(prefix)]

    def table(self, base_oid: str) -> Dict[str, Dict[int, Varbind]]:
        """Walk a table entry OID and return ``{row_index: {column: Varbind}}``."""
        base = base_oid.lstrip(".")
        rows: Dict[str, Dict[int, Varbind]] = {}
        for vb in self.walk(base):
            rel = vb.oid[len(base) + 1:].split(".")
            if len(rel) < 2 or not rel[0].isdigit():
                continue
            rows.setdefault(".".join(rel[1:]), {})[int(rel[0])] = vb
        return rows
