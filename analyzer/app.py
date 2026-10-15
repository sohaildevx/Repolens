"""RepoLens analyzer: a minimal Flask service for static analysis.

POST /analyze
    Body: {"files": [{"path": "src/app.py", "content": "..."}, ...]}
    Returns: {"findings": [{file_path, line_number, rule, severity,
                            description}, ...], "meta": {...}}

Security posture:
- Binds to 127.0.0.1 only (never 0.0.0.0) -- trusted components only.
- Validates file count, per-file size, total size, and path shape.
- Rejects sensitive filenames (.env, *.pem, id_rsa, ...) outright.
- Writes files ONLY to a per-request tempdir that is deleted afterwards.
- Never imports or executes submitted code.
- Returns generic error messages; no stack traces leak to clients.
"""

import tempfile
from pathlib import Path, PurePosixPath

from flask import Flask, jsonify, request

from engine import (
    MAX_FILES,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    analyze,
    is_sensitive_path,
)

app = Flask(__name__)


class ValidationError(Exception):
    """Raised on bad input; caught once at the endpoint to return 400."""


def _error(message: str, status: int):
    """Uniform error envelope -- no stack traces, no internals."""
    return jsonify({"error": message}), status


def _validate_path(rel_path) -> str:
    """Validate one submitted file path; return it normalized.

    Rules: non-empty string, relative, no traversal (..), no absolute
    roots, no Windows drives/backslashes, no null bytes, <= 500 chars,
    <= 10 levels deep, not a sensitive filename.

    Raises ValidationError with a human-readable message on any violation.
    """
    if not rel_path or not isinstance(rel_path, str):
        raise ValidationError("Every file needs a non-empty 'path'.")
    if "\x00" in rel_path:
        raise ValidationError("Path contains null bytes.")
    if len(rel_path) > 500:
        raise ValidationError(f"Path too long (>500 chars): {rel_path[:60]}...")
    if "\\" in rel_path:
        raise ValidationError(f"Use forward slashes in paths: {rel_path}")

    p = PurePosixPath(rel_path)
    if p.is_absolute():
        raise ValidationError(f"Absolute paths are not allowed: {rel_path}")
    if ".." in p.parts:
        raise ValidationError(f"Path traversal ('..') is not allowed: {rel_path}")
    if len(p.parts) > 10:
        raise ValidationError(f"Path nesting too deep (>10 levels): {rel_path}")
    if is_sensitive_path(rel_path):
        raise ValidationError(
            f"Sensitive files are excluded from analysis: {Path(rel_path).name}"
        )

    return p.as_posix()


@app.post("/analyze")
def analyze_endpoint():
    """Analyze a controlled set of Python files and return findings."""
    payload = request.get_json(silent=True)
    if payload is None:
        return _error("Request body must be JSON.", 400)

    files_raw = payload.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        return _error("'files' must be a non-empty list.", 400)
    if len(files_raw) > MAX_FILES:
        return _error(
            f"Too many files ({len(files_raw)}); limit is {MAX_FILES}.", 400
        )

    total_bytes = 0
    files: dict[str, str] = {}

    try:
        for i, entry in enumerate(files_raw):
            if not isinstance(entry, dict):
                raise ValidationError(f"files[{i}] must be an object.")

            norm_path = _validate_path(entry.get("path"))

            content = entry.get("content")
            if not isinstance(content, str):
                raise ValidationError(f"files[{i}].content must be a string.")

            size = len(content.encode("utf-8", errors="replace"))
            if size > MAX_FILE_BYTES:
                raise ValidationError(
                    f"File '{norm_path}' is {size} bytes; "
                    f"limit is {MAX_FILE_BYTES}."
                )
            total_bytes += size
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValidationError(
                    f"Total payload exceeds {MAX_TOTAL_BYTES} bytes."
                )

            if norm_path in files:
                raise ValidationError(f"Duplicate path: {norm_path}")
            files[norm_path] = content
    except ValidationError as exc:
        return _error(str(exc), 400)

    if not files:
        return _error("No analyzable files after validation.", 400)

    # Sandbox: write files only into a tempdir we created, analyze, delete.
    try:
        with tempfile.TemporaryDirectory(prefix="repolens-analyze-") as tmp:
            sandbox = Path(tmp)
            for rel, content in files.items():
                target = sandbox / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                # Binary-safe write; tools handle encoding detection.
                target.write_bytes(content.encode("utf-8", errors="replace"))

            findings = analyze(files, sandbox)
    except Exception:
        # Never leak internals; a generic 500 keeps stack traces server-side.
        return _error("Analysis failed unexpectedly.", 500)

    return jsonify({
        "findings": findings,
        "meta": {
            "files_submitted": len(files),
            "findings_count": len(findings),
        },
    })


@app.get("/health")
def health():
    """Liveness probe for the Django side and for ops tooling."""
    return jsonify({"status": "ok"})


# Bind to loopback only. In production this service sits behind the Django
# host (or a private network); it is never exposed to the internet.
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001)
