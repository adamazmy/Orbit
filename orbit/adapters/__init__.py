"""The adapter registry: which adapter class handles which firm.

An explicit dict on purpose (no auto-discovery or plugins): one line per firm,
easy to read and easy to grep. Keys are firm ids from config/firms.yaml.

To add a firm, import its adapter class here and add one line, e.g.
    "wachtell": WachtellAdapter,
"""

from orbit.adapters.base import Adapter

ADAPTERS: dict[str, type[Adapter]] = {}
