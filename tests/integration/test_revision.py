from unittest.mock import patch

from django.db import transaction
from django.apps import apps
from django.core.management import call_command
from django.test import TransactionTestCase

from tests.models import (
    Category,
    CategoryFixtures,
    CategoryOwner,
    CategoryWithCallableOwner,
    CategoryWithCascadeOwner,
    CategoryWithDefaultOwner,
    CategoryWithOwner,
    CategoryWithUnsupportedOwner,
)
from treenode.exceptions import SafeActivationError, SafeRegistrationError, SafeRoutingError
from treenode.models import TreeNodeCacheRevision
from treenode.revision import (
    _statuses,
    activate_registration,
    advance_revision,
    get_revision_status,
    install_revision_lifecycle,
)
from treenode.safe_codec import default_field_codecs
from treenode.safe_registry import register_safe_model
import treenode.safe_registry as safe_registry


class RevisionIntegrationTests(TransactionTestCase):
    reset_sequences = True

    def _clear_revision_test_state(self):
        safe_registry._registrations.clear()
        safe_registry._listeners.clear()
        _statuses.clear()

    def _fixture_teardown(self):
        # TransactionTestCase flush emits post_migrate. Clear prior registrations
        # before that signal so an earlier test cannot repopulate revision rows.
        self._clear_revision_test_state()
        super()._fixture_teardown()

    def setUp(self):
        self._clear_revision_test_state()
        install_revision_lifecycle()

    def register(self):
        register_safe_model(Category, fields=default_field_codecs(Category))
        registration = safe_registry.get_safe_model_registration(Category)
        self.assertTrue(activate_registration(registration))
        return registration

    def register_model(self, model):
        register_safe_model(model, fields=default_field_codecs(model))
        registration = safe_registry.get_safe_model_registration(model)
        self.assertTrue(activate_registration(registration))
        return registration

    def revision(self):
        return TreeNodeCacheRevision.objects.get(model_label=Category._meta.label_lower).revision

    def revision_value(self):
        return TreeNodeCacheRevision.objects.filter(
            model_label=Category._meta.label_lower
        ).values_list("revision", flat=True).first()

    def test_registered_model_activation_lifecycle(self):
        register_safe_model(Category, fields=default_field_codecs(Category))
        self.assertEqual(get_revision_status(Category).state, "pending")
        self.assertTrue(activate_registration(safe_registry.get_safe_model_registration(Category)))
        self.assertEqual(get_revision_status(Category).state, "active")

    def test_pending_registration_does_not_own_writes(self):
        register_safe_model(Category, fields=default_field_codecs(Category))
        before = self.revision_value()
        Category.objects.create(name="pending")
        self.assertEqual(self.revision_value(), before)

    def test_startup_activation_error(self):
        registration = self.register()
        with patch("treenode.revision._table_exists", side_effect=RuntimeError("schema unavailable")):
            with self.assertRaises(SafeActivationError):
                activate_registration(registration)
        self.assertEqual(get_revision_status(Category).state, "activation-failed")

    def test_activation_failed_is_db_only_when_revision_table_is_missing(self):
        register_safe_model(Category, fields=default_field_codecs(Category))
        registration = safe_registry.get_safe_model_registration(Category)
        with patch("treenode.revision._table_exists", side_effect=(True, False)):
            self.assertFalse(activate_registration(registration, raise_error=False))
        self.assertEqual(get_revision_status(Category).state, "activation-failed")
        before = self.revision_value()
        Category.objects.create(name="activation-failed")
        self.assertEqual(self.revision_value(), before)

    def test_save_and_parent_change_advance_revision(self):
        self.register()
        parent = Category(name="parent")
        parent.save()
        first = self.revision()
        child = Category(name="child", tn_parent=parent)
        child.save()
        self.assertGreater(self.revision(), first)
        child.set_parent(None)
        self.assertIsNone(Category.objects.get(pk=child.pk).tn_parent_id)

    def test_outer_rollback_leaves_revision_unchanged(self):
        self.register()
        before = self.revision_value()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Category(name="rolled-back").save()
                raise RuntimeError("rollback")
        self.assertFalse(Category.objects.filter(name="rolled-back").exists())
        self.assertEqual(self.revision_value(), before)

    def test_instance_save_route_mismatch_rejected_before_row_write(self):
        self.register()
        with patch("treenode.revision.router.db_for_write", return_value="other"):
            with self.assertRaises(SafeRoutingError):
                Category(name="wrong-alias").save()
        self.assertFalse(Category.objects.filter(name="wrong-alias").exists())

    def test_advance_revision_requires_atomic_transaction(self):
        self.register()
        with self.assertRaises(SafeActivationError):
            advance_revision(Category, "default")

    def test_instance_delete_route_mismatch_rejected_before_row_write(self):
        self.register()
        node = Category(name="wrong-delete")
        node.save()
        with patch("treenode.revision.router.db_for_write", return_value="other"):
            with self.assertRaises(SafeRoutingError):
                node.delete()
        self.assertTrue(Category.objects.filter(pk=node.pk).exists())

    def test_coordinated_bulk_write_advances_with_rows(self):
        self.register()
        before = self.revision_value() or 0
        with Category.tree_mutation(using="default"):
            Category.objects.bulk_create([Category(name="bulk-a"), Category(name="bulk-b")])
        self.assertEqual(Category.objects.filter(name__startswith="bulk-").count(), 2)
        self.assertEqual(self.revision(), before + 1)

    def test_delete_and_delete_tree_advance_revision(self):
        self.register()
        Category(name="delete-a").save()
        first = self.revision()
        Category(name="delete-b").save()
        Category.delete_tree()
        self.assertEqual(Category.objects.count(), 0)
        self.assertGreater(self.revision(), first)

    def test_queryset_delete_and_cross_model_cascade_advance_revision(self):
        self.register_model(CategoryWithCascadeOwner)
        owner = CategoryOwner.objects.create(category=Category.objects.create(name="owner-root"))
        node = CategoryWithCascadeOwner(name="cascade-node", owner=owner)
        node.save()
        before = TreeNodeCacheRevision.objects.get(
            model_label=CategoryWithCascadeOwner._meta.label_lower
        ).revision
        CategoryOwner.objects.filter(pk=owner.pk).delete()
        self.assertFalse(CategoryWithCascadeOwner.objects.filter(pk=node.pk).exists())
        self.assertGreater(
            TreeNodeCacheRevision.objects.get(
                model_label=CategoryWithCascadeOwner._meta.label_lower
            ).revision,
            before,
        )

    def test_related_owner_set_policies_advance_revision(self):
        models_and_names = (
            (CategoryWithOwner, "set-null"),
            (CategoryWithDefaultOwner, "set-default"),
            (CategoryWithCallableOwner, "set-callable"),
        )
        default_owner = CategoryOwner.objects.create(
            category=Category.objects.create(name="default-root")
        )
        self.assertEqual(default_owner.pk, 1)
        for model, name in models_and_names:
            self.register_model(model)
            for path in ("instance", "queryset"):
                owner = CategoryOwner.objects.create(
                    category=Category.objects.create(name=f"{name}-{path}-root")
                )
                node = model(name=f"{name}-{path}", owner=owner)
                node.save()
                before = TreeNodeCacheRevision.objects.get(model_label=model._meta.label_lower).revision
                if path == "instance":
                    owner.delete()
                else:
                    CategoryOwner.objects.filter(pk=owner.pk).delete()
                node.refresh_from_db()
                self.assertEqual(node.owner_id, default_owner.pk if model is not CategoryWithOwner else None)
                self.assertGreater(
                    TreeNodeCacheRevision.objects.get(model_label=model._meta.label_lower).revision,
                    before,
                )

    def test_raw_fixture_import_requires_coordinator(self):
        self.register_model(CategoryFixtures)
        before = CategoryFixtures.objects.count()
        with self.assertRaises(RuntimeError):
            call_command("loaddata", "test_fixtures_issue_0088.json")
        self.assertEqual(CategoryFixtures.objects.count(), before)

    def test_raw_fixture_import_coordinator_loads_and_rolls_back_atomically(self):
        self.register_model(CategoryFixtures)
        with CategoryFixtures.tree_mutation(using="default"):
            call_command("loaddata", "test_fixtures_issue_0088.json")
        self.assertEqual(CategoryFixtures.objects.count(), 19)
        revision = TreeNodeCacheRevision.objects.get(
            model_label=CategoryFixtures._meta.label_lower
        ).revision
        self.assertGreaterEqual(revision, 1)

        CategoryFixtures.delete_tree()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                with CategoryFixtures.tree_mutation(using="default"):
                    call_command("loaddata", "test_fixtures_issue_0088.json")
                raise RuntimeError("rollback fixture coordinator")
        self.assertEqual(CategoryFixtures.objects.count(), 0)

    def test_post_migrate_refresh_advances_revision(self):
        self.register()
        Category(name="migrate-root").save()
        before = self.revision()
        from treenode.signals import post_migrate_treenode

        post_migrate_treenode(sender=apps.get_app_config("tests"), using="default")
        self.assertGreater(self.revision(), before)

    def test_unregistered_model_preserves_current_mutation_behavior(self):
        before = self.revision_value()
        node = Category(name="unregistered")
        node.save()
        self.assertTrue(Category.objects.filter(pk=node.pk).exists())
        self.assertEqual(self.revision_value(), before)

    def test_queryset_delete_signal_coverage_and_final_derived_state(self):
        self.register()
        parent = Category(name="query-parent")
        parent.save()
        child = Category(name="query-child", tn_parent=parent)
        child.save()
        before = self.revision()
        Category.objects.filter(pk=child.pk).delete()
        parent.refresh_from_db()
        self.assertEqual(parent.tn_children_count, 0)
        self.assertEqual(parent.tn_children_pks, "")
        self.assertGreater(self.revision(), before)

    def test_delete_rollback_leaves_rows_and_revision_unchanged(self):
        self.register()
        node = Category(name="delete-rollback")
        node.save()
        before = self.revision()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                node.delete()
                raise RuntimeError("rollback delete")
        self.assertTrue(Category.objects.filter(pk=node.pk).exists())
        self.assertEqual(self.revision(), before)

    def test_revision_overflow_rolls_back_mutation(self):
        self.register()
        TreeNodeCacheRevision.objects.create(
            model_label=Category._meta.label_lower, revision=9223372036854775807
        )
        with self.assertRaises(OverflowError):
            Category(name="overflow").save()
        self.assertFalse(Category.objects.filter(name="overflow").exists())

    def test_unsupported_related_delete_policy_is_rejected(self):
        with self.assertRaises(SafeRegistrationError):
            register_safe_model(
                CategoryWithUnsupportedOwner,
                fields=default_field_codecs(CategoryWithUnsupportedOwner),
            )

    def test_unowned_save_base_is_rejected_before_row_write(self):
        self.register()
        node = Category(name="save-base")
        with self.assertRaises(RuntimeError):
            node.save_base(using="default")
        self.assertFalse(Category.objects.filter(name="save-base").exists())

    def test_raw_delete_is_unowned_and_does_not_advance_revision(self):
        self.register()
        node = Category(name="raw-delete")
        node.save()
        before = self.revision()
        Category.objects.filter(pk=node.pk)._raw_delete("default")
        self.assertFalse(Category.objects.filter(pk=node.pk).exists())
        self.assertEqual(self.revision(), before)
