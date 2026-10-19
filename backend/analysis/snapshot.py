"""Controlled GitHub source-snapshot provider for agent runs (Phase 7).

STEP B of the agent workflow. Fetches a bounded, in-memory snapshot of the
target repository's Python sources at a specific commit:

- resolves the default branch, then the exact commit SHA (recorded on the
  AgentRun so results are reproducible),
- lists the git tree recursively (bounded), keeping only .py blobs,
- enforces per-file, total-size, file-count and wall-clock limits,
- never fetches sensitive files (.env, private keys, credentials).

Every request targets the hardcoded https://api.github.com base built from
validated owner/repo components (same SSRF rules as Phase 4, see
repositories.services). The user-supplied URL is never fetched directly.

The original GitHub repository is only ever READ from. Nothing is written,
committed or pushed back -- ever.
"""

import base64
import os
import time
from dataclasses import dataclass, field

import requests

from repositories.services import (
    GITHUB_API_BASE,
    REQUEST_TIMEOUT,
    GitHubAPIError,
    GitHubRateLimited,
    GitHubRepoNotFound,
    GitHubTimeout,
    parse_github_repo_url,
)

from .serializers import SENSITIVE_PATTERNS, SENSITIVE_SUFFIXES

# Snapshot limits (deliberately stricter than the analyzer's upload limits).
SNAPSHOT_MAX_FILES = 40                # .py files fetched per run
SNAPSHOT_MAX_FILE_BYTES = 100_000      # 100 KB per file
SNAPSHOT_MAX_TOTAL_BYTES = 1_000_000   # 1 MB total content
SNAPSHOT_DEADLINE_SECONDS = 90.0       # wall-clock budget for the whole fetch


class SnapshotError(Exception):
    """Snapshot failure with a safe, user-facing message.

    Messages are short and never contain host paths, credentials or raw
    exception details.
    """


@dataclass
class Snapshot:
    """A bounded, in-memory snapshot of one repository's Python files."""

    owner: str
    repo: str
    commit_sha: str
    files: dict  # relative posix path -> content (str)
    skipped: list = field(default_factory=list)  # "path (reason)" notes

    def context_for(self, path: str, max_bytes: int) -> str:
        """Return one file's content for a prompt, truncated to max_bytes."""
        content = self.files.get(path, "")
        if len(content) > max_bytes:
            return content[:max_bytes] + "\n... [truncated for prompt size] ..."
        return content


def is_sensitive_path(path: str) -> bool:
    """True when a path looks like a secret-bearing file.

    Mirrors the analyzer's own exclusion rules (analysis/serializers.py) so
    both layers refuse the same files independently.

    Patterns are matched against the basename AND against the basename with
    its extension stripped, so a secret is caught whatever it is suffixed
    with (``id_rsa``, ``id_rsa.py``, ``id_rsa.bak``) while an innocent
    ``application.py`` -- which merely contains the substring "app" -- is not.
    """
    name = path.rsplit("/", 1)[-1].lower()
    stem = name.split(".", 1)[0]
    return (
        name in SENSITIVE_PATTERNS
        or stem in SENSITIVE_PATTERNS
        or name.startswith(".env")
        or name.endswith(SENSITIVE_SUFFIXES)
    )


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise SnapshotError(
            "Snapshot preparation exceeded its time budget. Try a smaller repository."
        )


def _get_json(session: requests.Session, url: str, deadline: float) -> dict:
    """GET a GitHub API JSON resource with Phase 4's error taxonomy."""
    _check_deadline(deadline)
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
    except requests.Timeout:
        raise GitHubTimeout("GitHub API request timed out. Try again later.")
    except requests.RequestException:
        raise GitHubAPIError("Could not connect to GitHub API.")

    if resp.status_code == 404:
        raise GitHubRepoNotFound(
            "Repository not found or is private.", status_code=404
        )
    if resp.status_code in (403, 429):
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining == "0" or resp.status_code == 429:
            raise GitHubRateLimited(
                "GitHub API rate limit exceeded. Try again later.", status_code=429
            )
        raise GitHubRepoNotFound(
            "Repository not found or is private.", status_code=403
        )
    if resp.status_code != 200:
        raise GitHubAPIError(
            "GitHub API returned an unexpected error. Try again later.",
            status_code=resp.status_code,
        )
    try:
        return resp.json()
    except ValueError:
        raise GitHubAPIError("GitHub API returned invalid JSON.")


def fetch_github_snapshot(repository) -> Snapshot:
    """Fetch a bounded snapshot of `repository`'s Python sources.

    Raises GitHubError subclasses (safe messages) or SnapshotError on any
    limit violation. Never raises with secrets or host details attached.
    """
    deadline = time.monotonic() + SNAPSHOT_DEADLINE_SECONDS

    try:
        owner, repo = parse_github_repo_url(repository.github_url)
    except ValueError:
        raise SnapshotError(
            "The stored repository URL is not a valid GitHub URL."
        )

    session = requests.Session()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RepoLens",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Optional token raises the rate limit (same env var as Phase 4).
    # Its value is never logged or returned anywhere.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    session.headers.update(headers)

    # 1) Repository metadata -> default branch.
    meta = _get_json(
        session, f"{GITHUB_API_BASE}/repos/{owner}/{repo}", deadline
    )
    branch = meta.get("default_branch") or "main"

    # 2) Exact commit SHA for the branch (reproducibility).
    commit = _get_json(
        session,
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{branch}",
        deadline,
    )
    commit_sha = str(commit.get("sha") or "")[:40]
    if not commit_sha:
        raise SnapshotError("Could not determine the repository commit.")

    # 3) Recursive tree listing -> candidate .py blobs, bounded.
    tree = _get_json(
        session,
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1",
        deadline,
    )
    candidates = []
    skipped = []
    total_bytes = 0
    for entry in tree.get("tree", []):
        if entry.get("type") != "blob":
            continue
        path = entry.get("path") or ""
        if not path.endswith(".py"):
            continue
        if is_sensitive_path(path):
            skipped.append(f"{path} (sensitive file excluded)")
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > SNAPSHOT_MAX_FILE_BYTES:
            skipped.append(f"{path} (file exceeds size limit)")
            continue
        if len(candidates) >= SNAPSHOT_MAX_FILES:
            skipped.append(f"{path} (file count limit reached)")
            continue
        if total_bytes + size > SNAPSHOT_MAX_TOTAL_BYTES:
            skipped.append(f"{path} (total size limit reached)")
            continue
        blob_sha = entry.get("sha")
        if not blob_sha:
            continue
        candidates.append((path, blob_sha))
        total_bytes += size

    if tree.get("truncated"):
        skipped.append(
            "(GitHub reported a truncated tree listing; "
            "some files were not considered)"
        )

    # 4) Download the bounded blob set.
    files = {}
    for path, blob_sha in candidates:
        blob = _get_json(
            session,
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/blobs/{blob_sha}",
            deadline,
        )
        if blob.get("encoding") != "base64":
            skipped.append(f"{path} (unsupported blob encoding)")
            continue
        try:
            content = base64.b64decode(
                blob.get("content") or "", validate=False
            ).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            skipped.append(f"{path} (undecodable content)")
            continue
        files[path] = content

    if not files:
        raise SnapshotError(
            "No analyzable Python files were found in the repository snapshot."
        )

    return Snapshot(
        owner=owner,
        repo=repo,
        commit_sha=commit_sha,
        files=files,
        skipped=skipped,
    )
