"""Tests for the Flask analyzer service.

Run from the analyzer/ directory:
    python -m pytest tests.py -v
or:
    python -m unittest tests -v

Covers:
- valid request returns findings
- invalid JSON / missing fields / oversized files / too many files
- sensitive files (.env, *.pem) rejected
- path traversal / absolute paths rejected
- AST detects eval/exec, bare except, syntax errors
- Ruff and Bandit produce findings
- health endpoint
"""

import json
import unittest

from app import app


class AnalyzerTestCase(unittest.TestCase):
    """Shared setup: Flask test client."""

    def setUp(self):
        self.client = app.test_client()

    def _post(self, files):
        return self.client.post(
            "/analyze",
            data=json.dumps({"files": files}),
            content_type="application/json",
        )


class ValidRequestTests(AnalyzerTestCase):
    """Happy-path analysis."""

    def test_clean_file_returns_no_findings(self):
        resp = self._post([
            {"path": "clean.py", "content": "x = 1\nprint(x)\n"},
        ])
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("findings", data)
        self.assertIn("meta", data)
        self.assertEqual(data["meta"]["files_submitted"], 1)

    def test_eval_detected(self):
        resp = self._post([
            {"path": "bad.py", "content": "eval('1+1')\n"},
        ])
        self.assertEqual(resp.status_code, 200)
        rules = [f["rule"] for f in resp.get_json()["findings"]]
        self.assertIn("AST002", rules)

    def test_bare_except_detected(self):
        resp = self._post([
            {"path": "bad.py", "content": "try:\n    pass\nexcept:\n    pass\n"},
        ])
        self.assertEqual(resp.status_code, 200)
        rules = [f["rule"] for f in resp.get_json()["findings"]]
        self.assertIn("AST003", rules)

    def test_syntax_error_detected(self):
        resp = self._post([
            {"path": "broken.py", "content": "def foo(:\n    pass\n"},
        ])
        self.assertEqual(resp.status_code, 200)
        rules = [f["rule"] for f in resp.get_json()["findings"]]
        self.assertIn("AST001", rules)

    def test_finding_shape(self):
        resp = self._post([
            {"path": "bad.py", "content": "eval('2')\n"},
        ])
        data = resp.get_json()
        for f in data["findings"]:
            for key in ("file_path", "line_number", "rule", "severity",
                        "description"):
                self.assertIn(key, f)

    def test_ruff_finds_issue(self):
        """Ruff should flag an unused import (F401)."""
        resp = self._post([
            {"path": "unused.py", "content": "import os\n"},
        ])
        self.assertEqual(resp.status_code, 200)
        rules = [f["rule"] for f in resp.get_json()["findings"]]
        self.assertIn("F401", rules)

    def test_bandit_finds_hardcoded_password(self):
        """Bandit should flag a hardcoded password string."""
        resp = self._post([
            {"path": "secrets.py",
             "content": "password = 'hunter2'\n"},
        ])
        self.assertEqual(resp.status_code, 200)
        rules = [f["rule"] for f in resp.get_json()["findings"]]
        # B105 = hardcoded_password_string
        self.assertTrue(
            any(r.startswith("B1") for r in rules),
            f"Expected a Bandit B1xx finding, got: {rules}",
        )


class InvalidInputTests(AnalyzerTestCase):
    """Validation and rejection."""

    def test_invalid_json_returns_400(self):
        resp = self.client.post(
            "/analyze",
            data=b"not json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_files_returns_400(self):
        resp = self.client.post(
            "/analyze",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_empty_files_list_returns_400(self):
        resp = self._post([])
        self.assertEqual(resp.status_code, 400)

    def test_too_many_files_returns_400(self):
        files = [
            {"path": f"f{i}.py", "content": "x=1\n"} for i in range(101)
        ]
        resp = self._post(files)
        self.assertEqual(resp.status_code, 400)

    def test_oversized_file_returns_400(self):
        big = "x" * 200_001
        resp = self._post([{"path": "big.py", "content": big}])
        self.assertEqual(resp.status_code, 400)

    def test_sensitive_env_rejected(self):
        resp = self._post([
            {"path": ".env", "content": "SECRET=abc\n"},
        ])
        self.assertEqual(resp.status_code, 400)

    def test_sensitive_pem_rejected(self):
        resp = self._post([
            {"path": "server.pem", "content": "-----BEGIN KEY-----\n"},
        ])
        self.assertEqual(resp.status_code, 400)

    def test_sensitive_id_rsa_rejected(self):
        resp = self._post([
            {"path": "id_rsa", "content": "PRIVATE KEY\n"},
        ])
        self.assertEqual(resp.status_code, 400)

    def test_path_traversal_rejected(self):
        resp = self._post([
            {"path": "../../etc/passwd", "content": "x\n"},
        ])
        self.assertEqual(resp.status_code, 400)

    def test_absolute_path_rejected(self):
        resp = self._post([
            {"path": "/etc/passwd", "content": "x\n"},
        ])
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_path_rejected(self):
        resp = self._post([
            {"path": "a.py", "content": "x=1\n"},
            {"path": "a.py", "content": "y=2\n"},
        ])
        self.assertEqual(resp.status_code, 400)

    def test_non_string_content_rejected(self):
        resp = self._post([
            {"path": "a.py", "content": 42},
        ])
        self.assertEqual(resp.status_code, 400)


class HealthTests(AnalyzerTestCase):
    """Health endpoint."""

    def test_health_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
