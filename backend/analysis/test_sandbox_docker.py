"""Real Docker sandbox integration tests (Phase 8, optional).

These are DELIBERATELY kept out of the ordinary unit suite's rhythm: they
launch real containers, so they run in their own module and skip (with an
explicit reason) whenever the environment cannot support them:

    - AGENT_TEST_EXECUTION is not enabled (explicit opt-in required),
    - the Docker CLI/daemon is unavailable,
    - the trusted image (SANDBOX_IMAGE) is not present locally.

Run them separately:

    cd backend
    python manage.py test analysis.test_sandbox_docker --noinput --keepdb

What they verify against the REAL Docker daemon (not mocks):

    1. a passing repository suite exits 0 inside the container,
    2. a failing repository suite exits non-zero (failures are not masked),
    3. the container has NO network (the --network=none restriction, proven
       by an outbound socket connect failing inside the container),
    4. application secrets from the host environment are NOT visible inside
       the container (empty-env restriction),
    5. a hanging suite produces a timeout result AND leaves no container
       behind (cleanup after timeout).

All fixture code in this file is FIRST-PARTY test code that we author; it
only ever executes INSIDE the restricted container, never on the Django host.
If these tests skip, real sandbox execution remains UNVERIFIED -- the unit
suite's mocked sandbox results do not prove container behavior.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from .sandbox import docker_available, image_present, run_tests

# A passing repository suite (what the sandbox discovers via test_*.py).
PASSING_TESTS = """\
import unittest

class DemoTests(unittest.TestCase):
    def test_math(self):
        self.assertEqual(1 + 1, 2)
"""

FAILING_TESTS = """\
import unittest

class DemoTests(unittest.TestCase):
    def test_intentionally_fails(self):
        self.assertEqual(1 + 1, 3)
"""

# Proves --network=none: an outbound connect to a public IP must fail.
# Uses an IP literal so no DNS lookup can mask the result.
NO_NETWORK_TESTS = """\
import socket
import unittest

class NetworkTests(unittest.TestCase):
    def test_outbound_connection_is_blocked(self):
        with self.assertRaises(OSError):
            socket.create_connection(("1.1.1.1", 80), timeout=3)
"""

# Proves the container env is empty of application secrets: none of these
# variable names may exist inside the container, even though the Django
# process that launched it may have them set.
NO_SECRETS_TESTS = """\
import os
import unittest

class SecretTests(unittest.TestCase):
    def test_application_secrets_are_not_visible(self):
        for name in ("NEBIUS_API_KEY", "SECRET_KEY", "DATABASE_URL",
                     "GITHUB_TOKEN", "ANALYZER_TOKEN"):
            self.assertIsNone(
                os.environ.get(name),
                f"host secret {name} leaked into the sandbox",
            )
"""

# Proves the wall-clock timeout: sleeps far longer than the test timeout.
HANGING_TESTS = """\
import time
import unittest

class HangTests(unittest.TestCase):
    def test_never_finishes(self):
        time.sleep(120)
"""


def _skip_reason():
    """Why real execution is impossible here, or None when it can run."""
    if not getattr(settings, "AGENT_TEST_EXECUTION", False):
        return ("AGENT_TEST_EXECUTION is not enabled -- real sandbox "
                "execution NOT verified (set it to 1 to opt in).")
    if not shutil.which("docker"):
        return "Docker CLI not found -- real sandbox execution NOT verified."
    if not docker_available():
        return "Docker daemon not running -- real sandbox execution NOT verified."
    if not image_present(settings.SANDBOX_IMAGE):
        return (f"trusted image {settings.SANDBOX_IMAGE} not present locally "
                "-- real sandbox execution NOT verified.")
    return None


def _leftover_containers():
    """Names of any repolens-agent containers that still exist."""
    exe = shutil.which("docker")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "ps", "-a", "--filter", "name=repolens-agent",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


class DockerSandboxIntegrationTests(SimpleTestCase):
    """Real-container checks; every test skips when the host can't run them."""

    def setUp(self):
        reason = _skip_reason()
        if reason:
            self.skipTest(reason)

    def _workspace_with(self, content):
        """Create a temp workspace containing one discovered test file."""
        tmp = tempfile.mkdtemp(prefix="repolens-docker-it-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (Path(tmp) / "test_suite.py").write_text(content, encoding="utf-8")
        return Path(tmp)

    def test_passing_suite_exits_zero(self):
        result = run_tests(self._workspace_with(PASSING_TESTS))
        self.assertIsNotNone(result, "sandbox refused to run (see logs)")
        self.assertFalse(result.timed_out)
        self.assertEqual(
            result.returncode, 0,
            f"passing suite must exit 0 inside the container:\n{result.output}",
        )

    def test_failing_suite_exits_nonzero(self):
        result = run_tests(self._workspace_with(FAILING_TESTS))
        self.assertIsNotNone(result)
        self.assertNotEqual(
            result.returncode, 0,
            "a failing repository suite must NOT be reported as success",
        )
        self.assertIn("FAIL", result.output.upper())

    def test_container_has_no_network(self):
        """--network=none, verified for real: outbound connect must fail."""
        result = run_tests(self._workspace_with(NO_NETWORK_TESTS))
        self.assertIsNotNone(result)
        self.assertEqual(
            result.returncode, 0,
            "outbound network from the sandbox must be blocked:\n"
            f"{result.output}",
        )

    def test_application_secrets_are_not_in_container_env(self):
        """The container env is empty: host secrets never reach the image."""
        result = run_tests(self._workspace_with(NO_SECRETS_TESTS))
        self.assertIsNotNone(result)
        self.assertEqual(
            result.returncode, 0,
            f"a host secret leaked into the sandbox:\n{result.output}",
        )

    @override_settings(SANDBOX_TIMEOUT_SECONDS=5)
    def test_hanging_suite_times_out_and_cleans_up(self):
        """Timeout is reported as a timeout (never a pass) and no container
        is left behind afterwards.

        @override_settings alone suffices: run_tests reads
        settings.SANDBOX_TIMEOUT_SECONDS at call time.
        """
        before = _leftover_containers()
        workspace = self._workspace_with(HANGING_TESTS)

        result = run_tests(workspace)

        self.assertIsNotNone(result)
        self.assertTrue(
            result.timed_out,
            f"expected timed_out=True, got returncode={result.returncode}",
        )
        self.assertIsNone(result.returncode)

        after = _leftover_containers()
        if before is not None and after is not None:
            new = [name for name in after if name not in before]
            self.assertEqual(
                new, [],
                f"containers leaked after timeout: {new}",
            )
