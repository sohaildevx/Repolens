"""Models for the repositories app: GitHub repositories registered for analysis."""

from django.conf import settings
from django.db import models


class Repository(models.Model):
    """A GitHub repository that a user has registered with RepoLens."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,          # Django's built-in User
        on_delete=models.CASCADE,          # user deleted -> their repos deleted
        related_name="repositories",       # user.repositories.all()
        help_text="The user who registered this repository.",
    )
    github_url = models.URLField(
        max_length=500,
        unique=True,                       # DB constraint: one registration per repo
        help_text="Canonical GitHub URL, e.g. https://github.com/owner/repo",
    )
    name = models.CharField(
        max_length=255,
        help_text="Repository name as shown on GitHub.",
    )
    default_branch = models.CharField(
        max_length=255,
        default="main",
        help_text="Branch to analyze when none is specified.",
    )
    created_at = models.DateTimeField(auto_now_add=True)  # when registered here
    updated_at = models.DateTimeField(auto_now=True)      # last edit, auto-managed

    class Meta:
        ordering = ["-created_at"]  # newest first in lists

    def __str__(self):
        return f"{self.owner}/{self.name}"
