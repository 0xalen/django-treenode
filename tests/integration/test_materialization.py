from django.test import TestCase, override_settings

from tests.models import Category, CategoryOwner, CategoryWithOwner
from treenode.exceptions import SafeRegistrationError
from treenode.memory import clear_refs, get_refs
from treenode.safe_codec import decode_snapshot, default_field_codecs, encode_snapshot
from treenode.safe_registry import register_safe_model
import treenode.safe_registry as safe_registry
from unittest.mock import patch


LIMITS = {
    "max_payload_bytes": 4096,
    "max_nodes": 8,
    "max_fields": 32,
    "max_depth": 4,
    "max_string_bytes": 128,
    "max_container_items": 32,
}


@override_settings(TREENODE_SAFE_CODEC_LIMITS=LIMITS)
class MaterializationIntegrationTests(TestCase):
    def setUp(self):
        safe_registry._registrations.clear()
        safe_registry._listeners.clear()
        clear_refs(Category)
        register_safe_model(Category, fields=default_field_codecs(Category))

    def test_from_db_state_and_relation_cache(self):
        parent = Category(pk=1, name="parent")
        child = Category(pk=2, name="child", tn_parent_id=1)
        raw = encode_snapshot(Category, "default", 3, (parent, child))
        decoded_parent, decoded_child = decode_snapshot(Category, "default", 3, raw)

        self.assertFalse(decoded_parent._state.adding)
        self.assertEqual(decoded_parent._state.db, "default")
        self.assertEqual(decoded_child.tn_parent_id, decoded_parent.pk)
        self.assertEqual(decoded_child._state.fields_cache, {})
        self.assertNotIn("tn_parent", decoded_child.__dict__)
        self.assertIn(decoded_parent, get_refs(Category))
        self.assertIn(decoded_child, get_refs(Category))

    def test_related_owner_cross_alias_is_rejected(self):
        def write_alias(model, **kwargs):
            return "replica" if model is CategoryOwner else "default"

        with patch("treenode.safe_registry.router.db_for_write", side_effect=write_alias), patch(
            "treenode.safe_registry.router.allow_migrate", return_value=True
        ):
            with self.assertRaises(SafeRegistrationError):
                register_safe_model(Category, fields=default_field_codecs(Category))

    def test_forward_owner_set_null_requires_local_alias(self):
        with patch("treenode.safe_registry.router.db_for_write", return_value="default"), patch(
            "treenode.safe_registry.router.allow_migrate", return_value=True
        ):
            register_safe_model(CategoryWithOwner, fields=default_field_codecs(CategoryWithOwner))

        safe_registry._registrations.pop(CategoryWithOwner._meta.label_lower)

        def write_alias(model, **kwargs):
            return "replica" if model is CategoryOwner else "default"

        with patch("treenode.safe_registry.router.db_for_write", side_effect=write_alias), patch(
            "treenode.safe_registry.router.allow_migrate", return_value=True
        ):
            with self.assertRaises(SafeRegistrationError):
                register_safe_model(CategoryWithOwner, fields=default_field_codecs(CategoryWithOwner))
