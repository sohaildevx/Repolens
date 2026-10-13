"""Serializers for the repositories app.

Two serializers serve two purposes:
- RepositoryCreateSerializer: validates the incoming POST payload (write path).
- RepositorySerializer: shapes the outgoing JSON (read path).

The write path only accepts `github_url` -- ownership comes from the
authenticated request.user, never from the request body (spec §1).
"""

from rest_framework import serializers

from .models import Repository
from .services import parse_github_repo_url


class RepositorySerializer(serializers.ModelSerializer):
    """Read-only representation of a saved repository.

    Exposes only safe metadata fields -- no owner ID (spec §2: list shows
    id, name, github_url, default_branch, description, created_at).
    """

    class Meta:
        model = Repository
        fields = (
            "id",
            "name",
            "github_url",
            "default_branch",
            "description",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class RepositoryDetailSerializer(RepositorySerializer):
    """Extended representation for the detail endpoint.

    Adds a lightweight analysis history summary so the React frontend can
    show "last analyzed" without extra requests.
    """

    analysis_run_count = serializers.SerializerMethodField()
    last_analysis_status = serializers.SerializerMethodField()

    class Meta(RepositorySerializer.Meta):
        fields = RepositorySerializer.Meta.fields + (
            "analysis_run_count",
            "last_analysis_status",
        )

    def get_analysis_run_count(self, obj):
        """Number of analysis runs recorded for this repository."""
        return obj.analysis_runs.count()

    def get_last_analysis_status(self, obj):
        """Status of the most recent analysis run, or None if never analyzed."""
        last = obj.analysis_runs.order_by("-created_at").first()
        return last.status if last else None


class RepositoryCreateSerializer(serializers.Serializer):
    """Validates the POST payload: a single github_url field.

    Validation layers (fail fast, cheap to expensive):
    1. Required + max length (serializer field).
    2. URL format + host + owner/repo syntax (parse_github_repo_url).
    3. Duplicate check (owner + canonical URL) -- done in the view after
       we know the canonical form.

    The validated `github_url` is stored in canonical form
    (https://github.com/{owner}/{repo}) regardless of the exact input
    variation the user typed (trailing slash, .git suffix, etc.).
    """

    github_url = serializers.CharField(max_length=2048, trim_whitespace=True)

    def validate_github_url(self, value):
        """Parse and validate the URL, returning the canonical form."""
        try:
            owner, repo = parse_github_repo_url(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        # Return canonical form for storage and duplicate checking.
        return f"https://github.com/{owner}/{repo}"
