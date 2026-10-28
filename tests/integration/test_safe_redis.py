import asyncio
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import caches
from django.test import SimpleTestCase
from treenode.backends.redis import SafeRedisCache


class SafeRedisCacheIntegrationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("TREENODE_TEST_REDIS_URL"):
            raise unittest.SkipTest("requires explicit TREENODE_TEST_REDIS_URL")
        super().setUpClass()

    def setUp(self):
        self.cache = caches["safe_redis"]
        self.cache.clear()

    def test_backend_full_public_api_and_native_atomic_counters(self):
        self.assertTrue(self.cache.set("value", {"nested": [1, True]}))
        self.assertEqual(self.cache.get("value"), {"nested": [1, True]})
        self.assertTrue(self.cache.add("add", "one"))
        self.assertFalse(self.cache.add("add", "two"))
        self.cache.set("counter", 1)
        self.assertEqual(self.cache.incr("counter"), 2)
        self.assertEqual(self.cache.decr("counter", 2), 0)
        asyncio.run(self.cache.aset("async", "value"))
        self.assertEqual(asyncio.run(self.cache.aget("async")), "value")

    def test_public_api_versions_expiry_and_async_wrappers(self):
        self.assertTrue(self.cache.add("add", "one", version=2))
        self.assertFalse(self.cache.add("add", "two", version=2))
        self.assertEqual(self.cache.get_or_set("computed", lambda: "value"), "value")
        self.assertTrue(self.cache.touch("computed", 60))
        self.assertTrue(self.cache.has_key("computed"))
        self.assertEqual(self.cache.delete_many(["add", "computed"], version=2), 1)
        self.assertFalse(self.cache.set("zero", "expired", timeout=0))
        self.assertIsNone(self.cache.get("zero"))
        self.assertTrue(asyncio.run(self.cache.aadd("async-add", "value")))
        self.assertEqual(asyncio.run(self.cache.aget_or_set("async-computed", "value")), "value")
        self.assertTrue(asyncio.run(self.cache.atouch("async-computed", 60)))
        asyncio.run(self.cache.aset("async-counter", 2))
        self.assertEqual(asyncio.run(self.cache.aincr("async-counter")), 3)
        self.assertEqual(asyncio.run(self.cache.adecr("async-counter", 2)), 1)
        self.assertIsNone(asyncio.run(self.cache.adelete_many(["async-add", "async-computed"])))
        self.assertFalse(self.cache.has_key("async-add"))
        self.assertFalse(self.cache.has_key("async-computed"))

    def test_concurrent_increments_are_atomic(self):
        self.cache.set("concurrent", 0)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.cache.incr("concurrent"), range(32)))
        self.assertEqual(sorted(results), list(range(1, 33)))
        self.assertEqual(self.cache.get("concurrent"), 32)

    def test_none_missing_malformed_isolation_and_namespace_clear(self):
        self.cache.set("none", None)
        self.cache.set("valid", "value")
        self.cache._redis.set(self.cache._key("malformed"), b"not-safe")
        self.assertEqual(self.cache.get_many(["none", "valid", "malformed"]), {"none": None, "valid": "value"})
        other = caches["safe_redis_other"]
        other.clear()
        other.set("isolated", "other-namespace")
        self.assertTrue(self.cache.clear())
        self.assertEqual(self.cache.get("valid"), None)
        self.assertEqual(other.get("isolated"), "other-namespace")

    def test_invalid_batch_entries_and_malformed_values_are_isolated(self):
        self.assertEqual(self.cache.set_many({"valid": "value", "invalid": object()}), ["invalid"])
        self.assertEqual(self.cache.get("valid"), "value")
        invalid_key = self.cache._key("invalid")
        self.assertIsNone(self.cache._redis.get(invalid_key))
        self.cache._redis.set(self.cache._key("malformed"), b"not-safe")
        self.assertEqual(self.cache.get_many(["valid", "malformed"]), {"valid": "value"})
        self.assertEqual(self.cache._redis.get(self.cache._key("malformed")), b"not-safe")

    def test_prefix_version_ttl_and_outage_diagnostics(self):
        self.cache.set("versioned", "value", version=3)
        self.assertEqual(self.cache.get("versioned", version=3), "value")
        self.assertIsNone(self.cache.get("versioned"))
        self.assertFalse(self.cache.set("expired", "value", timeout=0))
        outage = self.cache.__class__(
            "redis://127.0.0.1:1/0",
            {"TIMEOUT": 1, "OPTIONS": {"NAMESPACE": "safe-redis-outage", "socket_connect_timeout": 0.1}},
        )
        with self.assertRaises(Exception) as error:
            outage.get("unavailable")
        self.assertIn("Connection", type(error.exception).__name__)

    def test_positive_timeout_expires_by_monotonic_deadline(self):
        self.assertTrue(self.cache.set("positive-ttl", "value", timeout=1))
        self.assertEqual(self.cache.get("positive-ttl"), "value")
        deadline = time.monotonic() + 3
        waiter = threading.Event()
        while self.cache.get("positive-ttl") is not None and time.monotonic() < deadline:
            waiter.wait(0.02)
        self.assertLess(time.monotonic(), deadline)
        self.assertIsNone(self.cache.get("positive-ttl"))

    def test_tls_handshake_and_certificate_fail_closed(self):
        if "safe_redis_tls" not in caches:
            if os.environ.get("TREENODE_REQUIRE_REDIS_TLS") == "1":
                self.fail("TREENODE_REQUIRE_REDIS_TLS requires configured safe_redis_tls alias")
            raise unittest.SkipTest("requires TLS Redis URL and CA")
        tls = caches["safe_redis_tls"]
        tls.clear()
        self.assertTrue(tls.set("handshake", "verified"))
        self.assertEqual(tls.get("handshake"), "verified")

        untrusted = SafeRedisCache(
            os.environ["TREENODE_TEST_REDIS_TLS_URL"],
            {"TIMEOUT": 1, "OPTIONS": {"NAMESPACE": "safe-redis-untrusted", "ssl_cert_reqs": "required", "ssl_check_hostname": True, "socket_connect_timeout": 1}},
        )
        with self.assertRaises(Exception):
            untrusted.get("handshake")

        mismatched = SafeRedisCache(
            os.environ["TREENODE_TEST_REDIS_TLS_URL"].replace("localhost", "localhost."),
            {"TIMEOUT": 1, "OPTIONS": {"NAMESPACE": "safe-redis-mismatch", "ssl_cert_reqs": "required", "ssl_ca_certs": os.environ["TREENODE_TEST_REDIS_TLS_CA"], "ssl_check_hostname": True, "socket_connect_timeout": 1}},
        )
        with self.assertRaises(Exception):
            mismatched.get("handshake")
