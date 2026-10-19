"""URL routes for agent runs (mounted at /api/agent-runs/).

Kept separate from analysis/urls.py because agent runs get their own
top-level prefix per the API spec; both files live in the analysis app,
where the agent logic belongs.
"""

from django.urls import path

from . import views

app_name = "agent_runs"

urlpatterns = [
    path(
        "<int:pk>/",
        views.AgentRunDetailView.as_view(),
        name="agent-run-detail",
    ),
    path(
        "<int:pk>/changes/",
        views.AgentRunChangesView.as_view(),
        name="agent-run-changes",
    ),
    path(
        "<int:pk>/tests/",
        views.AgentRunTestsView.as_view(),
        name="agent-run-tests",
    ),
]
