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

from django.db import models

from repositories.models import Repository


class RunStatus(models.TextChoices):
    """Shared status vocabulary for long-running jobs."""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


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
    """One automated repair attempt produced by the (future) AI agent."""

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="agent_runs",         # run.agent_runs.all()
    )
    status = models.CharField(
        max_length=20,
        choices=RunStatus.choices,
        default=RunStatus.PENDING,
    )
    repair_attempts = models.PositiveIntegerField(
        default=0,
        help_text="How many repair tries the agent made in this run.",
    )
    final_summary = models.TextField(blank=True)
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["file_path"]

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
    output = models.TextField(
        blank=True,
        help_text="Captured stdout/stderr; may be truncated later.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["test_name"]

    def __str__(self):
        return f"{self.test_name} [{self.status}]"
