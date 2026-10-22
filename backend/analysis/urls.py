"""URL routes for the analysis app (mounted at /api/analysis/)."""

from django.urls import path

from . import views

app_name = "analysis"

urlpatterns = [
    path(
        "runs/",
        views.AnalysisRunCreateView.as_view(),
        name="run-create",
    ),
    path(
        "runs/<int:pk>/",
        views.AnalysisRunDetailView.as_view(),
        name="run-detail",
    ),
    path(
        "runs/<int:pk>/issues/",
        views.AnalysisRunIssuesView.as_view(),
        name="run-issues",
    ),
    path(
        "issues/<int:issue_id>/agent-run/",
        views.AgentRunCreateView.as_view(),
        name="agent-run-create",
    ),
]
