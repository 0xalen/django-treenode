from __future__ import annotations

import threading
import time

from django.core.cache.backends.base import DEFAULT_TIMEOUT

from treenode.backends.base import _MISSING, SafeBaseCache


class SafeMemoryCache(SafeBaseCache):
    """A process-local safe cache backend for development and tests."""

    def __init__(self, location, params):
        super().__init__(location, params)
        self._entries = {}
        self._lock = threading.RLock()

    def _read(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return _MISSING
        value, expires_at = entry
        if expires_at is not None and expires_at <= time.time():
            del self._entries[key]
            return _MISSING
        return value

    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        encoded = self.codec.encode(value)
        cache_key = self._key(key, version)
        expires_at = self._expires_at(timeout)
        if self._seconds(expires_at) is not None and self._seconds(expires_at) <= 0:
            return False
        with self._lock:
            if self._read(cache_key) is not _MISSING:
                return False
            self._entries[cache_key] = (encoded, expires_at)
            return True

    def get(self, key, default=None, version=None):
        with self._lock:
            value = self._read(self._key(key, version))
        decoded = self.codec.decode(value)
        return default if decoded is _MISSING else decoded

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        encoded = self.codec.encode(value)
        expires_at = self._expires_at(timeout)
        cache_key = self._key(key, version)
        with self._lock:
            if self._seconds(expires_at) is not None and self._seconds(expires_at) <= 0:
                self._entries.pop(cache_key, None)
                return False
            self._entries[cache_key] = (encoded, expires_at)
        return True

    def touch(self, key, timeout=DEFAULT_TIMEOUT, version=None):
        expires_at = self._expires_at(timeout)
        cache_key = self._key(key, version)
        with self._lock:
            value = self._read(cache_key)
            if value is _MISSING:
                return False
            seconds = self._seconds(expires_at)
            if seconds is not None and seconds <= 0:
                del self._entries[cache_key]
                return False
            self._entries[cache_key] = (value, expires_at)
        return True

    def delete(self, key, version=None):
        with self._lock:
            return self._entries.pop(self._key(key, version), None) is not None

    def get_many(self, keys, version=None):
        result = {}
        for key in keys:
            value = self.get(key, _MISSING, version)
            if value is not _MISSING:
                result[key] = value
        return result

    def set_many(self, data, timeout=DEFAULT_TIMEOUT, version=None):
        failed = []
        for key, value in data.items():
            try:
                if not self.set(key, value, timeout, version):
                    failed.append(key)
            except TypeError:
                failed.append(key)
        return failed

    def delete_many(self, keys, version=None):
        return sum(self.delete(key, version) for key in keys)

    def clear(self):
        with self._lock:
            self._entries.clear()
        return True

    def has_key(self, key, version=None):
        return self.get(key, _MISSING, version) is not _MISSING

    def incr(self, key, delta=1, version=None):
        if type(delta) is not int:
            raise TypeError("delta must be an integer")
        cache_key = self._key(key, version)
        with self._lock:
            encoded = self._read(cache_key)
            value = self.codec.decode(encoded)
            if type(value) is not int:
                raise ValueError("key is missing or does not contain an integer")
            result = value + delta
            if not -9223372036854775808 <= result <= 9223372036854775807:
                raise ValueError("counter exceeds safe cache range")
            self._entries[cache_key] = (self.codec.encode(result), self._entries[cache_key][1])
            return result

    def decr(self, key, delta=1, version=None):
        return self.incr(key, -delta, version)

    def close(self, **kwargs):
        return None
