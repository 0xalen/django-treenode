from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock

from django.db import connections, router, transaction

from treenode.exceptions import SafeActivationError, SafeRegistrationError, SafeRoutingError
from treenode.safe_registry import (
    SafeModelRegistration,
    add_registration_listener,
    get_safe_model_registration,
    iter_safe_model_registrations,
)


@dataclass(frozen=True)
class RevisionStatus:
    alias: str
    state: str
    error: str | None = None


_statuses: dict[str, RevisionStatus] = {}
_status_lock = RLock()
_mutation_models: ContextVar[frozenset[str]] = ContextVar("treenode_mutation_models", default=frozenset())
_mutation_aliases: ContextVar[dict[str, str]] = ContextVar("treenode_mutation_aliases", default={})


def _label(model: type) -> str:
    return model._meta.concrete_model._meta.label_lower


def _set_status(registration: SafeModelRegistration, state: str, error: str | None = None) -> None:
    with _status_lock:
        _statuses[registration.model._meta.label_lower] = RevisionStatus(
            alias=registration.alias, state=state, error=error
        )


def _registered(registration: SafeModelRegistration) -> None:
    _set_status(registration, "pending")


def install_revision_lifecycle() -> None:
    add_registration_listener(_registered)


def get_revision_status(model: type) -> RevisionStatus | None:
    with _status_lock:
        return _statuses.get(_label(model))


def _table_exists(alias: str, table_name: str) -> bool:
    return table_name in connections[alias].introspection.table_names()


def activate_registration(registration: SafeModelRegistration, *, raise_error: bool = True) -> bool:
    from treenode.models import TreeNodeCacheRevision

    try:
        if not _table_exists(registration.alias, registration.model._meta.db_table):
            _set_status(registration, "pending")
            return False
        if not _table_exists(registration.alias, TreeNodeCacheRevision._meta.db_table):
            raise SafeActivationError("revision table is missing after migration")
        _set_status(registration, "active")
        return True
    except Exception as exc:
        error = str(exc)
        _set_status(registration, "activation-failed", error)
        if raise_error:
            if isinstance(exc, SafeActivationError):
                raise
            raise SafeActivationError(error) from exc
        return False


def activate_registered_models(*, raise_error: bool = True) -> None:
    for registration in iter_safe_model_registrations():
        activate_registration(registration, raise_error=raise_error)


def is_active(model: type) -> bool:
    status = get_revision_status(model)
    return bool(status and status.state == "active")


def get_active_registration(model: type) -> SafeModelRegistration | None:
    try:
        registration = get_safe_model_registration(model)
    except SafeRegistrationError:
        return None
    return registration if is_active(registration.model) else None


def verify_instance_alias(instance, using: str | None = None) -> str | None:
    registration = get_active_registration(instance.__class__)
    if not registration:
        return None
    alias = router.db_for_write(registration.model, instance=instance)
    if alias != registration.alias or (using is not None and using != alias):
        raise SafeRoutingError("instance write alias differs from registered alias")
    return alias


@contextmanager
def mutation_scope(model: type, alias: str):
    label = _label(model)
    labels = _mutation_models.get()
    aliases = _mutation_aliases.get()
    token_labels = _mutation_models.set(labels | {label})
    token_aliases = _mutation_aliases.set({**aliases, label: alias})
    try:
        yield
    finally:
        _mutation_aliases.reset(token_aliases)
        _mutation_models.reset(token_labels)


def mutation_owned(model: type) -> bool:
    return _label(model) in _mutation_models.get()


def mutation_alias(model: type) -> str | None:
    return _mutation_aliases.get().get(_label(model))


def advance_revision(model: type, using: str) -> int:
    from treenode.models import TreeNodeCacheRevision

    registration = get_active_registration(model)
    if not registration or registration.alias != using:
        return 0
    if not connections[using].in_atomic_block:
        raise SafeActivationError("revision advance requires an atomic transaction")
    identity = registration.model._meta.label_lower
    connection = connections[using]
    if connection.vendor not in {"postgresql", "sqlite"}:
        raise SafeActivationError("revision increments require PostgreSQL or SQLite")
    quote = connection.ops.quote_name
    table = quote(TreeNodeCacheRevision._meta.db_table)
    label_column = quote("model_label")
    revision_column = quote("revision")
    sql = (
        f"INSERT INTO {table} ({label_column}, {revision_column}) VALUES (%s, 1) "
        f"ON CONFLICT ({label_column}) DO UPDATE SET {revision_column} = "
        f"{table}.{revision_column} + 1 WHERE {table}.{revision_column} < %s "
        f"RETURNING {revision_column}"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, [identity, 9223372036854775807])
        row = cursor.fetchone()
    if row is None:
        raise OverflowError("treenode revision overflow")
    return row[0]


@contextmanager
def tree_mutation(model: type, *, using: str | None = None):
    registration = get_active_registration(model)
    if not registration:
        yield
        return
    alias = using or registration.alias
    if alias != registration.alias:
        raise SafeRoutingError("mutation alias differs from registered alias")
    with transaction.atomic(using=alias), mutation_scope(registration.model, alias):
        yield
        registration.model.update_tree(using=alias)
        advance_revision(registration.model, alias)


def _related_tree_registrations(sender, instance, using: str):
    source_alias = router.db_for_write(sender, instance=instance)
    registrations = []
    for relation in sender._meta.related_objects:
        field = relation.field
        model = relation.related_model
        registration = get_active_registration(model)
        if not registration:
            continue
        target_alias = router.db_for_write(registration.model, instance=instance)
        if (
            source_alias != using
            or target_alias != using
            or registration.alias != using
        ):
            raise SafeRoutingError("related deletion alias differs from registered alias")
        registrations.append((field, registration))
    return registrations


def handle_related_delete(sender, instance, using: str) -> None:
    for field, registration in _related_tree_registrations(sender, instance, using):
        if field.remote_field.on_delete.__name__ in {"SET_NULL", "SET_DEFAULT", "set_on_delete"}:
            with mutation_scope(registration.model, using):
                advance_revision(registration.model, using)
