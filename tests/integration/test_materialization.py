from django.test import TestCase, override_settings

from tests.models import Category
from treenode.memory import clear_refs, get_refs
from treenode.safe_codec import decode_snapshot, default_field_codecs, encode_snapshot
from treenode.safe_registry import register_safe_model
import treenode.safe_registry as safe_registry


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
