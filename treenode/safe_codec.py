from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, TypeAlias

from django.conf import settings
from django.db import models

from treenode.exceptions import SafeCodecError

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class FieldCodec(Protocol):
    def validate(self, field: models.Field) -> None: ...

    def encode(self, value: object) -> JsonValue: ...

    def decode(self, value: JsonValue) -> object: ...


def _codec_state(value: object, *, root: bool = False) -> JsonValue:
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    if isinstance(value, tuple):
        return [_codec_state(item) for item in value]
    if isinstance(value, dict):
        if not root or not all(type(key) is str for key in value):
            raise SafeCodecError("codec configuration keys must be strings")
        return {key: _codec_state(item) for key, item in value.items()}
    if all(callable(getattr(value, name, None)) for name in ("validate", "encode", "decode")):
        return {"codec": codec_configuration_identity(value)}
    raise SafeCodecError("codec configuration is not canonical")


def codec_configuration_identity(codec: FieldCodec) -> str:
    slots = getattr(codec.__class__, "__slots__", ())
    if slots not in ((), None):
        raise SafeCodecError("codec slots are not supported")
    for name, value in vars(codec.__class__).items():
        if name.startswith("__") or callable(value) or isinstance(
            value, (classmethod, staticmethod, property)
        ):
            continue
        if isinstance(value, (bytearray, dict, list, set)):
            raise SafeCodecError("codec class configuration is mutable")
    try:
        state = _codec_state(vars(codec), root=True)
    except TypeError as exc:
        raise SafeCodecError("codec configuration is not inspectable") from exc
    payload = {
        "class": f"{codec.__class__.__module__}.{codec.__class__.__qualname__}",
        "state": state,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class VerifiedFieldCodec:
    codec: FieldCodec
    identity: str

    def _verified(self) -> FieldCodec:
        if codec_configuration_identity(self.codec) != self.identity:
            raise SafeCodecError("codec configuration changed after registration")
        return self.codec

    def validate(self, field: models.Field) -> None:
        self._verified().validate(field)

    def encode(self, value: object) -> JsonValue:
        return self._verified().encode(value)

    def decode(self, value: JsonValue) -> object:
        return self._verified().decode(value)


def verify_field_codec(codec: FieldCodec) -> VerifiedFieldCodec:
    return VerifiedFieldCodec(codec=codec, identity=codec_configuration_identity(codec))


@dataclass(frozen=True)
class SafeCodecLimits:
    max_payload_bytes: int
    max_nodes: int
    max_fields: int
    max_depth: int
    max_string_bytes: int
    max_container_items: int


def _limits() -> SafeCodecLimits:
    value = getattr(settings, "TREENODE_SAFE_CODEC_LIMITS", None)
    names = tuple(SafeCodecLimits.__annotations__)
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise SafeCodecError("TREENODE_SAFE_CODEC_LIMITS must define every codec limit")
    if any(type(value[name]) is not int or value[name] <= 0 for name in names):
        raise SafeCodecError("codec limits must be positive integers")
    return SafeCodecLimits(**{name: value[name] for name in names})


class _ExactFieldCodec:
    field_types: tuple[type[models.Field], ...] = ()

    def validate(self, field: models.Field) -> None:
        if type(field) not in self.field_types:
            raise SafeCodecError(f"unsupported field type {field.__class__.__name__}")


class StringCodec(_ExactFieldCodec):
    field_types = (models.CharField, models.TextField, models.EmailField, models.URLField, models.SlugField, models.GenericIPAddressField)

    def encode(self, value: object) -> JsonValue:
        if not isinstance(value, str):
            raise SafeCodecError("expected string")
        return value

    def decode(self, value: JsonValue) -> object:
        if not isinstance(value, str):
            raise SafeCodecError("expected string")
        return value


class IntegerCodec(_ExactFieldCodec):
    field_types = (models.AutoField, models.BigAutoField, models.SmallAutoField, models.IntegerField, models.BigIntegerField, models.SmallIntegerField, models.PositiveIntegerField, models.PositiveBigIntegerField, models.PositiveSmallIntegerField)

    def encode(self, value: object) -> JsonValue:
        if type(value) is not int:
            raise SafeCodecError("expected integer")
        return value

    def decode(self, value: JsonValue) -> object:
        if type(value) is not int:
            raise SafeCodecError("expected integer")
        return value


class BooleanCodec(_ExactFieldCodec):
    field_types = (models.BooleanField,)

    def encode(self, value: object) -> JsonValue:
        if type(value) is not bool:
            raise SafeCodecError("expected boolean")
        return value

    decode = encode


class FloatCodec(_ExactFieldCodec):
    field_types = (models.FloatField,)

    def encode(self, value: object) -> JsonValue:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise SafeCodecError("expected finite number")
        return float(value)

    decode = encode


class DecimalCodec(_ExactFieldCodec):
    field_types = (models.DecimalField,)

    def encode(self, value: object) -> JsonValue:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise SafeCodecError("expected finite Decimal")
        return str(value)

    def decode(self, value: JsonValue) -> object:
        if not isinstance(value, str):
            raise SafeCodecError("expected Decimal string")
        try:
            result = Decimal(value)
        except Exception as exc:
            raise SafeCodecError("invalid Decimal") from exc
        if not result.is_finite():
            raise SafeCodecError("expected finite Decimal")
        return result


class DateTimeCodec(_ExactFieldCodec):
    def __init__(self, field_type: type[models.Field]):
        self.field_types = (field_type,)

    def encode(self, value: object) -> JsonValue:
        if not isinstance(value, (dt.datetime, dt.date, dt.time)):
            raise SafeCodecError("expected date/time")
        return value.isoformat()

    def decode(self, value: JsonValue) -> object:
        if not isinstance(value, str):
            raise SafeCodecError("expected date/time string")
        try:
            if self.field_types == (models.DateTimeField,):
                return dt.datetime.fromisoformat(value)
            if self.field_types == (models.DateField,):
                return dt.date.fromisoformat(value)
            return dt.time.fromisoformat(value)
        except (ValueError, binascii.Error) as exc:
            raise SafeCodecError("invalid date/time") from exc


class UUIDCodec(_ExactFieldCodec):
    field_types = (models.UUIDField,)

    def encode(self, value: object) -> JsonValue:
        if not isinstance(value, uuid.UUID):
            raise SafeCodecError("expected UUID")
        return str(value)

    def decode(self, value: JsonValue) -> object:
        if not isinstance(value, str):
            raise SafeCodecError("expected UUID string")
        try:
            return uuid.UUID(value)
        except ValueError as exc:
            raise SafeCodecError("invalid UUID") from exc


class BinaryCodec(_ExactFieldCodec):
    field_types = (models.BinaryField,)

    def encode(self, value: object) -> JsonValue:
        if not isinstance(value, bytes):
            raise SafeCodecError("expected bytes")
        return base64.b64encode(value).decode("ascii")

    def decode(self, value: JsonValue) -> object:
        if not isinstance(value, str):
            raise SafeCodecError("expected base64 string")
        try:
            return base64.b64decode(value.encode("ascii"), validate=True)
        except ValueError as exc:
            raise SafeCodecError("invalid base64") from exc


class JSONCodec(_ExactFieldCodec):
    field_types = (models.JSONField,)

    def encode(self, value: object) -> JsonValue:
        _validate_json(value, _limits(), 0)
        return value

    def decode(self, value: JsonValue) -> object:
        _validate_json(value, _limits(), 0)
        return value


class ForeignKeyCodec:
    def __init__(self, target_codec: FieldCodec):
        self.target_codec = target_codec

    def validate(self, field: models.Field) -> None:
        if not isinstance(field, models.ForeignKey):
            raise SafeCodecError("expected foreign key")
        self.target_codec.validate(field.target_field)

    def encode(self, value: object) -> JsonValue:
        return self.target_codec.encode(value)

    def decode(self, value: JsonValue) -> object:
        return self.target_codec.decode(value)


def _codec_for_field(field: models.Field) -> FieldCodec:
    if type(field) is models.ForeignKey:
        return ForeignKeyCodec(_codec_for_field(field.target_field))
    if type(field) in (models.DateTimeField, models.DateField, models.TimeField):
        return DateTimeCodec(type(field))
    for codec in (StringCodec(), IntegerCodec(), BooleanCodec(), FloatCodec(), DecimalCodec(), UUIDCodec(), BinaryCodec(), JSONCodec()):
        try:
            codec.validate(field)
        except SafeCodecError:
            continue
        return codec
    raise SafeCodecError(f"no built-in codec for {field.__class__.__name__}")


def default_field_codecs(model: type[models.Model]) -> dict[str, FieldCodec]:
    return {field.attname: _codec_for_field(field) for field in model._meta.concrete_fields}


def schema_fingerprint(model: type, fields: Sequence[tuple[str, FieldCodec]]) -> str:
    material = "\n".join(
        f"{field.attname}:{field.__class__.__module__}.{field.__class__.__qualname__}:{codec_configuration_identity(codec)}"
        for field, (_, codec) in zip(model._meta.concrete_fields, fields, strict=True)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _validate_json(value: object, limits: SafeCodecLimits, depth: int) -> None:
    if depth > limits.max_depth:
        raise SafeCodecError("value exceeds maximum depth")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise SafeCodecError("non-finite float")
        return
    if type(value) is str:
        if len(value.encode("utf-8")) > limits.max_string_bytes:
            raise SafeCodecError("string exceeds limit")
        return
    if type(value) is list:
        if len(value) > limits.max_container_items:
            raise SafeCodecError("list exceeds limit")
        for item in value:
            _validate_json(item, limits, depth + 1)
        return
    if type(value) is dict:
        if len(value) > limits.max_container_items:
            raise SafeCodecError("object exceeds limit")
        for key, item in value.items():
            if type(key) is not str:
                raise SafeCodecError("object keys must be strings")
            _validate_json(key, limits, depth + 1)
            _validate_json(item, limits, depth + 1)
        return
    raise SafeCodecError("value is outside the JSON domain")


def _json_loads(raw: bytes, limits: SafeCodecLimits) -> dict[str, JsonValue]:
    if len(raw) > limits.max_payload_bytes:
        raise SafeCodecError("payload exceeds limit")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SafeCodecError("duplicate JSON member")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafeCodecError("invalid JSON snapshot") from exc
    _validate_json(value, limits, 0)
    if type(value) is not dict:
        raise SafeCodecError("snapshot root must be an object")
    return value


def encode_snapshot(model: type[models.Model], using: str, revision: int, rows: Sequence[models.Model]) -> bytes:
    from treenode.safe_registry import get_safe_model_registration

    registration = get_safe_model_registration(model)
    limits = _limits()
    if using != registration.alias or type(revision) is not int or revision < 0:
        raise SafeCodecError("invalid snapshot identity")
    fields = tuple(model._meta.concrete_fields)
    if len(fields) > limits.max_fields or len(rows) > limits.max_nodes:
        raise SafeCodecError("snapshot exceeds configured bounds")
    encoded_rows: list[JsonValue] = []
    for row in rows:
        if not isinstance(row, registration.model):
            raise SafeCodecError("snapshot contains wrong model")
        values: list[JsonValue] = []
        for field, (_, codec) in zip(fields, registration.fields, strict=True):
            value = getattr(row, field.attname)
            if value is None and field.null:
                values.append(None)
            else:
                values.append(codec.encode(value))
        encoded_rows.append(values)
    payload: dict[str, JsonValue] = {
        "version": 1,
        "model": registration.model._meta.label_lower,
        "database": using,
        "schema": registration.schema_fingerprint,
        "revision": revision,
        "fields": [field.attname for field in fields],
        "rows": encoded_rows,
    }
    _validate_json(payload, limits, 0)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(raw) > limits.max_payload_bytes:
        raise SafeCodecError("payload exceeds limit")
    return raw


def decode_snapshot(model: type[models.Model], using: str, revision: int, raw: bytes) -> tuple[models.Model, ...]:
    from treenode.safe_registry import get_safe_model_registration

    registration = get_safe_model_registration(model)
    limits = _limits()
    payload = _json_loads(raw, limits)
    fields = tuple(model._meta.concrete_fields)
    expected = {
        "version": 1,
        "model": registration.model._meta.label_lower,
        "database": using,
        "schema": registration.schema_fingerprint,
        "revision": revision,
        "fields": [field.attname for field in fields],
    }
    if any(payload.get(key) != value for key, value in expected.items()) or set(payload) != set(expected) | {"rows"}:
        raise SafeCodecError("snapshot identity mismatch")
    rows = payload["rows"]
    if type(rows) is not list or len(rows) > limits.max_nodes:
        raise SafeCodecError("invalid snapshot rows")
    decoded: list[models.Model] = []
    identities = set()
    parent_index = next((index for index, field in enumerate(fields) if field.name == "tn_parent"), None)
    pk_index = next(index for index, field in enumerate(fields) if field.primary_key)
    for encoded in rows:
        if type(encoded) is not list or len(encoded) != len(fields):
            raise SafeCodecError("invalid snapshot row")
        values = []
        for field, (_, codec), value in zip(fields, registration.fields, encoded, strict=True):
            if value is None and field.null:
                values.append(None)
            else:
                values.append(codec.decode(value))
        identity = json.dumps(encoded[pk_index], sort_keys=True, separators=(",", ":"))
        if identity in identities:
            raise SafeCodecError("duplicate primary key")
        identities.add(identity)
        decoded.append(registration.model.from_db(using, [field.attname for field in fields], values))
    if parent_index is not None:
        parent_map = {str(getattr(row, fields[pk_index].attname)): getattr(row, fields[parent_index].attname) for row in decoded}
        if any(
            parent is not None and str(parent) not in parent_map
            for parent in parent_map.values()
        ):
            raise SafeCodecError("unknown parent identity")
        for key in parent_map:
            seen = set()
            cursor = key
            while parent_map.get(cursor) is not None:
                if cursor in seen:
                    raise SafeCodecError("parent cycle")
                seen.add(cursor)
                cursor = str(parent_map[cursor])
    return tuple(decoded)
