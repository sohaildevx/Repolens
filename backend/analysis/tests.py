"""Tests for the analysis app: models (Phase 3), API (Phase 5), AI service."""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import httpx2
import requests as http_requests
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from repositories.models import Repository

from .ai_service import (
    AIConfigurationError,
    AITimeout,
    AIUnavailable,
    chat_json,
    is_configured,
)
from .models import (
    AgentRun,
    AnalysisRun,
    CodeChange,
    Issue,
    RunStatus,
    Severity,
    TestResult,
    TestStatus,
)
from .services import (
    AnalyzerTimeout,
    AnalyzerUnavailable,
    run_analysis,
)

User = get_user_model()

RUNS_URL = "/api/analysis/runs/"


def run_detail_url(pk):
    return f"/api/analysis/runs/{pk}/"


class AnalysisModelTestCase(TestCase):
    """Shared fixtures: a user, a repository, and an analysis run."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="Str0ng-Pass!x"
        )
        cls.repo = Repository.objects.create(
            owner=cls.user,
            github_url="https://github.com/octo/hello",
            name="hello",
        )
        cls.analysis_run = AnalysisRun.objects.create(repository=cls.repo)


class AnalysisRunTests(AnalysisModelTestCase):
    def test_create_with_defaults(self):
        """A fresh run is PENDING with null start/completion times."""
        self.assertEqual(self.analysis_run.status, RunStatus.PENDING)
        self.assertIsNone(self.analysis_run.start_time)
        self.assertIsNone(self.analysis_run.completion_time)
        # Use the actual pk: sequence numbers are global across the test run,
        # so asserting a hard-coded "#1" would be order-dependent.
        self.assertEqual(
            str(self.analysis_run),
            f"alice/hello run #{self.analysis_run.pk} [pending]",
        )

    def test_valid_time_range_accepted(self):
        now = timezone.now()
        self.analysis_run.start_time = now
        self.analysis_run.completion_time = now + timedelta(minutes=5)
        self.analysis_run.save()  # must NOT raise

    def test_completion_equal_to_start_accepted(self):
        now = timezone.now()
        self.analysis_run.start_time = now
        self.analysis_run.completion_time = now
        self.analysis_run.save()  # boundary case: equal times are valid

    def test_completion_before_start_rejected_by_db(self):
        """analysisrun_completion_not_before_start: DB rejects completed < started."""
        now = timezone.now()
        self.analysis_run.start_time = now
        self.analysis_run.completion_time = now - timedelta(minutes=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.analysis_run.save()

    def test_invalid_status_rejected_by_validation(self):
        """Choices are validated in Python (the DB column is plain varchar)."""
        self.analysis_run.status = "exploded"
        with self.assertRaises(ValidationError):
            self.analysis_run.full_clean()

    def test_repository_cascade_deletes_runs(self):
        self.repo.delete()
        self.assertEqual(AnalysisRun.objects.count(), 0)


class IssueTests(AnalysisModelTestCase):
    def _make_issue(self, line=10, severity=Severity.HIGH):
        return Issue.objects.create(
            analysis_run=self.analysis_run,
            file_path="src/app.py",
            line_number=line,
            severity=severity,
            description="Possible SQL injection.",
        )

    def test_create_and_str(self):
        issue = self._make_issue()
        self.assertEqual(str(issue), "src/app.py:10 [high]")
        self.assertEqual(issue.suggested_fix, "")  # blank=True default

    def test_related_name_from_run(self):
        self._make_issue()
        self.assertEqual(self.analysis_run.issues.count(), 1)

    def test_line_one_accepted(self):
        """issue_line_number_gte_1 boundary: line 1 is the minimum valid line."""
        self._make_issue(line=1)

    def test_line_zero_rejected_by_db(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_issue(line=0)

    def test_negative_line_rejected_by_db(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_issue(line=-5)

    def test_deleting_run_deletes_issues(self):
        self._make_issue()
        self.analysis_run.delete()
        self.assertEqual(Issue.objects.count(), 0)

    def test_invalid_severity_rejected_by_validation(self):
        issue = self._make_issue()
        issue.severity = "apocalyptic"
        with self.assertRaises(ValidationError):
            issue.full_clean()


class AgentRunChainTests(AnalysisModelTestCase):
    """AgentRun -> CodeChange / TestResult creation and cascade rules."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.agent = AgentRun.objects.create(analysis_run=cls.analysis_run)

    def test_agent_run_defaults(self):
        self.assertEqual(self.agent.status, RunStatus.PENDING)
        self.assertEqual(self.agent.repair_attempts, 0)
        self.assertEqual(self.agent.final_summary, "")
        self.assertEqual(self.analysis_run.agent_runs.count(), 1)

    def test_repair_attempts_cannot_be_negative(self):
        self.agent.repair_attempts = -1
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.agent.save()

    def test_code_change_and_test_result_creation(self):
        change = CodeChange.objects.create(
            agent_run=self.agent,
            file_path="src/app.py",
            proposed_diff="--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@",
        )
        result = TestResult.objects.create(
            agent_run=self.agent,
            test_name="tests.test_login",
            status=TestStatus.PASSED,
            output="1 passed in 0.01s",
        )

        self.assertEqual(self.agent.code_changes.count(), 1)
        self.assertEqual(self.agent.test_results.count(), 1)
        self.assertEqual(
            str(change),
            f"Change to src/app.py (run #{self.agent.pk})",
        )
        self.assertEqual(str(result), "tests.test_login [passed]")
        # FK back-reference works the other way too:
        self.assertEqual(change.agent_run, self.agent)

    def test_deleting_agent_run_cascades_to_changes_and_results(self):
        CodeChange.objects.create(
            agent_run=self.agent, file_path="a.py", proposed_diff="x"
        )
        TestResult.objects.create(
            agent_run=self.agent, test_name="t", status=TestStatus.FAILED
        )

        self.agent.delete()

        self.assertEqual(CodeChange.objects.count(), 0)
        self.assertEqual(TestResult.objects.count(), 0)

    def test_full_chain_cascade_from_repository(self):
        """Deleting the repository wipes the entire chain."""
        CodeChange.objects.create(
            agent_run=self.agent, file_path="a.py", proposed_diff="x"
        )
        TestResult.objects.create(
            agent_run=self.agent, test_name="t", status=TestStatus.PASSED
        )
        Issue.objects.create(
            analysis_run=self.analysis_run,
            file_path="a.py",
            line_number=1,
            severity=Severity.LOW,
            description="minor",
        )

        self.repo.delete()

        self.assertEqual(AnalysisRun.objects.count(), 0)
        self.assertEqual(Issue.objects.count(), 0)
        self.assertEqual(AgentRun.objects.count(), 0)
        self.assertEqual(CodeChange.objects.count(), 0)
        self.assertEqual(TestResult.objects.count(), 0)

    def test_invalid_test_status_rejected_by_validation(self):
        result = TestResult.objects.create(
            agent_run=self.agent, test_name="t", status=TestStatus.PASSED
        )
        result.status = "flaky"
        with self.assertRaises(ValidationError):
            result.full_clean()


# ---------------------------------------------------------------------------
# Phase 5: analyzer service + API tests (all external HTTP mocked)
# ---------------------------------------------------------------------------


def _analyzer_response(findings=None, meta=None):
    """Fake Flask /analyze JSON payload."""
    return {
        "findings": findings if findings is not None else [],
        "meta": meta or {"files_submitted": 1, "findings_count": 0},
    }


def _mock_http_ok(payload=None):
    """A fake requests.post(...) return value with .status_code and .json()."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = payload or _analyzer_response()
    return mock


SAMPLE_FILES = [{"path": "app.py", "content": "x = 1\nprint(x)\n"}]


class AnalyzerServiceTests(TestCase):
    """Unit tests for run_analysis() (no HTTP, no API client)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="Str0ng-Pass!x"
        )
        cls.repo = Repository.objects.create(
            owner=cls.user,
            github_url="https://github.com/octo/hello",
            name="hello",
        )

    @patch("analysis.services.requests.post")
    def test_success_saves_issues_and_completes(self, mock_post):
        payload = _analyzer_response(findings=[
            {
                "file_path": "app.py",
                "line_number": 1,
                "rule": "B105",
                "severity": "high",
                "description": "Hardcoded password.",
            },
        ])
        mock_post.return_value = _mock_http_ok(payload)

        run = run_analysis(self.repo, SAMPLE_FILES, user=self.user)

        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertIsNotNone(run.start_time)
        self.assertIsNotNone(run.completion_time)
        issue = run.issues.get()
        self.assertEqual(issue.rule, "B105")
        self.assertEqual(issue.severity, Severity.HIGH)
        self.assertEqual(issue.file_path, "app.py")
        self.assertEqual(issue.line_number, 1)

    @patch("analysis.services.requests.post")
    def test_timeout_marks_run_failed(self, mock_post):
        """A timeout must not leave the run stuck in 'running'."""
        mock_post.side_effect = http_requests.Timeout("too slow")

        run = run_analysis(self.repo, SAMPLE_FILES, user=self.user)

        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("did not respond", run.summary)
        self.assertIsNotNone(run.completion_time)

    @patch("analysis.services.requests.post")
    def test_connection_error_marks_run_failed(self, mock_post):
        mock_post.side_effect = http_requests.ConnectionError("refused")

        run = run_analysis(self.repo, SAMPLE_FILES, user=self.user)

        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("unreachable", run.summary)

    @patch("analysis.services.requests.post")
    def test_http_500_marks_run_failed(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500)

        run = run_analysis(self.repo, SAMPLE_FILES, user=self.user)

        self.assertEqual(run.status, RunStatus.FAILED)

    @patch("analysis.services.requests.post")
    def test_invalid_json_marks_run_failed(self, mock_post):
        mock = MagicMock(status_code=200)
        mock.json.side_effect = ValueError("bad json")
        mock_post.return_value = mock

        run = run_analysis(self.repo, SAMPLE_FILES, user=self.user)

        self.assertEqual(run.status, RunStatus.FAILED)

    @patch("analysis.services.requests.post")
    def test_line_zero_finding_clamped_to_one(self, mock_post):
        """A tool reporting line 0 must not violate the DB constraint."""
        payload = _analyzer_response(findings=[
            {
                "file_path": "app.py",
                "line_number": 0,
                "rule": "X",
                "severity": "low",
                "description": "weird",
            },
        ])
        mock_post.return_value = _mock_http_ok(payload)

        run = run_analysis(self.repo, SAMPLE_FILES, user=self.user)

        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.issues.get().line_number, 1)

    @patch("analysis.services.requests.post")
    def test_call_analyzer_timeout_raises_typed_error(self, mock_post):
        from .services import call_analyzer

        mock_post.side_effect = http_requests.Timeout("slow")
        with self.assertRaises(AnalyzerTimeout):
            call_analyzer(SAMPLE_FILES)

    @patch("analysis.services.requests.post")
    def test_call_analyzer_unreachable_raises_typed_error(self, mock_post):
        from .services import call_analyzer

        mock_post.side_effect = http_requests.ConnectionError("down")
        with self.assertRaises(AnalyzerUnavailable):
            call_analyzer(SAMPLE_FILES)

    @patch("analysis.services.requests.post")
    def test_token_header_sent_when_configured(self, mock_post):
        """When ANALYZER_TOKEN is set, the shared secret rides along."""
        from .services import call_analyzer

        mock_post.return_value = _mock_http_ok()
        with self.settings(ANALYZER_TOKEN="s3cret"):
            call_analyzer(SAMPLE_FILES)

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"], {"X-Analyzer-Token": "s3cret"})

    @patch("analysis.services.requests.post")
    def test_no_token_header_when_not_configured(self, mock_post):
        from .services import call_analyzer

        mock_post.return_value = _mock_http_ok()
        with self.settings(ANALYZER_TOKEN=""):
            call_analyzer(SAMPLE_FILES)

        _, kwargs = mock_post.call_args
        self.assertIsNone(kwargs["headers"])

    @patch("analysis.services.requests.post")
    def test_summary_uses_files_analyzed_not_submitted(self, mock_post):
        """Files the analyzer skipped (non-.py) must not be claimed analyzed."""
        mock_post.return_value = _mock_http_ok({
            "findings": [],
            "meta": {"files_submitted": 2, "files_analyzed": 1,
                     "findings_count": 0, "skipped": []},
        })
        files = [
            {"path": "a.py", "content": "x = 1\n"},
            {"path": "notes.txt", "content": "hello"},
        ]

        run = run_analysis(self.repo, files, user=self.user)

        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertIn("Analyzed 1 file(s)", run.summary)


class AnalysisRunAPITests(APITestCase):
    """POST /api/analysis/runs/ and GET /api/analysis/runs/<id>/."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="Str0ng-Pass!x"
        )
        cls.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="Str0ng-Pass!x"
        )
        cls.repo = Repository.objects.create(
            owner=cls.alice,
            github_url="https://github.com/alice/hello",
            name="hello",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.alice)

    def _payload(self, **overrides):
        data = {"repository": self.repo.pk, "files": SAMPLE_FILES}
        data.update(overrides)
        return data

    @patch("analysis.services.requests.post")
    def test_successful_run_returns_201_with_findings(self, mock_post):
        payload = _analyzer_response(findings=[
            {
                "file_path": "app.py",
                "line_number": 2,
                "rule": "F401",
                "severity": "medium",
                "description": "Unused import.",
            },
        ])
        mock_post.return_value = _mock_http_ok(payload)

        response = self.client.post(RUNS_URL, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(len(response.data["issues"]), 1)
        self.assertEqual(response.data["issues"][0]["rule"], "F401")
        # Persisted in PostgreSQL:
        run = AnalysisRun.objects.get(pk=response.data["id"])
        self.assertEqual(run.issues.count(), 1)

    @patch("analysis.services.requests.post")
    def test_analyzer_timeout_returns_502_but_run_saved_failed(self, mock_post):
        mock_post.side_effect = http_requests.Timeout("slow")

        response = self.client.post(RUNS_URL, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data["status"], "failed")
        # The run is saved (not stuck), with a safe summary.
        run = AnalysisRun.objects.get(pk=response.data["id"])
        self.assertEqual(run.status, RunStatus.FAILED)

    @patch("analysis.services.requests.post")
    def test_analyzer_down_returns_502(self, mock_post):
        mock_post.side_effect = http_requests.ConnectionError("down")

        response = self.client.post(RUNS_URL, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data["status"], "failed")

    def test_missing_files_returns_400(self):
        response = self.client.post(
            RUNS_URL, {"repository": self.repo.pk}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("files", response.data)

    def test_too_many_files_returns_400(self):
        files = [
            {"path": f"f{i}.py", "content": "x=1\n"} for i in range(101)
        ]
        response = self.client.post(
            RUNS_URL, self._payload(files=files), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_users_repository_returns_404(self):
        bobs_repo = Repository.objects.create(
            owner=self.bob,
            github_url="https://github.com/bob/secret",
            name="secret",
        )
        response = self.client.post(
            RUNS_URL, self._payload(repository=bobs_repo.pk), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_nonexistent_repository_returns_404(self):
        response = self.client.post(
            RUNS_URL, self._payload(repository=99999), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("analysis.services.requests.post")
    def test_detail_returns_run_with_issues(self, mock_post):
        payload = _analyzer_response(findings=[
            {
                "file_path": "app.py",
                "line_number": 1,
                "rule": "B105",
                "severity": "high",
                "description": "Hardcoded password.",
            },
        ])
        mock_post.return_value = _mock_http_ok(payload)
        created = self.client.post(RUNS_URL, self._payload(), format="json")

        response = self.client.get(run_detail_url(created.data["id"]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(len(response.data["issues"]), 1)
        self.assertIn("summary", response.data)

    @patch("analysis.services.requests.post")
    def test_cannot_access_other_users_run(self, mock_post):
        mock_post.return_value = _mock_http_ok()
        created = self.client.post(RUNS_URL, self._payload(), format="json")

        self.client.force_authenticate(user=self.bob)
        response = self.client.get(run_detail_url(created.data["id"]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_rejected(self):
        client = APIClient()
        response = client.post(RUNS_URL, self._payload(), format="json")
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )


class RepositoryAnalyzeEndpointTests(APITestCase):
    """POST /api/repositories/<id>/analyze/ -- the spec-preferred endpoint.

    Covers: happy path with source attribution, ownership enforcement,
    unauthenticated rejection, Django-side path/size validation, and
    analyzer-timeout handling.
    """

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="Str0ng-Pass!x"
        )
        cls.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="Str0ng-Pass!x"
        )
        cls.repo = Repository.objects.create(
            owner=cls.alice,
            github_url="https://github.com/alice/hello",
            name="hello",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.alice)
        self.url = f"/api/repositories/{self.repo.pk}/analyze/"

    @staticmethod
    def _finding(**overrides):
        base = {
            "file_path": "app.py",
            "line_number": 1,
            "rule": "F401",
            "severity": "medium",
            "description": "`os` imported but unused",
            "source": "ruff",
        }
        base.update(overrides)
        return base

    def _payload(self, files=None):
        return {"files": SAMPLE_FILES if files is None else files}

    @patch("analysis.services.requests.post")
    def test_analyze_completes_and_saves_source(self, mock_post):
        mock_post.return_value = _mock_http_ok(
            {"findings": [self._finding()],
             "meta": {"files_submitted": 1, "files_analyzed": 1,
                      "findings_count": 1, "skipped": []}}
        )

        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["repository"], self.repo.pk)
        issue = response.data["issues"][0]
        self.assertEqual(issue["rule"], "F401")
        self.assertEqual(issue["source"], "ruff")
        # Persisted in PostgreSQL with the source column populated:
        db_issue = Issue.objects.get(pk=issue["id"])
        self.assertEqual(db_issue.source, "ruff")
        self.assertEqual(db_issue.analysis_run.repository_id, self.repo.pk)

    @patch("analysis.services.requests.post")
    def test_other_users_repository_returns_404(self, mock_post):
        bobs_repo = Repository.objects.create(
            owner=self.bob,
            github_url="https://github.com/bob/secret",
            name="secret",
        )
        response = self.client.post(
            f"/api/repositories/{bobs_repo.pk}/analyze/",
            self._payload(), format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        # Ownership rejected BEFORE any analyzer call was made:
        mock_post.assert_not_called()
        self.assertFalse(AnalysisRun.objects.exists())

    @patch("analysis.services.requests.post")
    def test_nonexistent_repository_returns_404(self, mock_post):
        response = self.client.post(
            "/api/repositories/99999/analyze/",
            self._payload(), format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_post.assert_not_called()

    def test_unauthenticated_rejected(self):
        client = APIClient()
        response = client.post(self.url, self._payload(), format="json")
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    @patch("analysis.services.requests.post")
    def test_path_traversal_rejected_by_django(self, mock_post):
        response = self.client.post(
            self.url,
            self._payload([{"path": "../../etc/passwd", "content": "x\n"}]),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_post.assert_not_called()   # rejected before reaching Flask

    @patch("analysis.services.requests.post")
    def test_sensitive_file_rejected_by_django(self, mock_post):
        response = self.client.post(
            self.url,
            self._payload([{"path": ".env", "content": "SECRET=1\n"}]),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_post.assert_not_called()

    @patch("analysis.services.requests.post")
    def test_absolute_path_rejected_by_django(self, mock_post):
        response = self.client.post(
            self.url,
            self._payload([{"path": "/etc/passwd", "content": "x\n"}]),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_post.assert_not_called()

    @patch("analysis.services.requests.post")
    def test_oversized_file_rejected_by_django(self, mock_post):
        response = self.client.post(
            self.url,
            self._payload([{"path": "big.py", "content": "x" * 200_001}]),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_post.assert_not_called()

    @patch("analysis.services.requests.post")
    def test_too_many_files_rejected_by_django(self, mock_post):
        files = [
            {"path": f"f{i}.py", "content": "x=1\n"} for i in range(101)
        ]
        response = self.client.post(
            self.url, self._payload(files), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_post.assert_not_called()

    @patch("analysis.services.requests.post")
    def test_analyzer_timeout_returns_502_not_stuck(self, mock_post):
        """Timeout -> 502 to the client, run saved as failed (never running)."""
        mock_post.side_effect = http_requests.Timeout("slow")

        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data["status"], "failed")
        run = AnalysisRun.objects.get(pk=response.data["id"])
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIsNotNone(run.completion_time)


# ---------------------------------------------------------------------------
# AI service (Nemotron via Nebius Token Factory): configuration safety and
# error mapping. The OpenAI SDK is fully mocked -- no network, no credentials.
# ---------------------------------------------------------------------------


@override_settings(
    NEBIUS_API_KEY="TEST-SECRET-KEY-MUST-NOT-LEAK",
    NEBIUS_BASE_URL="https://api.tokenfactory.nebius.com/v1/",
    NEBIUS_MODEL="nvidia/nemotron-3-super-120b-a12b",
    NEBIUS_TIMEOUT_SECONDS=7,
)
class AIServiceConfigurationTests(SimpleTestCase):
    """Config errors name variables -- never values. (No DB needed.)"""

    KEY = "TEST-SECRET-KEY-MUST-NOT-LEAK"

    @override_settings(NEBIUS_API_KEY="")
    def test_missing_api_key_named_in_error(self):
        with self.assertRaises(AIConfigurationError) as ctx:
            chat_json("sys", "user")
        msg = str(ctx.exception)
        self.assertIn("NEBIUS_API_KEY", msg)
        self.assertNotIn(self.KEY, msg)

    @override_settings(NEBIUS_MODEL="")
    def test_missing_model_named_and_key_never_leaked(self):
        with self.assertRaises(AIConfigurationError) as ctx:
            chat_json("sys", "user")
        msg = str(ctx.exception)
        self.assertIn("NEBIUS_MODEL", msg)
        # The configured key value must never appear in any error text:
        self.assertNotIn(self.KEY, msg)

    @override_settings(NEBIUS_BASE_URL="")
    def test_missing_base_url_named_in_error(self):
        with self.assertRaises(AIConfigurationError) as ctx:
            chat_json("sys", "user")
        self.assertIn("NEBIUS_BASE_URL", str(ctx.exception))

    @override_settings(NEBIUS_API_KEY="", NEBIUS_MODEL="")
    def test_all_missing_names_listed_together(self):
        with self.assertRaises(AIConfigurationError) as ctx:
            chat_json("sys", "user")
        msg = str(ctx.exception)
        self.assertIn("NEBIUS_API_KEY", msg)
        self.assertIn("NEBIUS_MODEL", msg)

    def test_is_configured_true_when_complete(self):
        self.assertTrue(is_configured())

    @override_settings(NEBIUS_API_KEY="")
    def test_is_configured_false_when_key_missing(self):
        self.assertFalse(is_configured())

    @patch("analysis.ai_service.OpenAI")
    def test_error_before_any_client_construction(self, mock_openai):
        """A config failure must not even build an SDK client."""
        with override_settings(NEBIUS_API_KEY=""):
            with self.assertRaises(AIConfigurationError):
                chat_json("sys", "user")
        mock_openai.assert_not_called()


@override_settings(
    NEBIUS_API_KEY="TEST-SECRET-KEY-MUST-NOT-LEAK",
    NEBIUS_BASE_URL="https://api.tokenfactory.nebius.com/v1/",
    NEBIUS_MODEL="nvidia/nemotron-3-super-120b-a12b",
    NEBIUS_TIMEOUT_SECONDS=7,
)
class AIServiceCallTests(SimpleTestCase):
    """Happy path + failure mapping with the OpenAI SDK fully mocked."""

    KEY = "TEST-SECRET-KEY-MUST-NOT-LEAK"
    MODEL = "nvidia/nemotron-3-super-120b-a12b"

    @staticmethod
    def _response(raw):
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = raw
        resp.choices = [choice]
        return resp

    @staticmethod
    def _request():
        return httpx2.Request(
            "POST", "https://api.tokenfactory.nebius.com/v1/chat/completions"
        )

    @patch("analysis.ai_service.OpenAI")
    def test_success_returns_parsed_json_and_wires_correct_client(self, mock_openai):
        client = mock_openai.return_value
        client.chat.completions.create.return_value = self._response(
            json.dumps({"explanation": "use literal_eval", "confidence": 0.9})
        )

        result = chat_json("Return JSON.", "Fix eval()")

        self.assertEqual(result,
                         {"explanation": "use literal_eval", "confidence": 0.9})
        # Client wired with credentials FROM SETTINGS, official base URL:
        kwargs = mock_openai.call_args.kwargs
        self.assertEqual(kwargs["api_key"], self.KEY)
        self.assertEqual(kwargs["base_url"],
                         "https://api.tokenfactory.nebius.com/v1/")
        self.assertEqual(kwargs["timeout"], 7)
        # Request goes to the exact configured model -- no silent fallback:
        create_kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(create_kwargs["model"], self.MODEL)
        self.assertEqual(
            [m["role"] for m in create_kwargs["messages"]],
            ["system", "user"],
        )
        self.assertEqual(create_kwargs["response_format"],
                         {"type": "json_object"})

    @patch("analysis.ai_service.OpenAI")
    def test_non_json_reply_is_safe_error(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = \
            self._response("definitely not json")
        with self.assertRaises(AIUnavailable) as ctx:
            chat_json("s", "u")
        self.assertIn("valid JSON", str(ctx.exception))

    @patch("analysis.ai_service.OpenAI")
    def test_non_object_json_rejected(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = \
            self._response("[1, 2, 3]")
        with self.assertRaises(AIUnavailable):
            chat_json("s", "u")

    @patch("analysis.ai_service.OpenAI")
    def test_empty_reply_is_safe_error(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value = \
            self._response("")
        with self.assertRaises(AIUnavailable) as ctx:
            chat_json("s", "u")
        self.assertIn("empty", str(ctx.exception))

    @patch("analysis.ai_service.OpenAI")
    def test_auth_error_mapped_without_leaking_key(self, mock_openai):
        mock_openai.return_value.chat.completions.create.side_effect = (
            openai_auth_error()
        )
        with self.assertRaises(AIUnavailable) as ctx:
            chat_json("s", "u")
        msg = str(ctx.exception)
        self.assertIn("NEBIUS_API_KEY", msg)
        self.assertNotIn(self.KEY, msg)

    @patch("analysis.ai_service.OpenAI")
    def test_timeout_mapped_to_typed_error(self, mock_openai):
        mock_openai.return_value.chat.completions.create.side_effect = (
            openai_timeout_error()
        )
        with self.assertRaises(AITimeout) as ctx:
            chat_json("s", "u")
        self.assertIn("7 seconds", str(ctx.exception))

    @patch("analysis.ai_service.OpenAI")
    def test_connection_error_maps_to_unreachable(self, mock_openai):
        import openai as openai_sdk

        mock_openai.return_value.chat.completions.create.side_effect = (
            openai_sdk.APIConnectionError(
                message="Connection error.",
                request=httpx2.Request("POST", "https://example.invalid"),
            )
        )
        with self.assertRaises(AIUnavailable) as ctx:
            chat_json("s", "u")
        self.assertIn("unreachable", str(ctx.exception))

    @patch("analysis.ai_service.OpenAI")
    def test_rate_limit_maps_to_budget_message(self, mock_openai):
        import openai as openai_sdk

        mock_openai.return_value.chat.completions.create.side_effect = (
            openai_sdk.RateLimitError(
                "429",
                response=httpx2.Response(
                    429, request=httpx2.Request("POST", "https://x/v1")
                ),
                body=None,
            )
        )
        with self.assertRaises(AIUnavailable) as ctx:
            chat_json("s", "u")
        self.assertIn("budget", str(ctx.exception))

    @patch("analysis.ai_service.OpenAI")
    def test_unexpected_exception_never_leaks_details(self, mock_openai):
        mock_openai.return_value.chat.completions.create.side_effect = (
            RuntimeError(f"boom details {self.KEY}")
        )
        with self.assertRaises(AIUnavailable) as ctx:
            chat_json("s", "u")
        msg = str(ctx.exception)
        self.assertNotIn(self.KEY, msg)
        self.assertNotIn("boom", msg)

    @patch("analysis.ai_service.OpenAI")
    def test_no_silent_fallback_on_missing_model(self, mock_openai):
        """With the model unset, nothing is sent anywhere."""
        with override_settings(NEBIUS_MODEL=""):
            with self.assertRaises(AIConfigurationError):
                chat_json("s", "u")
        mock_openai.return_value.chat.completions.create.assert_not_called()


def openai_auth_error():
    import openai as openai_sdk

    return openai_sdk.AuthenticationError(
        "401 denied",
        response=httpx2.Response(
            401, request=httpx2.Request("POST", "https://x/v1")
        ),
        body=None,
    )


def openai_timeout_error():
    import openai as openai_sdk

    return openai_sdk.APITimeoutError(
        request=httpx2.Request("POST", "https://x/v1")
    )
