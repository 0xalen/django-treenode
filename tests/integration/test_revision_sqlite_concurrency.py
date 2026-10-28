import multiprocessing
import os
import queue
import sqlite3
import unittest

from django.test import TransactionTestCase


def _setup_worker(database_path):
    os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"
    os.environ["TREENODE_TEST_SQLITE_PATH"] = database_path
    import django

    django.setup()
    from tests.models import Category
    from treenode.revision import _statuses, activate_registration, install_revision_lifecycle
    from treenode.safe_codec import default_field_codecs
    from treenode.safe_registry import register_safe_model
    import treenode.safe_registry as safe_registry

    safe_registry._registrations.clear()
    safe_registry._listeners.clear()
    _statuses.clear()
    install_revision_lifecycle()
    register_safe_model(Category, fields=default_field_codecs(Category))
    registration = safe_registry.get_safe_model_registration(Category)
    if not activate_registration(registration):
        raise RuntimeError("worker registration did not activate")
    return Category


def _first_row_worker(database_path, ready, go, results):
    try:
        Category = _setup_worker(database_path)
        from django.db import transaction
        from treenode.revision import advance_revision

        ready.set()
        if not go.wait(10):
            raise RuntimeError("first-row race barrier timed out")
        with transaction.atomic(using="default"):
            results.put(("ok", advance_revision(Category, "default")))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def _coherence_writer(database_path, ready, release, results):
    try:
        Category = _setup_worker(database_path)
        with Category.tree_mutation(using="default"):
            Category.objects.create(name="coherence-writer")
            ready.set()
            if not release.wait(10):
                raise RuntimeError("coherence barrier timed out")
        results.put(("ok",))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def _immediate_writer(database_path, ready, release, results):
    connection = sqlite3.connect(database_path, timeout=0.1, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        ready.set()
        if not release.wait(10):
            raise RuntimeError("writer barrier timed out")
        connection.execute("COMMIT")
        results.put(("ok",))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        connection.close()


class SQLiteRevisionConcurrencyTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not os.environ.get("TREENODE_TEST_SQLITE_PATH"):
            raise unittest.SkipTest("requires explicit TREENODE_TEST_SQLITE_PATH")
        from django.db import connections

        if connections["default"].vendor != "sqlite":
            raise RuntimeError("SQLite concurrency tests require the SQLite backend")
        cls.database_path = connections["default"].settings_dict["NAME"]
        if not os.path.exists(cls.database_path):
            raise RuntimeError("SQLite concurrency tests require the file-backed test database")

    def setUp(self):
        self.context = multiprocessing.get_context("spawn")

    def _result(self, results):
        try:
            return results.get(timeout=15)
        except queue.Empty as exc:
            self.fail("worker did not report a result")
            raise exc

    def test_first_revision_row_race(self):
        ready = [self.context.Event(), self.context.Event()]
        go = self.context.Event()
        results = self.context.Queue()
        workers = [
            self.context.Process(target=_first_row_worker, args=(self.database_path, event, go, results))
            for event in ready
        ]
        for worker in workers:
            worker.start()
        for event in ready:
            self.assertTrue(event.wait(15), "worker failed to reach race barrier")
        go.set()
        outcomes = [self._result(results), self._result(results)]
        for worker in workers:
            worker.join(15)
            self.assertFalse(worker.is_alive(), "first-row worker did not exit")
        if any(outcome[0] != "ok" for outcome in outcomes):
            self.fail(f"first revision row race worker outcomes: {outcomes!r}")
        self.assertEqual(sorted(outcome[0] for outcome in outcomes), ["ok", "ok"])
        values = sorted(outcome[1] for outcome in outcomes)
        self.assertEqual(values, [1, 2])

    def test_sqlite_first_writer_contention(self):
        ready = self.context.Event()
        release = self.context.Event()
        results = self.context.Queue()
        worker = self.context.Process(
            target=_immediate_writer, args=(self.database_path, ready, release, results)
        )
        worker.start()
        self.assertTrue(ready.wait(15), "first writer did not acquire the database")
        contender = sqlite3.connect(self.database_path, timeout=0.05, isolation_level=None)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                contender.execute("BEGIN IMMEDIATE")
        finally:
            contender.close()
            release.set()
        self.assertEqual(self._result(results), ("ok",))
        worker.join(15)
        self.assertFalse(worker.is_alive(), "writer did not exit")

    def test_revision_rows_are_coherent_across_processes(self):
        ready = self.context.Event()
        release = self.context.Event()
        results = self.context.Queue()
        worker = self.context.Process(
            target=_coherence_writer, args=(self.database_path, ready, release, results)
        )
        worker.start()
        self.assertTrue(ready.wait(15), "writer did not reach coherence barrier")
        reader = sqlite3.connect(self.database_path, timeout=1, isolation_level=None)
        try:
            row_count = reader.execute("SELECT COUNT(*) FROM tests_category WHERE name = 'coherence-writer'").fetchone()[0]
            revision = reader.execute(
                "SELECT revision FROM treenode_treenodecacherevision WHERE model_label = 'tests.category'"
            ).fetchone()
            self.assertEqual(row_count, 0)
            self.assertIsNone(revision)
        finally:
            reader.close()
            release.set()
        self.assertEqual(self._result(results), ("ok",))
        worker.join(15)
        self.assertFalse(worker.is_alive(), "coherence writer did not exit")
        reader = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                reader.execute("SELECT COUNT(*) FROM tests_category WHERE name = 'coherence-writer'").fetchone()[0],
                1,
            )
            self.assertEqual(
                reader.execute(
                    "SELECT revision FROM treenode_treenodecacherevision WHERE model_label = 'tests.category'"
                ).fetchone()[0],
                1,
            )
        finally:
            reader.close()
