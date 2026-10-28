import os
import unittest
from unittest.mock import patch

from django.db import connections
from django.test import TransactionTestCase

from tests.models import Category
from treenode.exceptions import SafeRegistrationError, SafeRoutingError
from treenode.models import TreeNodeCacheRevision
from treenode.revision import (
    _statuses,
    activate_registration,
    get_revision_status,
    install_revision_lifecycle,
)
from treenode.safe_codec import default_field_codecs
from treenode.safe_registry import register_safe_model
import treenode.safe_registry as safe_registry


class RevisionRouterIntegrationTests(TransactionTestCase):
    databases = {"default", "revision"} if os.environ.get("TREENODE_TEST_ROUTER_ALIASES") else {"default"}

    @classmethod
    def setUpClass(cls):
        if not os.environ.get("TREENODE_TEST_ROUTER_ALIASES"):
            raise unittest.SkipTest("requires explicit TREENODE_TEST_ROUTER_ALIASES")
        super().setUpClass()

    def _clear_revision_test_state(self):
        safe_registry._registrations.clear()
        safe_registry._listeners.clear()
        _statuses.clear()

    def _fixture_teardown(self):
        self._clear_revision_test_state()
        super()._fixture_teardown()

    def setUp(self):
        if "revision" not in connections:
            raise RuntimeError("router integration requires the configured revision alias")
        self._clear_revision_test_state()
        install_revision_lifecycle()

    def _register(self, route, allow_migrate=lambda *args, **kwargs: True):
        with patch("django.db.router.db_for_write", side_effect=route), patch(
            "django.db.router.allow_migrate", side_effect=allow_migrate
        ):
            register_safe_model(Category, fields=default_field_codecs(Category))
        return safe_registry.get_safe_model_registration(Category)

    def test_router_routes_revision_and_tree_together(self):
        registration = self._register(lambda model, **hints: "default")
        self.assertEqual(registration.alias, "default")
        self.assertTrue(activate_registration(registration))
        with patch("django.db.router.db_for_write", return_value="default"):
            Category(name="co-located").save()
        self.assertEqual(Category.objects.using("default").filter(name="co-located").count(), 1)
        self.assertEqual(Category.objects.using("revision").filter(name="co-located").count(), 0)
        self.assertEqual(
            TreeNodeCacheRevision.objects.using("default").get(
                model_label=Category._meta.label_lower
            ).revision,
            1,
        )
        self.assertFalse(
            TreeNodeCacheRevision.objects.using("revision").filter(
                model_label=Category._meta.label_lower
            ).exists()
        )

    def test_router_rejects_model_revision_split(self):
        def allow_migrate(alias, app_label, **kwargs):
            return not (alias == "default" and app_label == "treenode")

        with self.assertRaises(SafeRegistrationError):
            self._register(lambda model, **hints: "default", allow_migrate)

    def test_instance_hint_mismatch_is_rejected_before_row_sql(self):
        def route(model, **hints):
            return "revision" if hints.get("instance") is not None else "default"

        registration = self._register(route)
        self.assertTrue(activate_registration(registration))
        with patch("django.db.router.db_for_write", side_effect=route):
            with self.assertRaises(SafeRoutingError):
                Category(name="instance-mismatch").save()
        self.assertFalse(Category.objects.using("default").filter(name="instance-mismatch").exists())
        self.assertFalse(Category.objects.using("revision").filter(name="instance-mismatch").exists())

    def test_pending_to_active_and_failed_activation_after_schema_boundary(self):
        registration = self._register(lambda model, **hints: "revision")
        self.assertEqual(get_revision_status(Category).state, "pending")
        self.assertTrue(activate_registration(registration))
        self.assertEqual(get_revision_status(Category).state, "active")

        with connections["revision"].schema_editor() as editor:
            editor.delete_model(TreeNodeCacheRevision)
        try:
            self.assertFalse(activate_registration(registration, raise_error=False))
            self.assertEqual(get_revision_status(Category).state, "activation-failed")
        finally:
            with connections["revision"].schema_editor() as editor:
                editor.create_model(TreeNodeCacheRevision)

        self.assertTrue(activate_registration(registration))
        self.assertEqual(get_revision_status(Category).state, "active")
