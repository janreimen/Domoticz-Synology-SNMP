"""
<plugin key="SynologySNMP" name="Synology NAS (net-snmp)" author="janreimen" version="0.2.1">
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
        <h3>SNMPv3</h3>
        Fill in Username and Password (authentication passphrase, min. 8 characters). "SNMPv3 options" is
        <tt>level;authProtocol;privProtocol;privPassphrase</tt>, for example <tt>authPriv;SHA;AES;</tt>.
        An empty privacy passphrase re-uses the authentication passphrase.
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
        <param field="Mode3" label="SNMPv3 options" width="250px" default="authPriv;SHA;AES;"/>
        <param field="Mode4" label="Poll interval (seconds)" width="75px" required="true" default="60"/>
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
from synoSNMP.snmpcli import SnmpClient, SnmpUnavailable  # noqa: E402

HEARTBEAT_SECONDS = 5
STOP_TIMEOUT = 15


class BasePlugin:
    def __init__(self):
        self.poller = None
        self.manager = None
        self.settings = None

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
        if self.settings.log_level >= 2:
            Domoticz.Log("Synology SNMP plugin %s starting for %s (SNMP v%s, every %ds, detail level %d, logging %s)" % (
                __version__, self.settings.host, self.settings.version, self.settings.interval, self.settings.level,
                ("Debug" if self.settings.log_level == 2 else "Trace")))
        else:
            Domoticz.Log("Synology SNMP plugin %s starting" % __version__)

        try:
            client = SnmpClient(self.settings, trace=self._trace if self.settings.log_level >= 3 else None)
        except SnmpUnavailable as exc:
            Domoticz.Error(str(exc))
            return

        self.manager = DeviceManager(Domoticz, lambda: Devices, self.settings.refresh_interval)
        self.manager.sync()
        if self.settings.log_level >= 3:
            self._trace("device manager synchronized: %d existing device units" % len(Devices))
        self.poller = Poller(Engine(self.settings, client,
                                    trace=self._trace if self.settings.log_level >= 3 else None),
                             self.settings.interval)
        self.poller.start()
        Domoticz.Heartbeat(HEARTBEAT_SECONDS)

    def onHeartbeat(self):
        if self.poller is None:
            return
        if self.settings and self.settings.log_level >= 3:
            Domoticz.Debug("TRACE: Domoticz heartbeat received; poll interval=%ds" % self.settings.interval)
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
        if not latest.reachable:
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
        Domoticz.Log("Synology SNMP plugin stopped")

    def _trace(self, text):
        # Trace output is deliberately routed through one gate. Never pass argv or
        # private SNMP configuration content here: it can contain authentication data.
        if self.settings and self.settings.log_level >= 3:
            Domoticz.Debug("TRACE: " + text)

    def _log(self, result):
        level_setting = self.settings.log_level if self.settings else 1
        for level, text in result.messages:
            is_timeout = "timeout" in text.lower() or "no snmp answer" in text.lower() or "no response" in text.lower()
            if level == "error":
                # Errors remain visible at every level; timeouts are the only routine
                # polling messages allowed at Normal level.
                if level_setting >= 2 or is_timeout or result.fatal:
                    Domoticz.Error(text)
            elif level == "info":
                if level_setting >= 2:
                    Domoticz.Log(text)
            elif level_setting >= 3:
                Domoticz.Debug("TRACE: " + text)
        if level_setting >= 2:
            next_poll = max(1, int(round(self.settings.interval - result.duration)))
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
