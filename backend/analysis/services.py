"""Django-side client for the Flask analyzer service.

This is the only place Django knows Flask exists. The view layer calls
`run_analysis(repository, files)` and gets back a finished AnalysisRun
with Issues saved -- or a typed exception it can map to an HTTP status.

Flow:
    run_analysis()
      1. creates an AnalysisRun (status=running, start_time set)
      2. POSTs the files to ANALYZER_URL/analyze with a hard timeout
      3. on success: saves Issue rows, marks the run completed
      4. on any failure: marks the run failed with a safe summary message
         (never leaves a run stuck in "running")

No stack traces or credentials ever leave this module.
"""

import logging

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import AnalysisRun, Issue, RunStatus

logger = logging.getLogger(__name__)


class AnalyzerError(Exception):
    """Base for analyzer-communication failures."""


class AnalyzerTimeout(AnalyzerError):
    """The analyzer did not respond in time."""


class AnalyzerUnavailable(AnalyzerError):
    """The analyzer returned an error or could not be reached."""


def call_analyzer(files: list[dict]) -> dict:
    """POST files to the Flask service and return its JSON response.

    `files` is a list of {"path": str, "content": str} dicts.

    Raises AnalyzerTimeout / AnalyzerUnavailable on failure.
    """
    url = settings.ANALYZER_URL.rstrip("/") + "/analyze"
    timeout = settings.ANALYZER_TIMEOUT_SECONDS

    # Shared-secret header (both sides read ANALYZER_TOKEN). When unset,
    # header is None and the analyzer runs open on loopback (dev mode).
    token = getattr(settings, "ANALYZER_TOKEN", "") or ""
    headers = {"X-Analyzer-Token": token} if token else None

    try:
        response = requests.post(
            url,
            json={"files": files},
            timeout=timeout,
            headers=headers,
        )
    except requests.Timeout:
        raise AnalyzerTimeout(
            f"Analyzer did not respond within {timeout} seconds."
        )
    except requests.RequestException:
        # Connection refused, DNS failure, etc.
        raise AnalyzerUnavailable("Analyzer service is unreachable.")

    if response.status_code != 200:
        # Never forward the analyzer's internal error text to clients;
        # log it server-side for debugging.
        logger.warning(
            "Analyzer returned %s: %.200s",
            response.status_code,
            response.text,
        )
        raise AnalyzerUnavailable(
            f"Analyzer returned status {response.status_code}."
        )

    try:
        payload = response.json()
    except ValueError:
        raise AnalyzerUnavailable("Analyzer returned invalid JSON.")

    if not isinstance(payload, dict) or "findings" not in payload:
        raise AnalyzerUnavailable("Analyzer response missing 'findings'.")

    return payload


def run_analysis(repository, files: list[dict], user=None) -> AnalysisRun:
    """Full pipeline: create run -> call analyzer -> save issues.

    Always leaves the run in a terminal state (completed or failed).
    Returns the run. Raises nothing -- failures are recorded on the run
    itself (status=failed, summary=reason).
    """
    run = AnalysisRun.objects.create(
        repository=repository,
        status=RunStatus.RUNNING,
        start_time=timezone.now(),
    )
    # Operational event: analysis started (counts only, no file contents).
    logger.info(
        "analysis run %s started for repository %s (%s file(s))",
        run.pk, repository.pk, len(files),
    )

    try:
        payload = call_analyzer(files)
        findings = payload.get("findings", [])
        _save_findings(run, findings)
        run.status = RunStatus.COMPLETED
        # Prefer Flask's own count: non-.py files were skipped, not analyzed.
        meta = payload.get("meta") or {}
        analyzed = meta.get("files_analyzed", len(files))
        run.summary = _build_summary(analyzed, findings)
        logger.info(
            "analysis run %s completed: %s file(s), %s finding(s)",
            run.pk, analyzed, len(findings),
        )
    except AnalyzerTimeout as exc:
        run.status = RunStatus.FAILED
        run.summary = str(exc)
        logger.warning("Analyzer timeout for run %s: %s", run.pk, exc)
    except AnalyzerUnavailable as exc:
        run.status = RunStatus.FAILED
        run.summary = str(exc)
        logger.warning("Analyzer failure for run %s: %s", run.pk, exc)
    except Exception:
        # Catch-all: any bug here must not leave the run stuck in "running".
        run.status = RunStatus.FAILED
        run.summary = "Internal error while saving analysis results."
        logger.exception("Unexpected error saving run %s", run.pk)

    run.completion_time = timezone.now()
    run.save()
    return run


def _save_findings(run: AnalysisRun, findings: list[dict]) -> None:
    """Bulk-create Issue rows from analyzer findings.

    Runs inside a transaction so a partial write never persists.
    Line numbers from the analyzer are clamped to the model's >= 1
    constraint -- a tool reporting line 0 must not crash the save.
    """
    issues = []
    for f in findings:
        issues.append(
            Issue(
                analysis_run=run,
                file_path=str(f.get("file_path", "?"))[:1024],
                line_number=max(int(f.get("line_number") or 1), 1),
                severity=f.get("severity", "info"),
                rule=str(f.get("rule", ""))[:50],
                source=str(f.get("source", ""))[:20],
                description=str(f.get("description", ""))[:10000],
            )
        )
    with transaction.atomic():
        Issue.objects.bulk_create(issues)


def _build_summary(file_count: int, findings: list[dict]) -> str:
    """One-line human summary stored on the run."""
    by_severity: dict[str, int] = {}
    for f in findings:
        sev = f.get("severity", "info")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    parts = [
        f"{sev}: {count}"
        for sev, count in sorted(by_severity.items())
    ]
    detail = ", ".join(parts) if parts else "no issues"
    return (
        f"Analyzed {file_count} file(s); "
        f"{len(findings)} finding(s) ({detail})."
    )
