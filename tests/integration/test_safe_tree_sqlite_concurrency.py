import multiprocessing
import os
import queue
import sqlite3
import unittest

from django.test import TransactionTestCase, override_settings

LIMITS = {
    "max_payload_bytes": 4096,
    "max_nodes": 64,
    "max_fields": 32,
    "max_depth": 8,
    "max_string_bytes": 256,
    "max_container_items": 64,
}


def _setup_worker(database_path):
    os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"
    os.environ["TREENODE_TEST_SQLITE_PATH"] = database_path
    import django

    django.setup()
    import treenode.safe_registry as safe_registry
    from tests.models import Category
    from treenode.revision import (
        _statuses,
        activate_registration,
        install_revision_lifecycle,
    )
    from treenode.safe_codec import default_field_codecs
    from treenode.safe_registry import register_safe_model

    safe_registry._registrations.clear()
    safe_registry._listeners.clear()
    _statuses.clear()
    install_revision_lifecycle()
    register_safe_model(Category, fields=default_field_codecs(Category))
    registration = safe_registry.get_safe_model_registration(Category)
    if not activate_registration(registration):
        raise RuntimeError("worker registration did not activate")
    return Category


def _sqlite_lock_worker(database_path, ready, release, results):
    connection = sqlite3.connect(database_path, timeout=0.1, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        ready.set()
        if not release.wait(15):
            raise RuntimeError("lock release barrier timed out")
        connection.execute("COMMIT")
        results.put(("ok",))
    except Exception as exc:
        ready.set()
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        connection.close()


def _reparent_then_delete_worker(database_path, ready, go, results):
    try:
        Category = _setup_worker(database_path)
        child = Category.objects.get(name="child")
        child.tn_parent = Category.objects.get(name="parent")
        child.save()
        ready.set()
        if not go.wait(15):
            raise RuntimeError("delete barrier timed out")
        Category.objects.get(name="parent").delete()
        results.put(("ok",))
    except Exception as exc:
        ready.set()
        results.put(("error", type(exc).__name__, str(exc)))


@override_settings(
    TREENODE_SAFE_CACHE_ALIAS="safe_redis",
    TREENODE_SAFE_CACHE_TIMEOUT=60,
    TREENODE_SAFE_CODEC_LIMITS=LIMITS,
)
class SQLiteSafeTreeConcurrencyTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("TREENODE_TEST_SQLITE_PATH") or not os.environ.get(
            "TREENODE_TEST_REDIS_URL"
        ):
            raise unittest.SkipTest(
                "requires explicit file-backed SQLite and Redis resources"
            )
        super().setUpClass()
        from django.db import connections

        if connections["default"].vendor != "sqlite":
            raise RuntimeError("safe tree SQLite concurrency tests require SQLite")
        cls.database_path = connections["default"].settings_dict["NAME"]

    def setUp(self):
        from django.core.cache import caches

        import treenode.safe_registry as safe_registry
        from tests.models import Category
        from treenode.revision import (
            _statuses,
            activate_registration,
            install_revision_lifecycle,
        )
        from treenode.safe_codec import default_field_codecs
        from treenode.safe_registry import register_safe_model

        safe_registry._registrations.clear()
        safe_registry._listeners.clear()
        _statuses.clear()
        install_revision_lifecycle()
        register_safe_model(Category, fields=default_field_codecs(Category))
        self.registration = safe_registry.get_safe_model_registration(Category)
        self.assertTrue(activate_registration(self.registration))
        self.cache = caches["safe_redis"]
        self.cache.clear()
        self.context = multiprocessing.get_context("spawn")

    def _fixture_teardown(self):
        import treenode.safe_registry as safe_registry
        from treenode.revision import _statuses

        safe_registry._registrations.clear()
        safe_registry._listeners.clear()
        _statuses.clear()
        super()._fixture_teardown()

    def _result(self, results):
        try:
            return results.get(timeout=15)
        except queue.Empty as exc:
            self.fail("worker did not report a result")
            raise exc

    def test_busy_builder_returns_database_rows_without_publication(self):
        from tests.models import Category
        from treenode.cache import _snapshot_key, get_safe_snapshot

        Category(name="busy-row").save()
        ready, release, results = (
            self.context.Event(),
            self.context.Event(),
            self.context.Queue(),
        )
        worker = self.context.Process(
            target=_sqlite_lock_worker,
            args=(self.database_path, ready, release, results),
        )
        worker.start()
        self.assertTrue(
            ready.wait(15), "lock worker did not acquire SQLite writer lock"
        )
        try:
            before_keys = set(
                self.cache._redis.scan_iter(match=f"{self.cache.namespace}:*")
            )
            witness = sqlite3.connect(self.database_path, timeout=1)
            try:
                authoritative = [
                    row[0]
                    for row in witness.execute(
                        "SELECT name FROM tests_category ORDER BY id"
                    )
                ]
            finally:
                witness.close()
            self.assertEqual(
                [row.name for row in get_safe_snapshot(Category, using="default")],
                authoritative,
            )
            self.assertEqual(authoritative, ["busy-row"])
            self.assertIsNone(self.cache.get(_snapshot_key(self.registration, 1)))
            self.assertEqual(
                set(self.cache._redis.scan_iter(match=f"{self.cache.namespace}:*")),
                before_keys,
            )
        finally:
            release.set()
        self.assertEqual(self._result(results), ("ok",))
        worker.join(15)
        self.assertFalse(worker.is_alive(), "lock worker did not exit")

    def test_multiprocess_reparent_and_delete_invalidates_cached_revision(self):
        from tests.models import Category
        from treenode.cache import get_safe_snapshot

        Category(name="parent").save()
        Category(name="child").save()
        before_pairs = {
            (row.name, row.tn_parent_id)
            for row in get_safe_snapshot(Category, using="default")
        }
        self.assertEqual({name for name, _parent in before_pairs}, {"parent", "child"})
        ready, go, results = (
            self.context.Event(),
            self.context.Event(),
            self.context.Queue(),
        )
        worker = self.context.Process(
            target=_reparent_then_delete_worker,
            args=(self.database_path, ready, go, results),
        )
        worker.start()
        self.assertTrue(ready.wait(15), "reparent worker did not reach delete barrier")
        go.set()
        self.assertEqual(self._result(results), ("ok",))
        worker.join(15)
        self.assertFalse(worker.is_alive(), "mutation worker did not exit")
        rows = get_safe_snapshot(Category, using="default")
        expected = list(
            Category.objects.order_by("pk").values_list("name", "tn_parent_id")
        )
        after_pairs = [(row.name, row.tn_parent_id) for row in rows]
        self.assertEqual(after_pairs, expected)
        self.assertNotEqual(set(after_pairs), before_pairs)
