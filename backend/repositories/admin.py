"""Admin configuration for the repositories app."""

from django.contrib import admin

from .models import Repository


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "default_branch", "github_url", "created_at")
    list_filter = ("default_branch", "created_at")
    search_fields = ("name", "github_url", "owner__username")
    # Fetch owner in the same SQL query -> avoids N+1 in list view.
    list_select_related = ("owner",)
    readonly_fields = ("created_at", "updated_at")
