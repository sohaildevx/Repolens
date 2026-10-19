"""Docker-based validation sandbox for agent runs (Phase 7, STEP E).

PRINCIPLE: untrusted repository code NEVER executes directly on the Django
host. Test execution happens in a disposable, restricted container built
from a trusted, explicitly configured image. When Docker or the image is
unavailable -- or the feature is not explicitly enabled -- execution stays
DISABLED and callers must record results as skipped, never as passed.

Restrictions applied per run:
- trusted image only (settings.SANDBOX_IMAGE; repository-provided
  Dockerfiles are never executed and arbitrary images never pulled),
- no network (--network=none), no Docker socket, no host mounts except the
  single temporary workspace (rw) and a tmpfs /tmp,
- read-only root filesystem, all capabilities dropped, no-new-privileges,
- non-root user (nobody, uid 65534 by default),
- pids/memory/cpu limits and a hard wall-clock timeout,
- fixed command allowlist (python -m unittest only; no shell, no git,
  no pip, no package installation),
- empty application environment (only two fixed PY* variables passed),
- output size cap; container always removed (--rm plus a forced cleanup).

Remaining risks (documented, not claimed away): a container escape via a
kernel or runtime vulnerability would still be consequential; image
trustworthiness depends on its publisher; resource-limit enforcement
depends on the Docker daemon. This sandbox reduces -- it does not
eliminate -- risk.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from django.conf import settings

# Fixed command allowlist: only these exact argument shapes may ever run.
# Anything else (git, pip, curl, shell flags...) is structurally impossible.
ALLOWED_COMMANDS = (
    ("python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-q"),
)

# Raw captured output kept before per-result bounding by the caller.
OUTPUT_RAW_LIMIT = 20_000


class SandboxConfigurationError(Exception):
    """Refuse to run with an unsafe sandbox configuration (e.g. root user)."""


@dataclass
class SandboxResult:
    """Outcome of one sandboxed test execution."""

    returncode: int | None  # None when the run did not complete
    output: str  # bounded captured stdout/stderr
    timed_out: bool = False


def docker_available() -> bool:
    """True when the Docker CLI exists AND a daemon is running."""
    exe = shutil.which("docker")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def image_present(image: str) -> bool:
    """True when the trusted sandbox image is already available locally."""
    exe = shutil.which("docker")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "image", "inspect", image],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def build_run_command(exe, name, workspace, image, user) -> list:
    """Pure builder for the docker run argv (unit-testable)."""
    if str(user) in ("0", "root"):
        raise SandboxConfigurationError("Refusing to run the sandbox as root.")
    return [
        exe, "run",
        "--rm",
        "--name", name,
        "--network=none",
        "--read-only",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--user={user}",
        f"--pids-limit={settings.SANDBOX_PIDS_LIMIT}",
        f"--memory={settings.SANDBOX_MEMORY}",
        f"--cpus={settings.SANDBOX_CPUS}",
        "--stop-timeout=5",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PYTHONHASHSEED=0",
        "-v", f"{Path(workspace).resolve()}:/workspace:rw",
        "-w", "/workspace",
        image,
        *ALLOWED_COMMANDS[0],
    ]


def _force_remove(exe: str, name: str) -> None:
    """Best-effort guaranteed cleanup (stops and removes the container)."""
    try:
        subprocess.run(
            [exe, "rm", "-f", name], capture_output=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_tests(workspace) -> SandboxResult | None:
    """Run the workspace's unittest suite inside the restricted sandbox.

    Returns None when execution is not permitted in this environment
    (feature disabled, Docker unavailable, image missing, or unsafe user
    configuration). Callers MUST record that as a skipped result -- never
    as a pass.
    """
    if not settings.AGENT_TEST_EXECUTION:
        return None
    if not docker_available():
        return None
    image = settings.SANDBOX_IMAGE
    if not image_present(image):
        return None

    exe = shutil.which("docker")
    name = f"repolens-agent-{uuid4().hex[:12]}"
    try:
        command = build_run_command(
            exe, name, workspace, image, settings.SANDBOX_USER
        )
    except SandboxConfigurationError:
        return None

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=settings.SANDBOX_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            returncode=None,
            output="Sandbox execution timed out.",
            timed_out=True,
        )
    except OSError:
        return SandboxResult(returncode=None, output="Sandbox failed to start.")
    finally:
        # --rm covers clean exits; this guarantees removal after timeouts,
        # crashes and failed starts.
        _force_remove(exe, name)

    output = ((proc.stdout or "") + (proc.stderr or ""))[:OUTPUT_RAW_LIMIT]
    return SandboxResult(returncode=proc.returncode, output=output)
