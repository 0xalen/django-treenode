from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from tests.models import Category
from treenode.models import TreeNodeCacheRevision
from treenode.revision import _statuses, activate_registration, install_revision_lifecycle
from treenode.safe_codec import default_field_codecs
from treenode.safe_registry import register_safe_model
import treenode.safe_registry as safe_registry


class RevisionMigrationUpgradeTests(TransactionTestCase):
    def _clear_revision_test_state(self):
        safe_registry._registrations.clear()
        safe_registry._listeners.clear()
        _statuses.clear()

    def _fixture_teardown(self):
        self._clear_revision_test_state()
        super()._fixture_teardown()

    def setUp(self):
        self._clear_revision_test_state()
        install_revision_lifecycle()

    def test_populated_pre_feature_database_upgrades_without_changing_tree_rows(self):
        parent = Category.objects.create(name="legacy-parent")
        child = Category.objects.create(name="legacy-child", tn_parent=parent)
        Category.update_tree()
        before = list(
            Category.objects.order_by("pk").values(
                "pk", "name", "tn_parent_id", "tn_order", "tn_children_count", "tn_ancestors_pks"
            )
        )
        revision_table = TreeNodeCacheRevision._meta.db_table

        try:
            MigrationExecutor(connection).migrate([("treenode", None)])
            self.assertNotIn(revision_table, connection.introspection.table_names())
            self.assertEqual(
                list(
                    Category.objects.order_by("pk").values(
                        "pk", "name", "tn_parent_id", "tn_order", "tn_children_count", "tn_ancestors_pks"
                    )
                ),
                before,
            )

            MigrationExecutor(connection).migrate([("treenode", "0001_initial")])
            self.assertIn(revision_table, connection.introspection.table_names())
            self.assertEqual(
                list(
                    Category.objects.order_by("pk").values(
                        "pk", "name", "tn_parent_id", "tn_order", "tn_children_count", "tn_ancestors_pks"
                    )
                ),
                before,
            )

            register_safe_model(Category, fields=default_field_codecs(Category))
            registration = safe_registry.get_safe_model_registration(Category)
            self.assertTrue(activate_registration(registration))
            Category(name="upgraded-write", tn_parent=child).save()
            self.assertEqual(Category.objects.filter(name="upgraded-write").count(), 1)
            self.assertEqual(
                TreeNodeCacheRevision.objects.get(model_label=Category._meta.label_lower).revision,
                1,
            )
        finally:
            MigrationExecutor(connection).migrate([("treenode", "0001_initial")])
