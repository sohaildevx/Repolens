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
]
