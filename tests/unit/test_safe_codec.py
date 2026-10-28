import json
import os
import pickle
import unittest
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")

import django

django.setup()

from django.db import models
from django.test import override_settings

from tests.models import AbstractCategoryProxy, Category, CategoryWithStringPk, CategoryWithUUIDPk
from treenode.exceptions import SafeCodecError, SafeRegistrationError
from treenode.safe_codec import (
    BinaryCodec,
    DateTimeCodec,
    DecimalCodec,
    IntegerCodec,
    JSONCodec,
    StringCodec,
    UUIDCodec,
    decode_snapshot,
    default_field_codecs,
    encode_snapshot,
)
from treenode.safe_registry import add_registration_listener, get_safe_model_registration, register_safe_model
import treenode.safe_registry as safe_registry


LIMITS = {
    "max_payload_bytes": 4096,
    "max_nodes": 8,
    "max_fields": 32,
    "max_depth": 4,
    "max_string_bytes": 128,
    "max_container_items": 32,
}


class SafeCodecUnitTests(unittest.TestCase):
    def setUp(self):
        self.settings_override = override_settings(TREENODE_SAFE_CODEC_LIMITS=LIMITS)
        self.settings_override.enable()
        safe_registry._registrations.clear()
        safe_registry._listeners.clear()

    def tearDown(self):
        self.settings_override.disable()

    def register(self, model=Category):
        register_safe_model(model, fields=default_field_codecs(model))

    def snapshot(self, model=Category, rows=()):
        self.register(model)
        return encode_snapshot(model, "default", 1, rows)

    def test_field_codec_contract(self):
        self.assertEqual(StringCodec().decode("value"), "value")
        self.assertEqual(IntegerCodec().decode(7), 7)
        self.assertEqual(DecimalCodec().decode("1.20"), Decimal("1.20"))
        self.assertEqual(UUIDCodec().decode(str(uuid.UUID(int=1))), uuid.UUID(int=1))
        self.assertEqual(BinaryCodec().decode("Ynl0ZXM="), b"bytes")
        self.assertEqual(DateTimeCodec(models.DateField).decode("2024-01-02"), date(2024, 1, 2))
        with self.assertRaises(SafeCodecError):
            IntegerCodec().encode(True)

    def test_empty_snapshot_is_valid(self):
        raw = self.snapshot()
        self.assertEqual(decode_snapshot(Category, "default", 1, raw), ())

    def test_int_string_uuid_pk_roundtrip(self):
        for model, row in (
            (Category, Category(pk=1, name="integer")),
            (CategoryWithStringPk, CategoryWithStringPk(id="string", name="string")),
            (CategoryWithUUIDPk, CategoryWithUUIDPk(id=uuid.UUID(int=1), name="uuid")),
        ):
            raw = self.snapshot(model, (row,))
            decoded = decode_snapshot(model, "default", 1, raw)
            self.assertEqual(decoded[0].pk, row.pk)

    def test_decimal_datetime_uuid_binary_json_fields(self):
        self.assertEqual(DecimalCodec().decode(DecimalCodec().encode(Decimal("1.20"))), Decimal("1.20"))
        self.assertEqual(DateTimeCodec(models.DateTimeField).decode("2024-01-02T03:04:05"), datetime(2024, 1, 2, 3, 4, 5))
        self.assertEqual(DateTimeCodec(models.TimeField).decode("03:04:05"), time(3, 4, 5))
        value = uuid.UUID(int=2)
        self.assertEqual(UUIDCodec().decode(UUIDCodec().encode(value)), value)
        self.assertEqual(BinaryCodec().decode(BinaryCodec().encode(b"binary")), b"binary")
        self.assertEqual(JSONCodec().decode({"items": [None, True, 3]}), {"items": [None, True, 3]})

    def test_parent_and_order_roundtrip(self):
        parent = Category(pk=1, name="parent", tn_order=2)
        child = Category(pk=2, name="child", tn_parent_id=1, tn_order=3)
        raw = self.snapshot(rows=(parent, child))
        decoded = decode_snapshot(Category, "default", 1, raw)
        self.assertEqual([(row.pk, row.tn_parent_id, row.tn_order) for row in decoded], [(1, None, 2), (2, 1, 3)])

    def test_malformed_and_duplicate_members_rejected(self):
        self.register()
        with self.assertRaises(SafeCodecError):
            decode_snapshot(Category, "default", 1, b'{"rows":[],"rows":[]}')
        raw = json.loads(encode_snapshot(Category, "default", 1, (Category(pk=1, name="one"),)))
        raw["rows"].append(raw["rows"][0])
        with self.assertRaises(SafeCodecError):
            decode_snapshot(Category, "default", 1, json.dumps(raw).encode())

    def test_unknown_model_field_alias_version_rejected(self):
        self.register()
        raw = json.loads(encode_snapshot(Category, "default", 1, ()))
        for key, value in (("model", "other.model"), ("database", "other"), ("version", 2), ("fields", [])):
            changed = dict(raw)
            changed[key] = value
            with self.assertRaises(SafeCodecError):
                decode_snapshot(Category, "default", 1, json.dumps(changed).encode())

    def test_pickle_payload_never_executes(self):
        self.register()
        with patch.object(pickle, "loads", side_effect=AssertionError("pickle executed")) as loads:
            with self.assertRaises(SafeCodecError):
                decode_snapshot(Category, "default", 1, b"cos\nsystem\n(S'boom'\ntR.")
        loads.assert_not_called()

    def test_bounds_exact_edge_and_overflow(self):
        self.assertEqual(JSONCodec().decode("x" * LIMITS["max_string_bytes"]), "x" * LIMITS["max_string_bytes"])
        with self.assertRaises(SafeCodecError):
            JSONCodec().decode("x" * (LIMITS["max_string_bytes"] + 1))
        self.snapshot(rows=tuple(Category(pk=index, name=str(index)) for index in range(LIMITS["max_nodes"])))
        with self.assertRaises(SafeCodecError):
            encode_snapshot(
                Category,
                "default",
                1,
                tuple(Category(pk=index, name=str(index)) for index in range(LIMITS["max_nodes"] + 1)),
            )

    def test_custom_field_requires_explicit_codec(self):
        class CustomCharField(models.CharField):
            pass

        with self.assertRaises(SafeCodecError):
            StringCodec().validate(CustomCharField())

    def test_registration_canonicalizes_concrete_model(self):
        self.register(Category)
        self.assertIs(get_safe_model_registration(Category).model, Category)

    def test_registration_requires_exact_persisted_field_mapping(self):
        fields = default_field_codecs(Category)
        fields.pop("name")
        with self.assertRaises(SafeRegistrationError):
            register_safe_model(Category, fields=fields)
        fields = default_field_codecs(Category)
        fields["unexpected"] = StringCodec()
        with self.assertRaises(SafeRegistrationError):
            register_safe_model(Category, fields=fields)

    def test_registration_rejects_abstract_proxy_unknown_and_conflicting_entries(self):
        with self.assertRaises(SafeRegistrationError):
            register_safe_model(object, fields={})
        with self.assertRaises(SafeRegistrationError):
            register_safe_model(AbstractCategoryProxy, fields=default_field_codecs(Category))
        self.register()
        fields = default_field_codecs(Category)
        fields["name"] = IntegerCodec()
        with self.assertRaises(SafeRegistrationError):
            register_safe_model(Category, fields=fields)

    def test_registration_rejects_custom_from_db_and_unvalidated_field_codec(self):
        with patch.object(Category, "from_db", classmethod(lambda cls, *args: None)):
            with self.assertRaises(SafeRegistrationError):
                register_safe_model(Category, fields=default_field_codecs(Category))
        fields = default_field_codecs(Category)
        fields["name"] = object()
        with self.assertRaises(SafeRegistrationError):
            register_safe_model(Category, fields=fields)

    def test_registration_validates_no_hint_router_and_migration_policy(self):
        with patch("treenode.safe_registry.router.db_for_write", return_value="missing"):
            with self.assertRaises(SafeRegistrationError):
                self.register()
        with patch("treenode.safe_registry.router.allow_migrate", return_value=False):
            with self.assertRaises(SafeRegistrationError):
                self.register()

    def test_registration_idempotence(self):
        self.register()
        self.register()
        self.assertEqual(len(safe_registry.iter_safe_model_registrations()), 1)

    def test_registration_has_no_database_access(self):
        with patch("treenode.safe_registry.router.db_for_write", return_value="default"), patch(
            "treenode.safe_registry.router.allow_migrate", return_value=True
        ), patch("django.db.connections.__getitem__", side_effect=AssertionError("database access")):
            self.register()

    def test_lifecycle_listener_observes_existing_and_new_entries(self):
        self.register(Category)
        observed = []
        add_registration_listener(observed.append)
        self.assertEqual(observed, [get_safe_model_registration(Category)])
        self.register(CategoryWithStringPk)
        self.assertEqual(observed[-1], get_safe_model_registration(CategoryWithStringPk))
