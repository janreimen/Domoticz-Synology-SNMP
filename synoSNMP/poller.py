"""Background thread that runs the engine, so Domoticz' main plugin thread never blocks on SNMP."""
from __future__ import annotations

import queue
import threading
import time

from .engine import CycleResult, Engine


class Poller(threading.Thread):
    """Runs ``engine.cycle()`` every ``interval`` seconds and queues the results.

    The thread never touches Domoticz; the plugin's heartbeat drains ``results``.
    """

    def __init__(self, engine: Engine, interval: int) -> None:
        super().__init__(name="SynologySNMP-poller", daemon=True)
        self.engine = engine
        self.interval = interval
        self.results: "queue.Queue[CycleResult]" = queue.Queue()
        self._halt = threading.Event()        # not "_stop": Thread uses that name internally

    def run(self) -> None:
        while not self._halt.is_set():
            started = time.monotonic()
            result = self.engine.cycle()
            self.results.put(result)
            if result.fatal:
                self.engine.client.stop()
                break
            # a cycle may ask for a different pause (a NAS that is meant to be off is only probed every few minutes)
            period = result.wait if result.wait is not None else self.interval
            self._halt.wait(max(1.0, period - (time.monotonic() - started)))

    def stop(self) -> None:
        self._halt.set()
        self.engine.client.stop()
