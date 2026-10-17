"""URL routes for the repositories app (mounted at /api/repositories/)."""

from django.urls import path

from analysis import views as analysis_views

from . import views

app_name = "repositories"

urlpatterns = [
    path(
        "",
        views.RepositoryListCreateView.as_view(),
        name="repository-list-create",
    ),
    path(
        "<int:pk>/",
        views.RepositoryDetailView.as_view(),
        name="repository-detail",
    ),
    # Analyze a snapshot of this repository. The view lives in the
    # analysis app (all analysis logic stays there); the route lives here
    # so every /api/repositories/... path is declared in one file.
    path(
        "<int:pk>/analyze/",
        analysis_views.RepositoryAnalyzeView.as_view(),
        name="repository-analyze",
    ),
]
