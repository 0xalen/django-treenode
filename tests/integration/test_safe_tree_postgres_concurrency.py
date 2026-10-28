import multiprocessing
import os
import queue
import unittest
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings

LIMITS = {
    "max_payload_bytes": 4096,
    "max_nodes": 64,
    "max_fields": 32,
    "max_depth": 8,
    "max_string_bytes": 256,
    "max_container_items": 64,
}


def _postgres_writer(database_name, ready, commit, results):
    try:
        os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"
        os.environ["DATABASE_ENGINE"] = "postgres"
        os.environ["GITHUB_WORKFLOW"] = "1"
        os.environ["TREENODE_TEST_POSTGRES_NAME"] = database_name
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
        ready.set()
        if not commit.wait(20):
            raise RuntimeError("commit barrier timed out")
        Category(name="postgres-r2-write").save()
        results.put(("ok",))
    except Exception as exc:
        ready.set()
        results.put(("error", type(exc).__name__, str(exc)))


@override_settings(
    TREENODE_SAFE_CACHE_ALIAS="safe_redis",
    TREENODE_SAFE_CACHE_TIMEOUT=60,
    TREENODE_SAFE_CODEC_LIMITS=LIMITS,
)
class PostgreSQLSafeTreeConcurrencyTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("DATABASE_ENGINE") or not os.environ.get(
            "TREENODE_TEST_REDIS_URL"
        ):
            raise unittest.SkipTest("requires explicit PostgreSQL and Redis resources")
        super().setUpClass()
        from django.db import connections

        if connections["default"].vendor != "postgresql":
            raise RuntimeError(
                "safe tree PostgreSQL concurrency tests require PostgreSQL"
            )
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT current_database()")
            cls.database_name = cursor.fetchone()[0]

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
        caches["safe_redis"].clear()
        self.context = multiprocessing.get_context("spawn")

    def _fixture_teardown(self):
        import treenode.safe_registry as safe_registry
        from treenode.revision import _statuses

        safe_registry._registrations.clear()
        safe_registry._listeners.clear()
        _statuses.clear()
        super()._fixture_teardown()

    def test_changed_r2_discards_builder_rows_and_publishes_retry_revision(self):
        from django.core.cache import caches

        from tests.models import Category
        from treenode.cache import _db_rows, _snapshot_key, get_safe_snapshot

        ready, commit, results = (
            self.context.Event(),
            self.context.Event(),
            self.context.Queue(),
        )
        worker = self.context.Process(
            target=_postgres_writer, args=(self.database_name, ready, commit, results)
        )
        worker.start()
        self.assertTrue(ready.wait(20), "writer did not reach commit barrier")
        first_read = True

        def rows_with_r2_commit(model, using):
            nonlocal first_read
            if first_read:
                first_read = False
                commit.set()
                try:
                    outcome = results.get(timeout=20)
                except queue.Empty as exc:
                    self.fail("writer did not commit")
                    raise exc
                self.assertEqual(outcome, ("ok",))
                stored = _db_rows(model, using)
                self.assertEqual([row.name for row in stored], ["postgres-r2-write"])
                discarded = Category(name="discarded-observation")
                for field in Category._meta.concrete_fields:
                    if field.attname != "name":
                        setattr(
                            discarded, field.attname, getattr(stored[0], field.attname)
                        )
                return (discarded,)
            return _db_rows(model, using)

        try:
            with patch("treenode.cache._db_rows", side_effect=rows_with_r2_commit):
                rows = get_safe_snapshot(Category, using="default")
        finally:
            commit.set()
        worker.join(20)
        self.assertFalse(worker.is_alive(), "writer did not exit")
        self.assertEqual([row.name for row in rows], ["postgres-r2-write"])
        self.assertTrue(all(row.name != "discarded-observation" for row in rows))
        backend = caches["safe_redis"]
        self.assertIsNone(backend.get(_snapshot_key(self.registration, 0)))
        published = backend.get(_snapshot_key(self.registration, 1))
        self.assertIsInstance(published, bytes)
        self.assertIn(b"postgres-r2-write", published)
        self.assertNotIn(b"discarded-observation", published)
