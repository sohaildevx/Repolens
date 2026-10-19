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

from .models import AnalysisRun, AgentRun, CodeChange, Issue, RunStatus, TestResult
from .serializers import (
    AgentRunSerializer,
    AnalysisRequestSerializer,
    AnalysisRunSerializer,
    AnalyzeFilesSerializer,
    CodeChangeSerializer,
    TestResultSerializer,
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


def _owned_issue(user, issue_id):
    """Return the user's issue, or None (caller answers 404).

    Ownership is repository ownership, matching every other endpoint:
    another user's issue is invisible, so IDs never leak between accounts.
    """
    return (
        Issue.objects
        .filter(pk=issue_id, analysis_run__repository__owner=user)
        .select_related("analysis_run__repository")
        .first()
    )


class AgentRunCreateView(CreateAPIView):
    """POST /api/analysis/issues/<issue_id>/agent-run/ -- start a run.

    Executes the agent workflow SYNCHRONOUSLY (there is no background-job
    system in this project; runs are bounded by strict limits). On success
    the response is 201 with the completed run; if the agent could not
    produce a validated fix the run is still persisted with status=failed
    and a safe summary, and the response is 200 so the record is visible.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AgentRunSerializer

    def create(self, request, issue_id=None, *args, **kwargs):
        issue = _owned_issue(request.user, issue_id)
        if issue is None:
            return Response(
                {"detail": "Issue not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Imported here so a circular import at module load is impossible.
        from .agent_service import run_agent

        agent_run = run_agent(issue)
        serializer = self.get_serializer(agent_run)
        if agent_run.status == RunStatus.FAILED:
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AgentRunOwnedMixin:
    """Shared queryset: only runs belonging to the requesting user."""

    permission_classes = [IsAuthenticated]

    def get_agent_run(self):
        return (
            AgentRun.objects
            .filter(
                pk=self.kwargs["pk"],
                analysis_run__repository__owner=self.request.user,
            )
            .select_related("analysis_run__repository", "issue")
            .first()
        )

    def _not_found(self):
        return Response(
            {"detail": "Agent run not found."},
            status=status.HTTP_404_NOT_FOUND,
        )


class AgentRunDetailView(AgentRunOwnedMixin, RetrieveAPIView):
    """GET /api/agent-runs/<id>/ -- status, attempts, checks, summary."""

    serializer_class = AgentRunSerializer

    def get(self, request, pk=None, *args, **kwargs):
        agent_run = self.get_agent_run()
        if agent_run is None:
            return self._not_found()
        return Response(self.get_serializer(agent_run).data)


class AgentRunChangesView(AgentRunOwnedMixin, RetrieveAPIView):
    """GET /api/agent-runs/<id>/changes/ -- proposed diffs for the run."""

    serializer_class = CodeChangeSerializer

    def get(self, request, pk=None, *args, **kwargs):
        agent_run = self.get_agent_run()
        if agent_run is None:
            return self._not_found()
        changes = agent_run.code_changes.all()
        return Response(self.get_serializer(changes, many=True).data)


class AgentRunTestsView(AgentRunOwnedMixin, RetrieveAPIView):
    """GET /api/agent-runs/<id>/tests/ -- validation & test results."""

    serializer_class = TestResultSerializer

    def get(self, request, pk=None, *args, **kwargs):
        agent_run = self.get_agent_run()
        if agent_run is None:
            return self._not_found()
        results = agent_run.test_results.order_by("attempt", "test_name")
        return Response(self.get_serializer(results, many=True).data)
