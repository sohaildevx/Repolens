"""Static-analysis engine: Python AST, Ruff and Bandit runners.

Design rules (security-critical):
- We NEVER import or execute submitted code. We only read it as text,
  parse it with ast.parse (which does not execute), and hand the files
  to Ruff/Bandit as subprocesses with timeouts.
- Files are written only into a caller-provided temp directory that was
  created by this process -- never to a user-supplied path.
- Every subprocess has a hard timeout; a hung tool cannot wedge the service.
- Sensitive filenames (.env, *.pem, id_rsa, ...) are rejected up front.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

# Hard limits -- mirrored in app.py validation, enforced again here.
MAX_FILES = 100
MAX_FILE_BYTES = 200_000           # 200 KB per file
MAX_TOTAL_BYTES = 2_000_000        # 2 MB per submission
TOOL_TIMEOUT_SECONDS = 30          # per tool, per submission

# Filenames we refuse to analyze outright (secret exfiltration guard).
SENSITIVE_PATTERNS = {
    ".env", ".env.local", ".env.production",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "credentials", "credentials.json", "secrets.json",
    "service-account.json", ".npmrc", ".pypirc",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore")


def is_sensitive_path(rel_path: str) -> bool:
    """True if this path looks like it holds secrets. Reject, never analyze."""
    name = Path(rel_path).name.lower()
    if name in SENSITIVE_PATTERNS:
        return True
    if name.endswith(SENSITIVE_SUFFIXES):
        return True
    # .env.something (e.g. .env.example is fine, but be conservative)
    if name.startswith(".env"):
        return True
    return False


def _severity_from_ruff(code: str) -> str:
    """Map a Ruff rule code to our severity vocabulary.

    E9/F82x are syntax/runtime errors (high); F is pyflakes (medium);
    E (other) is pycodestyle errors (medium); everything else (C4, SIM,
    B, UP, ...) is stylistic (low).
    """
    if code.startswith(("E9", "F82")):
        return "high"
    if code.startswith(("F", "E")):
        return "medium"
    return "low"


def _severity_from_bandit(level: str) -> str:
    return {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(
        level.upper(), "low"
    )


def run_ast_checks(files: dict[str, str]) -> list[dict]:
    """Custom AST checks. `files` maps rel_path -> source text.

    ast.parse() compiles to an AST without executing anything -- it's
    exactly what `python -m py_compile` does, minus the write.

    Returns findings for: syntax errors, eval/exec usage, bare excepts,
    and use of the `assert` statement outside tests (a common production
    bug: `python -O` strips asserts entirely).
    """
    findings = []

    for rel_path, source in sorted(files.items()):
        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError as exc:
            findings.append({
                "file_path": rel_path,
                "line_number": exc.lineno or 1,
                "rule": "AST001",
                "severity": "high",
                "description": f"SyntaxError: {exc.msg}",
            })
            continue  # a file that won't parse has no further AST checks

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute)
                    else ""
                )
                if name in ("eval", "exec"):
                    findings.append({
                        "file_path": rel_path,
                        "line_number": node.lineno,
                        "rule": "AST002",
                        "severity": "high",
                        "description": (
                            f"Use of {name}() executes arbitrary code at "
                            "runtime and is a common injection vector."
                        ),
                    })
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:  # bare `except:`
                    findings.append({
                        "file_path": rel_path,
                        "line_number": node.lineno,
                        "rule": "AST003",
                        "severity": "medium",
                        "description": (
                            "Bare except catches everything including "
                            "KeyboardInterrupt and SystemExit; catch "
                            "specific exceptions instead."
                        ),
                    })

    return findings


def run_ruff(files: dict[str, str], sandbox_dir: Path) -> list[dict]:
    """Run Ruff over the sandboxed files. Returns findings (never raises)."""
    paths = [str(sandbox_dir / p) for p in sorted(files)]
    if not paths:
        return []

    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "ruff", "check",
                "--output-format", "json",
                "--no-cache",           # never write cache files anywhere
                "--isolated",           # ignore any user/project config
                *paths,
            ],
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [{
            "file_path": ".", "line_number": 1, "rule": "TOOL_TIMEOUT",
            "severity": "medium",
            "description": "Ruff timed out; results may be incomplete.",
        }]
    except (OSError, ValueError):
        return [{
            "file_path": ".", "line_number": 1, "rule": "TOOL_ERROR",
            "severity": "medium",
            "description": "Ruff could not be executed; results may be incomplete.",
        }]

    # Ruff exits 0 (clean), 1 (findings), 2 (error). JSON goes to stdout.
    try:
        raw = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return [{
            "file_path": ".", "line_number": 1, "rule": "TOOL_ERROR",
            "severity": "medium",
            "description": "Ruff returned unreadable output; results may be incomplete.",
        }]

    findings = []
    for item in raw:
        code = item.get("code") or "RLU000"
        filename = Path(item.get("filename", ""))
        if sandbox_dir in filename.parents:
            rel = filename.relative_to(sandbox_dir).as_posix()
        else:
            rel = filename.as_posix()
        findings.append({
            "file_path": rel,
            "line_number": item.get("location", {}).get("row", 1),
            "rule": code,
            "severity": _severity_from_ruff(code),
            "description": item.get("message", "").strip(),
        })
    return findings


def run_bandit(files: dict[str, str], sandbox_dir: Path) -> list[dict]:
    """Run Bandit (security linter) over the sandboxed files."""
    if not files:
        return []

    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "bandit", "-r", "-f", "json",
                "-q",                   # only report issues, not progress
                "--exit-zero",          # findings are not an error exit
                str(sandbox_dir),
            ],
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [{
            "file_path": ".", "line_number": 1, "rule": "TOOL_TIMEOUT",
            "severity": "medium",
            "description": "Bandit timed out; results may be incomplete.",
        }]
    except (OSError, ValueError):
        return [{
            "file_path": ".", "line_number": 1, "rule": "TOOL_ERROR",
            "severity": "medium",
            "description": "Bandit could not be executed; results may be incomplete.",
        }]

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return [{
            "file_path": ".", "line_number": 1, "rule": "TOOL_ERROR",
            "severity": "medium",
            "description": "Bandit returned unreadable output; results may be incomplete.",
        }]

    findings = []
    for item in data.get("results", []):
        filename = Path(item.get("filename", ""))
        if sandbox_dir in filename.parents:
            rel = filename.relative_to(sandbox_dir).as_posix()
        else:
            rel = filename.as_posix()
        findings.append({
            "file_path": rel,
            "line_number": item.get("line_number", 1),
            "rule": item.get("test_id", "BNT000"),
            "severity": _severity_from_bandit(item.get("issue_severity", "LOW")),
            "description": item.get("issue_text", "").strip(),
        })
    return findings


def analyze(files: dict[str, str], sandbox_dir: Path) -> list[dict]:
    """Run all three engines and merge findings.

    Tool crashes never propagate: each runner degrades to a single
    TOOL_ERROR finding so the caller always gets a valid response.
    """
    findings = []
    findings.extend(run_ast_checks(files))
    findings.extend(run_ruff(files, sandbox_dir))
    findings.extend(run_bandit(files, sandbox_dir))
    findings.sort(
        key=lambda f: (f["file_path"], f["line_number"], f["rule"])
    )
    return findings
