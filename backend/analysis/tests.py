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
from repositories.services import GitHubRateLimited, GitHubRepoNotFound

from .ai_service import (
    AIConfigurationError,
    AITimeout,
    AIUnavailable,
    _reasoning_effort_body,
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
    VerificationStatus,
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
    def test_zero_findings_is_completed_not_failed(self, mock_post):
        """A clean analysis with ZERO findings is a SUCCESS, not a failure.

        Definition-of-done: 'completed with 0 issues' must remain distinct
        from 'analysis failed'. Both the status and the timestamps say
        success; only the issue list is empty.
        """
        mock_post.return_value = _mock_http_ok(_analyzer_response(findings=[]))

        run = run_analysis(self.repo, SAMPLE_FILES, user=self.user)

        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.issues.count(), 0)
        self.assertIsNotNone(run.completion_time)
        self.assertNotIn("error", run.summary.lower())

    @patch("analysis.services.requests.post")
    def test_zero_findings_and_analysis_failure_are_distinct(self, mock_post):
        """The direct contrast: clean run vs failed run are distinguishable."""
        # 1) Clean run: completed + empty issue list.
        mock_post.return_value = _mock_http_ok(_analyzer_response(findings=[]))
        clean = run_analysis(self.repo, SAMPLE_FILES, user=self.user)

        # 2) Failed run: 500 from the analyzer.
        mock_post.return_value = MagicMock(status_code=500)
        broken = run_analysis(self.repo, SAMPLE_FILES, user=self.user)

        self.assertEqual(clean.status, RunStatus.COMPLETED)
        self.assertEqual(clean.issues.count(), 0)
        self.assertEqual(broken.status, RunStatus.FAILED)
        self.assertTrue(broken.summary)          # a reason, never blank
        # The failure must never masquerade as a clean empty result:
        self.assertNotEqual(
            (clean.status, clean.issues.count()),
            (broken.status, broken.issues.count()),
        )

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
        # Bounded-issues metadata (Phase 8): always present, accurate here.
        self.assertEqual(response.data["issue_count"], 1)
        self.assertFalse(response.data["issues_truncated"])

    @patch("analysis.services.requests.post")
    def test_detail_caps_embedded_issues_and_reports_truncation(self, mock_post):
        """Past DETAIL_ISSUE_LIMIT the embedded list is cut, count stays true."""
        from .serializers import DETAIL_ISSUE_LIMIT

        findings = [
            {
                "file_path": f"f{i}.py",
                "line_number": 1,
                "rule": "F401",
                "severity": "low",
                "description": "unused import",
            }
            for i in range(DETAIL_ISSUE_LIMIT + 25)
        ]
        mock_post.return_value = _mock_http_ok(_analyzer_response(findings=findings))
        created = self.client.post(RUNS_URL, self._payload(), format="json")

        response = self.client.get(run_detail_url(created.data["id"]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["issues"]), DETAIL_ISSUE_LIMIT)
        self.assertEqual(
            response.data["issue_count"], DETAIL_ISSUE_LIMIT + 25
        )
        self.assertTrue(response.data["issues_truncated"])

    @patch("analysis.services.requests.post")
    def test_paginated_issues_endpoint_returns_all(self, mock_post):
        """GET .../issues/ pages through the full list beyond the embed cap."""
        from .serializers import DETAIL_ISSUE_LIMIT

        findings = [
            {
                "file_path": f"f{i:04d}.py",
                "line_number": 1,
                "rule": "F401",
                "severity": "low",
                "description": "unused import",
            }
            for i in range(DETAIL_ISSUE_LIMIT + 10)
        ]
        mock_post.return_value = _mock_http_ok(_analyzer_response(findings=findings))
        created = self.client.post(RUNS_URL, self._payload(), format="json")
        run_id = created.data["id"]

        page1 = self.client.get(f"/api/analysis/runs/{run_id}/issues/")
        self.assertEqual(page1.status_code, status.HTTP_200_OK)
        self.assertIn("results", page1.data)
        self.assertEqual(page1.data["count"], DETAIL_ISSUE_LIMIT + 10)
        self.assertEqual(len(page1.data["results"]), 20)   # default page size

        # Walk every page and confirm we see exactly `count` unique issues.
        seen, url = set(), f"/api/analysis/runs/{run_id}/issues/"
        while url:
            page = self.client.get(url)
            for row in page.data["results"]:
                seen.add(row["id"])
            url = page.data.get("next")
        self.assertEqual(len(seen), DETAIL_ISSUE_LIMIT + 10)

    @patch("analysis.services.requests.post")
    def test_paginated_issues_endpoint_foreign_run_404(self, mock_post):
        mock_post.return_value = _mock_http_ok()
        created = self.client.post(RUNS_URL, self._payload(), format="json")

        self.client.force_authenticate(user=self.bob)
        response = self.client.get(
            f"/api/analysis/runs/{created.data['id']}/issues/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

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
    def _response(raw, finish_reason=None):
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = raw
        if finish_reason is not None:
            choice.finish_reason = finish_reason
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
    def test_completion_is_bounded_by_max_tokens(self, mock_openai):
        """A single completion is capped -- no unbounded model output.

        The default bound applies automatically; an explicit smaller value is
        honored (the agent layer can tighten it further when needed).
        """
        from .ai_service import MAX_COMPLETION_TOKENS

        client = mock_openai.return_value
        client.chat.completions.create.return_value = self._response(
            json.dumps({"ok": True})
        )

        chat_json("Return JSON.", "hello")
        create_kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(create_kwargs["max_tokens"], MAX_COMPLETION_TOKENS)

        chat_json("Return JSON.", "hello", max_tokens=256)
        create_kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(create_kwargs["max_tokens"], 256)
        # The bound is a real integer, not None/0 (which would mean
        # "provider default" or "no output at all").
        self.assertIsInstance(MAX_COMPLETION_TOKENS, int)
        self.assertGreater(MAX_COMPLETION_TOKENS, 0)

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
    def test_empty_reply_cut_by_token_limit_is_specific(self, mock_openai):
        """A reasoning model can burn the whole budget on hidden thinking.

        finish_reason == "length" with no visible content must be reported
        as a token-limit truncation, not as a generic empty response.
        """
        mock_openai.return_value.chat.completions.create.return_value = \
            self._response("", finish_reason="length")
        with self.assertRaises(AIUnavailable) as ctx:
            chat_json("s", "u")
        self.assertIn("token limit", str(ctx.exception))
        self.assertNotIn("empty response", str(ctx.exception))

    @patch("analysis.ai_service.OpenAI")
    def test_truncated_json_reports_token_limit(self, mock_openai):
        """Cut-off mid-JSON is diagnosed as truncation, not malformed JSON."""
        mock_openai.return_value.chat.completions.create.return_value = \
            self._response('{"path": "ap', finish_reason="length")
        with self.assertRaises(AIUnavailable) as ctx:
            chat_json("s", "u")
        self.assertIn("token limit", str(ctx.exception))

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

    def test_reasoning_effort_valid_value_is_forwarded(self):
        """The official Nebius reasoning_effort knob maps to extra_body."""
        with override_settings(NEBIUS_REASONING_EFFORT="low"):
            self.assertEqual(
                _reasoning_effort_body(),
                {"extra_body": {"reasoning_effort": "low"}},
            )
        for value in ("NONE", "xhigh"):
            with override_settings(NEBIUS_REASONING_EFFORT=value):
                self.assertEqual(
                    _reasoning_effort_body(),
                    {"extra_body": {"reasoning_effort": value.lower()}},
                )

    def test_reasoning_effort_empty_default_or_unknown_is_omitted(self):
        """No parameter is sent unless the effort is a real enum value."""
        for value in ("", "default", "  DEFAULT  ", "turbo", None):
            with override_settings(NEBIUS_REASONING_EFFORT=value):
                self.assertEqual(_reasoning_effort_body(), {}, repr(value))

    @patch("analysis.ai_service.OpenAI")
    def test_chat_json_sends_reasoning_effort_extra_body(self, mock_openai):
        client = mock_openai.return_value
        client.chat.completions.create.return_value = self._response(
            '{"ok": true}'
        )
        with override_settings(NEBIUS_REASONING_EFFORT="minimal"):
            chat_json("Return JSON.", "hello")
        create_kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(
            create_kwargs["extra_body"], {"reasoning_effort": "minimal"}
        )

    @patch("analysis.ai_service.OpenAI")
    def test_server_5xx_is_reported_as_temporary(self, mock_openai):
        """Token Factory endpoints cold-start into 503s (verified live).

        That is a temporary condition, not an auth/config problem; only
        the status code reaches the message, never the response body.
        """
        import openai as openai_sdk

        mock_openai.return_value.chat.completions.create.side_effect = (
            openai_sdk.APIStatusError(
                "503 no capacity",
                response=httpx2.Response(503, request=self._request()),
                body=None,
            )
        )
        with self.assertRaises(AIUnavailable) as ctx:
            chat_json("s", "u")
        msg = str(ctx.exception)
        self.assertIn("temporarily unavailable", msg)
        self.assertIn("503", msg)
        self.assertNotIn(self.KEY, msg)

    @patch("analysis.ai_service.OpenAI")
    def test_server_4xx_status_is_safe_generic_message(self, mock_openai):
        import openai as openai_sdk

        mock_openai.return_value.chat.completions.create.side_effect = (
            openai_sdk.APIStatusError(
                "400 bad request",
                response=httpx2.Response(400, request=self._request()),
                body=None,
            )
        )
        with self.assertRaises(AIUnavailable) as ctx:
            chat_json("s", "u")
        msg = str(ctx.exception)
        self.assertIn("HTTP 400", msg)
        self.assertNotIn(self.KEY, msg)


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


# ---------------------------------------------------------------------------
# Phase 7 -- Agent run (autonomous bug fixing) tests
# ---------------------------------------------------------------------------

import base64 as _base64
import os as _os
import tempfile as _tempfile
from types import SimpleNamespace as _NS

from . import sandbox as _sandbox_module
from .agent_service import run_agent
from .patching import PatchError, apply_patch, make_diff, parse_patch
from .sandbox import SandboxConfigurationError, SandboxResult, build_run_command
from .snapshot import (
    BLOB_CACHE_MAX_BYTES,
    Snapshot,
    SnapshotError,
    _BlobCache,
    clear_snapshot_cache,
    fetch_github_snapshot,
    is_sensitive_path,
)

BUGGY_SOURCE = "def add(a, b):\n    return a - b\n"
FIXED_SOURCE = "def add(a, b):\n    return a + b\n"
GOOD_PATCH = {
    "path": "app.py",
    "old_code": "return a - b",
    "new_code": "return a + b",
    "explanation": "Replace the subtraction with addition.",
}
CLEAN_ANALYZER = {"summary": "clean", "files_analyzed": 1, "issues": []}

# Sentinel: distinguishes "no sandbox argument" (use default) from an
# explicit None (sandbox unavailable).
_SENTINEL = object()


def make_snapshot(files=None):
    return Snapshot(
        owner="octo",
        repo="hello",
        commit_sha="a" * 40,
        files=files if files is not None else {"app.py": BUGGY_SOURCE},
    )


def agent_detail_url(pk):
    return f"/api/agent-runs/{pk}/"


def agent_changes_url(pk):
    return f"/api/agent-runs/{pk}/changes/"


def agent_tests_url(pk):
    return f"/api/agent-runs/{pk}/tests/"


def agent_run_url_for_issue(issue_id):
    return f"/api/analysis/issues/{issue_id}/agent-run/"


class AgentFixtureMixin:
    """Shared fixtures: user, repo, analysis run, one issue in app.py."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="Str0ng-Pass!x"
        )
        cls.other = User.objects.create_user(
            username="bob", email="bob@example.com", password="Str0ng-Pass!x"
        )
        cls.repo = Repository.objects.create(
            owner=cls.user,
            github_url="https://github.com/octo/hello",
            name="hello",
        )
        cls.analysis_run = AnalysisRun.objects.create(repository=cls.repo)
        cls.issue = Issue.objects.create(
            analysis_run=cls.analysis_run,
            file_path="app.py",
            line_number=2,
            severity=Severity.LOW,
            rule="AST001",
            source="ast",
            description="add() subtracts instead of adding.",
        )


class PatchingUnitTests(SimpleTestCase):
    """parse_patch / apply_patch / make_diff: untrusted-patch validation."""

    def test_valid_patch_parses(self):
        patch = parse_patch(GOOD_PATCH, make_snapshot())
        self.assertEqual(patch["path"], "app.py")

    def test_non_dict_reply_rejected(self):
        with self.assertRaises(PatchError):
            parse_patch(["not", "a", "dict"], make_snapshot())

    def test_path_traversal_rejected(self):
        bad = dict(GOOD_PATCH, path="../evil.py")
        with self.assertRaises(PatchError):
            parse_patch(bad, make_snapshot())

    def test_absolute_path_rejected(self):
        bad = dict(GOOD_PATCH, path="/etc/passwd.py")
        with self.assertRaises(PatchError):
            parse_patch(bad, make_snapshot())

    def test_backslash_path_rejected(self):
        bad = dict(GOOD_PATCH, path="src\\evil.py")
        with self.assertRaises(PatchError):
            parse_patch(bad, make_snapshot())

    def test_non_python_file_rejected(self):
        snap = make_snapshot({"README.md": "hi"})
        bad = dict(GOOD_PATCH, path="README.md")
        with self.assertRaises(PatchError):
            parse_patch(bad, snap)

    def test_sensitive_path_rejected(self):
        snap = make_snapshot({".env.py": "SECRET = 1\n"})
        bad = dict(GOOD_PATCH, path=".env.py")
        with self.assertRaises(PatchError):
            parse_patch(bad, snap)

    def test_path_outside_snapshot_rejected(self):
        with self.assertRaises(PatchError):
            parse_patch(GOOD_PATCH, make_snapshot({"other.py": "x = 1\n"}))

    def test_empty_old_code_rejected(self):
        bad = dict(GOOD_PATCH, old_code="   ")
        with self.assertRaises(PatchError):
            parse_patch(bad, make_snapshot())

    def test_identical_new_code_rejected(self):
        bad = dict(GOOD_PATCH, new_code="return a - b")
        with self.assertRaises(PatchError):
            parse_patch(bad, make_snapshot())

    def test_old_code_not_in_file_rejected(self):
        bad = dict(GOOD_PATCH, old_code="return a * b")
        with self.assertRaises(PatchError):
            parse_patch(bad, make_snapshot())

    def test_ambiguous_old_code_rejected(self):
        dup = "x = 1\ny = 2\n"
        snap = make_snapshot({"app.py": dup + dup})
        bad = {"path": "app.py", "old_code": "x = 1", "new_code": "x = 2"}
        with self.assertRaises(PatchError):
            parse_patch(bad, snap)

    def test_mismatch_message_teaches_byte_exact_copy(self):
        """The rejection doubles as actionable retry feedback for the model.

        A model that quoted the snippet imperfectly must be told HOW to
        correct it: byte-for-byte, same indentation and quotes.
        """
        snap = make_snapshot({"app.py": "x = 1\n"})
        bad = {"path": "app.py", "old_code": "  nope  ", "new_code": "y = 2",
               "explanation": "change it"}
        with self.assertRaises(PatchError) as ctx:
            parse_patch(bad, snap)
        msg = str(ctx.exception)
        self.assertIn("does not match the snapshot file", msg)
        self.assertIn("byte-for-byte", msg)

    def test_make_diff_has_headers_and_change(self):
        diff = make_diff("app.py", BUGGY_SOURCE, FIXED_SOURCE)
        self.assertIn("--- a/app.py", diff)
        self.assertIn("+++ b/app.py", diff)
        self.assertIn("-    return a - b", diff)
        self.assertIn("+    return a + b", diff)

    def test_make_diff_truncates_huge_output(self):
        big_before = "\n".join(f"line {i}" for i in range(5000))
        big_after = "\n".join(f"changed {i}" for i in range(5000))
        diff = make_diff("app.py", big_before, big_after)
        self.assertLessEqual(len(diff), 20_000 + 100)
        self.assertIn("[diff truncated]", diff)

    def test_apply_patch_writes_workspace_only(self):
        with _tempfile.TemporaryDirectory() as tmp:
            target = _os.path.join(tmp, "app.py")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(BUGGY_SOURCE)
            updated = apply_patch(
                _os.path.join(tmp), "app.py", "return a - b", "return a + b"
            )
            self.assertIn("return a + b", updated)
            with open(target, encoding="utf-8") as fh:
                self.assertIn("return a + b", fh.read())

    def test_apply_patch_refuses_workspace_escape(self):
        with _tempfile.TemporaryDirectory() as tmp:
            with _tempfile.TemporaryDirectory() as outside:
                victim = _os.path.join(outside, "victim.py")
                with open(victim, "w", encoding="utf-8") as fh:
                    fh.write(BUGGY_SOURCE)
                escape = "../" + _os.path.basename(outside) + "/victim.py"
                with self.assertRaises(PatchError):
                    apply_patch(tmp, escape, "return a - b", "return a + b")
                with open(victim, encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), BUGGY_SOURCE)

    def test_apply_patch_missing_file_rejected(self):
        with _tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PatchError):
                apply_patch(tmp, "missing.py", "a", "b")

    def test_apply_patch_stale_old_code_rejected(self):
        with _tempfile.TemporaryDirectory() as tmp:
            with open(_os.path.join(tmp, "app.py"), "w", encoding="utf-8") as fh:
                fh.write(FIXED_SOURCE)
            with self.assertRaises(PatchError):
                apply_patch(tmp, "app.py", "return a - b", "return a + b")


class SandboxUnitTests(SimpleTestCase):
    """Docker sandbox: restrictions, disabled paths, timeout cleanup."""

    def test_command_includes_all_restrictions(self):
        cmd = build_run_command("docker", "n1", "/tmp/ws", "python:3.12-slim", "65534")
        joined = " ".join(cmd)
        for flag in (
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65534",
            "--pids-limit=",
            "--memory=",
            "--cpus=",
        ):
            self.assertIn(flag, joined)
        # The command tail is exactly the allowlisted unittest invocation.
        self.assertEqual(
            tuple(cmd[-len(_sandbox_module.ALLOWED_COMMANDS[0]):]),
            _sandbox_module.ALLOWED_COMMANDS[0],
        )

    def test_command_never_allows_git_pip_or_shell(self):
        cmd = build_run_command("docker", "n1", "/tmp/ws", "img", "65534")
        joined = " ".join(cmd)
        for forbidden in ("git", "push", "pip", "curl", "bash", "sh ", "cmd"):
            self.assertNotIn(forbidden, joined)

    def test_root_user_refused(self):
        with self.assertRaises(SandboxConfigurationError):
            build_run_command("docker", "n1", "/tmp/ws", "img", "0")
        with self.assertRaises(SandboxConfigurationError):
            build_run_command("docker", "n1", "/tmp/ws", "img", "root")

    @override_settings(AGENT_TEST_EXECUTION=False)
    def test_disabled_by_default_returns_none(self):
        self.assertIsNone(_sandbox_module.run_tests("/tmp/ws"))

    @override_settings(AGENT_TEST_EXECUTION=True)
    def test_returns_none_without_docker_daemon(self):
        with patch("analysis.sandbox.docker_available", return_value=False):
            self.assertIsNone(_sandbox_module.run_tests("/tmp/ws"))

    @override_settings(AGENT_TEST_EXECUTION=True)
    def test_returns_none_when_image_missing(self):
        with patch("analysis.sandbox.docker_available", return_value=True), \
                patch("analysis.sandbox.image_present", return_value=False):
            self.assertIsNone(_sandbox_module.run_tests("/tmp/ws"))

    @override_settings(
        AGENT_TEST_EXECUTION=True, SANDBOX_TIMEOUT_SECONDS=5
    )
    def test_timeout_is_reported_and_container_removed(self):
        import subprocess as _sp

        with patch("analysis.sandbox.docker_available", return_value=True), \
                patch("analysis.sandbox.image_present", return_value=True), \
                patch("analysis.sandbox.shutil.which", return_value="docker"), \
                patch(
                    "analysis.sandbox.subprocess.run",
                    side_effect=_sp.TimeoutExpired(cmd="docker", timeout=5),
                ) as mock_run:
            result = _sandbox_module.run_tests("/tmp/ws")
        self.assertIsNotNone(result)
        self.assertTrue(result.timed_out)
        self.assertIn("timed out", result.output)
        # Cleanup must have been attempted despite the timeout.
        cleanup_calls = [
            c for c in mock_run.call_args_list if "rm" in c.args[0]
        ]
        self.assertTrue(cleanup_calls)

    @override_settings(AGENT_TEST_EXECUTION=True)
    def test_start_failure_is_reported_safely(self):
        with patch("analysis.sandbox.docker_available", return_value=True), \
                patch("analysis.sandbox.image_present", return_value=True), \
                patch("analysis.sandbox.shutil.which", return_value="docker"), \
                patch(
                    "analysis.sandbox.subprocess.run",
                    side_effect=OSError("no daemon"),
                ):
            result = _sandbox_module.run_tests("/tmp/ws")
        self.assertIsNotNone(result)
        self.assertIsNone(result.returncode)
        self.assertIn("failed to start", result.output)

    @override_settings(AGENT_TEST_EXECUTION=True)
    def test_nonzero_exit_reports_failure(self):
        ok = _NS(returncode=0, stdout="ok", stderr="")
        rm = _NS(returncode=0, stdout="", stderr="")
        failed = _NS(returncode=1, stdout="", stderr="FAILED (1 test)")
        with patch("analysis.sandbox.docker_available", return_value=True), \
                patch("analysis.sandbox.image_present", return_value=True), \
                patch("analysis.sandbox.shutil.which", return_value="docker"), \
                patch("analysis.sandbox.subprocess.run", side_effect=[failed, rm]):
            result = _sandbox_module.run_tests("/tmp/ws")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED", result.output)


class _FakeResponse:
    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self._data = data or {}
        self.headers = headers or {}

    def json(self):
        return self._data


def _fake_github(responses):
    """A fake requests.Session whose .get() walks through `responses`."""
    session = _NS()
    session.headers = {}
    session.get = MagicMock(
        side_effect=[_FakeResponse(**r) if isinstance(r, dict) else r
                     for r in responses]
    )
    return session


class SnapshotUnitTests(SimpleTestCase):
    """Snapshot fetching: exclusions, limits, GitHub error taxonomy."""

    def setUp(self):
        # The blob cache is module-level and persists across tests; clear it
        # so each test sees a cold cache and its ordered fake-response list
        # stays in sync with the number of HTTP calls actually made.
        clear_snapshot_cache()

    def tearDown(self):
        clear_snapshot_cache()

    def test_sensitive_path_detection(self):
        for p in (".env", "src/.env.local", "id_rsa", "keys/server.pem",
                  "cfg/credentials"):
            self.assertTrue(is_sensitive_path(p), p)
        for p in ("app.py", "src/main.py", "environment.py"):
            self.assertFalse(is_sensitive_path(p), p)

    def test_context_for_small_file_returned_whole(self):
        snap = make_snapshot({"app.py": "x = 1\n"})
        self.assertEqual(snap.context_for("app.py", 100), "x = 1\n")

    def test_context_for_big_file_head_fallback_without_focus(self):
        big = "".join(f"# line {i}\n" for i in range(1, 2001))
        snap = make_snapshot({"app.py": big})
        out = snap.context_for("app.py", 200)
        self.assertTrue(out.startswith("# line 1\n"))
        self.assertIn("[truncated for prompt size]", out)
        self.assertLessEqual(len(out), 200 + 40)

    def test_context_for_big_file_centers_on_focus_line(self):
        """A production-scale file must show the BUGGY line, not just its head.

        With head-truncation alone, an issue at line 1500 of a big file
        could never be patched exactly, because the model never saw it.
        """
        big = "".join(f"# line {i}\n" for i in range(1, 2001))
        snap = make_snapshot({"app.py": big})
        out = snap.context_for("app.py", 200, focus_line=1500)
        self.assertIn("# line 1500\n", out)
        self.assertIn("[earlier lines omitted]", out)
        self.assertIn("[later lines omitted]", out)
        self.assertNotIn("# line 1\n", out)  # the old head is gone
        self.assertLessEqual(len(out), 300)

    def test_context_for_focus_near_file_start_has_no_prefix_marker(self):
        big = "".join(f"# line {i}\n" for i in range(1, 2001))
        snap = make_snapshot({"app.py": big})
        out = snap.context_for("app.py", 200, focus_line=3)
        self.assertIn("# line 3\n", out)
        self.assertNotIn("[earlier lines omitted]", out)
        self.assertIn("[later lines omitted]", out)

    def test_focus_window_keeps_lines_whole_when_budget_is_tight(self):
        """The hard byte bound must never leave a HALF line in the prompt.

        Regression: chunk[:max_bytes] cut the boundary line mid-way (its
        head became a phantom short line); a model quoting that fragment
        byte-for-byte could never match the real file.
        """
        big = "".join(
            f"# line {i:04d} {'y' * 36}\n" for i in range(1, 61)
        )
        snap = make_snapshot({"app.py": big})
        out = snap.context_for("app.py", 300, focus_line=30)
        self.assertIn("# line 0030", out)  # the focused line survives
        body = out.removeprefix("... [earlier lines omitted] ...\n")
        body = body.removesuffix("\n... [later lines omitted] ...")
        self.assertGreater(len(body), 0)
        segments = body.splitlines(keepends=True)
        self.assertTrue(
            all(seg.endswith("\n") for seg in segments),
            "a half line leaked into the prompt window",
        )

    def test_fetch_happy_path_records_commit_sha(self):
        blob = _base64.b64encode(BUGGY_SOURCE.encode()).decode()
        responses = [
            dict(data={"default_branch": "main"}),
            dict(data={"sha": "c" * 40}),
            dict(data={"truncated": False, "tree": [
                {"type": "blob", "path": "app.py", "sha": "b1", "size": 40},
            ]}),
            dict(data={"encoding": "base64", "content": blob}),
        ]
        with patch("analysis.snapshot.requests.Session",
                   return_value=_fake_github(responses)):
            snap = fetch_github_snapshot(
                _NS(github_url="https://github.com/octo/hello")
            )
        self.assertEqual(snap.commit_sha, "c" * 40)
        self.assertEqual(snap.files["app.py"], BUGGY_SOURCE)
        self.assertEqual(snap.skipped, [])

    def test_blob_cache_avoids_repeat_downloads_on_second_fetch(self):
        """A second fetch of the same blobs issues NO new blob HTTP requests.

        This is the N+1 fix: blobs are content-addressed by SHA, so a cached
        blob is byte-identical to a fresh download. Only the metadata calls
        (repo meta, commit, tree) repeat; blob downloads are served from cache.
        """
        blob = _base64.b64encode(BUGGY_SOURCE.encode()).decode()
        tree = dict(data={"truncated": False, "tree": [
            {"type": "blob", "path": "app.py", "sha": "b1", "size": 40},
        ]})

        # First fetch: full sequence -- meta, commit, tree, blob.
        first = [
            dict(data={"default_branch": "main"}),
            dict(data={"sha": "c" * 40}),
            tree,
            dict(data={"encoding": "base64", "content": blob}),
        ]
        with patch("analysis.snapshot.requests.Session",
                   return_value=_fake_github(first)):
            snap1 = fetch_github_snapshot(
                _NS(github_url="https://github.com/octo/hello")
            )

        # Second fetch: blob is cached, so NO blob response is provided. If the
        # code tried to download it again, the side_effect list would be
        # exhausted and raise StopIteration -- proving the cache was consulted.
        second = [
            dict(data={"default_branch": "main"}),
            dict(data={"sha": "c" * 40}),
            tree,
        ]
        fake_session = _fake_github(second)
        with patch("analysis.snapshot.requests.Session",
                   return_value=fake_session):
            snap2 = fetch_github_snapshot(
                _NS(github_url="https://github.com/octo/hello")
            )
            # Exactly 3 HTTP calls (meta, commit, tree) -- the blob was cached.
            self.assertEqual(fake_session.get.call_count, 3)

        self.assertEqual(snap1.files["app.py"], BUGGY_SOURCE)
        self.assertEqual(snap2.files["app.py"], BUGGY_SOURCE)

    def test_blob_cache_evicts_when_entry_bound_exceeded(self):
        """The cache never exceeds its configured entry bound (FIFO)."""
        cache = _BlobCache(max_entries=2, max_bytes=BLOB_CACHE_MAX_BYTES)
        cache.put("s1", "one")
        cache.put("s2", "two")
        cache.put("s3", "three")
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get("s1"))   # oldest evicted
        self.assertEqual(cache.get("s3"), "three")

    def test_fetch_excludes_sensitive_oversized_and_non_python(self):
        blob = _base64.b64encode(BUGGY_SOURCE.encode()).decode()
        responses = [
            dict(data={"default_branch": "main"}),
            dict(data={"sha": "c" * 40}),
            dict(data={"truncated": False, "tree": [
                {"type": "blob", "path": "secrets.py", "sha": "sec", "size": 10},
                {"type": "blob", "path": "README.md", "sha": "x", "size": 10},
                {"type": "blob", "path": "big.py", "sha": "x",
                 "size": 10_000_000},
                {"type": "blob", "path": "app.py", "sha": "b1", "size": 40},
            ]}),
            # Only app.py is downloaded; id_rsa.py is excluded, never fetched.
            dict(data={"encoding": "base64", "content": blob}),
        ]
        with patch("analysis.snapshot.requests.Session",
                   return_value=_fake_github(responses)):
            snap = fetch_github_snapshot(
                _NS(github_url="https://github.com/octo/hello")
            )
        self.assertEqual(set(snap.files), {"app.py"})
        self.assertNotIn("secrets.py", snap.files)
        self.assertTrue(any("secrets.py" in s for s in snap.skipped))
        self.assertTrue(any("big.py" in s for s in snap.skipped))
        # Non-.py files are filtered by extension and are not "skipped".
        self.assertFalse(any("README" in s for s in snap.skipped))

    def test_fetch_maps_404_to_safe_error(self):
        with patch("analysis.snapshot.requests.Session",
                   return_value=_fake_github([dict(status_code=404)])):
            with self.assertRaises(GitHubRepoNotFound):
                fetch_github_snapshot(
                    _NS(github_url="https://github.com/octo/hello")
                )

    def test_fetch_maps_rate_limit_to_safe_error(self):
        with patch("analysis.snapshot.requests.Session",
                   return_value=_fake_github([dict(status_code=429)])):
            with self.assertRaises(GitHubRateLimited):
                fetch_github_snapshot(
                    _NS(github_url="https://github.com/octo/hello")
                )

    def test_fetch_without_python_files_raises(self):
        responses = [
            dict(data={"default_branch": "main"}),
            dict(data={"sha": "c" * 40}),
            dict(data={"truncated": False, "tree": [
                {"type": "blob", "path": "README.md", "sha": "x", "size": 10},
            ]}),
        ]
        with patch("analysis.snapshot.requests.Session",
                   return_value=_fake_github(responses)):
            with self.assertRaises(SnapshotError):
                fetch_github_snapshot(
                    _NS(github_url="https://github.com/octo/hello")
                )

    def test_invalid_github_url_raises_snapshot_error(self):
        with self.assertRaises(SnapshotError):
            fetch_github_snapshot(_NS(github_url="https://evil.com/x"))


@override_settings(AGENT_TEST_EXECUTION=False)
class AgentServiceTests(AgentFixtureMixin, TestCase):
    """Full lifecycle with snapshot, AI, analyzer and sandbox mocked."""

    def _run_with(self, snapshot=None, chat=None, analyzer=None,
                  sandbox=_SENTINEL):
        """Drive run_agent with all externals mocked.

        - chat / analyzer: a dict -> single return value; a list -> a
          sequence of per-attempt values (side_effect).
        - sandbox: _SENTINEL -> default passing result; None -> sandbox
          unavailable; a list -> per-attempt results.
        """
        snap = snapshot or make_snapshot()
        chat_kw = (
            {"side_effect": chat} if isinstance(chat, list)
            else {"return_value": chat or GOOD_PATCH}
        )
        analyzer_kw = (
            {"side_effect": analyzer} if isinstance(analyzer, list)
            else {"return_value": analyzer or CLEAN_ANALYZER}
        )
        if sandbox is _SENTINEL:
            sandbox_kw = {"return_value": SandboxResult(0, "OK")}
        elif isinstance(sandbox, list):
            sandbox_kw = {"side_effect": sandbox}
        else:
            sandbox_kw = {"return_value": sandbox}
        with patch(
            "analysis.agent_service.fetch_github_snapshot", return_value=snap
        ), patch(
            "analysis.agent_service.chat_json", **chat_kw
        ), patch(
            "analysis.agent_service.call_analyzer", **analyzer_kw
        ), patch(
            "analysis.sandbox.run_tests", **sandbox_kw
        ):
            return run_agent(self.issue)

    def test_successful_run_completes_and_persists(self):
        run = self._run_with()
        self.assertEqual(run.status, RunStatus.COMPLETED)
        # Execution completed AND every required check passed: verified.
        self.assertEqual(run.verification_status, VerificationStatus.VERIFIED)
        self.assertIs(run.tests_ran, True)
        self.assertIn("addition", run.explanation)
        self.assertEqual(run.repair_attempts, 1)
        self.assertEqual(run.start_commit_sha, "a" * 40)
        self.assertIn("attempt 1", run.final_summary)

        changes = list(run.code_changes.all())
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].file_path, "app.py")
        self.assertEqual(changes[0].attempt, 1)
        self.assertIn("+    return a + b", changes[0].proposed_diff)

        results = {r.test_name: r.status for r in run.test_results.all()}
        self.assertEqual(results["syntax:app.py"], TestStatus.PASSED)
        self.assertEqual(results["static-analysis"], TestStatus.PASSED)
        self.assertEqual(results["tests"], TestStatus.PASSED)

    def test_original_workspace_not_required_after_run(self):
        # The temp workspace must be gone; nothing may persist on disk.
        run = self._run_with()
        self.assertEqual(run.status, RunStatus.COMPLETED)

    def test_snapshot_failure_fails_run_without_ai_call(self):
        from repositories.services import GitHubRepoNotFound

        with patch(
            "analysis.agent_service.fetch_github_snapshot",
            side_effect=GitHubRepoNotFound("Repository not found or is private."),
        ), patch("analysis.agent_service.chat_json") as mock_chat:
            run = run_agent(self.issue)
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("Snapshot failed", run.final_summary)
        mock_chat.assert_not_called()
        self.assertEqual(run.start_commit_sha, "")

    def test_issue_file_missing_from_snapshot_fails(self):
        snap = make_snapshot({"other.py": "x = 1\n"})
        run = self._run_with(snapshot=snap)
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("not present in the fetched snapshot", run.final_summary)
        # No source context -> no patch, no checks -> nothing to verify.
        self.assertEqual(run.verification_status, VerificationStatus.UNVERIFIED)
        self.assertIsNone(run.tests_ran)

    def test_invalid_ai_patch_gets_one_retry_then_fails(self):
        bad = {"path": "nope.py", "old_code": "x", "new_code": "y",
               "explanation": "bad"}
        run = self._run_with(chat=[bad, bad])
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(run.verification_status, VerificationStatus.UNVERIFIED)
        self.assertEqual(run.repair_attempts, 2)
        self.assertIn("invalid AI patch", run.final_summary)
        self.assertEqual(run.code_changes.count(), 0)
        # Both rejections are recorded, tagged per attempt (history kept).
        rejections = list(run.test_results.filter(test_name="patch"))
        self.assertEqual(len(rejections), 2)
        self.assertEqual({r.attempt for r in rejections}, {1, 2})
        self.assertTrue(all(r.status == TestStatus.ERROR for r in rejections))
        # Bounded forensics: the rejected snippet head is preserved so a
        # whitespace/quote mismatch becomes diagnosable from the UI alone.
        self.assertIn("Proposed old_code (first 300 chars): x",
                      rejections[0].output)

    def test_retry_after_invalid_patch_succeeds(self):
        bad = {"path": "nope.py", "old_code": "x", "new_code": "y",
               "explanation": "bad"}
        run = self._run_with(chat=[bad, GOOD_PATCH])
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.repair_attempts, 2)
        self.assertIn("after one repair retry", run.final_summary)

    def test_ai_transport_error_fails_immediately_without_retry(self):
        with patch(
            "analysis.agent_service.fetch_github_snapshot",
            return_value=make_snapshot(),
        ), patch(
            "analysis.agent_service.chat_json",
            side_effect=AIUnavailable("AI service is unreachable."),
        ) as mock_chat:
            run = run_agent(self.issue)
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("AI request failed", run.final_summary)
        # The fixed, credential-free client message is surfaced verbatim.
        self.assertIn("AI service is unreachable", run.final_summary)
        mock_chat.assert_called_once()

    def test_static_failure_then_clean_retry_completes(self):
        dirty = {
            "summary": "1 issue", "files_analyzed": 1,
            "issues": [{"file_path": "app.py", "line_number": 1,
                        "severity": "low", "source": "ruff",
                        "rule": "E1", "description": "style"}],
        }
        run = self._run_with(analyzer=[dirty, CLEAN_ANALYZER])
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.repair_attempts, 2)
        statuses = [
            (r.test_name, r.status) for r in run.test_results.all()
        ]
        self.assertIn(("static-analysis", TestStatus.FAILED), statuses)
        self.assertIn(("static-analysis", TestStatus.PASSED), statuses)

    def test_static_failure_on_both_attempts_fails_run(self):
        dirty = {
            "summary": "1 issue", "files_analyzed": 1,
            "issues": [{"file_path": "app.py", "line_number": 1,
                        "severity": "low", "source": "ruff",
                        "rule": "E1", "description": "style"}],
        }
        run = self._run_with(analyzer=[dirty, dirty])
        # Execution ran its validation phase -> completed; the failing
        # static check is a verification verdict, not an execution crash.
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.verification_status, VerificationStatus.FAILED)
        self.assertIs(run.tests_ran, True)
        self.assertEqual(run.repair_attempts, 2)
        self.assertIn("static-analysis", run.final_summary)

    def test_analyzer_error_fails_without_retry(self):
        with patch(
            "analysis.agent_service.fetch_github_snapshot",
            return_value=make_snapshot(),
        ), patch(
            "analysis.agent_service.chat_json", return_value=GOOD_PATCH
        ), patch(
            "analysis.agent_service.call_analyzer",
            side_effect=AnalyzerUnavailable("Analyzer is down."),
        ) as mock_call:
            run = run_agent(self.issue)
        # The analyzer died mid-validation: execution completed, but the
        # evidence is incomplete -- partially verified, never verified.
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(
            run.verification_status, VerificationStatus.PARTIALLY_VERIFIED
        )
        self.assertEqual(run.repair_attempts, 1)
        mock_call.assert_called_once()
        results = {r.test_name: r.status for r in run.test_results.all()}
        self.assertEqual(results["static-analysis"], TestStatus.ERROR)

    def test_syntax_broken_patch_fails_and_retries(self):
        broken = dict(GOOD_PATCH, new_code="def add(a, b:\n    return 0\n")
        run = self._run_with(chat=[broken, broken])
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.verification_status, VerificationStatus.FAILED)
        self.assertEqual(run.repair_attempts, 2)
        results = {r.test_name: r.status for r in run.test_results.all()}
        self.assertEqual(results["syntax:app.py"], TestStatus.FAILED)

    def test_sandbox_unavailable_records_skipped_and_completes(self):
        run = self._run_with(sandbox=None)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        # Skipped tests can never produce "verified": only partially.
        self.assertEqual(
            run.verification_status, VerificationStatus.PARTIALLY_VERIFIED
        )
        self.assertIs(run.tests_ran, False)
        tests_row = run.test_results.get(test_name="tests")
        self.assertEqual(tests_row.status, TestStatus.SKIPPED)
        self.assertIn("NOT run", tests_row.output)
        self.assertIn("were not executed", run.final_summary.lower())

    def test_failing_tests_then_passing_retry(self):
        first = SandboxResult(1, "FAILED (test_add)")
        second = SandboxResult(0, "OK")
        run = self._run_with(sandbox=[first, second])
        self.assertEqual(run.status, RunStatus.COMPLETED)
        # The failed first attempt does not taint the final verdict.
        self.assertEqual(run.verification_status, VerificationStatus.VERIFIED)
        self.assertIs(run.tests_ran, True)
        self.assertEqual(run.repair_attempts, 2)
        rows = [
            (r.attempt, r.status) for r in
            run.test_results.filter(test_name="tests")
        ]
        self.assertIn((1, TestStatus.FAILED), rows)
        self.assertIn((2, TestStatus.PASSED), rows)

    def test_workspace_is_deleted_after_run(self):
        real_mkdtemp = _tempfile.mkdtemp
        created = []

        def tracking_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            created.append(path)
            return path

        with patch("analysis.agent_service.tempfile.mkdtemp",
                   side_effect=tracking_mkdtemp):
            run = self._run_with()
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(len(created), 1)
        self.assertFalse(_os.path.exists(created[0]))

    def test_no_git_commands_anywhere_in_sandbox_layer(self):
        for command in _sandbox_module.ALLOWED_COMMANDS:
            self.assertNotIn("git", command)
            self.assertEqual(tuple(command[:3]), ("python", "-m", "unittest"))


class AgentRunAPITests(AgentFixtureMixin, APITestCase):
    """Endpoints: authentication, ownership, status codes, shapes."""

    def _finished_run(self, run_status=RunStatus.COMPLETED):
        return AgentRun.objects.create(
            analysis_run=self.analysis_run,
            issue=self.issue,
            status=run_status,
            repair_attempts=1,
            start_commit_sha="a" * 40,
            final_summary="Fix validated on attempt 1.",
        )

    def test_create_requires_authentication(self):
        response = self.client.post(agent_run_url_for_issue(self.issue.pk))
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_detail_change_test_endpoints_require_authentication(self):
        run = self._finished_run()
        for url in (agent_detail_url(run.pk), agent_changes_url(run.pk),
                    agent_tests_url(run.pk)):
            response = self.client.get(url)
            self.assertIn(
                response.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
            )

    def test_create_agent_run_success(self):
        self.client.force_authenticate(user=self.user)
        finished = self._finished_run()
        with patch("analysis.agent_service.run_agent",
                   return_value=finished) as mock_run:
            response = self.client.post(agent_run_url_for_issue(self.issue.pk))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_run.assert_called_once()
        data = response.json()
        self.assertEqual(data["status"], RunStatus.COMPLETED)
        self.assertEqual(data["repair_attempts"], 1)
        self.assertEqual(data["start_commit_sha"], "a" * 40)
        self.assertIn("checks", data)

    def test_create_agent_run_failed_returns_200_with_record(self):
        self.client.force_authenticate(user=self.user)
        failed = self._finished_run(run_status=RunStatus.FAILED)
        failed.final_summary = "Validation failed after 2 attempt(s)."
        failed.save()
        with patch("analysis.agent_service.run_agent",
                   return_value=failed):
            response = self.client.post(agent_run_url_for_issue(self.issue.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], RunStatus.FAILED)

    def test_create_agent_run_foreign_issue_404(self):
        self.client.force_authenticate(user=self.other)
        response = self.client.post(agent_run_url_for_issue(self.issue.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_agent_run_invalid_id_404(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(agent_run_url_for_issue(999999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_own_run_200(self):
        run = self._finished_run()
        self.client.force_authenticate(user=self.user)
        response = self.client.get(agent_detail_url(run.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["id"], run.pk)
        self.assertEqual(data["checks"]["passed"], 0)

    def test_detail_foreign_run_404(self):
        run = self._finished_run()
        self.client.force_authenticate(user=self.other)
        response = self.client.get(agent_detail_url(run.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_invalid_id_404(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(agent_detail_url(999999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_changes_endpoint_lists_diffs(self):
        run = self._finished_run()
        CodeChange.objects.create(
            agent_run=run, file_path="app.py",
            proposed_diff=make_diff("app.py", BUGGY_SOURCE, FIXED_SOURCE),
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.get(agent_changes_url(run.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["file_path"], "app.py")
        self.assertIn("+    return a + b", data[0]["proposed_diff"])

    def test_changes_endpoint_foreign_run_404(self):
        run = self._finished_run()
        self.client.force_authenticate(user=self.other)
        response = self.client.get(agent_changes_url(run.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_tests_endpoint_lists_results_with_attempt(self):
        run = self._finished_run()
        TestResult.objects.create(
            agent_run=run, test_name="syntax:app.py",
            status=TestStatus.PASSED, attempt=1,
        )
        TestResult.objects.create(
            agent_run=run, test_name="tests", status=TestStatus.SKIPPED,
            attempt=1, output="Sandbox execution not available.",
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.get(agent_tests_url(run.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual({row["attempt"] for row in data}, {1})

    def test_tests_endpoint_foreign_run_404(self):
        run = self._finished_run()
        self.client.force_authenticate(user=self.other)
        response = self.client.get(agent_tests_url(run.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# Phase 8 -- Agent evaluation suite (deterministic, mocked, zero credits)
# ---------------------------------------------------------------------------

from .evaluation import EVAL_CASES, evaluate_case  # noqa: E402


class Phase8ReliabilityTests(AgentFixtureMixin, TestCase):
    """Direct regression tests for the Phase 8 reliability fixes."""

    def _run_with(self, snapshot=None, chat=None, analyzer=None,
                  sandbox=_SENTINEL):
        snap = snapshot or make_snapshot()
        chat_kw = (
            {"side_effect": chat} if isinstance(chat, list)
            else {"return_value": chat or GOOD_PATCH}
        )
        analyzer_kw = (
            {"side_effect": analyzer} if isinstance(analyzer, list)
            else {"return_value": analyzer or CLEAN_ANALYZER}
        )
        if sandbox is _SENTINEL:
            sandbox_kw = {"return_value": SandboxResult(0, "OK")}
        elif isinstance(sandbox, list):
            sandbox_kw = {"side_effect": sandbox}
        else:
            sandbox_kw = {"return_value": sandbox}
        with patch(
            "analysis.agent_service.fetch_github_snapshot", return_value=snap
        ), patch(
            "analysis.agent_service.chat_json", **chat_kw
        ), patch(
            "analysis.agent_service.call_analyzer", **analyzer_kw
        ), patch(
            "analysis.sandbox.run_tests", **sandbox_kw
        ):
            return run_agent(self.issue)

    # -- attempt history must be PRESERVED (STEP F) --------------------------

    def test_two_attempt_run_preserves_both_attempts_changes(self):
        """Both attempts' diffs stay on record, tagged by attempt.

        The final verdict only concerns the last attempt, but the earlier
        patch must remain inspectable -- a later success never erases an
        earlier failure.
        """
        first = SandboxResult(1, "FAILED attempt 1")
        second = SandboxResult(0, "OK")
        run = self._run_with(sandbox=[first, second])
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.repair_attempts, 2)
        changes = list(run.code_changes.all())
        self.assertEqual(
            len(changes), 2,
            f"expected 2 CodeChanges (one per attempt), got {len(changes)}",
        )
        self.assertEqual({c.attempt for c in changes}, {1, 2})
        self.assertTrue(all(c.file_path == "app.py" for c in changes))
        # The verdict reflects only the final attempt's evidence.
        self.assertEqual(
            run.verification_status, VerificationStatus.VERIFIED
        )

    # -- Bug 2: analyzer tool crash must NOT become a false pass ------------

    def test_analyzer_tool_error_is_error_not_pass(self):
        """A TOOL_ERROR finding (linter crashed) records ERROR, not PASSED."""
        tool_crash = {
            "summary": "tool failed", "files_analyzed": 1,
            "issues": [{"file_path": ".", "line_number": 1,
                        "severity": "medium", "source": "ruff",
                        "rule": "TOOL_ERROR",
                        "description": "Ruff could not be executed."}],
        }
        run = self._run_with(analyzer=[tool_crash, tool_crash])
        # Execution completed; the crashed linter leaves the evidence
        # incomplete -> partially verified, NEVER verified.
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(
            run.verification_status, VerificationStatus.PARTIALLY_VERIFIED
        )
        results = {r.test_name: r.status for r in run.test_results.all()}
        self.assertEqual(results["static-analysis"], TestStatus.ERROR)
        # A dead linter is not retryable -- no wasted repair attempt.
        self.assertEqual(run.repair_attempts, 1)

    def test_tool_timeout_findings_also_error(self):
        """TOOL_TIMEOUT findings are likewise never counted as a pass."""
        tool_timeout = {
            "summary": "timeout", "files_analyzed": 1,
            "issues": [{"file_path": ".", "line_number": 1,
                        "severity": "medium", "source": "bandit",
                        "rule": "TOOL_TIMEOUT",
                        "description": "Bandit timed out."}],
        }
        run = self._run_with(analyzer=[tool_timeout, tool_timeout])
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(
            run.verification_status, VerificationStatus.PARTIALLY_VERIFIED
        )
        results = {r.test_name: r.status for r in run.test_results.all()}
        self.assertEqual(results["static-analysis"], TestStatus.ERROR)

    # -- Bug 3: AGENT_MAX_REPAIR_ATTEMPTS is honored ------------------------

    def test_max_repair_attempts_setting_is_read(self):
        """The configured attempt count (not a hardcode) bounds the loop."""
        from .agent_service import _max_attempts

        with override_settings(AGENT_MAX_REPAIR_ATTEMPTS=1):
            self.assertEqual(_max_attempts(), 1)
        with override_settings(AGENT_MAX_REPAIR_ATTEMPTS=2):
            self.assertEqual(_max_attempts(), 2)

    def test_max_repair_attempts_is_clamped(self):
        """Out-of-range config values are clamped, never trusted blindly.

        The workflow allows at most ONE additional repair attempt, so the
        ceiling is 2 no matter what the environment claims.
        """
        from .agent_service import _max_attempts

        with override_settings(AGENT_MAX_REPAIR_ATTEMPTS=0):
            self.assertEqual(_max_attempts(), 1)   # floor
        with override_settings(AGENT_MAX_REPAIR_ATTEMPTS=99):
            self.assertEqual(_max_attempts(), 2)   # ceiling

    def test_single_attempt_setting_stops_after_one(self):
        """With attempts=1, a failing run makes exactly one attempt."""
        with override_settings(AGENT_MAX_REPAIR_ATTEMPTS=1):
            run = self._run_with(sandbox=SandboxResult(1, "FAILED"))
        # Execution ran to its verdict; the failing check is a
        # verification failure, not an execution crash.
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.verification_status, VerificationStatus.FAILED)
        self.assertEqual(run.repair_attempts, 1)

    # -- Bug 4: no overclaiming "validated" when tests are skipped ----------

    def test_skipped_tests_do_not_claim_validated(self):
        """A completed run with skipped tests is partially verified only."""
        run = self._run_with(sandbox=None)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertNotIn("validated by tests", run.final_summary)
        self.assertIn("were not executed", run.final_summary.lower())
        self.assertEqual(
            run.verification_status, VerificationStatus.PARTIALLY_VERIFIED
        )
        self.assertIs(run.tests_ran, False)

    def test_tests_run_summary_claims_validated_by_tests(self):
        """When tests genuinely ran and passed, 'validated by tests' is OK."""
        run = self._run_with(sandbox=SandboxResult(0, "OK"))
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertIn("validated by tests", run.final_summary)
        self.assertEqual(run.verification_status, VerificationStatus.VERIFIED)
        self.assertIs(run.tests_ran, True)

    # -- Bug 5: static-check helper returns a uniform 3-tuple ---------------

    def test_static_check_returns_three_tuple_when_no_changes(self):
        """The no-changed-files path returns 3 values like every other path."""
        from .agent_service import _run_static_checks

        with patch("analysis.agent_service._record"):
            result = _run_static_checks(self.issue, make_snapshot(), [], 1)
        self.assertEqual(len(result), 3)
        passed, summary, retryable = result
        self.assertTrue(passed)
        self.assertEqual(summary, "")
        self.assertTrue(retryable)


class AgentDuplicateRequestTests(AgentFixtureMixin, APITestCase):
    """A second submit while a run is in flight must not start a second run."""

    def test_second_submit_while_running_returns_409(self):
        AgentRun.objects.create(
            analysis_run=self.analysis_run,
            issue=self.issue,
            status=RunStatus.RUNNING,
        )
        self.client.force_authenticate(user=self.user)
        with patch("analysis.agent_service.run_agent") as mock_run:
            response = self.client.post(agent_run_url_for_issue(self.issue.pk))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        mock_run.assert_not_called()   # no second Nebius-bound run started

    def test_submit_after_previous_run_finished_is_allowed(self):
        AgentRun.objects.create(
            analysis_run=self.analysis_run,
            issue=self.issue,
            status=RunStatus.COMPLETED,
        )
        self.client.force_authenticate(user=self.user)
        finished = AgentRun.objects.create(
            analysis_run=self.analysis_run, issue=self.issue,
            status=RunStatus.COMPLETED,
        )
        with patch("analysis.agent_service.run_agent",
                   return_value=finished) as mock_run:
            response = self.client.post(agent_run_url_for_issue(self.issue.pk))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_run.assert_called_once()

    def test_in_flight_run_of_another_issue_does_not_block(self):
        """The guard is per-issue: a different issue is not blocked."""
        other_issue = Issue.objects.create(
            analysis_run=self.analysis_run,
            file_path="other.py", line_number=1, severity=Severity.LOW,
            description="x",
        )
        AgentRun.objects.create(
            analysis_run=self.analysis_run,
            issue=self.issue,           # a DIFFERENT issue is in flight
            status=RunStatus.RUNNING,
        )
        self.client.force_authenticate(user=self.user)
        finished = AgentRun.objects.create(
            analysis_run=self.analysis_run, issue=other_issue,
            status=RunStatus.COMPLETED,
        )
        with patch("analysis.agent_service.run_agent",
                   return_value=finished) as mock_run:
            response = self.client.post(agent_run_url_for_issue(other_issue.pk))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_run.assert_called_once()


class AgentEvaluationTests(TestCase):
    """Drive every fixture scenario through the real run_agent.

    Each case records the outcomes the spec requires (initial test result,
    patch generated/applied, checks executed, final test result, repair
    attempts, final status) and -- where a real bug is fixable -- runs a
    trusted behavioral regression check so a patch only counts as a FIX when
    the expected behavior is actually validated, never merely because it
    parsed or applied.
    """

    def _eval(self, name):
        return evaluate_case(EVAL_CASES[name])

    # -- scenarios that must genuinely FIX and VERIFY behavior -------------

    def test_logic_bug_fixed_and_verified(self):
        out = self._eval("logic_bug")
        self.assertEqual(out.final_status, RunStatus.COMPLETED)
        self.assertEqual(out.repair_attempts, 1)
        self.assertTrue(out.patch_applied)
        self.assertTrue(out.behavior_verified, out.regression_output)
        self.assertEqual(out.final_test_result, TestStatus.PASSED)

    def test_division_by_zero_fixed_and_verified(self):
        out = self._eval("division_by_zero")
        self.assertEqual(out.final_status, RunStatus.COMPLETED)
        self.assertTrue(out.patch_applied)
        self.assertTrue(out.behavior_verified, out.regression_output)

    def test_syntax_error_repaired(self):
        out = self._eval("syntax_error")
        self.assertEqual(out.final_status, RunStatus.COMPLETED)
        self.assertTrue(out.patch_applied)
        self.assertTrue(out.behavior_verified, out.regression_output)

    def test_successful_patch_passes_regression(self):
        out = self._eval("successful_patch")
        self.assertEqual(out.final_status, RunStatus.COMPLETED)
        self.assertTrue(out.patch_applied)
        # The palindrome fix must handle spaces, punctuation and case.
        self.assertTrue(out.behavior_verified, out.regression_output)
        self.assertIn("syntax", out.checks_executed)
        self.assertIn("static-analysis", out.checks_executed)

    # -- scenarios that must FAIL honestly ----------------------------------

    def test_invalid_ai_patch_fails_after_retry(self):
        out = self._eval("invalid_ai_patch")
        self.assertEqual(out.final_status, RunStatus.FAILED)
        self.assertEqual(out.repair_attempts, 2)
        self.assertFalse(out.patch_applied)
        self.assertFalse(out.behavior_verified)

    def test_patch_that_does_not_apply_fails(self):
        out = self._eval("patch_does_not_apply")
        self.assertEqual(out.final_status, RunStatus.FAILED)
        self.assertFalse(out.patch_applied)

    def test_repair_still_fails_reports_failed(self):
        out = self._eval("repair_still_fails")
        # Execution completed its validation phase; the FIX failed
        # verification -- exactly the separation the spec demands.
        self.assertEqual(out.final_status, RunStatus.COMPLETED)
        self.assertEqual(out.verification_status,
                         VerificationStatus.FAILED)
        self.assertIs(out.tests_ran, True)
        self.assertEqual(out.repair_attempts, 2)
        self.assertEqual(out.final_test_result, TestStatus.FAILED)

    def test_sandbox_timeout_is_error_not_pass(self):
        out = self._eval("sandbox_timeout")
        self.assertEqual(out.final_status, RunStatus.COMPLETED)
        self.assertEqual(out.final_test_result, TestStatus.ERROR)
        # A timed-out suite is incomplete evidence: partially verified.
        self.assertEqual(out.verification_status,
                         VerificationStatus.PARTIALLY_VERIFIED)

    # -- Docker unavailable: skipped, never passed --------------------------

    def test_docker_unavailable_records_skipped_not_passed(self):
        out = self._eval("docker_unavailable")
        # Static checks pass, so the run completes; tests are SKIPPED.
        self.assertEqual(out.final_status, RunStatus.COMPLETED)
        self.assertEqual(out.final_test_result, TestStatus.SKIPPED)
        # Behavioral verification still happens via the trusted check.
        self.assertTrue(out.behavior_verified, out.regression_output)
        # And the summary must NOT claim tests validated the fix.
        self.assertNotIn("validated by tests", out.final_summary)
        self.assertIn("were not executed", out.final_summary.lower())
        self.assertEqual(out.verification_status,
                         VerificationStatus.PARTIALLY_VERIFIED)
        self.assertIs(out.tests_ran, False)

    # -- failing-then-passing suite ----------------------------------------

    def test_failing_test_suite_then_passing_retry(self):
        out = self._eval("failing_test_suite")
        self.assertEqual(out.final_status, RunStatus.COMPLETED)
        self.assertEqual(out.repair_attempts, 2)
        self.assertEqual(out.final_test_result, TestStatus.PASSED)
        self.assertTrue(out.behavior_verified, out.regression_output)

    # -- structural guarantees ---------------------------------------------

    def test_all_ten_required_scenarios_present(self):
        """The suite covers every category the spec requires."""
        required = {
            "logic_bug", "division_by_zero", "syntax_error",
            "invalid_ai_patch", "patch_does_not_apply", "failing_test_suite",
            "repair_still_fails", "sandbox_timeout", "docker_unavailable",
            "successful_patch",
        }
        self.assertEqual(required, set(EVAL_CASES))

    def test_every_case_records_required_outcomes(self):
        """Each scenario populates the outcome fields the spec asks for."""
        for name in sorted(EVAL_CASES):
            with self.subTest(case=name):
                out = self._eval(name)
                self.assertIn(
                    out.final_status, (RunStatus.COMPLETED, RunStatus.FAILED)
                )
                self.assertGreaterEqual(out.repair_attempts, 1)
                self.assertIsInstance(out.patch_generated, bool)
                self.assertIsInstance(out.patch_applied, bool)
                if out.patch_applied:
                    # Checks only run once a patch is actually in the
                    # workspace; an invalid/non-applying patch records none.
                    self.assertTrue(out.checks_executed)
                self.assertTrue(out.final_summary)

    def test_no_case_claims_fix_without_behavioral_verification(self):
        """A completed run with a real bug must be behavior-verified.

        Lint/syntax success alone is never treated as proof the bug is fixed.
        """
        for name in ("logic_bug", "division_by_zero", "successful_patch"):
            with self.subTest(case=name):
                out = self._eval(name)
                self.assertEqual(out.final_status, RunStatus.COMPLETED)
                self.assertTrue(
                    out.behavior_verified,
                    f"{name}: completed without behavioral verification",
                )


# ---------------------------------------------------------------------------
# Phase 9: public (anonymous) scanning -- new scan endpoint + visibility
# ---------------------------------------------------------------------------


class PublicScanAPITests(APITestCase):
    """POST /api/analysis/scans/ and the public/private visibility rules."""

    SCAN_URL = "/api/analysis/scans/"

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # throttle counters are shared state (LocMemCache)
        self.alice = User.objects.create_user(
            username="scan-alice", password="Str0ng-Pass!x1"
        )
        self.bob = User.objects.create_user(
            username="scan-bob", password="Str0ng-Pass!x2"
        )

    # -- helpers -----------------------------------------------------------

    def _snapshot(self, files=None):
        from .snapshot import Snapshot

        return Snapshot(
            owner="octo",
            repo="hello",
            commit_sha="a" * 40,
            files=files if files is not None else {"app.py": "print(1)\n"},
        )

    def _analyzer_response(self, findings=None, analyzed=1):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "findings": findings or [],
            "meta": {"files_analyzed": analyzed},
        }
        return resp

    def _post_scan(self, url="https://github.com/octo/hello"):
        return self.client.post(
            self.SCAN_URL, {"github_url": url}, format="json"
        )

    # -- anonymous scan lifecycle -----------------------------------------

    @patch("analysis.services.requests.post")
    @patch("analysis.views.fetch_github_snapshot")
    def test_anonymous_scan_completes_and_is_public(self, mock_snap, mock_post):
        mock_snap.return_value = self._snapshot()
        mock_post.return_value = self._analyzer_response()

        resp = self._post_scan()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        run = AnalysisRun.objects.get(pk=resp.data["run"]["id"])
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.files_analyzed, 1)
        self.assertIsNone(run.repository.owner)  # public scan
        self.assertEqual(
            run.repository.github_url, "https://github.com/octo/hello"
        )

        # The run is readable without any authentication.
        detail = self.client.get(run_detail_url(run.pk))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["status"], RunStatus.COMPLETED)

    @patch("analysis.views.fetch_github_snapshot")
    def test_nonexistent_repo_is_404_and_leaves_no_junk(self, mock_snap):
        mock_snap.side_effect = GitHubRepoNotFound(
            "Repository not found or is private."
        )

        resp = self._post_scan("https://github.com/octo/nope")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Repository.objects.count(), 0)

    @patch("analysis.views.fetch_github_snapshot")
    def test_snapshot_limit_error_is_400_with_safe_message(self, mock_snap):
        from .snapshot import SnapshotError

        mock_snap.side_effect = SnapshotError(
            "No analyzable Python files were found in the repository snapshot."
        )

        resp = self._post_scan()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("No analyzable Python files", resp.data["detail"])

    @patch("analysis.services.requests.post")
    @patch("analysis.views.fetch_github_snapshot")
    def test_analyzer_down_yields_502_with_failed_run(self, mock_snap, mock_post):
        mock_snap.return_value = self._snapshot()
        mock_post.side_effect = http_requests.ConnectionError()

        resp = self._post_scan()
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        run = AnalysisRun.objects.get(pk=resp.data["run"]["id"])
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIsNone(run.files_analyzed)  # unknown, never a fake 0

    def test_invalid_url_rejected_before_any_network(self):
        resp = self._post_scan("https://evil.example.com/o/r")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("github.com", str(resp.data))

    # -- authenticated scans ----------------------------------------------

    @patch("analysis.services.requests.post")
    @patch("analysis.views.fetch_github_snapshot")
    def test_authenticated_scan_is_owned_and_deduplicated(self, mock_snap, mock_post):
        mock_snap.return_value = self._snapshot()
        mock_post.return_value = self._analyzer_response()
        self.client.force_authenticate(user=self.alice)

        first = self._post_scan()
        second = self._post_scan()
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            first.data["repository"]["id"], second.data["repository"]["id"]
        )
        repo = Repository.objects.get(pk=first.data["repository"]["id"])
        self.assertEqual(repo.owner, self.alice)

    # -- visibility rules --------------------------------------------------

    def _owned_run(self):
        repo = Repository.objects.create(
            owner=self.alice,
            github_url="https://github.com/alice/private-repo",
            name="private-repo",
        )
        return AnalysisRun.objects.create(
            repository=repo, status=RunStatus.COMPLETED
        )

    def test_private_run_is_404_for_everyone_but_owner(self):
        run = self._owned_run()

        self.assertEqual(  # anonymous
            self.client.get(run_detail_url(run.pk)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.client.force_authenticate(user=self.bob)
        self.assertEqual(
            self.client.get(run_detail_url(run.pk)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.client.force_authenticate(user=self.alice)
        self.assertEqual(
            self.client.get(run_detail_url(run.pk)).status_code,
            status.HTTP_200_OK,
        )

    @patch("analysis.services.requests.post")
    @patch("analysis.views.fetch_github_snapshot")
    def test_public_run_and_issue_endpoints_open_to_anonymous(self, mock_snap, mock_post):
        mock_snap.return_value = self._snapshot(
            files={"app.py": "import os\nos.system(1)\n"}
        )
        mock_post.return_value = self._analyzer_response(
            findings=[
                {
                    "file_path": "app.py",
                    "line_number": 2,
                    "severity": "high",
                    "rule": "B605",
                    "source": "bandit",
                    "description": "Start a process with a shell.",
                }
            ],
            analyzed=1,
        )
        resp = self._post_scan()
        run_id = resp.data["run"]["id"]
        issue_id = resp.data["run"]["issues"][0]["id"]

        self.assertEqual(  # run issues list
            self.client.get(f"/api/analysis/runs/{run_id}/issues/").status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(  # single issue (deep-link target)
            self.client.get(f"/api/analysis/issues/{issue_id}/").status_code,
            status.HTTP_200_OK,
        )

    def test_private_issue_is_404_for_non_owner(self):
        run = self._owned_run()
        issue = Issue.objects.create(
            analysis_run=run,
            file_path="app.py",
            line_number=1,
            severity=Severity.LOW,
            description="x",
        )

        self.assertEqual(
            self.client.get(f"/api/analysis/issues/{issue.pk}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.client.force_authenticate(user=self.bob)
        self.assertEqual(
            self.client.get(f"/api/analysis/issues/{issue.pk}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    # -- authentication gate for fixes ------------------------------------

    def _public_issue(self):
        repo = Repository.objects.create(
            owner=None,
            github_url="https://github.com/octo/public-repo",
            name="public-repo",
        )
        run = AnalysisRun.objects.create(
            repository=repo, status=RunStatus.COMPLETED
        )
        return Issue.objects.create(
            analysis_run=run,
            file_path="app.py",
            line_number=3,
            severity=Severity.MEDIUM,
            description="finding",
        )

    def test_agent_run_requires_authentication_even_for_public_issues(self):
        issue = self._public_issue()
        resp = self.client.post(f"/api/analysis/issues/{issue.pk}/agent-run/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @patch("analysis.agent_service.run_agent")
    def test_signed_in_user_can_repair_a_public_scan(self, mock_agent):
        issue = self._public_issue()
        mock_agent.return_value = AgentRun.objects.create(
            analysis_run=issue.analysis_run,
            issue=issue,
            status=RunStatus.COMPLETED,
            final_summary="Fix validated by tests on attempt 1.",
        )
        self.client.force_authenticate(user=self.bob)  # not the scan owner

        resp = self.client.post(f"/api/analysis/issues/{issue.pk}/agent-run/")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("validated", resp.data["final_summary"])

    def test_agent_run_stays_404_on_other_users_private_issues(self):
        run = self._owned_run()
        issue = Issue.objects.create(
            analysis_run=run,
            file_path="app.py",
            line_number=1,
            severity=Severity.LOW,
            description="x",
        )
        self.client.force_authenticate(user=self.bob)

        resp = self.client.post(f"/api/analysis/issues/{issue.pk}/agent-run/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # -- rate limiting ------------------------------------------------------

    @patch("analysis.views.ScanAnonThrottle.THROTTLE_RATES", {"scan_anon": "2/hour"})
    @patch("analysis.services.requests.post")
    @patch("analysis.views.fetch_github_snapshot")
    def test_anonymous_scans_are_rate_limited_per_ip(self, mock_snap, mock_post):
        mock_snap.return_value = self._snapshot()
        mock_post.return_value = self._analyzer_response()

        codes = [self._post_scan().status_code for _ in range(3)]
        self.assertEqual(
            codes,
            [
                status.HTTP_201_CREATED,
                status.HTTP_201_CREATED,
                status.HTTP_429_TOO_MANY_REQUESTS,
            ],
        )


# ---------------------------------------------------------------------------
# Reliable AI bug-fixing workflow -- acceptance criteria (spec 2/4/F/G)
# ---------------------------------------------------------------------------


class AgentOwnershipAPITests(AgentFixtureMixin, APITestCase):
    """Agent runs belong to the user who launched them (spec 2, 7.16)."""

    def _public_issue(self):
        public_repo = Repository.objects.create(
            owner=None,
            github_url="https://github.com/octo/public-repo",
            name="public-repo",
        )
        run = AnalysisRun.objects.create(repository=public_repo)
        return Issue.objects.create(
            analysis_run=run, file_path="app.py", line_number=2,
            severity=Severity.LOW, description="x",
        )

    def test_create_records_launching_user(self):
        self.client.force_authenticate(user=self.user)
        finished = AgentRun.objects.create(
            analysis_run=self.analysis_run, issue=self.issue,
            user=self.user, status=RunStatus.COMPLETED,
        )
        with patch("analysis.agent_service.run_agent",
                   return_value=finished) as mock_run:
            response = self.client.post(
                agent_run_url_for_issue(self.issue.pk)
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        args, kwargs = mock_run.call_args
        self.assertEqual(kwargs["user"], self.user)
        # The pre-created (queued) run carries the owner from the start.
        queued = AgentRun.objects.get(
            issue=self.issue, status=RunStatus.PENDING
        )
        self.assertEqual(queued.user, self.user)

    def test_other_user_cannot_read_my_run_on_public_scan(self):
        """Public scan access never grants access to another user's runs."""
        issue = self._public_issue()
        run = AgentRun.objects.create(
            analysis_run=issue.analysis_run, issue=issue,
            user=self.user, status=RunStatus.COMPLETED,
        )
        CodeChange.objects.create(
            agent_run=run, file_path="app.py",
            proposed_diff=make_diff("app.py", BUGGY_SOURCE, FIXED_SOURCE),
        )
        self.client.force_authenticate(user=self.user)
        self.assertEqual(
            self.client.get(agent_detail_url(run.pk)).status_code,
            status.HTTP_200_OK,
        )
        self.client.force_authenticate(user=self.other)
        for url in (agent_detail_url(run.pk),
                    agent_changes_url(run.pk), agent_tests_url(run.pk)):
            self.assertEqual(
                self.client.get(url).status_code,
                status.HTTP_404_NOT_FOUND,
            )

    def test_anonymous_cannot_read_agent_run_data(self):
        issue = self._public_issue()
        run = AgentRun.objects.create(
            analysis_run=issue.analysis_run, issue=issue,
            user=self.user, status=RunStatus.COMPLETED,
        )
        response = self.client.get(agent_detail_url(run.pk))
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_legacy_userless_run_on_public_scan_stays_readable(self):
        """Rows created before the user field keep their old visibility."""
        issue = self._public_issue()
        run = AgentRun.objects.create(
            analysis_run=issue.analysis_run, issue=issue,
            status=RunStatus.COMPLETED,   # no user: legacy row
        )
        self.client.force_authenticate(user=self.other)
        self.assertEqual(
            self.client.get(agent_detail_url(run.pk)).status_code,
            status.HTTP_200_OK,
        )


class AgentConcurrencyGuardTests(AgentFixtureMixin, APITestCase):
    """Duplicate/concurrent submissions never launch duplicate runs (7.15)."""

    def test_stale_pending_run_stops_blocking(self):
        """A crashed worker's leftover PENDING run expires after the window."""
        from .views import AGENT_RUN_STALE_AFTER

        stale = AgentRun.objects.create(
            analysis_run=self.analysis_run, issue=self.issue,
            status=RunStatus.PENDING,
        )
        AgentRun.objects.filter(pk=stale.pk).update(
            updated_at=timezone.now() - AGENT_RUN_STALE_AFTER
            - timedelta(minutes=1)
        )
        self.client.force_authenticate(user=self.user)
        finished = AgentRun.objects.create(
            analysis_run=self.analysis_run, issue=self.issue,
            status=RunStatus.COMPLETED,
        )
        with patch("analysis.agent_service.run_agent",
                   return_value=finished) as mock_run:
            response = self.client.post(
                agent_run_url_for_issue(self.issue.pk)
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_run.assert_called_once()

    def test_guard_creates_queued_run_owned_by_user(self):
        """The queued row exists (with owner) before execution starts."""
        self.client.force_authenticate(user=self.user)
        finished = AgentRun.objects.create(
            analysis_run=self.analysis_run, issue=self.issue,
            user=self.user, status=RunStatus.COMPLETED,
        )
        observed = {}

        def fake_run(issue, user=None, agent_run=None):
            observed["run"] = agent_run
            observed["user"] = user
            return finished

        with patch("analysis.agent_service.run_agent",
                   side_effect=fake_run):
            response = self.client.post(
                agent_run_url_for_issue(self.issue.pk)
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(observed["user"], self.user)
        self.assertIsNotNone(observed["run"])
        self.assertEqual(observed["run"].status, RunStatus.PENDING)
        self.assertEqual(observed["run"].user, self.user)


@override_settings(AGENT_TEST_EXECUTION=False)
class AgentVerificationMappingTests(AgentFixtureMixin, TestCase):
    """Spec 4: the verdict is mapped from checks that ACTUALLY ran."""

    def _run_with(self, snapshot=None, chat=None, analyzer=None,
                  sandbox=_SENTINEL):
        snap = snapshot or make_snapshot()
        chat_kw = (
            {"side_effect": chat} if isinstance(chat, list)
            else {"return_value": chat or GOOD_PATCH}
        )
        analyzer_kw = (
            {"side_effect": analyzer} if isinstance(analyzer, list)
            else {"return_value": analyzer or CLEAN_ANALYZER}
        )
        if sandbox is _SENTINEL:
            sandbox_kw = {"return_value": SandboxResult(0, "OK")}
        elif isinstance(sandbox, list):
            sandbox_kw = {"side_effect": sandbox}
        else:
            sandbox_kw = {"return_value": sandbox}
        with patch(
            "analysis.agent_service.fetch_github_snapshot", return_value=snap
        ), patch(
            "analysis.agent_service.chat_json", **chat_kw
        ), patch(
            "analysis.agent_service.call_analyzer", **analyzer_kw
        ), patch(
            "analysis.sandbox.run_tests", **sandbox_kw
        ):
            return run_agent(self.issue, user=self.user)

    def test_spec_example_static_ok_tests_skipped_is_partial(self):
        """Execution completed / verification partially verified (spec 4)."""
        run = self._run_with(sandbox=None)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(
            run.verification_status, VerificationStatus.PARTIALLY_VERIFIED
        )
        self.assertIs(run.tests_ran, False)
        results = {r.test_name: r.status for r in run.test_results.all()}
        self.assertEqual(results["syntax:app.py"], TestStatus.PASSED)
        self.assertEqual(results["static-analysis"], TestStatus.PASSED)
        self.assertEqual(results["tests"], TestStatus.SKIPPED)

    def test_zero_discovered_tests_never_counts_as_passed(self):
        """'Ran 0 tests' with exit 0 is skipped, not passed (spec E)."""
        empty = SandboxResult(0, "Ran 0 tests in 0.000s\n\nOK")
        run = self._run_with(sandbox=empty)
        tests_row = run.test_results.get(test_name="tests")
        self.assertEqual(tests_row.status, TestStatus.SKIPPED)
        self.assertIs(run.tests_ran, False)
        self.assertEqual(
            run.verification_status, VerificationStatus.PARTIALLY_VERIFIED
        )

    def test_python312_exit5_no_tests_is_skipped_not_failed(self):
        """Python 3.12 unittest exits 5 + 'NO TESTS RAN' on empty discovery.

        Verified live in python:3.12-slim: exit code 5. This must be an
        honest skip (repo has no tests), never a failing verdict -- the
        fix itself passed its syntax and static checks.
        """
        empty = SandboxResult(5, "Ran 0 tests in 0.000s\n\nNO TESTS RAN")
        run = self._run_with(sandbox=empty)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        tests_row = run.test_results.get(test_name="tests")
        self.assertEqual(tests_row.status, TestStatus.SKIPPED)
        self.assertIn("0 tests", tests_row.output)
        self.assertEqual(
            run.verification_status, VerificationStatus.PARTIALLY_VERIFIED
        )
        self.assertIs(run.tests_ran, False)

    def test_real_failing_suite_still_counts_as_failed(self):
        """Exit 5 skip logic must not swallow genuine suite failures."""
        run = self._run_with(
            sandbox=SandboxResult(1, "FAILED (failures=1)\nRan 3 tests")
        )
        rows = run.test_results.filter(test_name="tests")
        # The failing suite triggers the one allowed retry, so both
        # attempts ran and both recorded an honest failure.
        self.assertEqual(rows.count(), 2)
        self.assertTrue(all(r.status == TestStatus.FAILED for r in rows))
        self.assertEqual(
            run.verification_status, VerificationStatus.FAILED
        )

    def test_failed_checks_after_retry_yield_failed_verification(self):
        run = self._run_with(sandbox=[SandboxResult(1, "boom"),
                                      SandboxResult(1, "boom")])
        # Exactly one repair retry: two attempts, then stop (spec F).
        self.assertEqual(run.repair_attempts, 2)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(
            run.verification_status, VerificationStatus.FAILED
        )
        self.assertIs(run.tests_ran, True)

    def test_workflow_failure_yields_unverified(self):
        from repositories.services import GitHubRepoNotFound

        with patch(
            "analysis.agent_service.fetch_github_snapshot",
            side_effect=GitHubRepoNotFound("Repository not found."),
        ):
            run = run_agent(self.issue, user=self.user)
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(
            run.verification_status, VerificationStatus.UNVERIFIED
        )
        self.assertIsNone(run.tests_ran)

    def test_user_recorded_on_run_produced_by_pipeline(self):
        run = self._run_with()
        self.assertEqual(run.user, self.user)
        self.assertEqual(run.verification_status,
                         VerificationStatus.VERIFIED)

    def test_captured_output_is_sanitized(self):
        """ANSI escapes from repository output never reach the database."""
        colored = SandboxResult(1, "\x1b[31mFAILED\x1b[0m\x07 test_add")
        run = self._run_with(sandbox=colored)
        rows = run.test_results.filter(test_name="tests")
        self.assertEqual(rows.count(), 2)   # both attempts actually ran
        for row in rows:
            self.assertNotIn("\x1b", row.output)
            self.assertNotIn("\x07", row.output)
            self.assertIn("FAILED", row.output)

    def test_nothing_is_ever_pushed_or_committed(self):
        """The pipeline has no git integration at all (spec 7.14)."""
        from pathlib import Path as _Path

        from . import agent_service as agent_mod

        source = _Path(agent_mod.__file__).read_text(encoding="utf-8")
        for forbidden in ("git push", "git commit", "git clone"):
            self.assertNotIn(forbidden, source)