from __future__ import annotations

import math

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.exceptions import ImproperlyConfigured

from treenode.backends.base import _MISSING, SafeBaseCache


class SafeRedisCache(SafeBaseCache):
    def __init__(self, location, params):
        super().__init__(location, params)
        try:
            import redis
        except ImportError as exc:
            raise ImproperlyConfigured(
                "SafeRedisCache requires django-treenode[redis]"
            ) from exc
        options = params.get("OPTIONS", {}).copy()
        options.pop("NAMESPACE", None)
        options.pop("MAX_VALUE_BYTES", None)
        options.pop("MAX_VALUE_DEPTH", None)
        options.pop("MAX_VALUE_ITEMS", None)
        self._redis = redis.Redis.from_url(location, decode_responses=False, **options)
        self._response_error = redis.exceptions.ResponseError

    def _expiry(self, timeout):
        seconds = self._seconds(self._expires_at(timeout))
        if seconds is None:
            return None
        return max(0, math.ceil(seconds * 1000))

    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        encoded = self.codec.encode(value)
        expiry = self._expiry(timeout)
        if expiry == 0:
            return False
        return bool(self._redis.set(self._key(key, version), encoded, nx=True, px=expiry))

    def get(self, key, default=None, version=None):
        decoded = self.codec.decode(self._redis.get(self._key(key, version)))
        return default if decoded is _MISSING else decoded

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        encoded = self.codec.encode(value)
        expiry = self._expiry(timeout)
        cache_key = self._key(key, version)
        if expiry == 0:
            self._redis.delete(cache_key)
            return False
        return bool(self._redis.set(cache_key, encoded, px=expiry))

    def touch(self, key, timeout=DEFAULT_TIMEOUT, version=None):
        expiry = self._expiry(timeout)
        cache_key = self._key(key, version)
        if self.codec.decode(self._redis.get(cache_key)) is _MISSING:
            return False
        if expiry is None:
            self._redis.persist(cache_key)
            return True
        if expiry == 0:
            return bool(self._redis.delete(cache_key))
        return bool(self._redis.pexpire(cache_key, expiry))

    def delete(self, key, version=None):
        return bool(self._redis.delete(self._key(key, version)))

    def get_many(self, keys, version=None):
        requested = list(keys)
        cache_keys = [self._key(key, version) for key in requested]
        values = self._redis.mget(cache_keys)
        result = {}
        for key, value in zip(requested, values, strict=True):
            decoded = self.codec.decode(value)
            if decoded is not _MISSING:
                result[key] = decoded
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
        cache_keys = [self._key(key, version) for key in keys]
        return self._redis.delete(*cache_keys) if cache_keys else 0

    def clear(self):
        cursor = 0
        pattern = f"{self.namespace}:*"
        while True:
            cursor, keys = self._redis.scan(cursor=cursor, match=pattern, count=1000)
            if keys:
                self._redis.delete(*keys)
            if cursor == 0:
                return True

    def has_key(self, key, version=None):
        return self.get(key, _MISSING, version) is not _MISSING

    def incr(self, key, delta=1, version=None):
        if type(delta) is not int:
            raise TypeError("delta must be an integer")
        try:
            value = self._redis.eval(
                "local value = redis.call('GET', KEYS[1]); "
                "if not value then return false end; "
                "return redis.call('INCRBY', KEYS[1], ARGV[1])",
                1,
                self._key(key, version),
                delta,
            )
            if value is None:
                raise ValueError("key is missing or does not contain an integer")
            return value
        except self._response_error as exc:
            raise ValueError("key is missing or does not contain an integer") from exc

    def decr(self, key, delta=1, version=None):
        return self.incr(key, -delta, version)

    def close(self, **kwargs):
        self._redis.connection_pool.disconnect()
