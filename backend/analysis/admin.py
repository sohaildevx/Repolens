"""Admin configuration for the analysis app."""

from django.contrib import admin

from .models import AgentRun, AnalysisRun, CodeChange, Issue, TestResult


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ("id", "repository", "status", "start_time", "completion_time", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("repository__name", "summary")
    list_select_related = ("repository",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("file_path", "line_number", "severity", "analysis_run", "created_at")
    list_filter = ("severity", "created_at")
    search_fields = ("file_path", "description")
    list_select_related = ("analysis_run",)
    readonly_fields = ("created_at",)


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("id", "analysis_run", "status", "repair_attempts", "created_at")
    list_filter = ("status", "created_at")
    list_select_related = ("analysis_run",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(CodeChange)
class CodeChangeAdmin(admin.ModelAdmin):
    list_display = ("file_path", "agent_run", "created_at")
    search_fields = ("file_path",)
    list_select_related = ("agent_run",)
    readonly_fields = ("created_at",)


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    list_display = ("test_name", "status", "agent_run", "created_at")
    list_filter = ("status",)
    search_fields = ("test_name",)
    list_select_related = ("agent_run",)
    readonly_fields = ("created_at",)
