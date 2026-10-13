"""Tests for the repositories app models."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Repository

User = get_user_model()


class RepositoryModelTests(TestCase):
    """Creation, string representation, constraints, and deletion rules."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="Str0ng-Pass!x"
        )
        cls.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="Str0ng-Pass!x"
        )

    def _make_repo(self, owner=None, url="https://github.com/octo/hello"):
        return Repository.objects.create(
            owner=owner or self.alice,
            github_url=url,
            name="hello",
        )

    def test_create_repository_with_defaults(self):
        """Creation works; default_branch defaults to 'main'; timestamps set."""
        repo = self._make_repo()

        self.assertEqual(repo.default_branch, "main")
        self.assertIsNotNone(repo.created_at)
        self.assertIsNotNone(repo.updated_at)
        self.assertEqual(repo.pk, repo.id)

    def test_str_returns_owner_and_name(self):
        repo = self._make_repo()
        self.assertEqual(str(repo), "alice/hello")

    def test_user_repositories_related_name(self):
        """user.repositories reverse relation works."""
        self._make_repo()
        self.assertEqual(self.alice.repositories.count(), 1)
        self.assertEqual(self.bob.repositories.count(), 0)

    def test_same_owner_cannot_register_same_url_twice(self):
        """unique_repo_url_per_owner rejects (owner, github_url) duplicates."""
        self._make_repo()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():  # isolate so the test txn stays usable
                self._make_repo()  # same owner + same URL

    def test_different_owners_can_register_same_url(self):
        """The same GitHub URL may belong to two different owners."""
        self._make_repo(owner=self.alice)
        self._make_repo(owner=self.bob)  # must NOT raise

        self.assertEqual(Repository.objects.filter(
            github_url="https://github.com/octo/hello").count(), 2)

    def test_repository_requires_a_user(self):
        """owner_id is NOT NULL at the database level."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Repository.objects.create(
                    owner_id=None,
                    github_url="https://github.com/octo/none",
                    name="none",
                )

    def test_deleting_owner_deletes_their_repositories(self):
        """on_delete=CASCADE: user deleted -> repos deleted."""
        self._make_repo()
        self.alice.delete()

        self.assertEqual(Repository.objects.count(), 0)

    def test_ordering_newest_first(self):
        first = self._make_repo(url="https://github.com/octo/one")
        second = self._make_repo(url="https://github.com/octo/two")

        self.assertEqual(
            list(Repository.objects.values_list("id", flat=True)),
            [second.pk, first.pk],
        )
