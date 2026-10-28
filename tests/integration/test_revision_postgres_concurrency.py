import multiprocessing
import os
import queue
import unittest

from django.test import TransactionTestCase


def _setup_worker(database_name):
    os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"
    os.environ["DATABASE_ENGINE"] = "postgres"
    os.environ["GITHUB_WORKFLOW"] = "1"
    os.environ["TREENODE_TEST_POSTGRES_NAME"] = database_name
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
        from django.db import connections

        raise RuntimeError(
            "worker registration did not activate: "
            f"configured={database_name} database={connections['default'].connection.info.dbname} "
            f"tables={connections['default'].introspection.table_names()}"
        )
    return Category


def _revision_worker(database_name, ready, go, results):
    try:
        Category = _setup_worker(database_name)
        from django.db import transaction
        from treenode.revision import advance_revision

        ready.set()
        if not go.wait(15):
            raise RuntimeError("revision race barrier timed out")
        with transaction.atomic(using="default"):
            results.put(("ok", advance_revision(Category, "default")))
    except Exception as exc:
        ready.set()
        results.put(("error", type(exc).__name__, str(exc)))


def _mutation_worker(database_name, name, ready, go, results):
    try:
        Category = _setup_worker(database_name)
        with Category.tree_mutation(using="default"):
            Category.objects.create(name=name)
            ready.set()
            if not go.wait(15):
                raise RuntimeError("mutation barrier timed out")
        results.put(("ok",))
    except Exception as exc:
        ready.set()
        results.put(("error", type(exc).__name__, str(exc)))


def _coherence_writer(database_name, ready, release, results):
    try:
        Category = _setup_worker(database_name)
        with Category.tree_mutation(using="default"):
            Category.objects.create(name="postgres-coherence")
            ready.set()
            if not release.wait(15):
                raise RuntimeError("coherence barrier timed out")
        results.put(("ok",))
    except Exception as exc:
        ready.set()
        results.put(("error", type(exc).__name__, str(exc)))


def _committed_mutation_writer(database_name, ready, commit, results):
    try:
        Category = _setup_worker(database_name)
        ready.set()
        if not commit.wait(15):
            raise RuntimeError("committed-write barrier timed out")
        Category(name="postgres-bracket-write").save()
        results.put(("ok",))
    except Exception as exc:
        ready.set()
        results.put(("error", type(exc).__name__, str(exc)))


class PostgreSQLRevisionConcurrencyTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.db import connections

        if not os.environ.get("DATABASE_ENGINE"):
            raise unittest.SkipTest("requires explicit DATABASE_ENGINE=postgres")
        if connections["default"].vendor != "postgresql":
            raise RuntimeError("PostgreSQL concurrency tests require DATABASE_ENGINE=postgres")
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT current_database()")
            cls.database_name = cursor.fetchone()[0]

    def setUp(self):
        self.context = multiprocessing.get_context("spawn")

    def _result(self, results):
        try:
            return results.get(timeout=20)
        except queue.Empty as exc:
            self.fail("PostgreSQL worker did not report a result")
            raise exc

    def _join(self, workers):
        for worker in workers:
            worker.join(20)
            self.assertFalse(worker.is_alive(), "PostgreSQL worker did not exit")

    def test_first_revision_row_race_postgres(self):
        ready = [self.context.Event(), self.context.Event()]
        go = self.context.Event()
        results = self.context.Queue()
        workers = [
            self.context.Process(target=_revision_worker, args=(self.database_name, event, go, results))
            for event in ready
        ]
        for worker in workers:
            worker.start()
        for event in ready:
            self.assertTrue(event.wait(20), "worker failed to reach first-row barrier")
        go.set()
        outcomes = [self._result(results), self._result(results)]
        self._join(workers)
        self.assertEqual(sorted(outcome[0] for outcome in outcomes), ["ok", "ok"], outcomes)
        self.assertEqual(sorted(outcome[1] for outcome in outcomes), [1, 2])

    def test_postgres_concurrent_revision_increment(self):
        ready = [self.context.Event(), self.context.Event()]
        go = self.context.Event()
        results = self.context.Queue()
        workers = [
            self.context.Process(
                target=_mutation_worker,
                args=(self.database_name, f"postgres-mutation-{index}", event, go, results),
            )
            for index, event in enumerate(ready)
        ]
        for worker in workers:
            worker.start()
        for event in ready:
            self.assertTrue(event.wait(20), "worker failed to reach mutation barrier")
        go.set()
        outcomes = [self._result(results), self._result(results)]
        self._join(workers)
        self.assertEqual([outcome[0] for outcome in outcomes], ["ok", "ok"], outcomes)
        from tests.models import Category
        from treenode.models import TreeNodeCacheRevision

        self.assertEqual(Category.objects.filter(name__startswith="postgres-mutation-").count(), 2)
        self.assertEqual(
            TreeNodeCacheRevision.objects.get(model_label=Category._meta.label_lower).revision,
            2,
        )

    def test_revision_rows_stable_under_concurrent_commit_postgres(self):
        ready = self.context.Event()
        commit = self.context.Event()
        results = self.context.Queue()
        worker = self.context.Process(
            target=_committed_mutation_writer, args=(self.database_name, ready, commit, results)
        )
        worker.start()
        self.assertTrue(ready.wait(20), "writer failed to reach commit barrier")
        from django.db import connections

        reader = connections["default"].copy(alias="postgres_coherence_reader")
        try:
            reader.ensure_connection()
            with reader.cursor() as cursor:
                cursor.execute(
                    "SELECT revision FROM treenode_treenodecacherevision WHERE model_label = %s",
                    ["tests.category"],
                )
                r1 = cursor.fetchone()
                cursor.execute("SELECT COUNT(*) FROM tests_category WHERE name = %s", ["postgres-bracket-write"])
                self.assertEqual(cursor.fetchone()[0], 0)
                commit.set()
                self.assertEqual(self._result(results), ("ok",))
                cursor.execute(
                    "SELECT revision FROM treenode_treenodecacherevision WHERE model_label = %s",
                    ["tests.category"],
                )
                r2 = cursor.fetchone()
                self.assertNotEqual(r1, r2)
                cursor.execute("SELECT COUNT(*) FROM tests_category WHERE name = %s", ["postgres-bracket-write"])
                self.assertEqual(cursor.fetchone()[0], 1)
        finally:
            reader.close()
            commit.set()
        self._join([worker])
