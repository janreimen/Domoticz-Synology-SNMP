"""Collectors: one module per hardware area, all sharing the ``Collector`` interface."""
from __future__ import annotations

from typing import List

from .base import Collector, Context
from .diskio import DiskIoCollector
from .disks import DiskCollector
from .extras import EboxCollector, FlashCacheCollector, GpuCollector
from .network import NetworkCollector
from .resources import ResourceCollector
from .services import ServicesCollector
from .smart import SmartCollector
from .storage import MountCollector, RaidCollector
from .system import SystemCollector
from .ups import UpsCollector

#: Order matters only for log readability.
COLLECTOR_CLASSES = (
    SystemCollector, ResourceCollector, DiskCollector, RaidCollector, MountCollector,
    NetworkCollector, UpsCollector, EboxCollector,
    DiskIoCollector, SmartCollector, ServicesCollector, FlashCacheCollector, GpuCollector,
)


def build_collectors(ctx: Context) -> List[Collector]:
    """Instantiate every collector enabled at the configured detail level."""
    return [cls(ctx) for cls in COLLECTOR_CLASSES if cls.min_level <= ctx.level]


__all__ = ["Collector", "Context", "build_collectors", "COLLECTOR_CLASSES"]
