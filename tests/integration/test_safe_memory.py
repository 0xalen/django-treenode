import asyncio
import multiprocessing
import os
import unittest

from django.core.cache import caches
from django.test import SimpleTestCase


def _memory_worker(result):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
    import django

    django.setup()
    result.put(caches["safe_memory"].get("process-local"))


class SafeMemoryCacheIntegrationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("TREENODE_TEST_REDIS_URL"):
            raise unittest.SkipTest("requires explicit TREENODE_TEST_REDIS_URL")
        super().setUpClass()

    def setUp(self):
        self.cache = caches["safe_memory"]
        self.cache.clear()

    def test_backend_full_public_api_and_async_wrappers(self):
        self.assertTrue(self.cache.add("add", "one"))
        self.assertFalse(self.cache.add("add", "two"))
        self.assertEqual(self.cache.get_or_set("missing", "value"), "value")
        self.assertEqual(self.cache.get_many(["add", "missing"]), {"add": "one", "missing": "value"})
        self.assertEqual(self.cache.set_many({"many": 1, "bad": object()}), ["bad"])
        self.assertTrue(self.cache.touch("many", 60))
        self.assertTrue(self.cache.has_key("many"))
        self.assertEqual(self.cache.delete_many(["add", "many"]), 2)
        asyncio.run(self.cache.aset("async", "value"))
        self.assertEqual(asyncio.run(self.cache.aget("async")), "value")
        self.assertTrue(asyncio.run(self.cache.ahas_key("async")))
        self.assertTrue(asyncio.run(self.cache.aadd("async-add", "value")))
        self.assertEqual(asyncio.run(self.cache.aget_or_set("async-computed", "value")), "value")
        self.assertTrue(asyncio.run(self.cache.atouch("async-computed", 60)))
        asyncio.run(self.cache.aset("async-counter", 2))
        self.assertEqual(asyncio.run(self.cache.aincr("async-counter")), 3)
        self.assertEqual(asyncio.run(self.cache.adecr("async-counter", 2)), 1)
        self.assertIsNone(asyncio.run(self.cache.adelete_many(["async-add", "async-computed"])))
        self.assertFalse(self.cache.has_key("async-add"))
        self.assertFalse(self.cache.has_key("async-computed"))
        self.assertTrue(asyncio.run(self.cache.aclear()))

    def test_none_missing_prefix_version_ttl_and_process_local(self):
        self.cache.set("none", None)
        self.assertIsNone(self.cache.get("none", "default"))
        self.assertEqual(self.cache.get("absent", "default"), "default")
        self.cache.set("versioned", "one", version=2)
        self.assertEqual(self.cache.get("versioned", version=2), "one")
        self.assertIsNone(self.cache.get("versioned"))
        self.assertFalse(self.cache.set("expired", "value", timeout=0))
        self.assertIsNone(self.cache.get("expired"))
        self.cache.set("process-local", "parent")
        context = multiprocessing.get_context("spawn")
        result = context.Queue()
        worker = context.Process(target=_memory_worker, args=(result,))
        worker.start()
        self.assertIsNone(result.get(timeout=15))
        worker.join(15)
        self.assertFalse(worker.is_alive())
