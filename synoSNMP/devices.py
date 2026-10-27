"""The only module that talks to the Domoticz device API.

It turns ``Reading`` objects into Domoticz devices:

    * a device is identified by its ``DeviceID`` (= the reading key), never by unit number,
      so devices survive restarts and new hardware simply gets the next free unit
    * missing devices are created on first sight (dynamic detection becomes dynamic devices)
    * values are written when they change, or every ``refresh_interval`` seconds so the
      "last seen" time stays fresh and Domoticz' sensor time-out does not trigger
    * devices of hardware that disappeared are flagged "timed out" (red) instead of deleted; so are those of a
      collector that keeps failing, when a retry fails once its last good value is two intervals old
    * a device that was deleted in the Domoticz UI while the plugin runs is created again on the next poll
      (to get rid of one for good, clear its "Used" flag instead of deleting it)
    * the axis label of Custom sensors is owned by the plugin and brought up to date once per start
    * the plugin's own health devices (group ``plugin``) are written even while the NAS is down
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Set

from .engine import CycleResult
from .model import ALERT_DEV, CUSTOM, PERCENTAGE, TEMPERATURE, TEXT, Reading
from .util import group_of

TYPE_NAMES = {
    TEMPERATURE: "Temperature",
    PERCENTAGE: "Percentage",
    TEXT: "Text",
    ALERT_DEV: "Alert",
    CUSTOM: "Custom",
}

MAX_UNIT = 255
PLUGIN_GROUP = "plugin"      # key prefix of the plugin's own devices (health, poll duration)


class DeviceManager:
    def __init__(self, domoticz: Any, get_devices: Callable[[], Dict[int, Any]],
                 refresh_interval: int = 300, clock: Callable[[], float] = time.time) -> None:
        self.dz = domoticz
        self.get_devices = get_devices
        self.refresh_interval = refresh_interval
        self.clock = clock
        self._units: Dict[str, int] = {}          # key -> Domoticz unit
        self._written: Dict[str, float] = {}      # key -> time of last write
        self._warned_full = False
        self._synced: Set[str] = set()            # keys whose definition was checked since the start
        self.created = 0

    # -- start-up -------------------------------------------------------------------------
    def sync(self) -> None:
        """Learn which of our devices already exist (after a Domoticz restart)."""
        self._units.clear()
        for unit, dev in self.get_devices().items():
            device_id = getattr(dev, "DeviceID", "")
            if device_id and ("." in device_id or "~" in device_id):
                self._units[device_id] = unit

    # -- main entry -------------------------------------------------------------------------
    def apply(self, result: CycleResult) -> None:
        now = self.clock()
        for reading in list(result.readings) + list(result.health):
            self.dz.Debug("Reading %s [%s]: nValue=%d sValue=%r pending=%s" % (
                reading.name, reading.key, reading.nvalue, reading.svalue, reading.pending))
            if reading.pending:
                self.dz.Debug("Skipped pending reading %s [%s]" % (reading.name, reading.key))
                continue
            unit = self._unit_for(reading)
            if unit is not None:
                self._write(unit, reading, now)
        self._time_out_missing(result)

    def mark_all_timed_out(self) -> None:
        devices = self.get_devices()
        for key, unit in self._units.items():
            if group_of(key) == PLUGIN_GROUP:          # the health devices are what tells you the NAS is down
                continue
            self._set_timeout(devices.get(unit), 1)

    # -- internals --------------------------------------------------------------------------
    def _free_unit(self) -> Optional[int]:
        taken: Set[int] = set(self.get_devices().keys()) | set(self._units.values())
        for unit in range(1, MAX_UNIT + 1):
            if unit not in taken:
                return unit
        return None

    def _unit_for(self, r: Reading) -> Optional[int]:
        """The unit of this reading's device, creating the device when it does not exist (any more)."""
        unit = self._units.get(r.key)
        if unit is not None:
            dev = self.get_devices().get(unit)
            if dev is not None and getattr(dev, "DeviceID", r.key) == r.key:
                return unit
            # Deleted in the Domoticz UI while we run: forget it and make a new one instead of silently
            # never updating it again.
            del self._units[r.key]
            self._synced.discard(r.key)
            self.dz.Log("Device '%s' [%s] was removed in Domoticz; creating it again. To hide a device for good, "
                        "clear its 'Used' flag instead of deleting it." % (r.name, r.key))
        return self._create(r)

    def _create(self, r: Reading) -> Optional[int]:
        unit = self._free_unit()
        if unit is None:
            if not self._warned_full:
                self._warned_full = True
                self.dz.Error("All %d Domoticz units of this hardware are in use; new devices cannot be created. "
                              "Lower the detail level or delete unused devices." % MAX_UNIT)
            return None
        kwargs: Dict[str, Any] = dict(Name=r.name, Unit=unit, TypeName=TYPE_NAMES[r.kind],
                                      DeviceID=r.key, Used=1 if r.used else 0)
        if r.kind == CUSTOM:
            kwargs["Options"] = {"Custom": "1;%s" % r.unit}
        self.dz.Device(**kwargs).Create()
        if unit not in self.get_devices():
            self.dz.Error("Could not create device '%s' (unit %d)" % (r.name, unit))
            return None
        self._units[r.key] = unit
        self.created += 1
        self.dz.Debug("Created unit %d: %s [%s]" % (unit, r.name, r.key))
        return unit

    def _write(self, unit: int, r: Reading, now: float) -> None:
        dev = self.get_devices().get(unit)
        if dev is None:
            return
        if r.key not in self._synced:
            self._synced.add(r.key)
            self._sync_definition(dev, r)
        changed = dev.nValue != r.nvalue or dev.sValue != r.svalue
        stale = now - self._written.get(r.key, 0.0) >= self.refresh_interval
        timed_out = bool(getattr(dev, "TimedOut", 0))
        if not (changed or stale or timed_out):
            self.dz.Debug("TRACE: unchanged sensor %s [%s]; Domoticz update skipped (nValue=%d sValue=%r)" % (
                r.name, r.key, r.nvalue, r.svalue))
            return
        try:
            dev.Update(nValue=r.nvalue, sValue=r.svalue, TimedOut=0)
        except TypeError:                          # Domoticz older than the TimedOut argument
            dev.Update(nValue=r.nvalue, sValue=r.svalue)
        self._written[r.key] = now
        if changed:
            self.dz.Debug("TRACE: Domoticz update sent: %s [%s] unit=%d nValue=%d sValue=%r changed=%s refreshed=%s recovered=%s" % (
                r.name, r.key, unit, r.nvalue, r.svalue, changed, stale, timed_out))
        else:
            self.dz.Debug("TRACE: Domoticz refresh sent: %s [%s] unit=%d nValue=%d sValue=%r changed=%s refreshed=%s recovered=%s" % (
                r.name, r.key, unit, r.nvalue, r.svalue, changed, stale, timed_out))

    def _sync_definition(self, dev: Any, r: Reading) -> None:
        """Bring the parts of an existing device that the plugin owns up to date (once per start).

        Only the axis label of Custom sensors qualifies: names, visibility ("Used"), history and
        notifications stay whatever the user made them.
        """
        if r.kind != CUSTOM:
            return
        wanted = "1;%s" % r.unit
        options = dict(getattr(dev, "Options", None) or {})
        if options.get("Custom") == wanted:
            return
        options["Custom"] = wanted
        try:
            dev.Update(nValue=dev.nValue, sValue=dev.sValue, Options=options)
        except TypeError:
            return
        self.dz.Log("Device '%s' [%s]: axis label updated to %r" % (r.name, r.key, r.unit))

    def _time_out_missing(self, result: CycleResult) -> None:
        devices = self.get_devices()
        for key, unit in self._units.items():
            group = group_of(key)
            if (group in result.ran_groups and key not in result.present_keys) or group in result.stale_groups:
                self._set_timeout(devices.get(unit), 1)

    def _set_timeout(self, dev: Any, flag: int) -> None:
        if dev is None or bool(getattr(dev, "TimedOut", 0)) == bool(flag):
            return
        try:
            dev.Update(nValue=dev.nValue, sValue=dev.sValue, TimedOut=flag)
        except TypeError:
            pass
        self.dz.Debug("%s device %s: %s" % (
            "Timed out" if flag else "Recovered", getattr(dev, "Name", "unknown"),
            getattr(dev, "DeviceID", "")))
