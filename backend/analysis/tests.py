"""Tests for the analysis app models: creation, relationships, constraints."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from repositories.models import Repository

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

User = get_user_model()


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
