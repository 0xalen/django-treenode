from contextlib import contextmanager

from django.core.management.sql import emit_post_migrate_signal
from django.db import OperationalError, ProgrammingError, connection
from django.test import TransactionTestCase

from tests.models import Category, CategoryOwner

ModelToBeDestroyed = Category


class TreeNodeDropTableTestCase(TransactionTestCase):
    @contextmanager
    def assertNotRaises(self, exc_type):
        try:
            yield None
        except exc_type as error:
            raise self.failureException(f"{error} raised") from error

    def test_destroy_model(self):
        table_name = ModelToBeDestroyed._meta.db_table
        # tests_categoryowner.category references this table. PostgreSQL
        # rejects a plain DROP; CASCADE removes that foreign key, not the
        # referencing table. SQLite rejects CASCADE and accepts a plain DROP.
        drop_table = f"DROP TABLE {table_name}"
        if connection.vendor == "postgresql":
            drop_table = f"DROP TABLE {table_name} CASCADE"
        try:
            # drop the table (as if by a `migrate app zero`)
            with connection.cursor() as cursor:
                cursor.execute(drop_table)

            # try to use the model - this should raise an error
            with self.assertRaises((OperationalError, ProgrammingError)):
                list(ModelToBeDestroyed.objects.all())

            # emit a post_migrate signal as done after migration to zero
            with self.assertNotRaises((OperationalError, ProgrammingError)):
                emit_post_migrate_signal(
                    verbosity=1,
                    interactive=False,
                    db=connection.alias,
                )

            # verify the table is still gone
            with self.assertRaises((OperationalError, ProgrammingError)):
                list(ModelToBeDestroyed.objects.all())
        finally:
            with connection.schema_editor() as editor:
                editor.create_model(ModelToBeDestroyed)
                if connection.vendor == "postgresql":
                    # CASCADE dropped this constraint; the referencing table stayed.
                    field = CategoryOwner._meta.get_field("category")
                    editor.execute(
                        editor._create_fk_sql(
                            CategoryOwner,
                            field,
                            "_fk_%(to_table)s_%(to_column)s",
                        )
                    )


class TreeNodeAbstractProxyTestCase(TransactionTestCase):
    """
    Regression test for issue #215.

    post_migrate_treenode calls update_tree() on every model that passes
    __is_treenode_model().  That check uses only isclass() / issubclass() and
    therefore also matches proxy models that are Python ABCs (i.e. they carry
    unimplemented @abstractmethod declarations).  When the underlying table is
    non-empty, Django tries to materialise rows as instances of the abstract
    class, which raises TypeError.

    This test verifies the inspect.isabstract() guard in
    __is_treenode_model(): without that guard, post_migrate_treenode would
    still call update_tree() for abstract proxy models and raise TypeError.
    """

    @contextmanager
    def assertNotRaises(self, exc_type):
        try:
            yield None
        except exc_type as error:
            raise self.failureException(f"{error} raised") from error

    def test_post_migrate_does_not_raise_for_abstract_proxy_model(self):
        # Populate the underlying table so that update_tree() actually tries
        # to materialise model instances (empty querysets never trigger the bug).
        Category.objects.create(name="root")

        # Emitting post_migrate must not raise TypeError for abstract proxy
        # models registered in the app.  Without the fix this will fail with:
        #   TypeError: Can't instantiate abstract class AbstractCategoryProxy
        #   without an implementation for abstract method 'my_method'
        with self.assertNotRaises(TypeError):
            emit_post_migrate_signal(
                verbosity=0,
                interactive=False,
                db=connection.alias,
            )
