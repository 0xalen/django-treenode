from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import router

from treenode.exceptions import SafeRegistrationError

if TYPE_CHECKING:
    from treenode.safe_codec import FieldCodec


@dataclass(frozen=True)
class SafeModelRegistration:
    model: type
    alias: str
    fields: tuple[tuple[str, FieldCodec], ...]
    field_identities: tuple[tuple[str, str], ...]
    schema_fingerprint: str


_registrations: dict[str, SafeModelRegistration] = {}
_listeners: list[Callable[[SafeModelRegistration], None]] = []
_lock = RLock()


def _concrete_model(model: type) -> type:
    from treenode.models import TreeNodeModel

    if not isinstance(model, type) or not issubclass(model, TreeNodeModel):
        raise SafeRegistrationError("model must be a TreeNodeModel subclass")
    if model._meta.abstract or model._meta.proxy:
        raise SafeRegistrationError("model must be concrete and non-proxy")
    return model._meta.concrete_model


def _validate_router(model: type) -> str:
    alias = router.db_for_write(model)
    if not alias or alias not in settings.DATABASES:
        raise SafeRegistrationError("router must select a configured write alias")
    if not router.allow_migrate(
        alias,
        model._meta.app_label,
        model_name=model._meta.model_name,
        model=model,
    ):
        raise SafeRegistrationError("router disallows the registered model migration")
    if not router.allow_migrate(
        alias,
        "treenode",
        model_name="treenodecacherevision",
    ):
        raise SafeRegistrationError("router disallows the revision model migration")
    return alias


def _validate_fields(
    model: type, fields: Mapping[str, FieldCodec]
) -> tuple[tuple[tuple[str, FieldCodec], ...], tuple[tuple[str, str], ...]]:
    from treenode.safe_codec import codec_configuration_identity

    if not isinstance(fields, Mapping):
        raise SafeRegistrationError("fields must be a mapping")
    concrete_fields = tuple(model._meta.concrete_fields)
    expected = {field.attname for field in concrete_fields}
    if set(fields) != expected:
        raise SafeRegistrationError("fields must cover every persisted concrete field")
    validated: list[tuple[str, FieldCodec]] = []
    identities: list[tuple[str, str]] = []
    for field in concrete_fields:
        codec = fields[field.attname]
        if not all(callable(getattr(codec, name, None)) for name in ("validate", "encode", "decode")):
            raise SafeRegistrationError(f"field {field.attname} has no FieldCodec")
        try:
            codec.validate(field)
        except Exception as exc:
            raise SafeRegistrationError(f"field {field.attname} is not admissible") from exc
        validated.append((field.attname, codec))
        try:
            identities.append((field.attname, codec_configuration_identity(codec)))
        except Exception as exc:
            raise SafeRegistrationError(
                f"field {field.attname} has no canonical codec configuration"
            ) from exc
    return tuple(validated), tuple(identities)


def register_safe_model(model: type, *, fields: Mapping[str, FieldCodec]) -> None:
    from django.db.models import Model

    from treenode.safe_codec import schema_fingerprint

    concrete = _concrete_model(model)
    if getattr(concrete.from_db, "__func__", concrete.from_db) is not getattr(
        Model.from_db, "__func__", Model.from_db
    ):
        raise SafeRegistrationError("custom model from_db is not supported")
    alias = _validate_router(concrete)
    validated_fields, field_identities = _validate_fields(concrete, fields)
    registration = SafeModelRegistration(
        model=concrete,
        alias=alias,
        fields=validated_fields,
        field_identities=field_identities,
        schema_fingerprint=schema_fingerprint(concrete, validated_fields),
    )
    label = concrete._meta.label_lower
    with _lock:
        existing = _registrations.get(label)
        if existing:
            if (
                existing.model is not registration.model
                or existing.alias != registration.alias
                or existing.field_identities != registration.field_identities
                or existing.schema_fingerprint != registration.schema_fingerprint
            ):
                raise SafeRegistrationError("model is already registered differently")
            return
        _registrations[label] = registration
        listeners = tuple(_listeners)
    for listener in listeners:
        listener(registration)


def get_safe_model_registration(model: type) -> SafeModelRegistration:
    concrete = _concrete_model(model)
    try:
        return _registrations[concrete._meta.label_lower]
    except KeyError as exc:
        raise SafeRegistrationError("model is not registered") from exc


def iter_safe_model_registrations() -> tuple[SafeModelRegistration, ...]:
    with _lock:
        return tuple(_registrations.values())


def add_registration_listener(listener: Callable[[SafeModelRegistration], None]) -> None:
    with _lock:
        if listener in _listeners:
            return
        _listeners.append(listener)
        registrations = tuple(_registrations.values())
    for registration in registrations:
        listener(registration)
