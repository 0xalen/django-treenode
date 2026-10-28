from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from django.core.cache.backends.base import BaseCache, DEFAULT_TIMEOUT
from django.core.exceptions import ImproperlyConfigured


_CODEC_PREFIX = b"treenode-safe-cache-v1:"
_COUNTER = re.compile(rb"-?(?:0|[1-9][0-9]*)\Z")
_NAMESPACE = re.compile(r"[A-Za-z0-9_.-]+\Z")
_MAX_INTEGER = 9223372036854775807
_MISSING = object()


class SafeValueCodec:
    def __init__(self, options: Mapping[str, Any]):
        self.max_bytes = self._limit(options, "MAX_VALUE_BYTES", 1048576)
        self.max_depth = self._limit(options, "MAX_VALUE_DEPTH", 16)
        self.max_items = self._limit(options, "MAX_VALUE_ITEMS", 10000)

    @staticmethod
    def _limit(options: Mapping[str, Any], name: str, default: int) -> int:
        value = options.get(name, default)
        if type(value) is not int or value <= 0:
            raise ImproperlyConfigured(f"{name} must be a positive integer")
        return value

    def encode(self, value: object) -> bytes:
        if type(value) is int:
            if not -_MAX_INTEGER <= value <= _MAX_INTEGER:
                raise TypeError("integer value exceeds safe cache counter range")
            return str(value).encode("ascii")
        encoded = self._encode(value, depth=0, items=[0])
        try:
            payload = json.dumps(encoded, separators=(",", ":"), ensure_ascii=True).encode(
                "ascii"
            )
        except (TypeError, ValueError) as exc:
            raise TypeError("value is not safe cache data") from exc
        data = _CODEC_PREFIX + payload
        if len(data) > self.max_bytes:
            raise TypeError("safe cache value exceeds MAX_VALUE_BYTES")
        return data

    def _encode(self, value: object, *, depth: int, items: list[int]) -> object:
        if depth > self.max_depth:
            raise TypeError("safe cache value exceeds MAX_VALUE_DEPTH")
        items[0] += 1
        if items[0] > self.max_items:
            raise TypeError("safe cache value exceeds MAX_VALUE_ITEMS")
        if value is None:
            return ["n"]
        if type(value) is bool:
            return ["b", value]
        if type(value) is int:
            if not -_MAX_INTEGER <= value <= _MAX_INTEGER:
                raise TypeError("integer value exceeds safe cache counter range")
            return ["i", value]
        if type(value) is float:
            if not math.isfinite(value):
                raise TypeError("safe cache floats must be finite")
            return ["f", value]
        if type(value) is str:
            return ["s", value]
        if type(value) is bytes:
            return ["y", base64.b64encode(value).decode("ascii")]
        if type(value) in (list, tuple):
            tag = "l" if type(value) is list else "t"
            return [tag, [self._encode(item, depth=depth + 1, items=items) for item in value]]
        if type(value) is dict:
            entries = []
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError("safe cache dictionary keys must be strings")
                entries.append([key, self._encode(item, depth=depth + 1, items=items)])
            return ["d", entries]
        raise TypeError("value is not safe cache data")

    def decode(self, data: object) -> object:
        if not isinstance(data, bytes):
            return _MISSING
        if _COUNTER.fullmatch(data):
            try:
                return int(data)
            except ValueError:
                return _MISSING
        if not data.startswith(_CODEC_PREFIX) or len(data) > self.max_bytes:
            return _MISSING
        try:
            decoded = json.loads(data[len(_CODEC_PREFIX) :].decode("ascii"))
            return self._decode(decoded, depth=0, items=[0])
        except (TypeError, ValueError, UnicodeDecodeError):
            return _MISSING

    def _decode(self, value: object, *, depth: int, items: list[int]) -> object:
        if depth > self.max_depth or type(value) is not list or not value:
            raise ValueError("invalid safe cache value")
        items[0] += 1
        if items[0] > self.max_items:
            raise ValueError("invalid safe cache value")
        tag = value[0]
        if tag == "n" and len(value) == 1:
            return None
        if tag == "b" and len(value) == 2 and type(value[1]) is bool:
            return value[1]
        if tag == "i" and len(value) == 2 and type(value[1]) is int:
            if -_MAX_INTEGER <= value[1] <= _MAX_INTEGER:
                return value[1]
        if tag == "f" and len(value) == 2 and type(value[1]) in (int, float):
            number = float(value[1])
            if math.isfinite(number):
                return number
        if tag == "s" and len(value) == 2 and type(value[1]) is str:
            return value[1]
        if tag == "y" and len(value) == 2 and type(value[1]) is str:
            return base64.b64decode(value[1].encode("ascii"), validate=True)
        if tag in {"l", "t"} and len(value) == 2 and type(value[1]) is list:
            values = [self._decode(item, depth=depth + 1, items=items) for item in value[1]]
            return values if tag == "l" else tuple(values)
        if tag == "d" and len(value) == 2 and type(value[1]) is list:
            result = {}
            for entry in value[1]:
                if type(entry) is not list or len(entry) != 2 or type(entry[0]) is not str:
                    raise ValueError("invalid safe cache dictionary")
                if entry[0] in result:
                    raise ValueError("duplicate safe cache dictionary key")
                result[entry[0]] = self._decode(entry[1], depth=depth + 1, items=items)
            return result
        raise ValueError("invalid safe cache value")


class SafeBaseCache(BaseCache, ABC):
    def __init__(self, location: str, params: Mapping[str, Any]):
        super().__init__(params)
        options = params.get("OPTIONS", {})
        namespace = options.get("NAMESPACE") or self.key_prefix
        if not namespace:
            namespace = f"treenode-{hashlib.sha256(location.encode()).hexdigest()[:16]}"
        if type(namespace) is not str or not _NAMESPACE.fullmatch(namespace):
            raise ImproperlyConfigured("Safe cache NAMESPACE must contain only letters, digits, _, -, or .")
        self.namespace = namespace
        self.codec = SafeValueCodec(options)
        self.location = location

    def make_key(self, key, version=None):
        return f"{self.namespace}:{super().make_key(key, version=version)}"

    def _key(self, key, version=None) -> str:
        return self.make_and_validate_key(key, version=version)

    def _expires_at(self, timeout) -> float | None:
        return self.get_backend_timeout(timeout)

    @staticmethod
    def _seconds(expires_at: float | None) -> float | None:
        if expires_at is None:
            return None
        return expires_at - time.time()

    @abstractmethod
    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        raise NotImplementedError
