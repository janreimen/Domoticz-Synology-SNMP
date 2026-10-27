"""The polling brain: probe, discover, collect, and degrade gracefully.

``Engine.cycle()`` is synchronous and never raises; it returns a
``CycleResult``.  It knows nothing about Domoticz or threads, so it can be
tested with a fake SNMP client.

Failure policy
    * no SNMP answer      -> the whole cycle is "unreachable" (one log line, not one per poll)
    * a collector errors  -> only that collector's devices are skipped, the rest carry on;
                             the collector is retried with an increasing delay.  Its old values are
                             not replayed (that would keep them looking fresh); the devices keep their
                             last value and are flagged timed out when a retry fails once it is two
                             intervals old
    * a collector times out while the NAS still answers a trivial request
                          -> treated like an error of that collector, not as "NAS is down"
                             (in polling and in hardware detection alike)
    * tools not installed -> fatal, polling stops with a clear message

Scheduling
    Every collector has its own interval.  The poller wakes every ``Engine.tick`` seconds (the shortest
    interval of the collectors that are built at the chosen detail level) and a collector runs when its
    interval has elapsed *since the start of the cycle in which it last ran*.  Using the cycle start for
    both stamps makes the elapsed time equal to the poller period, however long discovery or the
    collectors themselves took.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from . import oids
from .collectors import Collector, Context, build_collectors
from .config import Settings
from .model import CRIT, OK, WARN, Reading
from .snmpcli import SnmpClient, SnmpError, SnmpTimeout, SnmpUnavailable

# (level, text) with level in "debug" | "info" | "notice" | "error".
# "notice" is a recovery message: it is shown at every logging level, like an error.
Message = Tuple[str, str]

SCHEDULE_SLACK = 0.25        # seconds: timing jitter tolerated when deciding that a schedule is due
BACKOFF_BASE_MAX = 300       # first retry of a failed collector: min(its interval, this) seconds
BACKOFF_CAP_MIN = 300        # retries never wait longer than max(its interval, this) seconds


class NotSynology(SnmpError):
    """The agent answers, but does not serve the Synology MIB."""


@dataclass
class CycleResult:
    readings: List[Reading] = field(default_factory=list)
    ran_groups: Set[str] = field(default_factory=set)       # collectors that completed this cycle
    present_keys: Set[str] = field(default_factory=set)
    messages: List[Message] = field(default_factory=list)
    reachable: bool = True
    not_synology: bool = False      # agent answers but has no Synology MIB (reachable is False then too)
    fatal: bool = False
    info: Dict[str, str] = field(default_factory=dict)
    duration: float = 0.0
    snmp_calls: int = 0
    health: List[Reading] = field(default_factory=list)     # the plugin's own devices; written even when the NAS is down
    stale_groups: Set[str] = field(default_factory=set)     # failing collectors whose last good data is too old: flag their devices


class Engine:
    def __init__(self, settings: Settings, client: SnmpClient,
                 collectors: Optional[Sequence[Collector]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 trace: Optional[Callable[[str], None]] = None) -> None:
        self.settings = settings
        self.client = client
        self.clock = clock
        self._trace = trace
        self.ctx = Context(settings, clock)
        self.collectors: List[Collector] = list(collectors) if collectors is not None else build_collectors(self.ctx)
        # Each collector has its own schedule from .env. Directly-created Settings
        # objects retain the old slow_interval behavior for compatibility with tests
        # and downstream integrations that instantiate Engine themselves.
        self._reading_cache: Dict[str, List[Reading]] = {}
        configured = settings.collector_intervals
        for collector in self.collectors:
            if configured is not None and collector.name in configured:
                collector.interval = configured[collector.name]
            elif collector.name == "smart":
                collector.interval = settings.slow_interval
            elif collector.name in {"disks", "raid", "mounts", "ebox", "flashcache", "gpu"}:
                collector.interval = settings.slow_interval
        # The scheduler tick: the shortest interval among the collectors that really exist at this detail level
        # (an interval configured for a collector that is not built must not make everything poll faster).
        intervals = [c.interval for c in self.collectors if c.interval] if configured is not None else []
        self.tick: int = int(min(intervals)) if intervals else settings.interval
        self.info: Dict[str, str] = {}
        self._connected = False
        self._last_discovery: Optional[float] = None
        self._open_problems: Dict[str, str] = {}
        self._fails: Dict[str, int] = {}                  # collector name -> consecutive failures
        self._retry_at: Dict[str, float] = {}             # collector name -> do not run before (cycle-start clock)
        self._failing_since: Dict[str, float] = {}        # collector name -> cycle start of its first failure in a row

    # -- public ---------------------------------------------------------------------
    def cycle(self, force: bool = False) -> CycleResult:
        """Run one poll cycle.  ``force`` runs every collector now, ignoring schedules and back-off (CLI)."""
        started = self.clock()
        calls_before = self.client.calls
        result = CycleResult()
        self._log_trace("poll cycle started for %s:%d%s" % (
            self.settings.host, self.settings.port, " (forced: all collectors)" if force else ""))
        try:
            self._log_trace("step: connect / verify Synology identity")
            self._connect(result)
            self._log_trace("step: hardware discovery")
            self._maybe_discover(result, started)
            self._log_trace("step: collect readings")
            self._collect(result, started, force)
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
            if isinstance(exc, NotSynology):
                result.reachable = False
                result.not_synology = True
        except Exception as exc:                          # a bug must never kill the poller thread
            self._problem(result, "engine", "Unexpected error: %s: %s" % (type(exc).__name__, exc))
        if not result.reachable:
            # Never replay cached or partial readings after a transport outage: the
            # plugin marks all devices timed out, and applying stale readings after
            # that would immediately mark some of them healthy again.
            if result.readings:
                self._log_trace("discarding %d partial/cached readings because NAS is unreachable" % len(result.readings))
            result.readings.clear()
            result.present_keys.clear()
            result.ran_groups.clear()
            result.stale_groups.clear()
        result.info = dict(self.info)
        # present_keys intentionally contains only fresh readings from collectors
        # that ran successfully. Cached readings refresh Domoticz values, but must
        # not hide a device that disappeared when its owning collector next runs.
        result.duration = self.clock() - started
        result.snmp_calls = self.client.calls - calls_before
        self._drain_notes(result)
        self._build_health(result)
        self._log_trace("poll cycle finished: reachable=%s fatal=%s readings=%d groups=%s SNMP calls=%d duration=%.3fs" % (
            result.reachable, result.fatal, len(result.readings),
            ",".join(sorted(result.ran_groups)) or "none", result.snmp_calls, result.duration))
        return result

    def request_discovery(self) -> None:
        self._last_discovery = None

    # -- steps ------------------------------------------------------------------------
    def _connect(self, result: CycleResult) -> None:
        if self._connected:
            self._log_trace("connection check skipped: session already marked connected")
            return
        self._log_trace("requesting system description, model, DSM version and serial OIDs")
        got = self.client.get_many([oids.SYS_DESCR, oids.SYS_MODEL, oids.SYS_VERSION, oids.SYS_SERIAL])
        self._log_trace("identity request returned %d OID values" % len(got))
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
        connected_message = "Connected to Synology %s%s" % (
            self.info["model"], (" (%s)" % self.info["version"]) if self.info["version"] else "")
        result.messages.append(("info", connected_message))
        # Connection identity is especially useful during initial diagnosis. Keep
        # it in Trace output as well as the concise operational info message.
        self._log_trace(connected_message)
        serial = got.get(oids.SYS_SERIAL)
        if serial:
            result.messages.append(("debug", "Serial number: %s" % serial.as_text()))

    def _maybe_discover(self, result: CycleResult, now: float) -> None:
        due = self._last_discovery is None or now - self._last_discovery >= self.settings.discovery_interval - SCHEDULE_SLACK
        if not due:
            return
        self._last_discovery = now
        self._log_trace("discovery due (first run or interval %.0fs elapsed)" % self.settings.discovery_interval)
        for c in self.collectors:
            self._log_trace("discovery start: collector=%s groups=%s" % (c.name, ",".join(c.groups) or "none"))
            try:
                c.discover(self.client)
                self._log_trace("discovery complete: collector=%s" % c.name)
            except SnmpUnavailable:
                self._log_trace("discovery aborted by SNMP tools missing: collector=%s" % c.name)
                raise
            except SnmpTimeout as exc:
                # Same rule as for collect(): one table that does not answer is not an outage while the NAS does.
                if not self._nas_answers():
                    self._log_trace("discovery aborted by SNMP transport failure: collector=%s" % c.name)
                    raise
                self._log_trace("discovery timeout while the NAS answers: collector=%s: %s" % (c.name, exc))
                result.messages.append(("debug", "%s discovery failed: %s" % (c.name, exc)))
            except Exception as exc:                       # collect() will retry discovery lazily
                self._log_trace("discovery exception: collector=%s error=%s: %s" % (c.name, type(exc).__name__, exc))
                result.messages.append(("debug", "%s discovery failed: %s" % (c.name, exc)))

    def _collect(self, result: CycleResult, now: float, force: bool = False) -> None:
        for c in self.collectors:
            cached = self._reading_cache.get(c.name, [])
            retry_at = self._retry_at.get(c.name)
            if not force and retry_at is not None and now < retry_at - SCHEDULE_SLACK:
                self._log_trace("collector skipped: %s is backing off after %d failure(s); next attempt in %.0fs" % (
                    c.name, self._fails.get(c.name, 0), retry_at - now))
                continue
            due_in = (now - c.last_run) if c.last_run is not None else None
            if not force and c.interval and due_in is not None and due_in < c.interval - SCHEDULE_SLACK:
                self._log_trace("collector skipped: %s schedule not due (%.1fs / %.1fs); reusing %d cached readings" % (
                    c.name, due_in, c.interval, len(cached)))
                result.readings.extend(cached)
                continue
            self._log_trace("collector start: %s" % c.name)
            try:
                readings = c.collect(self.client)
            except SnmpUnavailable:
                self._log_trace("collector aborted by SNMP tools missing: %s" % c.name)
                raise
            except SnmpTimeout as exc:
                if not self._nas_answers():
                    self._log_trace("collector aborted by SNMP transport failure: %s" % c.name)
                    raise
                self._log_trace("collector timeout while the NAS answers: %s: %s" % (c.name, exc))
                self._collector_failed(c, result, now, "%s: no answer within the SNMP timeout, although the NAS "
                                       "answers other requests (will retry with increasing delay)" % c.name)
                continue
            except SnmpError as exc:
                self._log_trace("collector SNMP exception: %s: %s" % (c.name, exc))
                self._collector_failed(c, result, now, "%s: SNMP error: %s" % (c.name, exc))
                continue
            except Exception as exc:
                self._log_trace("collector exception: %s: %s: %s" % (c.name, type(exc).__name__, exc))
                self._collector_failed(c, result, now, "%s: unexpected error: %s: %s" % (c.name, type(exc).__name__, exc))
                continue
            c.last_run = now
            self._fails.pop(c.name, None)
            self._retry_at.pop(c.name, None)
            self._failing_since.pop(c.name, None)
            self._reading_cache[c.name] = list(readings)
            self._log_trace("collector complete: %s readings=%d groups=%s" % (
                c.name, len(readings), ",".join(c.groups) or "none"))
            # Full Trace must show the actual sensor values produced by each
            # collector, not only the lower-level SNMP responses.
            for reading in readings:
                self._log_trace(
                    "sensor collected: name=%r key=%s kind=%s nValue=%d sValue=%r unit=%r used=%s pending=%s" % (
                        reading.name, reading.key, reading.kind, reading.nvalue,
                        reading.svalue, reading.unit, reading.used, reading.pending))
                result.present_keys.add(reading.key)
            self._resolved(c.name, result, "%s: working again" % c.name)
            result.readings.extend(readings)
            result.ran_groups.update(c.groups)
            self._drain_notes(result)

    # -- collector failures --------------------------------------------------------------
    def _nas_answers(self) -> bool:
        """After a timeout inside one collector: does the NAS still answer a trivial request?"""
        self._log_trace("probing the NAS after a collector timeout")
        try:
            return oids.SYS_MODEL in self.client.get_many([oids.SYS_MODEL])
        except SnmpUnavailable:
            raise
        except SnmpError:
            return False

    def _backoff(self, collector: Collector, failures: int) -> float:
        """Delay before a failed collector is tried again: its interval (at most 5 min), doubling, capped."""
        period = collector.interval or self.tick
        base = min(period, BACKOFF_BASE_MAX)
        cap = max(period, BACKOFF_CAP_MIN)
        return float(min(base * (2 ** min(failures - 1, 10)), cap))

    def _collector_failed(self, c: Collector, result: CycleResult, now: float, text: str) -> None:
        failures = self._fails.get(c.name, 0) + 1
        self._fails[c.name] = failures
        self._failing_since.setdefault(c.name, now)
        delay = self._backoff(c, failures)
        self._retry_at[c.name] = now + delay
        self._log_trace("collector %s failed %d time(s) in a row; next attempt in %.0fs" % (c.name, failures, delay))
        self._problem(result, c.name, text)
        self._flag_if_overdue(c, result, now)

    def _flag_if_overdue(self, c: Collector, result: CycleResult, now: float) -> None:
        """A failing collector's devices keep their last value.  When a retry fails and that value is older than
        two of the collector's intervals (at least two ticks), they are reported as stale and the device layer
        flags them timed out.  Judged only at real attempts, so waiting out a back-off never flips a device."""
        since = c.last_run if c.last_run is not None else self._failing_since.get(c.name, now)
        if now - since > 2 * max(c.interval or 0, self.tick) + SCHEDULE_SLACK:
            result.stale_groups.update(c.groups)
            self._log_trace("collector %s: last good data is %.0fs old, its devices are flagged as timed out" % (c.name, now - since))

    # -- log de-duplication: report a problem once, and its recovery once -------------
    def _problem(self, result: CycleResult, key: str, text: str) -> None:
        if self._open_problems.get(key) != text:
            self._open_problems[key] = text
            result.messages.append(("error", text))

    def _resolved(self, result_key: str, result: CycleResult, text: Optional[str]) -> None:
        if self._open_problems.pop(result_key, None) is not None and text:
            result.messages.append(("notice", text))

    def _lost(self, result: CycleResult, text: str) -> None:
        result.reachable = False
        if self._connected:
            self.ctx.rates.forget_all()
        self._connected = False
        self._problem(result, "link", text)

    # -- the plugin's own devices ---------------------------------------------------------
    def _build_health(self, result: CycleResult) -> None:
        failing = [c.name for c in self.collectors if c.name in self._fails]
        if result.fatal:
            level, text = CRIT, "Polling stopped: " + _first_line(_last_error(result))
        elif result.not_synology:
            level, text = CRIT, "Not a Synology: the Synology MIB is not served"
        elif not result.reachable:
            level, text = CRIT, "NAS not answering (no SNMP response)"
        elif "snmp" in self._open_problems or "engine" in self._open_problems:
            level, text = CRIT, _first_line(self._open_problems.get("snmp") or self._open_problems["engine"])
        elif failing:
            level, text = WARN, "%d of %d collectors failing: %s" % (len(failing), len(self.collectors), ", ".join(failing))
        else:
            level, text = OK, "%d collectors OK" % len(self.collectors)
        text += " | %d SNMP calls | %.1f s" % (result.snmp_calls, result.duration)
        result.health = [
            Reading.alert("plugin.health", "Plugin Health", level, text),
            Reading.custom("plugin.pollsecs", "Poll Duration", result.duration, "s", 2, used=False),
        ]

    def _log_trace(self, text: str) -> None:
        if self._trace is not None:
            self._trace(text)

    def _drain_notes(self, result: CycleResult) -> None:
        for note in self.ctx.notes:
            result.messages.append(("debug", note))
        self.ctx.notes.clear()


def _first_line(text: str, limit: int = 160) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= limit else line[: limit - 3] + "..."


def _last_error(result: CycleResult) -> str:
    errors = [t for level, t in result.messages if level == "error"]
    return errors[-1] if errors else "unknown error"
