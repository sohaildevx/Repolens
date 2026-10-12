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
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Let PostgreSQL reject impossible time ranges (completed < started).
            models.CheckConstraint(
                condition=(
                    models.Q(completed_at__isnull=True)
                    | models.Q(started_at__isnull=True)
                    | models.Q(completed_at__gte=models.F("started_at"))
                ),
                name="analysis_completed_at_not_before_started_at",
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
        max_length=500,
        help_text="Repo-relative path, e.g. src/app.py",
    )
    # Nullable: some issues apply to a whole file, not one line.
    line_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="1-based line number; null for file-level issues.",
    )
    severity = models.CharField(max_length=20, choices=Severity.choices)
    description = models.TextField()
    suggested_fix = models.TextField(
        blank=True,
        help_text="Human-readable repair guidance; may be empty.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["file_path", "line_number"]

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
    file_path = models.CharField(max_length=500)
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
    test_name = models.CharField(max_length=500)
    status = models.CharField(max_length=20, choices=TestStatus.choices)
    output = models.TextField(
        blank=True,
        help_text="Captured stdout/stderr; may be truncated later.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["test_name"]

    def __str__(self):
        return f"{self.test_name} [{self.status}]"
