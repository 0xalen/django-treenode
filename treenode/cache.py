import logging
from collections import defaultdict
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.db import connections, transaction

from treenode.exceptions import CacheError, SafeCodecError
from treenode.utils import split_pks

logger = logging.getLogger(__name__)


def _safe_cache_alias():
    alias = getattr(settings, "TREENODE_SAFE_CACHE_ALIAS", None)
    if alias is None:
        return None
    if type(alias) is not str or not alias or alias in {"default", "treenode"}:
        raise CacheError("TREENODE_SAFE_CACHE_ALIAS must name a dedicated cache alias")
    return alias


def _safe_cache():
    alias = _safe_cache_alias()
    if alias is None:
        return None
    try:
        from treenode.backends.base import SafeBaseCache

        backend = caches[alias]
    except Exception as exc:
        raise CacheError("safe cache alias is unavailable") from exc
    if not isinstance(backend, SafeBaseCache):
        raise CacheError("safe cache alias must use a treenode safe backend")
    return backend


def _safe_registration(model):
    from treenode.revision import get_active_registration

    return get_active_registration(model)


def _safe_cache_selected(model) -> bool:
    return (
        getattr(settings, "TREENODE_SAFE_CACHE_ALIAS", None) is not None
        and _safe_registration(model) is not None
    )


def _safe_timeout() -> int:
    timeout = getattr(settings, "TREENODE_SAFE_CACHE_TIMEOUT", 300)
    if type(timeout) is not int or timeout <= 0:
        raise CacheError("TREENODE_SAFE_CACHE_TIMEOUT must be a positive integer")
    return timeout


def _safe_attempts() -> int:
    attempts = getattr(settings, "TREENODE_SAFE_CACHE_REBUILD_ATTEMPTS", 2)
    if type(attempts) is not int or not 1 <= attempts <= 8:
        raise CacheError("TREENODE_SAFE_CACHE_REBUILD_ATTEMPTS must be between 1 and 8")
    return attempts


def _snapshot_key(registration, revision: int) -> str:
    return (
        f"safe-tree-v1:{registration.alias}:{registration.model._meta.label_lower}:"
        f"{registration.schema_fingerprint}:{revision}"
    )


def _db_rows(model, using: str):
    return tuple(model.objects.using(using).all())


@contextmanager
def _coherent_snapshot_transaction(using: str):
    connection = connections[using]
    if connection.vendor == "postgresql":
        with transaction.atomic(using=using):
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            yield
        return
    if connection.vendor == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        return
    raise CacheError("safe snapshots require PostgreSQL or SQLite")


def _coherent_snapshot(model, using: str):
    from treenode.revision import current_revision

    for _ in range(_safe_attempts()):
        with _coherent_snapshot_transaction(using):
            before = current_revision(model, using)
            rows = _db_rows(model, using)
            after = current_revision(model, using)
        if before == after:
            return before, rows
    return None


def get_safe_snapshot(model, *, using: str):
    from treenode.revision import current_revision
    from treenode.safe_codec import decode_snapshot, encode_snapshot

    registration = _safe_registration(model)
    if not registration or registration.alias != using:
        return _db_rows(model, using)
    if connections[using].in_atomic_block:
        return _db_rows(model, using)
    try:
        backend = _safe_cache()
        if backend is None:
            return _db_rows(model, using)
        revision = current_revision(model, using)
        key = _snapshot_key(registration, revision)
        raw = backend.get(key)
    except Exception as exc:
        logger.warning(
            "safe tree cache read failed for %s: %s", registration.model, exc
        )
        return _db_rows(model, using)
    if raw is not None:
        try:
            if type(raw) is not bytes:
                raise SafeCodecError("safe tree cache entry is not bytes")
            return decode_snapshot(registration.model, using, revision, raw)
        except SafeCodecError as exc:
            logger.warning(
                "safe tree cache entry is malformed for %s: %s",
                registration.model,
                exc,
            )
            return _db_rows(model, using)
    try:
        coherent = _coherent_snapshot(registration.model, using)
    except Exception as exc:
        logger.warning(
            "safe tree cache rebuild failed for %s: %s", registration.model, exc
        )
        return _db_rows(model, using)
    if coherent is None:
        logger.warning(
            "safe tree cache rebuild changed revision for %s", registration.model
        )
        return _db_rows(model, using)
    revision, rows = coherent
    try:
        raw = encode_snapshot(registration.model, using, revision, rows)
        backend.add(_snapshot_key(registration, revision), raw, timeout=_safe_timeout())
    except Exception as exc:
        logger.warning(
            "safe tree cache publish failed for %s: %s", registration.model, exc
        )
    return rows


def _get_cache():
    return caches["treenode"] if "treenode" in settings.CACHES else default_cache


def _get_cache_name():
    return "treenode" if "treenode" in settings.CACHES else "default"


def _get_cached_collection(key, dict_cls):
    c = _get_cache()
    value = c.get(key, None)
    if value is None:
        value = defaultdict(dict_cls)
        c.set(key, value)
    return value


def _get_cached_collections():
    ls = _get_cached_collection("treenode_list", list)
    d = _get_cached_collection("treenode_dict", dict)
    return (ls, d)


def _set_cached_collections(ls, d):
    c = _get_cache()
    c.set("treenode_list", ls)
    c.set("treenode_dict", d)


def clear_cache(cls):
    if _safe_cache_selected(cls):
        return
    ls, d = _get_cached_collections()
    del ls[cls][:]
    d[cls].clear()
    _set_cached_collections(ls, d)


def query_cache(cls, pk=None, pks=None):
    if _safe_cache_selected(cls):
        registration = _safe_registration(cls)
        rows = get_safe_snapshot(registration.model, using=registration.alias)
        values = {str(obj.pk): obj for obj in rows}
        if pk is not None:
            return values.get(str(pk))
        if pks is not None:
            return [values.get(str(value)) for value in split_pks(pks)]
        return list(rows)
    ls, d = _get_cached_collections()
    if not ls[cls] or not d[cls]:
        update_cache(cls)
        ls, d = _get_cached_collections()
    if pk is not None:
        return d[cls].get(str(pk))
    elif pks is not None:
        return [d[cls].get(str(pk)) for pk in split_pks(pks)]
    else:
        return list(ls[cls])


def update_cache(cls):
    if _safe_cache_selected(cls):
        return
    objs = list(cls.objects.all())
    ls, d = _get_cached_collections()
    ls[cls] = objs
    d[cls] = {str(obj.pk): obj for obj in objs}
    _set_cached_collections(ls, d)
    # ensure cache has been updated correctly
    if len(objs):
        ls, d = _get_cached_collections()
        if not ls[cls] or not d[cls]:
            cn = _get_cache_name()
            msg = (
                f"Unable to update cache '{cn}', "
                "please check 'settings.CACHES' configuration."
            )
            logger.warning(msg)
            raise CacheError(msg)
