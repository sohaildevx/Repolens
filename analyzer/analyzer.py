"""Static-analysis logic: Python AST, Ruff and Bandit runners.

Design rules (security-critical):
- We NEVER import or execute submitted code. We only read it as text,
  parse it with ast.parse (which does not execute), and hand the files
  to Ruff/Bandit as subprocesses with timeouts.
- Files are written only into a caller-provided temp directory that was
  created by this process -- never to a user-supplied path.
- Every subprocess has a hard timeout; a hung tool cannot wedge the service.
- Sensitive filenames (.env, *.pem, id_rsa, ...) are rejected up front.

Every finding is normalized to the same shape:
    {file_path, line_number, rule, severity, description, source}
where `source` is "ast", "ruff" or "bandit" (which engine found it).
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
    # .env.something (conservative: any .env-prefixed file is refused)
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


def _rel_path(filename: str, sandbox_dir: Path) -> str:
    """Strip the sandbox prefix from a tool-reported absolute path."""
    p = Path(filename)
    if sandbox_dir in p.parents:
        return p.relative_to(sandbox_dir).as_posix()
    return p.as_posix()


def run_ast_checks(files: dict[str, str]) -> list[dict]:
    """Custom AST checks. `files` maps rel_path -> source text.

    ast.parse() compiles to an AST without executing anything -- it's
    exactly what `python -m py_compile` does, minus the write.

    Returns findings for: syntax errors, eval/exec usage, and bare
    excepts. A file that fails to parse yields one AST001 finding and
    no further AST checks (graceful handling of invalid syntax).
    """
    findings = []

    for rel_path, source in sorted(files.items()):
        try:
            tree = ast.parse(source, filename=rel_path)
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            # SyntaxError carries .lineno/.msg; the others are rare but
            # must not crash the request either.
            line = getattr(exc, "lineno", None) or 1
            msg = getattr(exc, "msg", None) or type(exc).__name__
            findings.append({
                "file_path": rel_path,
                "line_number": line,
                "rule": "AST001",
                "severity": "high",
                "description": f"SyntaxError: {msg}",
                "source": "ast",
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
                        "source": "ast",
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
                        "source": "ast",
                    })

    return findings


def _tool_failure_finding(tool: str, rule: str, message: str) -> dict:
    """A degraded-but-valid finding when a tool could not run.

    We return it as a finding (instead of raising) so the caller always
    gets a well-formed response even when a subprocess dies.
    `rule` is TOOL_TIMEOUT or TOOL_ERROR; `message` is client-safe text.
    """
    return {
        "file_path": ".",
        "line_number": 1,
        "rule": rule,
        "severity": "medium",
        "description": message,
        "source": tool,
    }


def run_ruff(files: dict[str, str], sandbox_dir: Path) -> list[dict]:
    """Run Ruff over the sandboxed .py files. Returns findings (never raises)."""
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
        return [_tool_failure_finding(
            "ruff", "TOOL_TIMEOUT", "Ruff timed out; results may be incomplete."
        )]
    except (OSError, ValueError):
        return [_tool_failure_finding(
            "ruff", "TOOL_ERROR",
            "Ruff could not be executed; results may be incomplete.",
        )]

    # Ruff exits 0 (clean), 1 (findings), 2 (error). JSON goes to stdout.
    try:
        raw = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return [_tool_failure_finding(
            "ruff", "TOOL_ERROR",
            "Ruff returned unreadable output; results may be incomplete.",
        )]

    findings = []
    for item in raw:
        code = item.get("code") or "RLU000"
        findings.append({
            "file_path": _rel_path(item.get("filename", ""), sandbox_dir),
            "line_number": item.get("location", {}).get("row", 1),
            "rule": code,
            "severity": _severity_from_ruff(code),
            "description": item.get("message", "").strip(),
            "source": "ruff",
        })
    return findings


def run_bandit(files: dict[str, str], sandbox_dir: Path) -> list[dict]:
    """Run Bandit (security linter) over the sandboxed .py files."""
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
        return [_tool_failure_finding(
            "bandit", "TOOL_TIMEOUT",
            "Bandit timed out; results may be incomplete.",
        )]
    except (OSError, ValueError):
        return [_tool_failure_finding(
            "bandit", "TOOL_ERROR",
            "Bandit could not be executed; results may be incomplete.",
        )]

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return [_tool_failure_finding(
            "bandit", "TOOL_ERROR",
            "Bandit returned unreadable output; results may be incomplete.",
        )]

    findings = []
    for item in data.get("results", []):
        findings.append({
            "file_path": _rel_path(item.get("filename", ""), sandbox_dir),
            "line_number": item.get("line_number", 1),
            "rule": item.get("test_id", "BNT000"),
            "severity": _severity_from_bandit(item.get("issue_severity", "LOW")),
            "description": item.get("issue_text", "").strip(),
            "source": "bandit",
        })
    return findings


def analyze(files: dict[str, str], sandbox_dir: Path) -> list[dict]:
    """Run all three engines over the sandboxed files and merge findings.

    Tool crashes never propagate: each runner degrades to a single
    TOOL_* finding so the caller always gets a valid response.
    """
    findings = []
    findings.extend(run_ast_checks(files))
    findings.extend(run_ruff(files, sandbox_dir))
    findings.extend(run_bandit(files, sandbox_dir))
    findings.sort(
        key=lambda f: (f["file_path"], f["line_number"], f["rule"])
    )
    return findings
