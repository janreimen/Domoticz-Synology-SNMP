"""The polling brain: probe, discover, collect, and degrade gracefully.

``Engine.cycle()`` is synchronous and never raises; it returns a
``CycleResult``.  It knows nothing about Domoticz or threads, so it can be
tested with a fake SNMP client.

Failure policy
    * no SNMP answer      -> the whole cycle is "unreachable" (one log line, not one per poll)
    * a collector errors  -> only that collector's devices are skipped, the rest carry on
    * tools not installed -> fatal, polling stops with a clear message
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from . import oids
from .collectors import Collector, Context, build_collectors
from .config import Settings
from .model import Reading
from .snmpcli import SnmpClient, SnmpError, SnmpTimeout, SnmpUnavailable

Message = Tuple[str, str]      # (level, text) with level in "debug" | "info" | "error"


class NotSynology(SnmpError):
    """The agent answers, but does not serve the Synology MIB."""


@dataclass
class CycleResult:
    readings: List[Reading] = field(default_factory=list)
    ran_groups: Set[str] = field(default_factory=set)       # collectors that completed this cycle
    present_keys: Set[str] = field(default_factory=set)
    messages: List[Message] = field(default_factory=list)
    reachable: bool = True
    fatal: bool = False
    info: Dict[str, str] = field(default_factory=dict)
    duration: float = 0.0
    snmp_calls: int = 0


class Engine:
    def __init__(self, settings: Settings, client: SnmpClient,
                 collectors: Optional[Sequence[Collector]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.client = client
        self.clock = clock
        self.ctx = Context(settings, clock)
        self.collectors: List[Collector] = list(collectors) if collectors is not None else build_collectors(self.ctx)
        self.info: Dict[str, str] = {}
        self._connected = False
        self._last_discovery: Optional[float] = None
        self._open_problems: Dict[str, str] = {}

    # -- public ---------------------------------------------------------------------
    def cycle(self) -> CycleResult:
        started = self.clock()
        calls_before = self.client.calls
        result = CycleResult()
        try:
            self._connect(result)
            self._maybe_discover(result)
            self._collect(result)
        except SnmpUnavailable as exc:
            result.fatal = True
            result.reachable = False
            result.messages.append(("error", str(exc)))
        except SnmpTimeout as exc:
            self._lost(result, "No SNMP answer from %s:%d (%s). Check address, community/credentials, "
                       "that SNMP is enabled in DSM and that this host is allowed to query it."
                       % (self.settings.host, self.settings.port, exc))
        except SnmpError as exc:
            self._problem(result, "snmp", "SNMP request failed: %s" % exc)
            result.reachable = not isinstance(exc, NotSynology)
        except Exception as exc:                          # a bug must never kill the poller thread
            self._problem(result, "engine", "Unexpected error: %s: %s" % (type(exc).__name__, exc))
        result.info = dict(self.info)
        result.present_keys = {r.key for r in result.readings}
        result.duration = self.clock() - started
        result.snmp_calls = self.client.calls - calls_before
        self._drain_notes(result)
        return result

    def request_discovery(self) -> None:
        self._last_discovery = None

    # -- steps ------------------------------------------------------------------------
    def _connect(self, result: CycleResult) -> None:
        if self._connected:
            return
        got = self.client.get_many([oids.SYS_DESCR, oids.SYS_MODEL, oids.SYS_VERSION, oids.SYS_SERIAL])
        if not got:
            raise SnmpError("the agent answered but returned no data (SNMP view too restrictive?)")
        model = got.get(oids.SYS_MODEL)
        if model is None:
            raise NotSynology("%s does not serve the Synology MIB (1.3.6.1.4.1.6574). Is this a Synology NAS "
                              "with SNMP enabled in DSM?" % self.settings.host)
        version = got.get(oids.SYS_VERSION)
        self.info = {"model": model.as_text(), "version": version.as_text() if version else ""}
        self._connected = True
        self._last_discovery = None
        self._resolved("link", result, "SNMP connection restored")
        self._resolved("snmp", result, None)
        self._resolved("engine", result, None)
        result.messages.append(("info", "Connected to Synology %s%s" % (
            self.info["model"], (" (%s)" % self.info["version"]) if self.info["version"] else "")))
        serial = got.get(oids.SYS_SERIAL)
        if serial:
            result.messages.append(("debug", "Serial number: %s" % serial.as_text()))

    def _maybe_discover(self, result: CycleResult) -> None:
        now = self.clock()
        due = self._last_discovery is None or now - self._last_discovery >= self.settings.discovery_interval
        if not due:
            return
        self._last_discovery = now
        for c in self.collectors:
            try:
                c.discover(self.client)
            except (SnmpTimeout, SnmpUnavailable):
                raise
            except Exception as exc:                       # collect() will retry discovery lazily
                result.messages.append(("debug", "%s discovery failed: %s" % (c.name, exc)))

    def _collect(self, result: CycleResult) -> None:
        now = self.clock()
        for c in self.collectors:
            if c.interval and c.last_run is not None and now - c.last_run < c.interval:
                continue
            try:
                readings = c.collect(self.client)
            except (SnmpTimeout, SnmpUnavailable):
                raise
            except SnmpError as exc:
                self._problem(result, c.name, "%s: SNMP error: %s" % (c.name, exc))
                continue
            except Exception as exc:
                self._problem(result, c.name, "%s: unexpected error: %s: %s" % (c.name, type(exc).__name__, exc))
                continue
            c.last_run = now
            self._resolved(c.name, result, "%s: working again" % c.name)
            result.readings.extend(readings)
            result.ran_groups.update(c.groups)
            self._drain_notes(result)

    # -- log de-duplication: report a problem once, and its recovery once -------------
    def _problem(self, result: CycleResult, key: str, text: str) -> None:
        if self._open_problems.get(key) != text:
            self._open_problems[key] = text
            result.messages.append(("error", text))

    def _resolved(self, result_key: str, result: CycleResult, text: Optional[str]) -> None:
        if self._open_problems.pop(result_key, None) is not None and text:
            result.messages.append(("info", text))

    def _lost(self, result: CycleResult, text: str) -> None:
        result.reachable = False
        if self._connected:
            self.ctx.rates.forget_all()
        self._connected = False
        self._problem(result, "link", text)

    def _drain_notes(self, result: CycleResult) -> None:
        for note in self.ctx.notes:
            result.messages.append(("debug", note))
        self.ctx.notes.clear()
