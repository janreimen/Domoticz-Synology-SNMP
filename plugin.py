"""
<plugin key="SynologySNMP" name="Synology NAS (net-snmp)" author="janreimen" version="0.2.4">
    <description>
        <h2>Synology NAS monitor</h2><br/>
        Reads a Synology NAS through the native net-snmp command line tools (snmpget / snmpbulkwalk).
        No Python SNMP library is needed. The installed hardware is detected automatically:
        drives, RAID/volumes, mounted volumes, network ports, UPS, fans, temperatures, SMART,
        CPU/memory/swap/load and DSM version are turned into Domoticz devices.<br/><br/>
        <h3>Prerequisites</h3>
        <ul>
            <li>On the Domoticz host: <tt>sudo apt install snmp</tt></li>
            <li>On the NAS: DSM &gt; Control Panel &gt; Terminal &amp; SNMP &gt; enable SNMP (v1/v2c community or v3 user)</li>
        </ul>
        <h3>Polling intervals and tuning</h3>
        Polling intervals (and the SNMP timeout / retries) are set in the plugin directory's <tt>.env</tt> file. Copy <tt>.env.sample</tt> to <tt>.env</tt>; interval values are in seconds.
        A NAS that is switched off on a schedule (for example at night): set <tt>EXPECTED_OFFLINE</tt> there, so that its silence is not logged as an error.
        <h3>SNMPv3</h3>
        Fill in Username and Password (authentication passphrase, min. 8 characters). "SNMPv3 options" is
        <tt>level;authProtocol;privProtocol</tt>, for example <tt>authPriv;SHA;AES</tt>. Enter the privacy passphrase in its own
        password field; leave it empty to re-use the authentication passphrase.
    </description>
    <params>
        <param field="Address" label="NAS address" width="250px" required="true" default="192.168.1.1"/>
        <param field="Port" label="SNMP port" width="60px" required="true" default="161"/>
        <param field="Mode2" label="SNMP version" width="100px">
            <options>
                <option label="v2c" value="2c" default="true"/>
                <option label="v1" value="1"/>
                <option label="v3" value="3"/>
            </options>
        </param>
        <param field="Mode1" label="Community (v1/v2c)" width="250px" default="public"/>
        <param field="Username" label="SNMPv3 user" width="250px"/>
        <param field="Password" label="SNMPv3 passphrase" width="250px" password="true"/>
        <param field="Mode3" label="SNMPv3 options" width="250px" default="authPriv;SHA;AES"/>
        <param field="Mode4" label="SNMPv3 privacy passphrase" width="250px" password="true"/>
        <param field="Mode5" label="Detail level" width="150px">
            <options>
                <option label="Basic" value="1"/>
                <option label="Standard" value="2"/>
                <option label="Full" value="3" default="true"/>
            </options>
        </param>
        <param field="Mode6" label="Logging level" width="100px">
            <options>
                <option label="Normal" value="Normal" default="true"/>
                <option label="Debug" value="Debug"/>
                <option label="Trace" value="Trace"/>
            </options>
        </param>
    </params>
</plugin>
"""
import os
import queue
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import Domoticz  # noqa: E402  (provided by Domoticz)

# Domoticz injects the globals Parameters, Devices, Images and Settings into this module (and replaces
# them before every callback).  Never import anything under one of those names: `Settings` once did,
# and the injected dict silently shadowed the class.  The config module is bound under a different name.
from synoSNMP import __version__  # noqa: E402
from synoSNMP import config as syno_config  # noqa: E402
from synoSNMP.devices import DeviceManager  # noqa: E402
from synoSNMP.engine import Engine  # noqa: E402
from synoSNMP.poller import Poller  # noqa: E402
from synoSNMP.snmpcli import SnmpClient, SnmpError, SnmpUnavailable  # noqa: E402

HEARTBEAT_SECONDS = 5
STOP_TIMEOUT = 15


class BasePlugin:
    def __init__(self):
        self.poller = None
        self.manager = None
        self.settings = None
        self._client = None
        self._tick = 60
        # Engine/SNMP tracing runs on the poller thread. Queue it and emit it from
        # Domoticz callbacks instead of calling Domoticz APIs from a worker thread.
        self._trace_queue = queue.Queue()

    # -- Domoticz callbacks ------------------------------------------------------------
    def onStart(self):
        try:
            self.settings = syno_config.Settings.from_parameters(Parameters)
        except syno_config.SettingsError as exc:
            Domoticz.Error("Configuration error: %s" % exc)
            return
        # Domoticz.Debug output is reserved for Trace, so changing from Trace
        # back to Normal/Debug cannot leave per-device messages enabled.
        Domoticz.Debugging(1 if self.settings.log_level >= 3 else 0)
        Domoticz.Log("Synology SNMP plugin %s starting" % __version__)
        for warning in self.settings.warnings:
            Domoticz.Log("Configuration warning: %s" % warning)
        window = self.settings.offline_window
        if window is not None:
            Domoticz.Log("%s is switched off on schedule (%s, EXPECTED_OFFLINE): its silence is not an error, until %s" % (
                self.settings.host, window.label, window.deadline_label))

        try:
            client = SnmpClient(self.settings, trace=self._trace if self.settings.log_level >= 3 else None)
        except SnmpUnavailable as exc:
            Domoticz.Error(str(exc))
            return
        except (SnmpError, OSError) as exc:           # e.g. a control character in a credential, no usable temp directory
            Domoticz.Error("Cannot prepare the SNMP client: %s" % exc)
            return
        self._client = client

        try:
            self.manager = DeviceManager(Domoticz, lambda: Devices, self.settings.refresh_interval)
            self.manager.sync()
            if self.settings.log_level >= 3:
                self._trace("device manager synchronized: %d existing device units" % len(Devices))
            engine = Engine(self.settings, client, trace=self._trace if self.settings.log_level >= 3 else None)
            self._tick = engine.tick
            self.poller = Poller(engine, engine.tick)
            self.poller.start()
        except Exception as exc:                      # never leave the private SNMP configuration behind
            client.stop()
            self._client = None
            self.manager = None
            self.poller = None
            Domoticz.Error("Could not start polling: %s: %s" % (type(exc).__name__, exc))
            return
        if self.settings.log_level >= 2:
            source = (".env (%s)" % self.settings.env_file) if self.settings.env_file else "built-in defaults (no .env found)"
            Domoticz.Log("%s: SNMP v%s, detail level %d, logging %s, schedule from %s: scheduler tick=%ds, %d collector intervals" % (
                self.settings.host, self.settings.version, self.settings.level,
                ("Debug" if self.settings.log_level == 2 else "Trace"), source, self._tick,
                len(self.settings.collector_intervals or {})))
        Domoticz.Heartbeat(HEARTBEAT_SECONDS)

    def onHeartbeat(self):
        # Drain trace events first, even if the poll cycle has not completed yet.
        # This makes slow SNMP requests visible while they are in progress.
        if self.settings and self.settings.log_level >= 3:
            while True:
                try:
                    message = self._trace_queue.get_nowait()
                except queue.Empty:
                    break
                Domoticz.Debug("TRACE: " + message)
        if self.poller is None or self.manager is None:
            return
        if self.settings and self.settings.log_level >= 3:
            Domoticz.Debug("TRACE: Domoticz heartbeat received; poll interval=%ds" % self._tick)
        latest = None
        while True:
            try:
                result = self.poller.results.get_nowait()
            except Exception:
                break
            self._log(result)
            latest = result
        if latest is None:
            if self.settings and self.settings.log_level >= 3:
                Domoticz.Debug("TRACE: no completed poll result queued on this heartbeat")
            return
        if self.settings and self.settings.log_level >= 3:
            Domoticz.Debug("TRACE: applying poll result: reachable=%s fatal=%s readings=%d present_keys=%d ran_groups=%s" % (
                latest.reachable, latest.fatal, len(latest.readings), len(latest.present_keys),
                ",".join(sorted(latest.ran_groups)) or "none"))
        if not latest.reachable and not latest.expected_off:
            # A NAS that is meant to be off keeps its last values: flagging everything red every night is noise.
            self.manager.mark_all_timed_out()
        self.manager.apply(latest)
        if latest.fatal:
            Domoticz.Error("Polling stopped. Fix the problem above and disable/enable the hardware.")
            self.poller = None

    def onStop(self):
        if self.poller is not None:
            if self.settings and self.settings.log_level >= 3:
                self._trace("stop requested; stopping SNMP poller and cleaning private configuration")
            self.poller.stop()
            self.poller.join(STOP_TIMEOUT)
            if self.poller.is_alive():
                Domoticz.Error("SNMP poller thread did not stop within %ds" % STOP_TIMEOUT)
            self.poller = None
        if self._client is not None:
            self._client.stop()                 # idempotent: removes the private SNMP configuration
            self._client = None
        Domoticz.Log("Synology SNMP plugin stopped")

    def _trace(self, text):
        # Engine and SNMP tracing runs on the poller thread. Do not call Domoticz
        # APIs from that thread: queue messages for onHeartbeat() to emit safely.
        # Never pass argv or private SNMP configuration content here.
        if self.settings and self.settings.log_level >= 3:
            self._trace_queue.put(text)

    def _log(self, result):
        level_setting = self.settings.log_level if self.settings else 1
        for level, text in result.messages:
            if level == "error":
                # The engine reports each problem once, so every error is shown at every logging level.
                Domoticz.Error(text)
            elif level == "notice":
                Domoticz.Log(text)                      # a recovery: the counterpart of an error above
            elif level_setting >= 2:                    # "info" and "debug": identity, serial, detection notes
                Domoticz.Log(text)
        if level_setting >= 2:
            period = result.wait if result.wait is not None else self._tick
            next_poll = max(1, int(round(period - result.duration)))
            Domoticz.Log("Poll complete: %d readings, %d SNMP calls, %.2fs; next poll in about %ds" % (
                len(result.readings), result.snmp_calls, result.duration, next_poll))
        if level_setting >= 3:
            Domoticz.Debug("TRACE: poll result contains %d readings" % len(result.readings))


_plugin = BasePlugin()


def onStart():
    _plugin.onStart()


def onStop():
    _plugin.onStop()


def onHeartbeat():
    _plugin.onHeartbeat()
