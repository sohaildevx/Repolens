"""Analysis API views.

Endpoints (all require authentication):
- POST /api/analysis/runs/     -- submit a file snapshot, get an AnalysisRun
- GET  /api/analysis/runs/<id>/ -- retrieve a run + its issues

Ownership is enforced via get_queryset() filtering on request.user, exactly
like the repositories app: another user's run is invisible (404, not 403).
"""

from rest_framework import status
from rest_framework.generics import CreateAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from repositories.models import Repository

from .models import AnalysisRun, RunStatus
from .serializers import AnalysisRequestSerializer, AnalysisRunSerializer
from .services import run_analysis


class AnalysisRunCreateView(CreateAPIView):
    """POST /api/analysis/runs/ -- validate, analyze, save, return the run."""

    permission_classes = [IsAuthenticated]
    serializer_class = AnalysisRequestSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # Ownership check: the repository must exist AND belong to request.user.
        # 404 (not 403) so we don't leak which repository IDs exist.
        try:
            repository = Repository.objects.get(
                pk=data["repository"], owner=request.user
            )
        except Repository.DoesNotExist:
            return Response(
                {"detail": "Repository not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # The analyzer-facing list: {"path": ..., "content": ...}
        files = [
            {"path": f["path"], "content": f["content"]}
            for f in data["files"]
        ]

        # run_analysis() never raises -- it records failures on the run.
        run = run_analysis(repository, files, user=request.user)

        out = AnalysisRunSerializer(run).data
        if run.status == RunStatus.FAILED:
            # The run is still saved (status=failed); tell the client the
            # analyzer had trouble but the record exists.
            return Response(out, status=status.HTTP_502_BAD_GATEWAY)
        return Response(out, status=status.HTTP_201_CREATED)


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
