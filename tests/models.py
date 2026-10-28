import random
import string
import uuid
from abc import ABCMeta, abstractmethod

from django.db import models
from django.db.models.base import ModelBase

from treenode.models import TreeNodeModel


class Category(TreeNodeModel):
    treenode_display_field = "name"

    name = models.CharField(max_length=50, unique=True)

    class Meta(TreeNodeModel.Meta):
        app_label = "tests"
        verbose_name = "Category"
        verbose_name_plural = "Categories"


class CategoryFixtures(TreeNodeModel):
    treenode_display_field = "name"

    name = models.CharField(max_length=50, unique=True)

    class Meta(TreeNodeModel.Meta):
        app_label = "tests"
        verbose_name = "Category Fixtures"
        verbose_name_plural = "Categories Fixtures"


class CategoryWithStringPk(TreeNodeModel):
    treenode_display_field = "name"

    @staticmethod
    def get_random_string():
        return "".join(random.choice(string.letters + string.digits) for n in range(64))

    id = models.CharField(
        primary_key=True, max_length=100, default=get_random_string, editable=False
    )
    name = models.CharField(max_length=50, unique=True)

    class Meta(TreeNodeModel.Meta):
        app_label = "tests"
        verbose_name = "Category"
        verbose_name_plural = "Categories"


class CategoryWithUUIDPk(TreeNodeModel):
    treenode_display_field = "name"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, unique=True)

    class Meta(TreeNodeModel.Meta):
        app_label = "tests"
        verbose_name = "Category"
        verbose_name_plural = "Categories"


class CategoryWithoutDisplayField(TreeNodeModel):
    name = models.CharField(max_length=50, unique=True)

    class Meta(TreeNodeModel.Meta):
        app_label = "tests"
        verbose_name = "Category"
        verbose_name_plural = "Categories"


class CategoryOwner(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE)

    class Meta:
        app_label = "tests"


class CategoryWithOwner(TreeNodeModel):
    treenode_display_field = "name"

    name = models.CharField(max_length=50, unique=True)
    owner = models.ForeignKey(CategoryOwner, null=True, on_delete=models.SET_NULL)

    class Meta(TreeNodeModel.Meta):
        app_label = "tests"
        verbose_name = "Category With Owner"
        verbose_name_plural = "Categories With Owner"


class CategoryWithDefaultOwner(TreeNodeModel):
    treenode_display_field = "name"

    name = models.CharField(max_length=50, unique=True)
    owner = models.ForeignKey(
        CategoryOwner, default=1, null=True, on_delete=models.SET_DEFAULT
    )

    class Meta(TreeNodeModel.Meta):
        app_label = "tests"
        verbose_name = "Category With Default Owner"
        verbose_name_plural = "Categories With Default Owner"


class CategoryWithCallableOwner(TreeNodeModel):
    treenode_display_field = "name"

    name = models.CharField(max_length=50, unique=True)
    owner = models.ForeignKey(CategoryOwner, null=True, on_delete=models.SET(lambda: 1))

    class Meta(TreeNodeModel.Meta):
        app_label = "tests"
        verbose_name = "Category With Callable Owner"
        verbose_name_plural = "Categories With Callable Owner"


class CategoryWithCascadeOwner(TreeNodeModel):
    treenode_display_field = "name"

    name = models.CharField(max_length=50, unique=True)
    owner = models.ForeignKey(CategoryOwner, on_delete=models.CASCADE)

    class Meta(TreeNodeModel.Meta):
        app_label = "tests"
        verbose_name = "Category With Cascade Owner"
        verbose_name_plural = "Categories With Cascade Owner"


class CategoryWithUnsupportedOwner(TreeNodeModel):
    treenode_display_field = "name"

    name = models.CharField(max_length=50, unique=True)
    owner = models.ForeignKey(CategoryOwner, null=True, on_delete=lambda *args, **kwargs: None)

    class Meta(TreeNodeModel.Meta):
        app_label = "tests"
        verbose_name = "Category With Unsupported Owner"
        verbose_name_plural = "Categories With Unsupported Owner"


class _ABCMetaModelBase(ABCMeta, ModelBase):
    """Metaclass combining Python's ABCMeta with Django's ModelBase."""

    pass


class AbstractCategoryProxy(Category, metaclass=_ABCMetaModelBase):
    """
    A Django proxy model that is also a Python ABC.

    Used to reproduce issue #215: post_migrate_treenode calls update_tree()
    on this model even though it cannot be instantiated.
    """

    class Meta:
        app_label = "tests"
        proxy = True

    @abstractmethod
    def my_method(self):
        """Subclasses must implement this."""
        ...
