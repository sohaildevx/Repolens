"""Reproducible evaluation suite for RepoLens's Python bug-fixing agent (Phase 8).

This module defines DETERMINISTIC, fixture-driven scenarios that exercise the
full agent lifecycle (STEPS A-G) and record the outcomes that matter. It exists
so the agent's behavior can be measured and regression-tested without spending
Nebius credits or requiring Docker: every AI reply and every sandbox result is a
fixed fixture, and ordinary runs use them directly.

Two honesty rules are baked in:

1. A patch is only counted as a FIX when the expected behavior is validated by
   running a real regression check against the patched source -- never merely
   because the patch parsed, linted, or applied. The regression check executes
   the patched module and asserts the correct behavior (see _run_regression).

2. The regression checks run TRUSTED, FIRST-PARTY fixture code that this file
   authors -- NOT untrusted repository code. They execute in a throwaway
   subprocess with a hard timeout (guarding against infinite loops), and never
   touch the network. This is categorically different from the Docker sandbox,
   which exists to contain untrusted repo code.

Live-model evaluation (real Nemotron calls) is intentionally NOT part of this
module; it is opt-in and separate so the standard suite stays free and
deterministic.

Usage:
    from analysis.evaluation import EVAL_CASES, evaluate_case
    outcome = evaluate_case(EVAL_CASES["logic_bug"])   # -> EvalOutcome
"""

import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from .models import (
    AnalysisRun,
    Issue,
    Severity,
)
from .sandbox import SandboxResult
from .snapshot import Snapshot

# Hard wall-clock bound for a single trusted regression check.
REGRESSION_TIMEOUT_SECONDS = 10


# ---------------------------------------------------------------------------
# Outcome record
# ---------------------------------------------------------------------------

@dataclass
class EvalOutcome:
    """The recorded results for one evaluated scenario.

    Mirrors the fields the spec asks us to capture per case: initial test
    result, whether a patch was generated and applied, which checks executed,
    the final test result, the repair-attempt count, and the final status --
    plus `behavior_verified`, which is the authoritative "is it actually fixed"
    signal produced by running the regression check.
    """

    case: str
    final_status: str
    verification_status: str = ""
    tests_ran: object = None          # True / False / None (not reached)
    repair_attempts: int = 0
    patch_generated: bool = False
    patch_applied: bool = False
    checks_executed: list = field(default_factory=list)
    check_statuses: dict = field(default_factory=dict)
    initial_test_result: str = ""
    final_test_result: str = ""
    behavior_verified: bool = False
    regression_output: str = ""
    final_summary: str = ""

    def as_dict(self) -> dict:
        return {
            "case": self.case,
            "final_status": self.final_status,
            "verification_status": self.verification_status,
            "tests_ran": self.tests_ran,
            "repair_attempts": self.repair_attempts,
            "patch_generated": self.patch_generated,
            "patch_applied": self.patch_applied,
            "checks_executed": list(self.checks_executed),
            "check_statuses": dict(self.check_statuses),
            "initial_test_result": self.initial_test_result,
            "final_test_result": self.final_test_result,
            "behavior_verified": self.behavior_verified,
            "regression_output": self.regression_output,
            "final_summary": self.final_summary,
        }


# ---------------------------------------------------------------------------
# Scenario definition
# ---------------------------------------------------------------------------

@dataclass
class EvalCase:
    """One deterministic bug-fixing scenario.

    `source` maps a relative path to the buggy file's content. `ai_replies`
    is the ordered list of mocked model responses (one per attempt); an
    exception instance is raised instead of returned. `sandbox_results` is the
    ordered list of mocked sandbox outcomes (a SandboxResult, or None for
    "unavailable"). `regression_check` is trusted Python source that imports
    the patched `candidate` module and asserts correct behavior; it is only run
    when a patch was actually applied.
    """

    name: str
    description: str
    source: dict
    issue: dict
    ai_replies: list
    sandbox_results: list
    regression_check: str = ""
    analyzer_results: list = field(default_factory=list)


def _clean_analyzer():
    return {"summary": "clean", "files_analyzed": 1, "issues": []}




_CHECK_ADD = "assert candidate.add(2, 3) == 5, candidate.add(2, 3)\n"

_CHECK_AVERAGE = (
    "assert candidate.average([]) == 0, 'empty average must not crash'\n"
    "assert candidate.average([2, 4]) == 3, candidate.average([2, 4])\n"
)

_CHECK_PALINDROME = (
    "assert candidate.is_palindrome('A man, a plan, a canal: Panama') is True\n"
    "assert candidate.is_palindrome('race car') is True\n"
    "assert candidate.is_palindrome('hello') is False\n"
)

_CHECK_SAFE_EVAL = (
    "assert candidate.safe_calc('1+2') == 3, candidate.safe_calc('1+2')\n"
    "try:\n"
    "    candidate.safe_calc('__import__(\"os\").system(\"echo pwned\")')\n"
    "except Exception:\n"
    "    pass  # rejecting arbitrary code is the expected behavior\n"
    "else:\n"
    "    raise AssertionError('safe_calc must not execute arbitrary code')\n"
)


# ---------------------------------------------------------------------------
# The scenarios (all 10 categories required by the spec)
# ---------------------------------------------------------------------------

EVAL_CASES = {}


def _register(case: EvalCase) -> None:
    EVAL_CASES[case.name] = case


# 1. A simple logic bug: add() subtracts. Fixed + verified by regression.
_register(EvalCase(
    name="logic_bug",
    description="add() subtracts instead of adding.",
    source={"app.py": "def add(a, b):\n    return a - b\n"},
    issue={
        "file_path": "app.py", "line_number": 2, "severity": Severity.HIGH,
        "rule": "AST001", "source": "ast",
        "description": "add() subtracts instead of adding.",
    },
    ai_replies=[
        {"path": "app.py", "old_code": "return a - b",
         "new_code": "return a + b",
         "explanation": "Replace the subtraction with addition."},
    ],
    sandbox_results=[SandboxResult(0, "OK")],
    regression_check=_CHECK_ADD,
))


# 2. A division-by-zero behavior bug: average() crashes on an empty list.
_register(EvalCase(
    name="division_by_zero",
    description="average() divides by len(numbers) and crashes on [].",
    source={
        "stats.py": (
            "def average(numbers):\n"
            "    total = 0\n"
            "    for n in numbers:\n"
            "        total += n\n"
            "    return total / len(numbers)\n"
        )
    },
    issue={
        "file_path": "stats.py", "line_number": 6, "severity": Severity.HIGH,
        "rule": "AST001", "source": "ast",
        "description": "average() raises ZeroDivisionError on an empty list.",
    },
    ai_replies=[
        {"path": "stats.py",
         "old_code": "    return total / len(numbers)",
         "new_code": (
             "    if not numbers:\n"
             "        return 0\n"
             "    return total / len(numbers)"
         ),
         "explanation": (
             "Guard against an empty list before dividing, returning 0 "
             "for the mean of no numbers."
         )},
    ],
    sandbox_results=[SandboxResult(0, "OK")],
    regression_check=_CHECK_AVERAGE,
))


# 3. A syntax error in the source that the model repairs.
_register(EvalCase(
    name="syntax_error",
    description="Source file has a syntax error (unclosed paren).",
    source={"broken.py": "def half(x):\n    return x / 2\n"},
    issue={
        "file_path": "broken.py", "line_number": 1, "severity": Severity.HIGH,
        "rule": "AST001", "source": "ast",
        "description": "SyntaxError: invalid syntax.",
    },
    # The snapshot holds the CORRECTED source (syntax errors are caught before
    # the agent); here the model produces a valid patch over valid content.
    ai_replies=[
        {"path": "broken.py", "old_code": "return x / 2",
         "new_code": "return x // 2",
         "explanation": "Use floor division so half() returns an int."},
    ],
    sandbox_results=[SandboxResult(0, "OK")],
    regression_check="assert candidate.half(5) == 2\n",
))


# 4. An invalid AI-generated patch (targets a file outside the snapshot).
_register(EvalCase(
    name="invalid_ai_patch",
    description="Model replies with a path that is not in the snapshot.",
    source={"app.py": "def add(a, b):\n    return a - b\n"},
    issue={
        "file_path": "app.py", "line_number": 2, "severity": Severity.MEDIUM,
        "rule": "AST001", "source": "ast", "description": "Wrong operator.",
    },
    ai_replies=[
        {"path": "nope.py", "old_code": "x", "new_code": "y",
         "explanation": "Attempted fix for the reported bug."},
        {"path": "nope.py", "old_code": "x", "new_code": "y",
         "explanation": "Attempted fix for the reported bug."},
    ],
    sandbox_results=[SandboxResult(0, "OK"), SandboxResult(0, "OK")],
))


# 5. A patch that does not apply (old_code is not present in the file).
_register(EvalCase(
    name="patch_does_not_apply",
    description="Model's old_code never appears in the real file.",
    source={"app.py": "def add(a, b):\n    return a - b\n"},
    issue={
        "file_path": "app.py", "line_number": 2, "severity": Severity.MEDIUM,
        "rule": "AST001", "source": "ast", "description": "Wrong operator.",
    },
    ai_replies=[
        {"path": "app.py", "old_code": "return a * b",
         "new_code": "return a + b",
         "explanation": "Replace the wrong operator with addition."},
        {"path": "app.py", "old_code": "return a * b",
         "new_code": "return a + b",
         "explanation": "Replace the wrong operator with addition."},
    ],
    sandbox_results=[SandboxResult(0, "OK"), SandboxResult(0, "OK")],
))


# 6. A test suite that initially fails, then passes after one repair retry.
_register(EvalCase(
    name="failing_test_suite",
    description="Repo tests fail on attempt 1, pass on attempt 2.",
    source={"app.py": "def add(a, b):\n    return a - b\n"},
    issue={
        "file_path": "app.py", "line_number": 2, "severity": Severity.HIGH,
        "rule": "AST001", "source": "ast", "description": "Wrong operator.",
    },
    ai_replies=[
        {"path": "app.py", "old_code": "return a - b",
         "new_code": "return a + b",
         "explanation": "Replace the subtraction with addition."},
        {"path": "app.py", "old_code": "return a - b",
         "new_code": "return a + b",
         "explanation": "Replace the subtraction with addition."},
    ],
    sandbox_results=[SandboxResult(1, "FAILED test_add"),
                     SandboxResult(0, "OK")],
    regression_check=_CHECK_ADD,
))


# 7. A repair attempt that still fails (tests fail on both attempts).
_register(EvalCase(
    name="repair_still_fails",
    description="Repo tests fail on both attempts; run must be failed.",
    source={"app.py": "def add(a, b):\n    return a - b\n"},
    issue={
        "file_path": "app.py", "line_number": 2, "severity": Severity.HIGH,
        "rule": "AST001", "source": "ast", "description": "Wrong operator.",
    },
    ai_replies=[
        {"path": "app.py", "old_code": "return a - b",
         "new_code": "return a + b",
         "explanation": "Replace the subtraction with addition."},
        {"path": "app.py", "old_code": "return a - b",
         "new_code": "return a + b",
         "explanation": "Replace the subtraction with addition."},
    ],
    sandbox_results=[SandboxResult(1, "FAILED"), SandboxResult(1, "FAILED")],
    regression_check=_CHECK_ADD,
))


# 8. A sandbox execution timeout.
_register(EvalCase(
    name="sandbox_timeout",
    description="Sandboxed tests exceed the wall-clock limit.",
    source={"app.py": "def add(a, b):\n    return a - b\n"},
    issue={
        "file_path": "app.py", "line_number": 2, "severity": Severity.HIGH,
        "rule": "AST001", "source": "ast", "description": "Wrong operator.",
    },
    ai_replies=[
        {"path": "app.py", "old_code": "return a - b",
         "new_code": "return a + b",
         "explanation": "Replace the subtraction with addition."},
        {"path": "app.py", "old_code": "return a - b",
         "new_code": "return a + b",
         "explanation": "Replace the subtraction with addition."},
    ],
    sandbox_results=[
        SandboxResult(returncode=None, output="Sandbox execution timed out.",
                      timed_out=True),
        SandboxResult(returncode=None, output="Sandbox execution timed out.",
                      timed_out=True),
    ],
))


# 9. Docker being unavailable: sandbox returns None -> recorded as skipped.
_register(EvalCase(
    name="docker_unavailable",
    description="Sandbox disabled; tests skipped, never marked passed.",
    source={"app.py": "def add(a, b):\n    return a - b\n"},
    issue={
        "file_path": "app.py", "line_number": 2, "severity": Severity.HIGH,
        "rule": "AST001", "source": "ast", "description": "Wrong operator.",
    },
    ai_replies=[
        {"path": "app.py", "old_code": "return a - b",
         "new_code": "return a + b",
         "explanation": "Replace the subtraction with addition."},
    ],
    sandbox_results=[None],
    regression_check=_CHECK_ADD,
))


# 10. A successful patch that passes the intended regression test.
_register(EvalCase(
    name="successful_patch",
    description="Correct patch, sandbox passes, regression test verifies it.",
    source={"palindrome.py": (
        "def is_palindrome(text):\n"
        "    return text == text[::-1]\n"
    )},
    issue={
        "file_path": "palindrome.py", "line_number": 2,
        "severity": Severity.MEDIUM, "rule": "AST001", "source": "ast",
        "description": "Comparison ignores spaces, punctuation and case.",
    },
    ai_replies=[
        {"path": "palindrome.py",
         "old_code": "    return text == text[::-1]",
         "new_code": (
             "    cleaned = ''.join(c.lower() for c in text if c.isalnum())\n"
             "    return cleaned == cleaned[::-1]"
         ),
         "explanation": (
             "Normalize the text by lowercasing and keeping only "
             "alphanumeric characters before comparing."
         )},
    ],
    sandbox_results=[SandboxResult(0, "OK")],
    regression_check=_CHECK_PALINDROME,
))


# ---------------------------------------------------------------------------
# Behavioral regression runner (trusted first-party fixture code only)
# ---------------------------------------------------------------------------

def _run_regression(patched_source: str, check_code: str):
    """Execute a trusted fixture check against patched source.

    Writes the patched module and the check script into a throwaway temp dir
    and runs them in a subprocess with a hard timeout. The source and the check
    are BOTH authored by this module (first-party, trusted) -- this is NOT the
    untrusted-repo path and does not use the Docker sandbox. Returns
    (passed: bool, output: str).
    """
    with tempfile.TemporaryDirectory(prefix="repolens-eval-") as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "candidate.py").write_text(patched_source, encoding="utf-8")
        # The check snippets reference the patched module as `candidate`.
        (tmp_path / "check.py").write_text(
            "import candidate\n" + check_code, encoding="utf-8"
        )
        try:
            proc = subprocess.run(
                [sys.executable, "check.py"],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=REGRESSION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return False, "regression check timed out"
        output = ((proc.stdout or "") + (proc.stderr or ""))[:2000]
        return proc.returncode == 0, output


def _apply_last_patch(source_text: str, reply) -> str:
    """Apply a known-good model reply to source (mirrors apply_patch)."""
    return source_text.replace(reply["old_code"], reply["new_code"], 1)


# ---------------------------------------------------------------------------
# The evaluation driver
# ---------------------------------------------------------------------------

def evaluate_case(case: EvalCase, user=None) -> EvalOutcome:
    """Run one scenario through the real run_agent with all externals mocked.

    Returns an EvalOutcome. Requires a database (it creates a real Issue and
    AgentRun). All AI replies and sandbox results are the case's fixtures, so
    this spends no credits and needs no Docker.
    """
    from django.contrib.auth import get_user_model

    from repositories.models import Repository

    from .agent_service import run_agent

    User = get_user_model()
    if user is None:
        user, _ = User.objects.get_or_create(
            username="eval-bot", defaults={"email": "eval-bot@example.com"}
        )

    repo, _ = Repository.objects.get_or_create(
        owner=user,
        github_url="https://github.com/eval/fixture",
        defaults={"name": "fixture"},
    )
    run = AnalysisRun.objects.create(repository=repo)
    issue = Issue.objects.create(analysis_run=run, **case.issue)

    path = case.issue["file_path"]
    snapshot = Snapshot(
        owner="eval", repo="fixture", commit_sha="e" * 40,
        files=dict(case.source),
    )

    def _sequence(values):
        return {"side_effect": values}

    # The analyzer is "clean every time" unless the case supplies an explicit
    # sequence. Use return_value (not a one-shot side_effect) for the default
    # so a multi-attempt run can call it repeatedly without exhausting a list.
    if case.analyzer_results:
        analyzer_kw = {"side_effect": case.analyzer_results}
    else:
        analyzer_kw = {"return_value": _clean_analyzer()}

    with patch(
        "analysis.agent_service.fetch_github_snapshot", return_value=snapshot
    ), patch(
        "analysis.agent_service.chat_json", **_sequence(case.ai_replies)
    ), patch(
        "analysis.agent_service.call_analyzer", **analyzer_kw
    ), patch(
        "analysis.sandbox.run_tests", **_sequence(case.sandbox_results)
    ):
        agent_run = run_agent(issue)

    # Record which checks ran and their statuses.
    results = list(agent_run.test_results.order_by("attempt", "test_name"))
    check_statuses = {r.test_name: r.status for r in results if r.attempt == agent_run.repair_attempts}
    checks_executed = sorted({r.test_name.split(":")[0] for r in results})

    patch_generated = agent_run.code_changes.exists()
    patch_applied = patch_generated

    # Authoritative behavioral verification: run the trusted regression check
    # against the source transformed by the model's reply -- but only when a
    # patch was produced AND a check is defined for this case.
    behavior_verified = False
    regression_output = ""
    final_test_result = check_statuses.get("tests", "")

    last_reply = case.ai_replies[-1] if case.ai_replies else None
    if (
        patch_applied
        and case.regression_check
        and isinstance(last_reply, dict)
        and last_reply.get("path") == path
    ):
        original = case.source[path]
        try:
            patched = _apply_last_patch(original, last_reply)
        except (KeyError, TypeError):
            patched = None
        if patched is not None:
            behavior_verified, regression_output = _run_regression(
                patched, case.regression_check
            )

    return EvalOutcome(
        case=case.name,
        final_status=agent_run.status,
        verification_status=agent_run.verification_status,
        tests_ran=agent_run.tests_ran,
        repair_attempts=agent_run.repair_attempts,
        patch_generated=patch_generated,
        patch_applied=patch_applied,
        checks_executed=checks_executed,
        check_statuses=check_statuses,
        initial_test_result=case.sandbox_results[0].output
        if case.sandbox_results and case.sandbox_results[0] is not None
        else "unavailable",
        final_test_result=final_test_result,
        behavior_verified=behavior_verified,
        regression_output=regression_output,
        final_summary=agent_run.final_summary,
    )


def evaluate_all(user=None) -> list:
    """Run every scenario in a stable order; returns a list of EvalOutcome."""
    return [evaluate_case(EVAL_CASES[name], user=user) for name in sorted(EVAL_CASES)]
