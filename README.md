[![](https://img.shields.io/pypi/pyversions/django-treenode.svg?color=3776AB&logo=python&logoColor=white)](https://www.python.org/)
[![](https://img.shields.io/pypi/djversions/django-treenode?color=0C4B33&logo=django&logoColor=white&label=django)](https://www.djangoproject.com/)

[![](https://img.shields.io/pypi/v/django-treenode.svg?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/django-treenode/)
[![](https://static.pepy.tech/badge/django-treenode/month)](https://pepy.tech/project/django-treenode)
[![](https://img.shields.io/github/stars/fabiocaccamo/django-treenode?logo=github&style=flat)](https://github.com/fabiocaccamo/django-treenode/stargazers)
[![](https://img.shields.io/pypi/l/django-treenode.svg?color=blue)](https://github.com/fabiocaccamo/django-treenode/blob/main/LICENSE.txt)

[![](https://results.pre-commit.ci/badge/github/fabiocaccamo/django-treenode/main.svg)](https://results.pre-commit.ci/latest/github/fabiocaccamo/django-treenode/main)
[![](https://img.shields.io/github/actions/workflow/status/fabiocaccamo/django-treenode/test-package.yml?branch=main&label=build&logo=github)](https://github.com/fabiocaccamo/django-treenode)
[![](https://img.shields.io/codecov/c/gh/fabiocaccamo/django-treenode?logo=codecov)](https://codecov.io/gh/fabiocaccamo/django-treenode)
[![](https://img.shields.io/codacy/grade/0c79c196e5c9411babbaf5e8e5f7469c?logo=codacy)](https://www.codacy.com/app/fabiocaccamo/django-treenode)
[![](https://img.shields.io/badge/code%20style-black-000000.svg?logo=python&logoColor=black)](https://github.com/psf/black)
[![](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/fabiocaccamo/django-treenode/badge)](https://securityscorecards.dev/viewer/?uri=github.com/fabiocaccamo/django-treenode)

# django-treenode
Probably the best abstract model / admin for your **tree** based stuff.

## Features
-   **Fast** - get `ancestors`, `children`, `descendants`, `parent`, `root`, `siblings`, `tree` with **no queries**
-   **Synced** - in-memory model instances are automatically updated
-   **Compatibility** - you can easily add `treenode` to existing projects
-   **No dependencies**
-   **Easy configuration** - just extend the abstract model / model-admin
-   **Admin integration** - great tree visualization: **accordion**, **breadcrumbs** or **indentation**

| indentation (default) | breadcrumbs | accordion |
| --- | --- | --- |
| ![treenode-admin-display-mode-indentation][treenode-admin-display-mode-indentation] | ![treenode-admin-display-mode-breadcrumbs][treenode-admin-display-mode-breadcrumbs] | ![treenode-admin-display-mode-accordion][treenode-admin-display-mode-accordion] |

## Installation
-   Run `pip install django-treenode`
-   Add `treenode` to `settings.INSTALLED_APPS`
-   Make your model inherit from `treenode.models.TreeNodeModel` *(described below)*
-   Make your model-admin inherit from `treenode.admin.TreeNodeModelAdmin` *(described below)*
-   Run `python manage.py makemigrations` and `python manage.py migrate`

## Configuration
### `models.py`
Make your model class inherit from `treenode.models.TreeNodeModel`:

```python
from django.db import models

from treenode.models import TreeNodeModel


class Category(TreeNodeModel):

    # the field used to display the model instance
    # default value 'pk'
    treenode_display_field = "name"

    name = models.CharField(max_length=50)

    class Meta(TreeNodeModel.Meta):
        verbose_name = "Category"
        verbose_name_plural = "Categories"
```

The `TreeNodeModel` abstract class adds many fields (prefixed with `tn_` to prevent direct access) and public methods to your models.

:warning: **If you are extending a model that already has some fields, please ensure that your model existing fields names don't clash with `TreeNodeModel` public [methods/properties](#methodsproperties) names.**

---

### `admin.py`
Make your model-admin class inherit from `treenode.admin.TreeNodeModelAdmin`.

```python
from django.contrib import admin

from treenode.admin import TreeNodeModelAdmin
from treenode.forms import TreeNodeForm

from .models import Category


class CategoryAdmin(TreeNodeModelAdmin):

    # set the changelist display mode: 'accordion', 'breadcrumbs' or 'indentation' (default)
    # when changelist results are filtered by a querystring,
    # 'breadcrumbs' mode will be used (to preserve data display integrity)
    treenode_display_mode = TreeNodeModelAdmin.TREENODE_DISPLAY_MODE_ACCORDION
    # treenode_display_mode = TreeNodeModelAdmin.TREENODE_DISPLAY_MODE_BREADCRUMBS
    # treenode_display_mode = TreeNodeModelAdmin.TREENODE_DISPLAY_MODE_INDENTATION

    # use TreeNodeForm to automatically exclude invalid parent choices
    form = TreeNodeForm

admin.site.register(Category, CategoryAdmin)
```

---

### `settings.py`
You can use a custom cache backend by adding a `treenode` entry to `settings.CACHES`, otherwise the default cache backend will be used.

```python
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": "...",
    },
    "treenode": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
}
```

### Safe model registration

Safe model registration is opt-in. Register a concrete model after Django has loaded
applications, with a codec for every persisted field. Registration never opens a
database connection.

```python
from treenode.safe_codec import default_field_codecs
from treenode.safe_registry import register_safe_model

register_safe_model(Category, fields=default_field_codecs(Category))
```

Set all codec limits explicitly from representative measurements: payload bytes,
node count, field count, nesting depth, string bytes, and container items. Missing
or invalid limits reject snapshot encoding and decoding.

Registration accepts only concrete non-proxy `TreeNodeModel` classes with the
default model `from_db()` implementation. Custom fields require an explicit
`FieldCodec` whose `validate()`, `encode()`, and `decode()` methods accept that
exact field behavior. The built-in catalog covers standard Django scalar, date,
UUID, binary, JSON, and foreign-key values. Unknown or incomplete field mappings,
custom model materialization, and router configurations that cannot migrate the
model and revision table to one alias are rejected.

Repeating registration with equivalent codec classes and configuration is
idempotent. A different codec class or canonical codec configuration for a field
is rejected.

Codec configuration must be inspectable and immutable. Mutable instance or class
configuration at any nesting depth, slots that hide configuration, and
configuration changes after registration fail closed before values are encoded or
decoded. Related models that can change a registered tree through supported deletion
policies, including owner models referenced by a tree's forward foreign keys, must
statically route and migrate on the same alias. Valid local owner relations remain
supported; unknown policies or cross-alias relations are rejected.

The codec emits and accepts only its versioned bounded JSON snapshot format. It
does not read cache entries or enable shared cache reads. After the revision
migration has run on the registered alias, registered models activate transactional
revision ownership. Raw SQL, direct bulk updates, private queryset deletion, and
external writers remain outside any freshness guarantee.

Use `tree_mutation()` for supported bulk writes. It keeps the writes, tree refresh,
and revision increment in one database transaction.

```python
with Category.tree_mutation(using="default"):
    Category.objects.using("default").filter(...).update(...)
```

Registered instance saves, deletes, reparenting, and priority updates validate the
router-selected instance alias before row SQL. Fixture imports must run inside the
matching `tree_mutation()` context. Unregistered models retain existing behavior.
Before related-owner deletion SQL, every affected registered tree relation validates
both the owner instance-hint route and the queryset-origin alias against the
registered tree alias. A mismatch raises `SafeRoutingError` before `SET_NULL`,
`SET_DEFAULT`, or cascading SQL can mutate a registered tree without its revision.
On SQLite and PostgreSQL, a single database upsert creates or increments the first
revision row, so concurrent committed mutations receive distinct revisions without
a client-side retry loop.

### Safe cache backends

`SafeMemoryCache` is a single-process backend for development and tests.
`SafeRedisCache` is a shared backend installed with `django-treenode[redis]`.
Both accept only exact `None`, booleans, signed 64-bit integers, finite floats,
strings, bytes, and bounded nested lists, tuples, and dictionaries with string keys.
They encode every non-counter value as validated JSON data and never pickle values.

```python
CACHES = {
    "safe": {
        "BACKEND": "treenode.backends.redis.SafeRedisCache",
        "LOCATION": "rediss://cache.example:6379/0",
        "KEY_PREFIX": "my-service",
        "OPTIONS": {
            "NAMESPACE": "my-service-safe-v1",
            "MAX_VALUE_BYTES": 1048576,
            "MAX_VALUE_DEPTH": 16,
            "MAX_VALUE_ITEMS": 10000,
        },
    },
}
```

Use a unique `NAMESPACE` per cache alias. `clear()` scans and deletes only that
namespace, never flushes the Redis database. Counters use native Redis integer
operations and remain atomic. Malformed stored data is treated as a cache miss and
is not deleted, so it cannot remove a concurrent repair. Connection failures are
reported by the backend; they are not converted into unsafe fallback values.

CI validates Redis over both plaintext and TLS. The TLS endpoint uses an ephemeral
CI-only certificate and CA, requires certificate validation and hostname checking,
waits for Docker health events, and is removed after the test job. Deployment
certificates and connection settings remain application configuration, not package
artifacts.

### Revision-checked tree snapshots

Tree snapshots remain disabled until a registered active model and a dedicated safe
cache alias are configured. Do not use `default` or `treenode` for this alias.

```python
TREENODE_SAFE_CACHE_ALIAS = "safe"
TREENODE_SAFE_CACHE_TIMEOUT = 300
TREENODE_SAFE_CACHE_REBUILD_ATTEMPTS = 2
```

`get_safe_snapshot(Category, using="default")` reads the authoritative revision
before selecting its immutable revision key. Cache misses acquire a complete primary
database snapshot, verify its revision, and publish only with add-if-absent. Active
database transactions, malformed entries, cache failures, and unstable rebuilds use
database rows without writing or deleting a cache entry. The legacy object cache
remains unchanged unless this opt-in setting is configured for an active registered
model.

## Usage

### Methods/Properties

-   [`delete`](#delete)
-   [`delete_tree`](#delete_tree)
-   [`get_ancestors`](#get_ancestors)
-   [`get_ancestors_count`](#get_ancestors_count)
-   [`get_ancestors_pks`](#get_ancestors_pks)
-   [`get_ancestors_queryset`](#get_ancestors_queryset)
-   [`get_breadcrumbs`](#get_breadcrumbs)
-   [`get_children`](#get_children)
-   [`get_children_count`](#get_children_count)
-   [`get_children_pks`](#get_children_pks)
-   [`get_children_queryset`](#get_children_queryset)
-   [`get_depth`](#get_depth)
-   [`get_descendants`](#get_descendants)
-   [`get_descendants_count`](#get_descendants_count)
-   [`get_descendants_pks`](#get_descendants_pks)
-   [`get_descendants_queryset`](#get_descendants_queryset)
-   [`get_descendants_tree`](#get_descendants_tree)
-   [`get_descendants_tree_display`](#get_descendants_tree_display)
-   [`get_first_child`](#get_first_child)
-   [`get_index`](#get_index)
-   [`get_last_child`](#get_last_child)
-   [`get_level`](#get_level)
-   [`get_order`](#get_order)
-   [`get_parent`](#get_parent)
-   [`get_parent_pk`](#get_parent_pk)
-   [`set_parent`](#set_parent)
-   [`get_priority`](#get_priority)
-   [`set_priority`](#set_priority)
-   [`get_root`](#get_root)
-   [`get_root_pk`](#get_root_pk)
-   [`get_roots`](#get_roots)
-   [`get_roots_queryset`](#get_roots_queryset)
-   [`get_siblings`](#get_siblings)
-   [`get_siblings_count`](#get_siblings_count)
-   [`get_siblings_pks`](#get_siblings_pks)
-   [`get_siblings_queryset`](#get_siblings_queryset)
-   [`get_tree`](#get_tree)
-   [`get_tree_display`](#get_tree_display)
-   [`is_ancestor_of`](#is_ancestor_of)
-   [`is_child_of`](#is_child_of)
-   [`is_descendant_of`](#is_descendant_of)
-   [`is_first_child`](#is_first_child)
-   [`is_last_child`](#is_last_child)
-   [`is_leaf`](#is_leaf)
-   [`is_parent_of`](#is_parent_of)
-   [`is_root`](#is_root)
-   [`is_root_of`](#is_root_of)
-   [`is_sibling_of`](#is_sibling_of)
-   [`update_tree`](#update_tree)


#### `delete`
**Delete a node** if `cascade=True` (default behaviour), children and descendants will be deleted too,
otherwise children's parent will be set to `None` (then children become roots):
```python
obj.delete(cascade=True)
```

#### `delete_tree`
**Delete the whole tree** for the current node class:
```python
cls.delete_tree()
```

#### `get_ancestors`
Get a **list with all ancestors** (ordered from root to parent):
```python
obj.get_ancestors()
# or
obj.ancestors
```

#### `get_ancestors_count`
Get the **ancestors count**:
```python
obj.get_ancestors_count()
# or
obj.ancestors_count
```

#### `get_ancestors_pks`
Get the **ancestors pks** list:
```python
obj.get_ancestors_pks()
# or
obj.ancestors_pks
```

#### `get_ancestors_queryset`
Get the **ancestors queryset** (ordered from parent to root):
```python
obj.get_ancestors_queryset()
```

#### `get_breadcrumbs`
Get the **breadcrumbs** to current node (included):
```python
obj.get_breadcrumbs(attr=None)
# or
obj.breadcrumbs
```

#### `get_children`
Get a **list containing all children**:
```python
obj.get_children()
# or
obj.children
```

#### `get_children_count`
Get the **children count**:
```python
obj.get_children_count()
# or
obj.children_count
```

#### `get_children_pks`
Get the **children pks** list:
```python
obj.get_children_pks()
# or
obj.children_pks
```

#### `get_children_queryset`
Get the **children queryset**:
```python
obj.get_children_queryset()
```

#### `get_depth`
Get the **node depth** (how many levels of descendants):
```python
obj.get_depth()
# or
obj.depth
```

#### `get_descendants`
Get a **list containing all descendants**:
```python
obj.get_descendants()
# or
obj.descendants
```

#### `get_descendants_count`
Get the **descendants count**:
```python
obj.get_descendants_count()
# or
obj.descendants_count
```

#### `get_descendants_pks`
Get the **descendants pks** list:
```python
obj.get_descendants_pks()
# or
obj.descendants_pks
```

#### `get_descendants_queryset`
Get the **descendants queryset**:
```python
obj.get_descendants_queryset()
```

#### `get_descendants_tree`
Get a **n-dimensional** `dict` representing the **model tree**:
```python
obj.get_descendants_tree()
# or
obj.descendants_tree
```

#### `get_descendants_tree_display`
Get a **multiline** `string` representing the **model tree**:
```python
obj.get_descendants_tree_display()
# or
obj.descendants_tree_display
```

#### `get_first_child`
Get the **first child node**:
```python
obj.get_first_child()
# or
obj.first_child
```

#### `get_index`
Get the **node index** (index in node.parent.children list):
```python
obj.get_index()
# or
obj.index
```

#### `get_last_child`
Get the **last child node**:
```python
obj.get_last_child()
# or
obj.last_child
```

#### `get_level`
Get the **node level** (starting from 1):
```python
obj.get_level()
# or
obj.level
```

#### `get_order`
Get the **order value** used for ordering:
```python
obj.get_order()
# or
obj.order
```

#### `get_parent`
Get the **parent node**:
```python
obj.get_parent()
# or
obj.parent
```

#### `get_parent_pk`
Get the **parent node pk**:
```python
obj.get_parent_pk()
# or
obj.parent_pk
```

#### `set_parent`
Set the **parent node**:
```python
obj.set_parent(parent_obj)
```

#### `get_priority`
Get the **node priority**:
```python
obj.get_priority()
# or
obj.priority
```

#### `set_priority`
Set the **node priority**:
```python
obj.set_priority(100)
```

#### `get_root`
Get the **root node** for the current node:
```python
obj.get_root()
# or
obj.root
```

#### `get_root_pk`
Get the **root node pk** for the current node:
```python
obj.get_root_pk()
# or
obj.root_pk
```

#### `get_roots`
Get a **list with all root nodes**:
```python
cls.get_roots()
# or
cls.roots
```

#### `get_roots_queryset`
Get **root nodes queryset**:
```python
cls.get_roots_queryset()
```

#### `get_siblings`
Get a **list with all the siblings**:
```python
obj.get_siblings()
# or
obj.siblings
```

#### `get_siblings_count`
Get the **siblings count**:
```python
obj.get_siblings_count()
# or
obj.siblings_count
```

#### `get_siblings_pks`
Get the **siblings pks** list:
```python
obj.get_siblings_pks()
# or
obj.siblings_pks
```

#### `get_siblings_queryset`
Get the **siblings queryset**:
```python
obj.get_siblings_queryset()
```

#### `get_tree`
Get a **n-dimensional** `dict` representing the **model tree**:
```python
cls.get_tree()
# or
cls.tree
```

#### `get_tree_display`
Get a **multiline** `string` representing the **model tree**:
```python
cls.get_tree_display()
# or
cls.tree_display
```

#### `is_ancestor_of`
Return `True` if the current node **is ancestor** of target_obj:
```python
obj.is_ancestor_of(target_obj)
```

#### `is_child_of`
Return `True` if the current node **is child** of target_obj:
```python
obj.is_child_of(target_obj)
```

#### `is_descendant_of`
Return `True` if the current node **is descendant** of target_obj:
```python
obj.is_descendant_of(target_obj)
```

#### `is_first_child`
Return `True` if the current node is the **first child**:
```python
obj.is_first_child()
```

#### `is_last_child`
Return `True` if the current node is the **last child**:
```python
obj.is_last_child()
```

#### `is_leaf`
Return `True` if the current node is **leaf** (it has not children):
```python
obj.is_leaf()
```

#### `is_parent_of`
Return `True` if the current node **is parent** of target_obj:
```python
obj.is_parent_of(target_obj)
```

#### `is_root`
Return `True` if the current node **is root**:
```python
obj.is_root()
```

#### `is_root_of`
Return `True` if the current node **is root** of target_obj:
```python
obj.is_root_of(target_obj)
```

#### `is_sibling_of`
Return `True` if the current node **is sibling** of target_obj:
```python
obj.is_sibling_of(target_obj)
```

#### `update_tree`
**Update tree** manually, useful after **bulk updates**:
```python
cls.update_tree()
```

### Bulk Operations

To perform bulk operations it is recommended to turn off signals, then triggering the tree update at the end:

```python
from treenode.signals import no_signals

with no_signals():
    # execute custom bulk operations
    pass

# trigger tree update only once
YourModel.update_tree()
```

## FAQ

### Custom tree serialization
> How can I serialize a tree using a custom data structure?

This has been discussed [here](https://github.com/fabiocaccamo/django-treenode/discussions/89#discussioncomment-5521654).

## Testing
```bash
# clone repository
git clone https://github.com/fabiocaccamo/django-treenode.git && cd django-treenode

# create virtualenv and activate it
python -m venv venv && . venv/bin/activate

# upgrade pip
python -m pip install --upgrade pip

# install requirements
pip install -r requirements.txt -r requirements-test.txt

# install pre-commit to run formatters and linters
pre-commit install --install-hooks

# run tests
tox
# or
python runtests.py
# or
python -m django test --settings "tests.settings"
```

## License
Released under [MIT License](LICENSE.txt).

---

## Supporting

- :star: Star this project on [GitHub](https://github.com/fabiocaccamo/django-treenode)
- :octocat: Follow me on [GitHub](https://github.com/fabiocaccamo)
- :blue_heart: Follow me on [Bluesky](https://bsky.app/profile/fabiocaccamo.bsky.social)
- :moneybag: Sponsor me on [Github](https://github.com/sponsors/fabiocaccamo)

## See also

- [`django-admin-interface`](https://github.com/fabiocaccamo/django-admin-interface) - the default admin interface made customizable by the admin itself. popup windows replaced by modals. 🧙 ⚡

- [`django-cache-cleaner`](https://github.com/fabiocaccamo/django-cache-cleaner) - clear the entire cache or individual caches easily using the admin panel or management command. 🧹✨

- [`django-colorfield`](https://github.com/fabiocaccamo/django-colorfield) - simple color field for models with a nice color-picker in the admin. 🎨

- [`django-email-validators`](https://github.com/fabiocaccamo/django-email-validators) - no more invalid or disposable emails in your database. ✉️ ✅

- [`django-extra-settings`](https://github.com/fabiocaccamo/django-extra-settings) - config and manage typed extra settings using just the django admin. ⚙️

- [`django-maintenance-mode`](https://github.com/fabiocaccamo/django-maintenance-mode) - shows a 503 error page when maintenance-mode is on. 🚧 🛠️

- [`django-redirects`](https://github.com/fabiocaccamo/django-redirects) - redirects with full control. ↪️

- [`python-benedict`](https://github.com/fabiocaccamo/python-benedict) - dict subclass with keylist/keypath support, I/O shortcuts (base64, csv, json, pickle, plist, query-string, toml, xml, yaml) and many utilities. 📘

- [`python-codicefiscale`](https://github.com/fabiocaccamo/python-codicefiscale) - encode/decode Italian fiscal codes - codifica/decodifica del Codice Fiscale. 🇮🇹 💳

- [`python-fontbro`](https://github.com/fabiocaccamo/python-fontbro) - friendly font operations. 🧢

- [`python-fsutil`](https://github.com/fabiocaccamo/python-fsutil) - file-system utilities for lazy devs. 🧟‍♂️

[treenode-admin-display-mode-accordion]: https://user-images.githubusercontent.com/1035294/54942407-5040ec00-4f2f-11e9-873b-d0b3b521f534.png
[treenode-admin-display-mode-breadcrumbs]: https://user-images.githubusercontent.com/1035294/54942410-50d98280-4f2f-11e9-8a8b-a1ac6208398a.png
[treenode-admin-display-mode-indentation]: https://user-images.githubusercontent.com/1035294/54942411-50d98280-4f2f-11e9-9daf-d8339dd7a159.png
