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
                break
            self._halt.wait(max(1.0, self.interval - (time.monotonic() - started)))

    def stop(self) -> None:
        self._halt.set()
        self.engine.client.stop()
