"""URL routes for the repositories app (mounted at /api/repositories/)."""

from django.urls import path

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
]
