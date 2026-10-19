from contextlib import AbstractContextManager

from django.contrib.auth.models import User
from django.db import transaction

from allianceauth.tests.auth_utils import AuthUtils


class isolated_subtest(AbstractContextManager):
    """Guarantees database isolation for a subTest block by forcing
    an atomic savepoint to roll back on exit, regardless of whether
    the test passed, failed, or errored.

    To be used in tests created with `django.test.TestCase`.
    """

    def __enter__(self):
        self.atomic = transaction.atomic()
        self.atomic.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            transaction.set_rollback(True)
        finally:
            return self.atomic.__exit__(exc_type, exc_val, exc_tb)


def add_permission_to_user_by_name(perm: str, user: User) -> User:
    """adds permission to given user by name and returns updated user object"""
    AuthUtils.add_permission_to_user_by_name(perm, user)
    return User.objects.get(pk=user.pk)
