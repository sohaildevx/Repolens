"""Serializers for the analysis app.

The write path validates the file snapshot BEFORE it ever reaches Flask:
- path shape (no traversal, no absolute paths, no sensitive filenames)
- per-file and total size limits, file count limit

Limits intentionally mirror the analyzer's own guards (see
analyzer/analyzer.py). This is defense in depth: both services reject the
same bad input independently, so a bug in one layer cannot be exploited
to bypass the other.

Two request shapes are supported:
- AnalysisRequestSerializer  -- POST /api/analysis/runs/   (repository in body)
- AnalyzeFilesSerializer     -- POST /api/repositories/<id>/analyze/ (URL)
"""

from rest_framework import serializers

from .models import AnalysisRun, AgentRun, CodeChange, Issue, TestResult

# Keep these aligned with analyzer/analyzer.py (mirrored on purpose).
MAX_FILES = 100
MAX_FILE_BYTES = 200_000        # 200 KB per file
MAX_TOTAL_BYTES = 2_000_000     # 2 MB per submission

# Max issues embedded in a single run-detail response. Keeps the payload
# bounded on large repositories; the true total is always in `issue_count`
# and the full list is available via the paginated .../issues/ endpoint.
DETAIL_ISSUE_LIMIT = 100

# Filenames the analyzer refuses to touch; refuse them one layer earlier.
SENSITIVE_PATTERNS = {
    ".env", ".env.local", ".env.production",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "credentials", "credentials.json", "secrets.json",
    "secrets", "service-account.json", ".npmrc", ".pypirc",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore")


def _validate_snapshot_path(path: str) -> str:
    """Reject unsafe snapshot paths with a 400 (never a 500)."""
    if not path or "\x00" in path:
        raise serializers.ValidationError("File path is empty or invalid.")
    if "\\" in path:
        raise serializers.ValidationError(
            "Use forward slashes in file paths."
        )
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        raise serializers.ValidationError(
            "Absolute paths are not allowed."
        )
    if ".." in path.split("/"):
        raise serializers.ValidationError(
            "Path traversal ('..') is not allowed."
        )
    name = path.rsplit("/", 1)[-1].lower()
    if name in SENSITIVE_PATTERNS or name.startswith(".env") \
            or name.endswith(SENSITIVE_SUFFIXES):
        raise serializers.ValidationError(
            "Sensitive files are excluded from analysis."
        )
    return path


class FileEntrySerializer(serializers.Serializer):
    """One file in the snapshot: {"path": ..., "content": ...}."""

    path = serializers.CharField(max_length=500)
    content = serializers.CharField(
        allow_blank=True,
        trim_whitespace=False,   # code is whitespace-sensitive
    )

    def validate_path(self, value):
        return _validate_snapshot_path(value)

    def validate_content(self, value):
        size = len(value.encode("utf-8", errors="replace"))
        if size > MAX_FILE_BYTES:
            raise serializers.ValidationError(
                f"File content is {size} bytes; limit is {MAX_FILE_BYTES}."
            )
        return value


class BaseSnapshotSerializer(serializers.Serializer):
    """Shared shape + limits for both analysis request endpoints."""

    files = FileEntrySerializer(many=True, allow_empty=False)

    def validate_files(self, value):
        if len(value) > MAX_FILES:
            raise serializers.ValidationError(
                f"Too many files ({len(value)}); limit is {MAX_FILES}."
            )
        total = sum(
            len(f["content"].encode("utf-8", errors="replace"))
            for f in value
        )
        if total > MAX_TOTAL_BYTES:
            raise serializers.ValidationError(
                f"Total payload is {total} bytes; limit is {MAX_TOTAL_BYTES}."
            )
        return value


class AnalysisRequestSerializer(BaseSnapshotSerializer):
    """Payload for POST /api/analysis/runs/ (repository id in the body)."""

    repository = serializers.IntegerField(min_value=1)


class AnalyzeFilesSerializer(BaseSnapshotSerializer):
    """Payload for POST /api/repositories/<id>/analyze/ (repository in URL)."""


class IssueSerializer(serializers.ModelSerializer):
    """Read-only representation of a saved issue."""

    class Meta:
        model = Issue
        fields = (
            "id",
            "file_path",
            "line_number",
            "severity",
            "rule",
            "source",
            "description",
        )
        read_only_fields = fields


class AnalysisRunSerializer(serializers.ModelSerializer):
    """Read-only run detail, including nested issues for the detail view.

    The embedded issue list is BOUNDED: a run over a large repository could
    produce thousands of findings, and embedding all of them makes the detail
    response unbounded. At most ANALYZER_DETAIL_ISSUE_LIMIT issues are
    embedded; `issue_count` always reports the true total and
    `issues_truncated` is True when the embedded list was cut. Clients that
    need every issue use the paginated GET .../issues/ endpoint instead.
    """

    issues = serializers.SerializerMethodField()
    issue_count = serializers.SerializerMethodField()
    issues_truncated = serializers.SerializerMethodField()
    repository_name = serializers.CharField(
        source="repository.name", read_only=True
    )

    class Meta:
        model = AnalysisRun
        fields = (
            "id",
            "repository",
            "repository_name",
            "status",
            "start_time",
            "completion_time",
            "summary",
            "created_at",
            "issues",
            "issue_count",
            "issues_truncated",
        )
        read_only_fields = fields

    def _limited_issues(self, obj):
        # Cheap, stable slice: count is one query, the slice is bounded.
        return obj.issues.all()[:DETAIL_ISSUE_LIMIT]

    def get_issues(self, obj):
        return IssueSerializer(self._limited_issues(obj), many=True).data

    def get_issue_count(self, obj):
        return obj.issues.count()

    def get_issues_truncated(self, obj):
        return obj.issues.count() > DETAIL_ISSUE_LIMIT


class AgentRunSerializer(serializers.ModelSerializer):
    """Read-only agent-run detail.

    `issue_id` exposes which issue the run targets; `checks` gives a quick
    overview without downloading every TestResult row.
    """

    issue_id = serializers.IntegerField(
        read_only=True, allow_null=True
    )
    checks = serializers.SerializerMethodField()

    class Meta:
        model = AgentRun
        fields = (
            "id",
            "analysis_run",
            "issue_id",
            "status",
            "repair_attempts",
            "start_commit_sha",
            "final_summary",
            "checks",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_checks(self, obj):
        results = obj.test_results.values_list("status", flat=True)
        counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
        for s in results:
            counts[s] = counts.get(s, 0) + 1
        return counts


class CodeChangeSerializer(serializers.ModelSerializer):
    """Read-only proposed change (one per modified file)."""

    class Meta:
        model = CodeChange
        fields = ("id", "file_path", "proposed_diff", "created_at")
        read_only_fields = fields


class TestResultSerializer(serializers.ModelSerializer):
    """Read-only validation/test result, including which attempt ran it."""

    class Meta:
        model = TestResult
        fields = ("id", "test_name", "status", "attempt", "output", "created_at")
        read_only_fields = fields
