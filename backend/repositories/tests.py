"""Tests for the repositories app: models (Phase 3) + API endpoints (Phase 4)."""

from unittest.mock import MagicMock, patch

import requests as http_requests
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .models import Repository
from .services import (
    GitHubAPIError,
    GitHubRateLimited,
    GitHubRepoNotFound,
    GitHubTimeout,
    fetch_repository_metadata,
    parse_github_repo_url,
)

User = get_user_model()

CREATE_URL = "/api/repositories/"
LIST_URL = "/api/repositories/"


def detail_url(pk):
    return f"/api/repositories/{pk}/"


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


# ---------------------------------------------------------------------------
# Phase 4: URL validation unit tests (no DB, no network)
# ---------------------------------------------------------------------------


class GitHubURLValidationTests(TestCase):
    """Pure-Python tests for parse_github_repo_url (SSRF + format guards)."""

    def test_valid_url(self):
        self.assertEqual(
            parse_github_repo_url("https://github.com/octo/hello"),
            ("octo", "hello"),
        )

    def test_valid_url_with_trailing_slash(self):
        self.assertEqual(
            parse_github_repo_url("https://github.com/octo/hello/"),
            ("octo", "hello"),
        )

    def test_valid_url_with_git_suffix(self):
        self.assertEqual(
            parse_github_repo_url("https://github.com/octo/hello.git"),
            ("octo", "hello"),
        )

    def test_rejects_http_scheme(self):
        with self.assertRaises(ValueError):
            parse_github_repo_url("http://github.com/octo/hello")

    def test_rejects_wrong_host(self):
        with self.assertRaises(ValueError):
            parse_github_repo_url("https://gitlab.com/octo/hello")

    def test_rejects_subdomain(self):
        with self.assertRaises(ValueError):
            parse_github_repo_url("https://gist.github.com/octo/hello")

    def test_rejects_host_suffix_trick(self):
        """github.com.evil.com must be rejected."""
        with self.assertRaises(ValueError):
            parse_github_repo_url("https://github.com.evil.com/octo/hello")

    def test_rejects_userinfo_trick(self):
        """https://evil@github.com/octo/hello must be rejected."""
        with self.assertRaises(ValueError):
            parse_github_repo_url("https://evil@github.com/octo/hello")

    def test_rejects_port(self):
        with self.assertRaises(ValueError):
            parse_github_repo_url("https://github.com:8443/octo/hello")

    def test_rejects_ip_address(self):
        with self.assertRaises(ValueError):
            parse_github_repo_url("https://127.0.0.1/octo/hello")

    def test_rejects_localhost(self):
        with self.assertRaises(ValueError):
            parse_github_repo_url("https://localhost/octo/hello")

    def test_rejects_deep_path(self):
        with self.assertRaises(ValueError):
            parse_github_repo_url("https://github.com/octo/hello/tree/main")

    def test_rejects_empty_string(self):
        with self.assertRaises(ValueError):
            parse_github_repo_url("")

    def test_rejects_none(self):
        with self.assertRaises(ValueError):
            parse_github_repo_url(None)


# ---------------------------------------------------------------------------
# Phase 4: GitHub service unit tests (mocked requests.get)
# ---------------------------------------------------------------------------


def _mock_response(status_code=200, json_data=None, headers=None):
    """Build a fake requests.Response for mocking."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data or {}
    mock.headers = headers or {}
    return mock


GITHUB_SUCCESS_PAYLOAD = {
    "name": "hello",
    "default_branch": "main",
    "description": "A test repository",
    "html_url": "https://github.com/octo/hello",
}


class GitHubServiceTests(TestCase):
    """Unit tests for fetch_repository_metadata with mocked HTTP."""

    @patch("repositories.services.requests.get")
    def test_success_returns_metadata(self, mock_get):
        mock_get.return_value = _mock_response(json_data=GITHUB_SUCCESS_PAYLOAD)

        result = fetch_repository_metadata("octo", "hello")

        self.assertEqual(result["name"], "hello")
        self.assertEqual(result["default_branch"], "main")
        self.assertEqual(result["description"], "A test repository")
        self.assertEqual(result["github_url"], "https://github.com/octo/hello")
        # Verify the request URL is hardcoded api.github.com, not user input.
        call_url = mock_get.call_args[0][0]
        self.assertEqual(call_url, "https://api.github.com/repos/octo/hello")

    @patch("repositories.services.requests.get")
    def test_404_raises_not_found(self, mock_get):
        mock_get.return_value = _mock_response(status_code=404)

        with self.assertRaises(GitHubRepoNotFound):
            fetch_repository_metadata("octo", "nonexistent")

    @patch("repositories.services.requests.get")
    def test_403_rate_limit_raises_rate_limited(self, mock_get):
        mock_get.return_value = _mock_response(
            status_code=403, headers={"X-RateLimit-Remaining": "0"}
        )

        with self.assertRaises(GitHubRateLimited):
            fetch_repository_metadata("octo", "hello")

    @patch("repositories.services.requests.get")
    def test_429_raises_rate_limited(self, mock_get):
        mock_get.return_value = _mock_response(status_code=429)

        with self.assertRaises(GitHubRateLimited):
            fetch_repository_metadata("octo", "hello")

    @patch("repositories.services.requests.get")
    def test_timeout_raises_github_timeout(self, mock_get):
        mock_get.side_effect = http_requests.Timeout("timed out")

        with self.assertRaises(GitHubTimeout):
            fetch_repository_metadata("octo", "hello")

    @patch("repositories.services.requests.get")
    def test_connection_error_raises_api_error(self, mock_get):
        mock_get.side_effect = http_requests.ConnectionError("refused")

        with self.assertRaises(GitHubAPIError):
            fetch_repository_metadata("octo", "hello")

    @patch("repositories.services.requests.get")
    def test_500_raises_api_error(self, mock_get):
        mock_get.return_value = _mock_response(status_code=500)

        with self.assertRaises(GitHubAPIError):
            fetch_repository_metadata("octo", "hello")


# ---------------------------------------------------------------------------
# Phase 4: API endpoint tests (mocked GitHub API)
# ---------------------------------------------------------------------------


class RepositoryAPITestBase(APITestCase):
    """Shared setup: two users, authenticated client for alice."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="Str0ng-Pass!x"
        )
        cls.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="Str0ng-Pass!x"
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.alice)


class RepositoryCreateAPITests(RepositoryAPITestBase):
    """POST /api/repositories/ -- creation, validation, GitHub errors."""

    @patch("repositories.services.requests.get")
    def test_successful_creation(self, mock_get):
        """Valid URL + successful GitHub fetch -> 201 with metadata."""
        mock_get.return_value = _mock_response(json_data=GITHUB_SUCCESS_PAYLOAD)

        response = self.client.post(
            CREATE_URL,
            {"github_url": "https://github.com/octo/hello"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "hello")
        self.assertEqual(response.data["default_branch"], "main")
        self.assertEqual(response.data["description"], "A test repository")
        self.assertEqual(response.data["github_url"], "https://github.com/octo/hello")
        # Ownership comes from request.user, not the request body.
        repo = Repository.objects.get(pk=response.data["id"])
        self.assertEqual(repo.owner, self.alice)

    @patch("repositories.services.requests.get")
    def test_invalid_url_rejected(self, mock_get):
        """Malformed URL -> 400, GitHub API never called."""
        response = self.client.post(
            CREATE_URL,
            {"github_url": "not-a-url"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("github_url", response.data)
        mock_get.assert_not_called()

    @patch("repositories.services.requests.get")
    def test_unsupported_host_rejected(self, mock_get):
        """Non-github.com host -> 400, GitHub API never called."""
        response = self.client.post(
            CREATE_URL,
            {"github_url": "https://gitlab.com/octo/hello"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_get.assert_not_called()

    @patch("repositories.services.requests.get")
    def test_ssrf_localhost_rejected(self, mock_get):
        """localhost URL -> 400, no outbound request."""
        response = self.client.post(
            CREATE_URL,
            {"github_url": "https://localhost/octo/hello"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_get.assert_not_called()

    @patch("repositories.services.requests.get")
    def test_nonexistent_repository_404(self, mock_get):
        """GitHub returns 404 -> our API returns 404."""
        mock_get.return_value = _mock_response(status_code=404)

        response = self.client.post(
            CREATE_URL,
            {"github_url": "https://github.com/octo/nonexistent"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("detail", response.data)
        self.assertEqual(Repository.objects.count(), 0)

    @patch("repositories.services.requests.get")
    def test_github_timeout_504(self, mock_get):
        """GitHub API timeout -> 504 Gateway Timeout."""
        mock_get.side_effect = http_requests.Timeout("timed out")

        response = self.client.post(
            CREATE_URL,
            {"github_url": "https://github.com/octo/hello"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_504_GATEWAY_TIMEOUT)
        self.assertEqual(Repository.objects.count(), 0)

    @patch("repositories.services.requests.get")
    def test_github_rate_limit_429(self, mock_get):
        """GitHub rate limit -> 429 Too Many Requests."""
        mock_get.return_value = _mock_response(
            status_code=403, headers={"X-RateLimit-Remaining": "0"}
        )

        response = self.client.post(
            CREATE_URL,
            {"github_url": "https://github.com/octo/hello"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(Repository.objects.count(), 0)

    @patch("repositories.services.requests.get")
    def test_github_500_502(self, mock_get):
        """GitHub 500 -> our API returns 502 Bad Gateway."""
        mock_get.return_value = _mock_response(status_code=500)

        response = self.client.post(
            CREATE_URL,
            {"github_url": "https://github.com/octo/hello"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(Repository.objects.count(), 0)

    @patch("repositories.services.requests.get")
    def test_duplicate_rejected(self, mock_get):
        """Same user saves same repo twice -> 400 on second attempt."""
        mock_get.return_value = _mock_response(json_data=GITHUB_SUCCESS_PAYLOAD)

        # First save succeeds.
        response1 = self.client.post(
            CREATE_URL,
            {"github_url": "https://github.com/octo/hello"},
            format="json",
        )
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED)

        # Second save is a duplicate.
        response2 = self.client.post(
            CREATE_URL,
            {"github_url": "https://github.com/octo/hello"},
            format="json",
        )
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Repository.objects.count(), 1)

    @patch("repositories.services.requests.get")
    def test_different_users_can_save_same_repo(self, mock_get):
        """Two different users may save the same GitHub URL."""
        mock_get.return_value = _mock_response(json_data=GITHUB_SUCCESS_PAYLOAD)

        # Alice saves it.
        self.client.post(
            CREATE_URL,
            {"github_url": "https://github.com/octo/hello"},
            format="json",
        )
        # Bob saves it too.
        self.client.force_authenticate(user=self.bob)
        response = self.client.post(
            CREATE_URL,
            {"github_url": "https://github.com/octo/hello"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Repository.objects.count(), 2)

    @patch("repositories.services.requests.get")
    def test_owner_from_body_is_ignored(self, mock_get):
        """A malicious 'owner' field in the body cannot change ownership."""
        mock_get.return_value = _mock_response(json_data=GITHUB_SUCCESS_PAYLOAD)

        response = self.client.post(
            CREATE_URL,
            {
                "github_url": "https://github.com/octo/hello",
                "owner": self.bob.pk,  # attempt to hijack
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        repo = Repository.objects.get(pk=response.data["id"])
        self.assertEqual(repo.owner, self.alice)  # still alice, not bob


class RepositoryListAPITests(RepositoryAPITestBase):
    """GET /api/repositories/ -- list only the current user's repos."""

    def _seed_repos(self):
        r1 = Repository.objects.create(
            owner=self.alice,
            github_url="https://github.com/alice/one",
            name="one",
        )
        r2 = Repository.objects.create(
            owner=self.alice,
            github_url="https://github.com/alice/two",
            name="two",
        )
        Repository.objects.create(
            owner=self.bob,
            github_url="https://github.com/bob/secret",
            name="secret",
        )
        return r1, r2

    def test_lists_only_own_repositories(self):
        """Alice sees only her repos, never bob's."""
        r1, r2 = self._seed_repos()

        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [item["name"] for item in response.data["results"]]
        self.assertIn("one", names)
        self.assertIn("two", names)
        self.assertNotIn("secret", names)

    def test_list_includes_required_fields(self):
        """List response contains id, name, github_url, default_branch,
        description, created_at (spec §2)."""
        r1, _ = self._seed_repos()

        response = self.client.get(LIST_URL)
        item = response.data["results"][0]

        for field in ("id", "name", "github_url", "default_branch",
                       "description", "created_at"):
            self.assertIn(field, item)

    def test_pagination_is_active(self):
        """Response is paginated (has 'results' key)."""
        self._seed_repos()

        response = self.client.get(LIST_URL)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)


class RepositoryDetailAPITests(RepositoryAPITestBase):
    """GET /api/repositories/<id>/ -- retrieve with analysis summary."""

    def test_retrieve_own_repository(self):
        """Alice retrieves her own repo -> 200 with metadata."""
        repo = Repository.objects.create(
            owner=self.alice,
            github_url="https://github.com/alice/one",
            name="one",
            description="Alice's project",
        )

        response = self.client.get(detail_url(repo.pk))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "one")
        self.assertEqual(response.data["description"], "Alice's project")
        # Analysis history summary fields (spec §3).
        self.assertIn("analysis_run_count", response.data)
        self.assertIn("last_analysis_status", response.data)
        self.assertEqual(response.data["analysis_run_count"], 0)
        self.assertIsNone(response.data["last_analysis_status"])

    def test_cannot_access_other_users_repository(self):
        """Bob's repo is invisible to Alice -> 404, not 403 (no existence leak)."""
        bobs_repo = Repository.objects.create(
            owner=self.bob,
            github_url="https://github.com/bob/secret",
            name="secret",
        )

        response = self.client.get(detail_url(bobs_repo.pk))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_nonexistent_repository_404(self):
        response = self.client.get(detail_url(99999))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class RepositoryDeleteAPITests(RepositoryAPITestBase):
    """DELETE /api/repositories/<id>/ -- delete own repo + cascade."""

    def test_delete_own_repository(self):
        """Alice deletes her repo -> 204, gone from DB."""
        repo = Repository.objects.create(
            owner=self.alice,
            github_url="https://github.com/alice/one",
            name="one",
        )

        response = self.client.delete(detail_url(repo.pk))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Repository.objects.filter(pk=repo.pk).exists())

    def test_cannot_delete_other_users_repository(self):
        """Bob's repo is undeletable by Alice -> 404, still exists."""
        bobs_repo = Repository.objects.create(
            owner=self.bob,
            github_url="https://github.com/bob/secret",
            name="secret",
        )

        response = self.client.delete(detail_url(bobs_repo.pk))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Repository.objects.filter(pk=bobs_repo.pk).exists())

    def test_delete_cascades_to_analysis_data(self):
        """Deleting a repo removes its analysis runs and issues (CASCADE)."""
        from analysis.models import AnalysisRun, Issue, Severity

        repo = Repository.objects.create(
            owner=self.alice,
            github_url="https://github.com/alice/one",
            name="one",
        )
        run = AnalysisRun.objects.create(repository=repo)
        Issue.objects.create(
            analysis_run=run,
            file_path="app.py",
            line_number=1,
            severity=Severity.LOW,
            description="test",
        )

        response = self.client.delete(detail_url(repo.pk))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(AnalysisRun.objects.count(), 0)
        self.assertEqual(Issue.objects.count(), 0)


class RepositoryAuthTests(APITestCase):
    """Unauthenticated access is rejected on all endpoints."""

    def setUp(self):
        self.client = APIClient()

    def test_list_requires_auth(self):
        response = self.client.get(LIST_URL)
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_create_requires_auth(self):
        response = self.client.post(
            CREATE_URL, {"github_url": "https://github.com/o/r"}, format="json"
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_detail_requires_auth(self):
        response = self.client.get(detail_url(1))
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_delete_requires_auth(self):
        response = self.client.delete(detail_url(1))
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
