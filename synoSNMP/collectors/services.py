"""Logged-in users per service (Synology Services MIB): HTTP, CIFS, AFP, NFS, FTP, SFTP, SSH, ..."""
from __future__ import annotations

from typing import List

from .. import oids
from ..config import LEVEL_FULL
from ..model import Reading
from ..snmpcli import SnmpClient
from ..util import make_key, slug
from .base import Collector, cint, ctext, natural_key, unique_slug


class ServicesCollector(Collector):
    name = "services"
    groups = ("svc",)
    min_level = LEVEL_FULL

    def collect(self, client: SnmpClient) -> List[Reading]:
        rows = client.table(oids.SERVICES_TABLE)
        out: List[Reading] = []
        taken: set = set()
        for index in sorted(rows, key=natural_key):
            row = rows[index]
            name, users = ctext(row, 2), cint(row, 3)
            if not name or users is None:
                continue
            s = unique_slug(slug(name), taken, index)
            out.append(Reading.custom(make_key("svc", s, "users"), "%s Users" % name, float(users), "users", 0, used=False))
        return out
