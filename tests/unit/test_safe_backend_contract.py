import pickle
import unittest
from unittest.mock import patch

from treenode.backends.base import _MISSING, SafeValueCodec
from treenode.backends.redis import SafeRedisCache


class SafeBackendContractTests(unittest.TestCase):
    def test_safe_backend_rejects_arbitrary_object_and_bounds(self):
        codec = SafeValueCodec({"MAX_VALUE_BYTES": 32, "MAX_VALUE_DEPTH": 1, "MAX_VALUE_ITEMS": 2})
        with self.assertRaises(TypeError):
            codec.encode(object())
        with self.assertRaises(TypeError):
            codec.encode({"nested": {"too": "deep"}})
        with self.assertRaises(TypeError):
            codec.encode([1, 2, 3])
        with self.assertRaises(TypeError):
            codec.encode("x" * 64)

    def test_no_pickle_decoder_entrypoint(self):
        codec = SafeValueCodec({})
        with patch.object(pickle, "loads", side_effect=AssertionError("pickle executed")) as loads:
            self.assertIs(codec.decode(b"cos\nsystem\n(S'boom'\ntR."), _MISSING)
        loads.assert_not_called()

    def test_redis_tls_configuration_is_forwarded_without_default_fallback(self):
        with patch("redis.Redis.from_url") as from_url:
            SafeRedisCache(
                "rediss://cache.example/0",
                {"TIMEOUT": 60, "OPTIONS": {"NAMESPACE": "tls-test", "ssl_cert_reqs": "required"}},
            )
        from_url.assert_called_once_with(
            "rediss://cache.example/0", decode_responses=False, ssl_cert_reqs="required"
        )
