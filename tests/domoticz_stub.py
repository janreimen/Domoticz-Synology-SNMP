"""Minimal stand-in for Domoticz' embedded ``Domoticz`` module, for tests."""
Devices = {}
logs = []


def reset():
    Devices.clear()
    logs.clear()


def Log(text): logs.append(("log", text))
def Error(text): logs.append(("error", text))
def Debug(text): logs.append(("debug", text))
def Status(text): logs.append(("status", text))
def Debugging(flag): logs.append(("debugging", flag))
def Heartbeat(seconds): logs.append(("heartbeat", seconds))


class StubDevice:
    def __init__(self, **kw):
        self.kw = kw
        self.Name = kw["Name"]
        self.Unit = kw["Unit"]
        self.DeviceID = kw.get("DeviceID", "")
        self.TypeName = kw.get("TypeName")
        self.Options = kw.get("Options", {})
        self.Used = kw.get("Used", 0)
        self.nValue = 0
        self.sValue = ""
        self.TimedOut = 0
        self.updates = 0

    def Update(self, nValue, sValue, TimedOut=None, Options=None, **_):
        self.nValue, self.sValue = nValue, sValue
        if TimedOut is not None:
            self.TimedOut = TimedOut
        if Options is not None:                      # Domoticz replaces the whole Options dictionary
            self.Options = dict(Options)
        self.updates += 1


class Device:
    """``Domoticz.Device(...).Create()`` registers a device."""

    def __init__(self, **kw):
        self.kw = kw

    def Create(self):
        Devices[self.kw["Unit"]] = StubDevice(**self.kw)
