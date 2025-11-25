from collections import OrderedDict
from threading import Lock
from typing import Optional


class LRUCache:
    """Simple threadsafe LRU cache for per-project Q/A memoization."""

    def __init__(self, capacity: int = 256):
        self.capacity = capacity
        self._data: OrderedDict[str, str] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            if len(self._data) > self.capacity:
                self._data.popitem(last=False)


cache = LRUCache()
