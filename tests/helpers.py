import os, sys
sys.path[:0] = [os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), os.path.dirname(os.path.abspath(__file__))]

from synoSNMP.config import Settings
from synoSNMP.engine import Engine
from fakeclient import FakeClient
import mibdata  # noqa: F401  (re-exported: test modules do "from helpers import mibdata")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make(mib, level=3, **settings):
    clock = Clock()
    client = FakeClient(mib)
    engine = Engine(Settings(host="fake", level=level, **settings), client, clock=clock)
    return engine, client, clock


def by_key(result):
    return {r.key: r for r in result.readings}


def messages(result, level):
    return [t for l, t in result.messages if l == level]
