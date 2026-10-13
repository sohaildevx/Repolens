"""GitHub REST API client for repository metadata.

Security design (SSRF prevention):
- We NEVER fetch the user-supplied URL directly.
- We parse and validate it strictly (exact host match, no userinfo, no ports,
  no IP addresses), then build our own request to the hardcoded
  https://api.github.com/repos/{owner}/{repo} endpoint.
- The outbound hostname is always "api.github.com" -- never user-controlled.

Error taxonomy:
- GitHubRepoNotFound: 404 from API (nonexistent or private).
- GitHubRateLimited: 403/429 from API (rate limit exceeded).
- GitHubTimeout: request timed out (network issue).
- GitHubAPIError: any other API failure (5xx, connection error, etc.).

Each carries a user-safe message -- no stack traces, no credentials.
"""

import os
import re
from urllib.parse import urlparse

import requests
from django.conf import settings

# Hardcoded GitHub API base -- never derived from user input.
GITHUB_API_BASE = "https://api.github.com"

# Conservative timeout: 5s connect, 10s total read.
REQUEST_TIMEOUT = (5, 10)

# Accept only exact github.com host -- no subdomains, no IP, no localhost.
ALLOWED_HOSTS = {"github.com"}

# GitHub owner: alphanumeric + hyphens, cannot start/end with hyphen.
# GitHub repo: alphanumeric + hyphens + underscores + dots.
# Both 1-100 chars (GitHub's actual limits are ~39/100).
OWNER_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$"
REPO_PATTERN = r"^[A-Za-z0-9._-]{1,100}$"


class GitHubError(Exception):
    """Base for all GitHub API failures."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class GitHubRepoNotFound(GitHubError):
    """Repository does not exist or is private."""


class GitHubRateLimited(GitHubError):
    """GitHub API rate limit exceeded."""


class GitHubTimeout(GitHubError):
    """Request to GitHub API timed out."""


class GitHubAPIError(GitHubError):
    """Any other GitHub API failure (5xx, connection refused, etc.)."""


def parse_github_repo_url(url):
    """Parse and validate a GitHub repository URL.

    Accepts only https://github.com/{owner}/{repo}[/] [optional .git suffix].
    Rejects: wrong scheme, wrong host, userinfo, ports, path depth != 2,
    IP addresses, localhost, subdomains, invalid owner/repo names.

    Returns:
        tuple[str, str]: (owner, repo_name) -- repo_name has .git stripped.

    Raises:
        ValueError: URL is invalid or unsupported.
    """
    if not url or not isinstance(url, str):
        raise ValueError("Repository URL is required.")

    url = url.strip()
    parsed = urlparse(url)

    # Scheme must be https (not http, not ftp, not file).
    if parsed.scheme != "https":
        raise ValueError("URL must use https:// scheme.")

    # Host must be exactly github.com -- no userinfo, no ports, no IP.
    # urlparse puts userinfo in parsed.username/password, port in parsed.port.
    if parsed.username or parsed.password:
        raise ValueError("URL must not contain userinfo (user@host).")
    if parsed.port:
        raise ValueError("URL must not specify a port.")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("Only github.com repositories are supported.")

    # Path must be /{owner}/{repo} with optional trailing slash and optional
    # .git suffix on repo. Reject deeper paths (e.g., /tree/main).
    path = parsed.path.rstrip("/")
    parts = path.lstrip("/").split("/")
    if len(parts) != 2:
        raise ValueError(
            "URL must be a repository root: https://github.com/owner/repo"
        )

    owner, repo = parts

    # Strip .git suffix if present (git clone style).
    if repo.endswith(".git"):
        repo = repo[:-4]

    # Validate owner/repo charset and length.
    if not re.match(OWNER_PATTERN, owner):
        raise ValueError(f"Invalid GitHub owner name: {owner!r}")
    if not re.match(REPO_PATTERN, repo):
        raise ValueError(f"Invalid GitHub repository name: {repo!r}")

    return owner, repo


def fetch_repository_metadata(owner, repo):
    """Fetch repository metadata from GitHub REST API.

    Builds the request URL ourselves from validated owner/repo components --
    never uses the user-supplied URL string.

    Returns:
        dict: {"name", "default_branch", "description", "github_url"}

    Raises:
        GitHubRepoNotFound, GitHubRateLimited, GitHubTimeout, GitHubAPIError
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RepoLens",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Optional auth token from environment -- increases rate limit from
    # 60/hr to 5000/hr. Never logged, never returned in responses.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.Timeout:
        raise GitHubTimeout("GitHub API request timed out. Try again later.")
    except requests.RequestException:
        # Connection refused, DNS failure, SSL error, etc.
        raise GitHubAPIError("Could not connect to GitHub API.")

    # 404: nonexistent or private (GitHub doesn't distinguish to unauthenticated
    # clients -- good for privacy).
    if resp.status_code == 404:
        raise GitHubRepoNotFound(
            "Repository not found or is private.",
            status_code=404,
        )

    # 403/429: rate limit exceeded.
    if resp.status_code in (403, 429):
        # Check remaining quota header if present.
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining == "0" or resp.status_code == 429:
            raise GitHubRateLimited(
                "GitHub API rate limit exceeded. Try again later.",
                status_code=429,
            )
        # 403 that isn't rate limit -- likely repo is private or access denied.
        raise GitHubRepoNotFound(
            "Repository not found or is private.",
            status_code=403,
        )

    # Any other non-200 response.
    if resp.status_code != 200:
        raise GitHubAPIError(
            "GitHub API returned an unexpected error. Try again later.",
            status_code=resp.status_code,
        )

    try:
        data = resp.json()
    except ValueError:
        raise GitHubAPIError("GitHub API returned invalid JSON.")

    # Extract the fields we need -- never store the full API response.
    return {
        "name": data.get("name", repo),
        "default_branch": data.get("default_branch", "main"),
        "description": data.get("description") or "",
        # Canonical GitHub URL from the API response (not user input).
        "github_url": data.get("html_url", f"https://github.com/{owner}/{repo}"),
    }
