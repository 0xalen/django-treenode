import os
import pickle
import unittest
from unittest.mock import patch

from django.core.cache import caches
from django.db import transaction
from django.test import TransactionTestCase, override_settings

import treenode.safe_registry as safe_registry
from tests.models import Category, CategoryFixtures, CategoryOwner, CategoryWithOwner
from treenode.cache import _snapshot_key, get_safe_snapshot, query_cache
from treenode.revision import (
    _statuses,
    activate_registration,
    current_revision,
    install_revision_lifecycle,
)
from treenode.safe_codec import default_field_codecs
from treenode.safe_registry import register_safe_model


def _is_old_pickle_read(value):
    text = value.decode() if isinstance(value, bytes) else str(value)
    return "old-pickle" in text or text == "raw-old-pickle-key"


LIMITS = {
    "max_payload_bytes": 4096,
    "max_nodes": 64,
    "max_fields": 32,
    "max_depth": 8,
    "max_string_bytes": 256,
    "max_container_items": 64,
}


@override_settings(
    TREENODE_SAFE_CACHE_ALIAS="safe_redis",
    TREENODE_SAFE_CACHE_TIMEOUT=60,
    TREENODE_SAFE_CODEC_LIMITS=LIMITS,
)
class SafeTreeIntegrationTests(TransactionTestCase):
    databases = {"default", "other"}

    @classmethod
    def setUpClass(cls):
        if not os.environ.get("TREENODE_TEST_REDIS_URL"):
            raise unittest.SkipTest("requires explicit TREENODE_TEST_REDIS_URL")
        super().setUpClass()

    def _clear_state(self):
        safe_registry._registrations.clear()
        safe_registry._listeners.clear()
        _statuses.clear()

    def _fixture_teardown(self):
        self._clear_state()
        super()._fixture_teardown()

    def setUp(self):
        self._clear_state()
        install_revision_lifecycle()
        from django.core.cache import caches

        self.backend = caches["safe_redis"]
        self.backend.clear()

    def register(self, model=Category):
        register_safe_model(model, fields=default_field_codecs(model))
        registration = safe_registry.get_safe_model_registration(model)
        self.assertTrue(activate_registration(registration))
        return registration

    def test_empty_tree_cache_hit_persists_second_read(self):
        registration = self.register()
        self.assertEqual(get_safe_snapshot(Category, using="default"), ())
        key = _snapshot_key(registration, 0)
        self.assertIsInstance(self.backend.get(key), bytes)
        with patch("treenode.cache._db_rows", side_effect=AssertionError("cache miss")):
            self.assertEqual(get_safe_snapshot(Category, using="default"), ())

    def test_model_and_alias_identity_isolated(self):
        def route(model, **hints):
            if model._meta.label_lower == CategoryFixtures._meta.label_lower:
                return "other"
            return "default"

        with patch("django.db.router.db_for_write", side_effect=route):
            first = self.register(Category)
            second = self.register(CategoryFixtures)
            self.assertEqual(first.alias, "default")
            self.assertEqual(second.alias, "other")
            self.assertNotEqual(_snapshot_key(first, 0), _snapshot_key(second, 0))
            self.assertIn(":default:", _snapshot_key(first, 0))
            self.assertIn(":other:", _snapshot_key(second, 0))
            self.assertIn(Category._meta.label_lower, _snapshot_key(first, 0))
            self.assertIn(CategoryFixtures._meta.label_lower, _snapshot_key(second, 0))
            Category(name="alpha-default").save()
            CategoryFixtures(name="beta-other").save()
            self.assertEqual(
                list(Category.objects.using("default").values_list("name", flat=True)),
                ["alpha-default"],
            )
            self.assertEqual(Category.objects.using("other").count(), 0)
            self.assertEqual(
                list(
                    CategoryFixtures.objects.using("other").values_list(
                        "name", flat=True
                    )
                ),
                ["beta-other"],
            )
            self.assertEqual(CategoryFixtures.objects.using("default").count(), 0)
            self.assertEqual(
                [row.name for row in get_safe_snapshot(Category, using="default")],
                ["alpha-default"],
            )
            self.assertEqual(
                [
                    row.name
                    for row in get_safe_snapshot(CategoryFixtures, using="other")
                ],
                ["beta-other"],
            )
            default_key = _snapshot_key(first, current_revision(Category, "default"))
            other_key = _snapshot_key(
                second, current_revision(CategoryFixtures, "other")
            )
            default_bytes = self.backend.get(default_key)
            other_bytes = self.backend.get(other_key)
            self.assertIsInstance(default_bytes, bytes)
            self.assertIsInstance(other_bytes, bytes)
            self.assertNotEqual(default_key, other_key)
            self.assertNotEqual(default_bytes, other_bytes)
            self.assertNotIn(b"beta-other", default_bytes)
            self.assertNotIn(b"alpha-default", other_bytes)
            with patch(
                "treenode.cache._db_rows", side_effect=AssertionError("cache miss")
            ):
                self.assertEqual(
                    [row.name for row in get_safe_snapshot(Category, using="default")],
                    ["alpha-default"],
                )
                self.assertEqual(
                    [
                        row.name
                        for row in get_safe_snapshot(CategoryFixtures, using="other")
                    ],
                    ["beta-other"],
                )

    def test_malformed_and_old_namespace_never_delete_or_decode(self):
        registration = self.register()
        key = _snapshot_key(registration, 0)
        payload = pickle.dumps({"name": "legacy-pickle-row"})
        self.backend.set(key, b"malformed")
        self.backend.set("old-pickle-tree:tests.category", payload)
        self.backend._redis.set("raw-old-pickle-key", payload)
        loads = []
        redis_reads = []
        real_loads = pickle.loads
        real_redis_get = self.backend._redis.get

        def track_loads(data, *args, **kwargs):
            loads.append(data)
            return real_loads(data, *args, **kwargs)

        def track_redis(name):
            redis_reads.append(name)
            return real_redis_get(name)

        with (
            patch("pickle.loads", side_effect=track_loads),
            patch.object(self.backend._redis, "get", side_effect=track_redis),
        ):
            self.assertEqual(get_safe_snapshot(Category, using="default"), ())
        self.assertEqual(loads, [])
        self.assertFalse(any(_is_old_pickle_read(item) for item in redis_reads))
        self.assertEqual(self.backend.get(key), b"malformed")
        self.assertEqual(self.backend.get("old-pickle-tree:tests.category"), payload)
        self.assertEqual(self.backend._redis.get("raw-old-pickle-key"), payload)

    def test_same_revision_malformed_bytes_survive_concurrent_repair(self):
        registration = self.register()
        key = _snapshot_key(registration, 0)
        self.backend.set(key, b"malformed")
        repair = b"concurrent-repair"
        real_get = self.backend.get

        def get_and_repair(cache_key, default=None, version=None):
            value = real_get(cache_key, default=default, version=version)
            if cache_key == key:
                self.backend.set(key, repair)
            return value

        with patch.object(self.backend, "get", side_effect=get_and_repair):
            self.assertEqual(get_safe_snapshot(Category, using="default"), ())
        self.assertEqual(self.backend.get(key), repair)

        redis_key = self.backend._key(key)
        self.backend._redis.set(redis_key, b"malformed-raw")
        real_add = self.backend.add

        def add_after_repair(cache_key, value, timeout=60, version=None):
            if cache_key == key:
                self.backend._redis.set(redis_key, b"repaired-raw")
            return real_add(cache_key, value, timeout=timeout, version=version)

        with patch.object(self.backend, "add", side_effect=add_after_repair):
            self.assertEqual(get_safe_snapshot(Category, using="default"), ())
        self.assertEqual(self.backend._redis.get(redis_key), b"repaired-raw")

    def test_outage_and_active_transaction_use_database_rows(self):
        self.register()
        Category(name="database-row").save()
        legacy = Category(name="legacy-pickle-row", pk=424242)
        payload = pickle.dumps({"name": "legacy-pickle-row"})
        caches["treenode"].set("treenode_list", {Category: [legacy]})
        caches["default"].set("treenode_list", {Category: [legacy]})
        self.backend._redis.set("old-pickle-key", payload)
        loads = []
        legacy_reads = []
        redis_reads = []
        real_loads = pickle.loads
        real_tree_get = caches["treenode"].get
        real_default_get = caches["default"].get
        real_redis_get = self.backend._redis.get

        def track_loads(data, *args, **kwargs):
            loads.append(data)
            return real_loads(data, *args, **kwargs)

        def track_tree(key, default=None, version=None):
            legacy_reads.append(("treenode", key))
            return real_tree_get(key, default=default, version=version)

        def track_default(key, default=None, version=None):
            legacy_reads.append(("default", key))
            return real_default_get(key, default=default, version=version)

        def track_redis(name):
            redis_reads.append(name)
            return real_redis_get(name)

        with (
            patch("pickle.loads", side_effect=track_loads),
            patch.object(caches["treenode"], "get", side_effect=track_tree),
            patch.object(caches["default"], "get", side_effect=track_default),
            patch.object(self.backend._redis, "get", side_effect=track_redis),
            patch(
                "treenode.cache._safe_cache", side_effect=RuntimeError("redis outage")
            ),
        ):
            rows = get_safe_snapshot(Category, using="default")
            cached = query_cache(Category)
        self.assertEqual([row.name for row in rows], ["database-row"])
        self.assertEqual([row.name for row in cached], ["database-row"])
        self.assertEqual(loads, [])
        self.assertEqual(legacy_reads, [])
        self.assertEqual(redis_reads, [])
        self.assertEqual(self.backend._redis.get("old-pickle-key"), payload)
        with transaction.atomic():
            Category(name="transaction-row").save()
            in_txn = get_safe_snapshot(Category, using="default")
            self.assertEqual(
                {row.name for row in in_txn},
                {"database-row", "transaction-row"},
            )

    def test_active_transaction_reads_its_connection_not_the_shared_snapshot(self):
        registration = self.register()
        Category(name="committed").save()
        shared = get_safe_snapshot(Category, using="default")
        self.assertEqual([row.name for row in shared], ["committed"])
        revision = current_revision(Category, "default")
        key = _snapshot_key(registration, revision)
        shared_bytes = self.backend.get(key)
        self.assertIsInstance(shared_bytes, bytes)
        cache_calls = []

        def track_cache():
            cache_calls.append("shared")
            raise RuntimeError("shared snapshot")

        with transaction.atomic():
            Category(name="uncommitted").save()
            in_revision = current_revision(Category, "default")
            self.assertGreater(in_revision, revision)
            with patch("treenode.cache._safe_cache", side_effect=track_cache):
                rows = get_safe_snapshot(Category, using="default")
            self.assertEqual(cache_calls, [])
            self.assertEqual({row.name for row in rows}, {"committed", "uncommitted"})
            self.assertIsNone(
                self.backend.get(_snapshot_key(registration, in_revision))
            )
            self.assertEqual(self.backend.get(key), shared_bytes)

    def test_stale_builder_cannot_overwrite_new_revision_key(self):
        registration = self.register()
        Category(name="first").save()
        first_rows = get_safe_snapshot(Category, using="default")
        old_key = _snapshot_key(registration, 1)
        self.assertEqual([row.name for row in first_rows], ["first"])
        Category(name="second").save()
        self.assertIsInstance(self.backend.get(old_key), bytes)
        rows = get_safe_snapshot(Category, using="default")
        self.assertEqual({row.name for row in rows}, {"first", "second"})

    def test_stale_builder_cannot_replace_newer_revision_bytes(self):
        registration = self.register()
        Category(name="first").save()
        Category(name="second").save()
        fresh = get_safe_snapshot(Category, using="default")
        self.assertEqual({row.name for row in fresh}, {"first", "second"})
        new_key = _snapshot_key(registration, current_revision(Category, "default"))
        new_bytes = self.backend.get(new_key)
        self.assertIsInstance(new_bytes, bytes)
        old_rows = tuple(row for row in fresh if row.name == "first")
        self.assertEqual([row.name for row in old_rows], ["first"])

        def stale_snapshot(model, using):
            return current_revision(model, using), old_rows

        real_get = self.backend.get
        missed = {"done": False}

        def miss_once(cache_key, default=None, version=None):
            if cache_key == new_key and not missed["done"]:
                missed["done"] = True
                return None
            return real_get(cache_key, default=default, version=version)

        with (
            patch.object(self.backend, "get", side_effect=miss_once),
            patch("treenode.cache._coherent_snapshot", side_effect=stale_snapshot),
        ):
            get_safe_snapshot(Category, using="default")
        self.assertEqual(self.backend.get(new_key), new_bytes)
        with patch("treenode.cache._db_rows", side_effect=AssertionError("cache miss")):
            reread = get_safe_snapshot(Category, using="default")
        self.assertEqual({row.name for row in reread}, {"first", "second"})

    def test_nested_commit_and_rollback_publish_only_committed_revision(self):
        registration = self.register()
        with transaction.atomic():
            Category(name="outer").save()
            with transaction.atomic():
                Category(name="inner").save()
        committed = get_safe_snapshot(Category, using="default")
        self.assertEqual({row.name for row in committed}, {"outer", "inner"})
        committed_rev = current_revision(Category, "default")
        committed_key = _snapshot_key(registration, committed_rev)
        committed_bytes = self.backend.get(committed_key)
        self.assertIsInstance(committed_bytes, bytes)
        self.assertEqual(committed_rev, 2)
        with patch("treenode.cache._db_rows", side_effect=AssertionError("cache miss")):
            self.assertEqual(
                {row.name for row in get_safe_snapshot(Category, using="default")},
                {"outer", "inner"},
            )
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Category(name="rolled-back").save()
                in_rev = current_revision(Category, "default")
                self.assertGreater(in_rev, committed_rev)
                visible = get_safe_snapshot(Category, using="default")
                self.assertEqual(
                    {row.name for row in visible},
                    {"outer", "inner", "rolled-back"},
                )
                self.assertIsNone(self.backend.get(_snapshot_key(registration, in_rev)))
                self.assertEqual(self.backend.get(committed_key), committed_bytes)
                raise RuntimeError("rollback")
        self.assertEqual(current_revision(Category, "default"), committed_rev)
        self.assertIsNone(
            self.backend.get(_snapshot_key(registration, committed_rev + 1))
        )
        self.assertEqual(self.backend.get(committed_key), committed_bytes)
        with patch("treenode.cache._db_rows", side_effect=AssertionError("cache miss")):
            rows = get_safe_snapshot(Category, using="default")
        self.assertEqual({row.name for row in rows}, {"outer", "inner"})

    def test_publication_failure_returns_authoritative_rows_without_cache_entry(self):
        registration = self.register()
        Category(name="uncached").save()
        key = _snapshot_key(registration, 1)
        with patch.object(
            self.backend, "add", side_effect=RuntimeError("publication crash")
        ):
            rows = get_safe_snapshot(Category, using="default")
        self.assertEqual([row.name for row in rows], ["uncached"])
        self.assertIsNone(self.backend.get(key))

    def test_publication_failure_keeps_commit_and_rejects_earlier_revision(self):
        registration = self.register()
        Category(name="earlier").save()
        earlier_rows = get_safe_snapshot(Category, using="default")
        self.assertEqual([row.name for row in earlier_rows], ["earlier"])
        earlier_rev = current_revision(Category, "default")
        earlier_key = _snapshot_key(registration, earlier_rev)
        earlier_bytes = self.backend.get(earlier_key)
        self.assertIsInstance(earlier_bytes, bytes)
        with patch.object(
            self.backend, "add", side_effect=RuntimeError("publication crash")
        ):
            Category(name="later").save()
            rows = get_safe_snapshot(Category, using="default")
        self.assertEqual(
            set(Category.objects.values_list("name", flat=True)),
            {"earlier", "later"},
        )
        self.assertEqual({row.name for row in rows}, {"earlier", "later"})
        later_rev = current_revision(Category, "default")
        self.assertGreater(later_rev, earlier_rev)
        self.assertIsNone(self.backend.get(_snapshot_key(registration, later_rev)))
        self.assertEqual(self.backend.get(earlier_key), earlier_bytes)
        with patch.object(
            self.backend, "add", side_effect=RuntimeError("publication crash")
        ):
            reread = get_safe_snapshot(Category, using="default")
        self.assertEqual({row.name for row in reread}, {"earlier", "later"})

    def test_decoded_rows_keep_lazy_parent_and_owner_relations(self):
        self.register(Category)
        self.register(CategoryWithOwner)
        parent = Category(name="parent")
        parent.save()
        child = Category(name="child", tn_parent=parent)
        child.save()
        owner = CategoryOwner.objects.create(category=parent)
        CategoryWithOwner(name="owned", owner=owner).save()
        get_safe_snapshot(Category, using="default")
        get_safe_snapshot(CategoryWithOwner, using="default")
        with patch("treenode.cache._db_rows", side_effect=AssertionError("cache miss")):
            decoded_child = next(
                row
                for row in get_safe_snapshot(Category, using="default")
                if row.name == "child"
            )
            decoded_owned = get_safe_snapshot(CategoryWithOwner, using="default")[0]
        self.assertEqual(decoded_child._state.db, "default")
        self.assertEqual(decoded_owned._state.db, "default")
        self.assertFalse(Category._meta.get_field("tn_parent").is_cached(decoded_child))
        self.assertNotIn("tn_parent", decoded_child.__dict__)
        self.assertFalse(
            CategoryWithOwner._meta.get_field("owner").is_cached(decoded_owned)
        )
        self.assertNotIn("owner", decoded_owned.__dict__)
        self.assertEqual(decoded_child.tn_parent.name, "parent")
        loaded_owner = decoded_owned.owner
        self.assertFalse(
            CategoryOwner._meta.get_field("category").is_cached(loaded_owner)
        )
        self.assertEqual(loaded_owner.category.name, "parent")
        self.assertEqual(decoded_owned.owner_id, owner.pk)
