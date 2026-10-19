"""Patch validation and application for agent runs (Phase 7, STEPS C/D).

The AI model is asked for ONE minimal fix in JSON form:

    {"path": "pkg/module.py", "old_code": "...", "new_code": "..."}

Rules (all enforced here -- the model's output is untrusted data):
- "path" must exactly match one file in the snapshot, end with .py and not
  be a sensitive path. No traversal, no absolute paths, no new files.
- "old_code" must appear EXACTLY ONCE in the current file, otherwise the
  patch is ambiguous and is rejected.
- "new_code" must be present and differ from "old_code".

Patches are only ever applied to a temporary workspace handed in by the
caller; every write re-validates that the resolved path stays inside that
workspace. The user's GitHub repository is never touched.
"""

import difflib
from pathlib import Path

from .snapshot import is_sensitive_path

# Stored diffs are truncated well below this bound.
MAX_DIFF_CHARS = 20_000


class PatchError(Exception):
    """Invalid model patch; message is safe for logs and run summaries."""


def parse_patch(reply, snapshot) -> dict:
    """Validate the model's JSON reply against the snapshot.

    Returns {"path", "old_code", "new_code"} on success; raises PatchError
    with a safe message otherwise.
    """
    if not isinstance(reply, dict):
        raise PatchError("AI response was not a JSON object.")

    path = reply.get("path")
    old_code = reply.get("old_code")
    new_code = reply.get("new_code")

    if not isinstance(path, str) or not path:
        raise PatchError("AI patch is missing a file path.")
    if "\\" in path or path.startswith("/") or ".." in path.split("/"):
        raise PatchError("AI patch has an invalid file path.")
    if not path.endswith(".py"):
        raise PatchError("AI patch targets a non-Python file.")
    if is_sensitive_path(path):
        raise PatchError("AI patch targets a sensitive file.")
    if path not in snapshot.files:
        raise PatchError("AI patch targets a file outside the snapshot.")

    if not isinstance(old_code, str) or not old_code.strip():
        raise PatchError("AI patch is missing the original code block.")
    if not isinstance(new_code, str) or not new_code.strip():
        raise PatchError("AI patch is missing the replacement code block.")
    if new_code == old_code:
        raise PatchError("AI patch replacement is identical to the original.")

    occurrences = snapshot.files[path].count(old_code)
    if occurrences == 0:
        raise PatchError(
            "AI patch original code does not match the snapshot file."
        )
    if occurrences > 1:
        raise PatchError(
            "AI patch original code is ambiguous (matches more than once)."
        )
    return {"path": path, "old_code": old_code, "new_code": new_code}


def apply_patch(workspace: Path, path: str, old_code: str, new_code: str) -> str:
    """Apply a validated patch to the temporary workspace ONLY.

    Returns the new full-file content. Every safety property is re-checked
    here (defence in depth: even if a caller skipped parse_patch, this
    function refuses traversal and out-of-workspace writes).
    """
    workspace = Path(workspace)
    target = (workspace / path).resolve()
    try:
        target.relative_to(workspace.resolve())
    except ValueError:
        raise PatchError("Patch path escapes the workspace.")

    if not target.is_file():
        raise PatchError("Patch target does not exist in the workspace.")

    current = target.read_text(encoding="utf-8")
    if current.count(old_code) != 1:
        raise PatchError("Patch no longer matches the workspace file.")

    updated = current.replace(old_code, new_code, 1)
    target.write_text(updated, encoding="utf-8")
    return updated


def make_diff(path: str, before: str, after: str) -> str:
    """Unified (git-style) diff between two versions, bounded in size."""
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... [diff truncated] ..."
    return diff
