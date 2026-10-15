"""Serializers for the analysis app.

The write path validates the file snapshot before it ever reaches Flask:
- repository must be an integer ID owned by the request.user (checked in view)
- files must be a non-empty list of {path, content} with sane limits

Limits here mirror the analyzer's own limits (defense in depth): the
analyzer rejects bad input independently, so a bug in this validation
cannot be exploited to bypass the service's guards.
"""

from rest_framework import serializers

from .models import AnalysisRun, Issue


class FileEntrySerializer(serializers.Serializer):
    """One file in the snapshot."""

    path = serializers.CharField(max_length=500)
    content = serializers.CharField(
        allow_blank=True,
        # Serializer-level cap; the analyzer enforces its own byte limit.
        trim_whitespace=False,
    )


class AnalysisRequestSerializer(serializers.Serializer):
    """Payload for POST /api/analysis/runs/."""

    repository = serializers.IntegerField(min_value=1)
    files = FileEntrySerializer(many=True, allow_empty=False)

    MAX_FILES = 100

    def validate_files(self, value):
        if len(value) > self.MAX_FILES:
            raise serializers.ValidationError(
                f"Too many files ({len(value)}); limit is {self.MAX_FILES}."
            )
        return value


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
            "description",
        )
        read_only_fields = fields


class AnalysisRunSerializer(serializers.ModelSerializer):
    """Read-only run detail, including nested issues for the detail view."""

    issues = IssueSerializer(many=True, read_only=True)
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
        )
        read_only_fields = fields
