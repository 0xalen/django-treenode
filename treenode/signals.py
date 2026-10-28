from inspect import isabstract, isclass

from django.db import connections
from django.db.models.signals import (
    post_delete,
    post_init,
    post_migrate,
    post_save,
    pre_delete,
    pre_save,
)

from treenode.memory import set_ref


def __table_exists(table_name: str, connection_name: str) -> bool:
    return table_name in connections[connection_name].introspection.table_names()


def __is_treenode_model(sender):
    from .models import TreeNodeModel

    # return isinstance(instance, TreeNodeModel) and \
    #         instance.__class__ != TreeNodeModel
    return (
        isclass(sender)
        and issubclass(sender, TreeNodeModel)
        and sender != TreeNodeModel
        and not isabstract(sender)
    )


def post_init_treenode(sender, instance, **kwargs):
    if not __is_treenode_model(sender):
        return
    set_ref(sender, instance)


def post_migrate_treenode(sender, **kwargs):
    from treenode.revision import (
        activate_registered_models,
        advance_revision,
        get_active_registration,
    )

    activate_registered_models(raise_error=False)
    for sender_model in list(sender.get_models()):
        if __is_treenode_model(sender_model) and __table_exists(
            table_name=sender_model._meta.db_table, connection_name=kwargs["using"]
        ):
            registration = get_active_registration(sender_model)
            if registration and registration.alias == kwargs["using"]:
                from django.db import transaction

                with transaction.atomic(using=kwargs["using"]):
                    sender_model.update_tree(using=kwargs["using"])
                    advance_revision(sender_model, kwargs["using"])
            elif not registration:
                sender_model.update_tree()


def pre_save_treenode(sender, instance, raw=False, using=None, **kwargs):
    from treenode.revision import mutation_alias, mutation_owned, verify_instance_alias

    if not __is_treenode_model(sender):
        return
    alias = verify_instance_alias(instance, using)
    if alias is None:
        return
    if not mutation_owned(sender) or mutation_alias(sender) != alias:
        raise RuntimeError("registered treenode writes require TreeNodeModel ownership")


def post_save_treenode(sender, instance, **kwargs):
    from treenode.revision import get_active_registration, mutation_owned

    if not __is_treenode_model(sender):
        return
    set_ref(sender, instance)
    if get_active_registration(sender):
        if mutation_owned(sender):
            return
    sender.update_tree()


def post_delete_treenode(sender, instance, **kwargs):
    from treenode.revision import (
        advance_revision,
        get_active_registration,
        mutation_owned,
    )

    if not __is_treenode_model(sender):
        return
    registration = get_active_registration(sender)
    if registration:
        if mutation_owned(sender):
            return
        sender.update_tree(using=kwargs["using"])
        advance_revision(sender, kwargs["using"])
        return
    sender.update_tree()


def pre_delete_treenode(sender, instance, using, **kwargs):
    from treenode.revision import handle_related_delete, verify_instance_alias

    if __is_treenode_model(sender):
        verify_instance_alias(instance, using)
    handle_related_delete(sender, instance, using)


def connect_signals():
    post_init.connect(post_init_treenode, dispatch_uid="post_init_treenode")
    post_migrate.connect(post_migrate_treenode, dispatch_uid="post_migrate_treenode")
    pre_save.connect(pre_save_treenode, dispatch_uid="pre_save_treenode")
    post_save.connect(post_save_treenode, dispatch_uid="post_save_treenode")
    pre_delete.connect(pre_delete_treenode, dispatch_uid="pre_delete_treenode")
    post_delete.connect(post_delete_treenode, dispatch_uid="post_delete_treenode")


def disconnect_signals():
    post_init.disconnect(post_init_treenode, dispatch_uid="post_init_treenode")
    post_migrate.disconnect(post_migrate_treenode, dispatch_uid="post_migrate_treenode")
    pre_save.disconnect(pre_save_treenode, dispatch_uid="pre_save_treenode")
    post_save.disconnect(post_save_treenode, dispatch_uid="post_save_treenode")
    pre_delete.disconnect(pre_delete_treenode, dispatch_uid="pre_delete_treenode")
    post_delete.disconnect(post_delete_treenode, dispatch_uid="post_delete_treenode")


class no_signals:
    def __enter__(self):
        disconnect_signals()
        return None

    def __exit__(self, type_, value, traceback):
        connect_signals()
