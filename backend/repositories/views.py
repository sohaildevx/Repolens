"""Repository API views.

Endpoints (all require authentication):
- POST   /api/repositories/          -- create (fetch GitHub metadata + save)
- GET    /api/repositories/          -- list (only the current user's repos)
- GET    /api/repositories/<id>/     -- retrieve (with analysis history summary)
- DELETE /api/repositories/<id>/     -- delete (cascades to analysis data)

Security:
- Global IsAuthenticated (from settings) + explicit per-view permission.
- Ownership enforced via get_queryset() filtering on request.user.
- GitHub API errors mapped to safe HTTP responses (no stack traces).
- CSRF is active for unsafe methods (SessionAuthentication re-checks it).
"""

from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.generics import (
    ListCreateAPIView,
    RetrieveDestroyAPIView,
)
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Repository
from .serializers import (
    RepositoryCreateSerializer,
    RepositoryDetailSerializer,
    RepositorySerializer,
)
from .services import (
    GitHubAPIError,
    GitHubRateLimited,
    GitHubRepoNotFound,
    GitHubTimeout,
    fetch_repository_metadata,
    parse_github_repo_url,
)


class RepositoryPagination(PageNumberPagination):
    """Standard page-number pagination with a conservative page size."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class RepositoryListCreateView(ListCreateAPIView):
    """GET list / POST create for the current user's repositories."""

    permission_classes = [IsAuthenticated]
    pagination_class = RepositoryPagination

    def get_serializer_class(self):
        """Use the create serializer for POST, the read serializer for GET."""
        if self.request.method == "POST":
            return RepositoryCreateSerializer
        return RepositorySerializer

    def get_queryset(self):
        """Only the current user's repositories -- never expose others'."""
        return (
            Repository.objects.filter(owner=self.request.user)
            .order_by("-created_at")
        )

    def create(self, request, *args, **kwargs):
        """POST /api/repositories/ -- validate, fetch GitHub metadata, save.

        Flow:
        1. Validate URL format (serializer).
        2. Check duplicate (owner + canonical URL).
        3. Fetch metadata from GitHub API.
        4. Save with owner=request.user (never from request body).
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        canonical_url = serializer.validated_data["github_url"]

        # Duplicate check before hitting GitHub API (saves a network call).
        if Repository.objects.filter(
            owner=request.user, github_url=canonical_url
        ).exists():
            return Response(
                {"detail": "You have already saved this repository."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Parse owner/repo from the canonical URL for the API call.
        owner, repo = parse_github_repo_url(canonical_url)

        # Fetch metadata from GitHub -- map errors to safe HTTP responses.
        try:
            metadata = fetch_repository_metadata(owner, repo)
        except GitHubRepoNotFound as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_404_NOT_FOUND,
            )
        except GitHubRateLimited as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except GitHubTimeout as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except GitHubAPIError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Save with owner=request.user -- never trust request body for ownership.
        # Catch IntegrityError for race-condition duplicate protection (belt
        # and suspenders on top of the pre-check above).
        try:
            with transaction.atomic():
                repo_instance = Repository.objects.create(
                    owner=request.user,
                    github_url=metadata["github_url"],
                    name=metadata["name"],
                    default_branch=metadata["default_branch"],
                    description=metadata["description"],
                )
        except IntegrityError:
            return Response(
                {"detail": "You have already saved this repository."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            RepositorySerializer(repo_instance).data,
            status=status.HTTP_201_CREATED,
        )


class RepositoryDetailView(RetrieveDestroyAPIView):
    """GET detail / DELETE for a single repository owned by the current user."""

    permission_classes = [IsAuthenticated]
    serializer_class = RepositoryDetailSerializer

    def get_queryset(self):
        """Only the current user's repositories -- 404 if not owner."""
        return Repository.objects.filter(owner=self.request.user)

    def destroy(self, request, *args, **kwargs):
        """DELETE /api/repositories/<id>/ -- remove record + cascade analysis.

        Cascades to analysis runs, issues, agent runs, code changes, test
        results (all on_delete=CASCADE from Phase 3). Does NOT touch the
        actual GitHub repository -- only removes our local record.
        """
        instance = self.get_object()  # 404 if not in get_queryset()
        instance.delete()  # cascades via ORM
        return Response(status=status.HTTP_204_NO_CONTENT)
