import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")

import django

django.setup()

from tests.models import Category
from treenode.revision import _label


class RevisionIdentityUnitTests(unittest.TestCase):
    def test_revision_identity_uses_concrete_model(self):
        self.assertEqual(_label(Category), Category._meta.label_lower)
