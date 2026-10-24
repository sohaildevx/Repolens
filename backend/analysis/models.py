"""Models for the analysis app: analysis runs, issues, agent repair runs, changes, tests.

Relationship chain:
    Repository (repositories app)
      └── AnalysisRun
            ├── Issue
            └── AgentRun
                  ├── CodeChange
                  └── TestResult
All children CASCADE from their parent: deleting a run removes everything
it produced, preventing orphaned analysis data.
"""

from django.conf import settings
from django.db import models

from repositories.models import Repository


class RunStatus(models.TextChoices):
    """Shared EXECUTION status vocabulary for long-running jobs.

    Execution answers "did the workflow run?", never "is the bug fixed?".
    PENDING is the queued state (row created, work not started yet); the
    fix verdict lives separately in VerificationStatus.
    """

    PENDING = "pending", "Pending"      # queued: created, not started
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class VerificationStatus(models.TextChoices):
    """VERDICT vocabulary for agent runs, computed from checks that ran.

    Kept strictly separate from RunStatus: a run can complete execution
    while its fix is unverified or fails verification.

    - verified: every required check (syntax, static analysis, repository
      tests) actually ran and passed.
    - partially_verified: some checks passed but relevant checks were
      skipped, errored, or could not run (e.g. sandbox unavailable).
    - failed: one or more required checks produced a failing verdict.
    - unverified: there is no validation evidence to judge the fix.
    """

    VERIFIED = "verified", "Verified"
    PARTIALLY_VERIFIED = "partially_verified", "Partially verified"
    FAILED = "failed", "Failed"
    UNVERIFIED = "unverified", "Unverified"


class AnalysisRun(models.Model):
    """One analysis execution over a repository snapshot."""

    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,          # repo deleted -> its runs deleted
        related_name="analysis_runs",      # repository.analysis_runs.all()
    )
    status = models.CharField(
        max_length=20,
        choices=RunStatus.choices,
        default=RunStatus.PENDING,
    )
    # Nullable on purpose: a PENDING run has not started yet.
    start_time = models.DateTimeField(null=True, blank=True)
    completion_time = models.DateTimeField(null=True, blank=True)
    # How many files the analyzer actually examined. NULL = unknown (runs
    # recorded before this field existed, or a failed run) -- the UI must
    # render "unavailable", never a fabricated 0.
    files_analyzed = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Files examined by the analyzer; NULL if unknown.",
    )
    summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Let PostgreSQL reject impossible time ranges (completed < started).
            models.CheckConstraint(
                condition=(
                    models.Q(completion_time__isnull=True)
                    | models.Q(start_time__isnull=True)
                    | models.Q(completion_time__gte=models.F("start_time"))
                ),
                name="analysisrun_completion_not_before_start",
            ),
        ]

    def __str__(self):
        return f"{self.repository} run #{self.pk} [{self.status}]"


class Severity(models.TextChoices):
    """How serious an issue is, for filtering/sorting in the future UI."""

    CRITICAL = "critical", "Critical"
    HIGH = "high", "High"
    MEDIUM = "medium", "Medium"
    LOW = "low", "Low"
    INFO = "info", "Info"


class Issue(models.Model):
    """A problem found by an analysis run at a specific location."""

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,          # run deleted -> its issues deleted
        related_name="issues",             # run.issues.all()
    )
    file_path = models.CharField(
        max_length=1024,
        help_text="Repo-relative path, e.g. src/app.py",
    )
    line_number = models.PositiveIntegerField(
        help_text="1-based line number where the issue was detected.",
    )
    severity = models.CharField(max_length=10, choices=Severity.choices)
    rule = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Rule ID that triggered this issue, e.g. B608 or AST002.",
    )
    source = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Engine that produced the issue: ast, ruff or bandit.",
    )
    description = models.TextField()
    suggested_fix = models.TextField(
        blank=True,
        help_text="Human-readable repair guidance; may be empty.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["file_path", "line_number"]
        constraints = [
            # Lines are 1-based: 0 or negative makes no sense.
            models.CheckConstraint(
                condition=models.Q(line_number__gte=1),
                name="issue_line_number_gte_1",
            ),
        ]

    def __str__(self):
        return f"{self.file_path}:{self.line_number} [{self.severity}]"


class AgentRun(models.Model):
    """One automated repair attempt produced by the (future) AI agent.

    Execution status (RunStatus) and fix verdict (verification_status) are
    deliberately separate: "the agent ran" never implies "the bug is fixed".
    """

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="agent_runs",         # run.agent_runs.all()
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,         # keep run history if a user goes
        null=True,
        blank=True,
        related_name="agent_runs",
        help_text=(
            "Authenticated user who launched this run; NULL for legacy rows."
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=RunStatus.choices,
        default=RunStatus.PENDING,
    )
    verification_status = models.CharField(
        max_length=30,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
        help_text=(
            "Verdict computed from the validation checks that actually ran."
        ),
    )
    tests_ran = models.BooleanField(
        null=True,
        default=None,
        help_text=(
            "Whether repository tests actually executed; NULL when the "
            "validation phase was never reached."
        ),
    )
    repair_attempts = models.PositiveIntegerField(
        default=0,
        help_text="How many repair tries the agent made in this run.",
    )
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="agent_runs",        # issue.agent_runs.all()
        help_text="Issue this run targets; nullable only for legacy rows.",
    )
    start_commit_sha = models.CharField(
        max_length=40,
        blank=True,
        default="",
        help_text="Git commit SHA the source snapshot was taken at, if known.",
    )
    final_summary = models.TextField(blank=True)
    explanation = models.TextField(
        blank=True,
        help_text=(
            "AI-generated explanation of what changed and why, plus the "
            "expected behavior (empty when no valid patch was produced)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Agent run #{self.pk} [{self.status}]"


class CodeChange(models.Model):
    """A single proposed file edit produced by an agent run."""

    agent_run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="code_changes",       # agent_run.code_changes.all()
    )
    file_path = models.CharField(max_length=1024)
    proposed_diff = models.TextField(
        help_text="Unified diff text (git-style patch)."
    )
    attempt = models.PositiveSmallIntegerField(
        default=1,
        help_text=(
            "Which repair attempt produced this change; earlier attempts' "
            "changes are preserved for inspection."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["attempt", "file_path"]

    def __str__(self):
        return f"Change to {self.file_path} (run #{self.agent_run_id})"


class TestStatus(models.TextChoices):
    PASSED = "passed", "Passed"
    FAILED = "failed", "Failed"
    ERROR = "error", "Error"
    SKIPPED = "skipped", "Skipped"


class TestResult(models.Model):
    """Outcome of one sandbox test executed for an agent run."""

    agent_run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="test_results",      # agent_run.test_results.all()
    )
    test_name = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=TestStatus.choices)
    attempt = models.PositiveSmallIntegerField(
        default=1,
        help_text="Which repair attempt produced this result (1 or 2).",
    )
    output = models.TextField(
        blank=True,
        help_text="Captured stdout/stderr; may be truncated later.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["test_name"]

    def __str__(self):
        return f"{self.test_name} [{self.status}]"
