"""Analysis API views.

Endpoints (all require authentication):
- POST /api/repositories/<id>/analyze/ -- spec-preferred: analyze a snapshot
                                          of a repository you own
- POST /api/analysis/runs/             -- same pipeline, repository id in body
- GET  /api/analysis/runs/<id>/        -- retrieve a run + its issues

Ownership is enforced by filtering on request.user: another user's
repository or run is invisible (404, not 403), so repository/run IDs
don't leak between accounts.

Both POST endpoints funnel through _respond_with_run() -- one code path
for validation -> Flask -> save -> 201/502, so the two routes can never
disagree on behavior.
"""

from rest_framework import status
from rest_framework.generics import CreateAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from repositories.models import Repository

from .models import AnalysisRun, RunStatus
from .serializers import (
    AnalysisRequestSerializer,
    AnalysisRunSerializer,
    AnalyzeFilesSerializer,
)
from .services import run_analysis


def _owned_repository(user, repository_id):
    """Return the user's repository, or None (caller answers 404)."""
    try:
        return Repository.objects.get(pk=repository_id, owner=user)
    except Repository.DoesNotExist:
        return None


def _respond_with_run(repository, file_entries, user):
    """Forward the snapshot to Flask and answer with the saved run.

    201 when the run completed; 502 when the analyzer failed -- in both
    cases the AnalysisRun is already persisted (completed or failed),
    never left in "running".
    """
    files = [
        {"path": f["path"], "content": f["content"]}
        for f in file_entries
    ]

    # run_analysis() never raises -- it records failures on the run itself.
    run = run_analysis(repository, files, user=user)

    out = AnalysisRunSerializer(run).data
    if run.status == RunStatus.FAILED:
        # The run is still saved (status=failed); tell the client the
        # analyzer had trouble but the record exists.
        return Response(out, status=status.HTTP_502_BAD_GATEWAY)
    return Response(out, status=status.HTTP_201_CREATED)


class RepositoryAnalyzeView(CreateAPIView):
    """POST /api/repositories/<pk>/analyze/ -- analyze a snapshot of YOUR repo.

    The route is declared by the repositories app (so all
    /api/repositories/... paths live in one file) while the view stays
    here, where the analysis logic belongs.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AnalyzeFilesSerializer

    def create(self, request, pk=None, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        repository = _owned_repository(request.user, pk)
        if repository is None:
            return Response(
                {"detail": "Repository not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return _respond_with_run(
            repository, serializer.validated_data["files"], request.user
        )


class AnalysisRunCreateView(CreateAPIView):
    """POST /api/analysis/runs/ -- same pipeline, repository id in the body."""

    permission_classes = [IsAuthenticated]
    serializer_class = AnalysisRequestSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        repository = _owned_repository(request.user, data["repository"])
        if repository is None:
            return Response(
                {"detail": "Repository not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return _respond_with_run(
            repository, data["files"], request.user
        )


class AnalysisRunDetailView(RetrieveAPIView):
    """GET /api/analysis/runs/<id>/ -- one run with its issues."""

    permission_classes = [IsAuthenticated]
    serializer_class = AnalysisRunSerializer

    def get_queryset(self):
        return (
            AnalysisRun.objects
            .filter(repository__owner=self.request.user)
            .select_related("repository")
        )
