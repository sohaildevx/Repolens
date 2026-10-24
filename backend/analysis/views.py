"""Analysis API views.

Endpoints:
- POST /api/repositories/<id>/analyze/ -- analyze a snapshot you upload
                                           (authenticated, your repos)
- POST /api/analysis/runs/             -- same pipeline, repository id in body
                                           (authenticated, your repos)
- POST /api/analysis/scans/            -- PUBLIC: scan a GitHub repository
                                           server-side (anonymous allowed,
                                           rate-limited)
- GET  /api/analysis/runs/<id>/        -- a run + its issues
- GET  /api/analysis/runs/<id>/issues/ -- the run's issues, paginated
- GET  /api/analysis/issues/<id>/      -- one issue (public scans or yours)

Visibility model:
- A repository with owner=NULL is a PUBLIC scan: its runs and issues are
  readable by anyone (the scan was intentionally anonymous and contains
  only public GitHub data).
- A repository with an owner stays private: visible solely to that user,
  answering 404 (not 403) so IDs never leak between accounts.
- State-changing agent endpoints always require authentication; they also
  accept issues from public scans so a signed-in user can repair what an
  anonymous visitor found.

Both analysis POST endpoints funnel through _respond_with_run() -- one
code path for validation -> Flask -> save -> 201/502, so the two routes
can never disagree on behavior.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import (
    CreateAPIView,
    ListAPIView,
    RetrieveAPIView,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from repositories.models import Repository
from repositories.serializers import RepositorySerializer
from repositories.services import (
    GitHubAPIError,
    GitHubRateLimited,
    GitHubRepoNotFound,
    GitHubTimeout,
)
from repositories.views import RepositoryPagination

from .models import AnalysisRun, AgentRun, CodeChange, Issue, RunStatus, TestResult
from .serializers import (
    AgentRunSerializer,
    AnalysisRequestSerializer,
    AnalysisRunSerializer,
    AnalyzeFilesSerializer,
    CodeChangeSerializer,
    IssueSerializer,
    ScanRequestSerializer,
    TestResultSerializer,
)
from .services import run_analysis
from .snapshot import SnapshotError, fetch_github_snapshot


class ScanAnonThrottle(AnonRateThrottle):
    """Per-IP budget for anonymous public scans (scope: scan_anon)."""

    scope = "scan_anon"


class ScanUserThrottle(UserRateThrottle):
    """Per-user budget for signed-in scans (scope: scan_auth)."""

    scope = "scan_auth"


class AgentRunThrottle(UserRateThrottle):
    """Per-user budget for AI agent executions (scope: agent_run)."""

    scope = "agent_run"

AGENT_RUN_STALE_AFTER = timedelta(minutes=10)


def _visible_run_q(user, prefix=""):
    """Q-filter for analysis runs the user may read.

    Public scans (repository.owner IS NULL) are readable by everyone;
    owned runs only by their owner. Anonymous users see public runs only.
    `prefix` adapts the path when the filter is applied from a related
    model (e.g. "analysis_run__" when filtering Issues).
    """
    public = Q(**{f"{prefix}repository__owner__isnull": True})
    if getattr(user, "is_authenticated", False):
        return public | Q(**{f"{prefix}repository__owner": user})
    return public


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


class AnalysisScanCreateView(CreateAPIView):
    """POST /api/analysis/scans/ -- scan a GitHub repository server-side.

    The public entry point of the product: the client sends only a URL;
    the backend validates it (Phase 4 SSRF rules), fetches a bounded
    Python snapshot (Phase 7 limits: 40 files, 100 KB/file, 1 MB total,
    90 s deadline, sensitive files excluded), runs the analyzer, and
    answers with the finished run.

    Anonymous requests create an ownerless (public) repository record;
    signed-in requests attach it to their account. A repository row is
    persisted only AFTER the snapshot fetch succeeds, so nonexistent or
    private repos never leave junk records behind.

    Rate-limited per IP (anonymous) or per user (signed-in).
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScanAnonThrottle, ScanUserThrottle]
    serializer_class = ScanRequestSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        canonical = serializer.validated_data["github_url"]

        # Snapshot against an unsaved stand-in: fetch_github_snapshot()
        # only reads .github_url, and nothing is persisted unless the
        # fetch succeeds.
        candidate = Repository(
            owner=None,
            github_url=canonical,
            name=canonical.rsplit("/", 1)[-1],
        )
        try:
            snapshot = fetch_github_snapshot(candidate)
        except GitHubRepoNotFound as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND
            )
        except GitHubRateLimited as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        except GitHubTimeout as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except GitHubAPIError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )
        except SnapshotError as exc:
            # Safe user-facing messages only (limits, no Python files, ...).
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

        # Persist now that the repository is known to exist and be readable.
        if request.user.is_authenticated:
            repository, _created = Repository.objects.get_or_create(
                owner=request.user,
                github_url=canonical,
                defaults={
                    "name": candidate.name,
                    "default_branch": "main",
                },
            )
        else:
            repository = Repository.objects.create(
                owner=None,
                github_url=canonical,
                name=candidate.name,
                default_branch="main",
            )

        files = [
            {"path": path, "content": content}
            for path, content in snapshot.files.items()
        ]
        run = run_analysis(
            repository,
            files,
            user=request.user if request.user.is_authenticated else None,
        )

        payload = {
            "run": AnalysisRunSerializer(run).data,
            "repository": RepositorySerializer(repository).data,
        }
        if run.status == RunStatus.FAILED:
            return Response(payload, status=status.HTTP_502_BAD_GATEWAY)
        return Response(payload, status=status.HTTP_201_CREATED)


class AnalysisRunDetailView(RetrieveAPIView):
    """GET /api/analysis/runs/<id>/ -- one run with its issues (bounded).

    Readable for public scans (ownerless repositories) and for the
    owner's own runs; anything else is a 404.
    """

    permission_classes = [AllowAny]
    serializer_class = AnalysisRunSerializer

    def get_queryset(self):
        return (
            AnalysisRun.objects
            .filter(_visible_run_q(self.request.user))
            .select_related("repository")
        )


class AnalysisRunIssuesView(ListAPIView):
    """GET /api/analysis/runs/<id>/issues/ -- the run's issues, paginated.

    The detail endpoint embeds at most DETAIL_ISSUE_LIMIT issues to keep its
    payload bounded; this endpoint serves the FULL list through standard
    page-number pagination so large result sets never arrive in one response.
    Ownership is enforced by the queryset filter (404 for foreign runs).
    """

    permission_classes = [AllowAny]
    serializer_class = IssueSerializer
    pagination_class = RepositoryPagination

    def get_queryset(self):
        return (
            Issue.objects
            .filter(_visible_run_q(self.request.user, prefix="analysis_run__"))
            .filter(analysis_run_id=self.kwargs["pk"])
            .order_by("file_path", "line_number")
        )

    def list(self, request, *args, **kwargs):
        # A list view with a visibility-filtered queryset would otherwise
        # return 200 + [] for a foreign/nonexistent run -- indistinguishable
        # from an existing run that genuinely has no issues. Check the run
        # itself first so unauthorized access stays a 404, matching every
        # other endpoint in this API (IDs never leak between accounts).
        if not AnalysisRun.objects.filter(
            _visible_run_q(request.user), pk=self.kwargs["pk"]
        ).exists():
            return Response(
                {"detail": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return super().list(request, *args, **kwargs)


class AnalysisIssueDetailView(RetrieveAPIView):
    """GET /api/analysis/issues/<id>/ -- one issue (deep-link target).

    Readable for issues of public scans and of the caller's own runs.
    """

    permission_classes = [AllowAny]
    serializer_class = IssueSerializer

    def get(self, request, pk=None, *args, **kwargs):
        issue = _visible_issue(request.user, pk)
        if issue is None:
            return Response(
                {"detail": "Issue not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self.get_serializer(issue).data)


def _visible_issue(user, issue_id):
    """Return an issue the user may read, or None (caller answers 404).

    Readable when the scan is public (repository has no owner) or owned
    by the user -- the same rule as every run-level endpoint.
    """
    return (
        Issue.objects
        .filter(pk=issue_id)
        .filter(_visible_run_q(user, prefix="analysis_run__"))
        .select_related("analysis_run__repository")
        .first()
    )


class AgentRunCreateView(CreateAPIView):
    """POST /api/analysis/issues/<issue_id>/agent-run/ -- start a run.

    Always requires authentication (AI fixes are never anonymous), but
    accepts issues from public scans as well as the caller's own -- the
    signed-in user explicitly repairs what an anonymous visitor found.

    Executes the agent workflow SYNCHRONOUSLY (there is no background-job
    system in this project; runs are bounded by strict limits). On success
    the response is 201 with the completed run; if the agent could not
    produce a validated fix the run is still persisted with status=failed
    and a safe summary, and the response is 200 so the record is visible.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [AgentRunThrottle]
    serializer_class = AgentRunSerializer

    def create(self, request, issue_id=None, *args, **kwargs):
        issue = _visible_issue(request.user, issue_id)
        if issue is None:
            return Response(
                {"detail": "Issue not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Duplicate/concurrency guard: the in-flight check AND the insert
        # of the PENDING run happen inside one transaction holding a row
        # lock on the issue, so two simultaneous submits (double-click, two
        # tabs, two workers) are serialized at the database level -- the
        # second sees the first one's in-flight run and is refused with 409
        # BEFORE any Nebius call or snapshot fetch happens. Runs whose
        # worker died mid-execution stop blocking after a staleness window
        # so a crash can never permanently lock an issue.
        stale_before = timezone.now() - AGENT_RUN_STALE_AFTER
        with transaction.atomic():
            Issue.objects.select_for_update().filter(pk=issue.pk).first()
            if (
                AgentRun.objects.filter(
                    issue=issue,
                    status__in=[RunStatus.PENDING, RunStatus.RUNNING],
                )
                .filter(updated_at__gte=stale_before)
                .exists()
            ):
                return Response(
                    {
                        "detail": (
                            "An agent run for this issue is already in progress."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            agent_run = AgentRun.objects.create(
                analysis_run=issue.analysis_run,
                issue=issue,
                user=request.user,
                status=RunStatus.PENDING,
            )

        # Imported here so a circular import at module load is impossible.
        from .agent_service import run_agent

        agent_run = run_agent(issue, user=request.user, agent_run=agent_run)
        serializer = self.get_serializer(agent_run)
        if agent_run.status == RunStatus.FAILED:
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AgentRunOwnedMixin:
    """Shared queryset: runs the requesting user may read.

    Agent endpoints always require authentication. A run belongs to the
    user who launched it -- readable ONLY by them, even when it targets a
    public scan (patches and test output are private agent data, and public
    scan access never grants access to another user's runs). Legacy runs
    without a recorded user keep the original repository-based visibility.
    """

    permission_classes = [IsAuthenticated]

    def get_agent_run(self):
        return (
            AgentRun.objects
            .filter(
                pk=self.kwargs["pk"],
            )
            .filter(
                Q(user=self.request.user)
                | (
                    Q(user__isnull=True)
                    & (
                        Q(analysis_run__repository__owner__isnull=True)
                        | Q(
                            analysis_run__repository__owner=self.request.user
                        )
                    )
                )
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
