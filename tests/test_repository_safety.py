from __future__ import annotations

import json
import re
import unittest
from fnmatch import fnmatchcase
from pathlib import Path

from dashboard.snapshot_contract import SNAPSHOT_SCHEMA, normalize_snapshot, sanitize_report_payload


ROOT = Path(__file__).resolve().parents[1]
VCS_DIRECTORIES = frozenset({".git"})
PATH_BACKSLASH = chr(92)
ESCAPED_PATH_BACKSLASH = re.escape(PATH_BACKSLASH)
SYNTHETIC_HOME = "/" + "home" + "/demo/Vault"
SYNTHETIC_FILE_URI = "file:" + "/" + "/" + "home" + "/demo/Vault"
SYNTHETIC_WINDOWS = "C:" + PATH_BACKSLASH + "Users" + PATH_BACKSLASH + "Demo" + " Example" + PATH_BACKSLASH + "Vault"
SYNTHETIC_UNC = PATH_BACKSLASH * 2 + "demo-host" + PATH_BACKSLASH + "share" + PATH_BACKSLASH + "Vault"
SYNTHETIC_MEMORY_UNC = PATH_BACKSLASH * 2 + "demo-host" + PATH_BACKSLASH + "share" + PATH_BACKSLASH + "memory"
PLACEHOLDER_PERSON = "Owner" + " Example"
PLACEHOLDER_CLIENT = "Customer" + " Example"
SYNTHETIC_PATH_PROBES = (
    SYNTHETIC_FILE_URI,
    SYNTHETIC_HOME,
    SYNTHETIC_WINDOWS,
    SYNTHETIC_UNC,
    SYNTHETIC_MEMORY_UNC,
)
PRIVATE_HOME_PATH = re.compile(
    r"(?i)(?:file:" + "/" + "/)?/" + r"(?:root|home|Users)" + "/"
    + r"|[A-Z]:" + ESCAPED_PATH_BACKSLASH + r"Users" + ESCAPED_PATH_BACKSLASH
    + r"|(?<![A-Za-z0-9_" + ESCAPED_PATH_BACKSLASH + r"])" + ESCAPED_PATH_BACKSLASH * 2
    + r"(?!Users" + ESCAPED_PATH_BACKSLASH + r")[A-Za-z0-9][A-Za-z0-9._-]+" + ESCAPED_PATH_BACKSLASH
)
LOCAL_SERVICE_URL = re.compile(
    r"(?i)\bhttps?://(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0)(?::\d+)?(?:[/?#]|$)"
)
SECRET_SHAPE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[opsu]_[A-Za-z0-9]{20,})\b")
URL_CREDENTIALS = re.compile(r"://[^/\s:@{}]+:[^@\s/{}]+@")


class RepositorySafetyTests(unittest.TestCase):
    def ignore_rules(self) -> tuple[str, ...]:
        return tuple(
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )

    def is_ignored_by_rules(self, relative: str) -> bool:
        parts = relative.split("/")
        for rule in self.ignore_rules():
            if rule.endswith("/"):
                directory = rule.rstrip("/")
                if directory in parts:
                    return True
            elif rule.startswith("**/"):
                if fnmatchcase(parts[-1], rule.removeprefix("**/")):
                    return True
            elif "/" not in rule and any(fnmatchcase(part, rule) for part in parts):
                return True
            elif fnmatchcase(relative, rule):
                return True
        return False

    def release_tree_files(self) -> list[Path]:
        return sorted(
            path
            for path in ROOT.rglob("*")
            if path.is_file() and not VCS_DIRECTORIES.intersection(path.relative_to(ROOT).parts)
        )

    def candidate_files(self) -> list[Path]:
        return [
            path
            for path in self.release_tree_files()
            if not self.is_ignored_by_rules(path.relative_to(ROOT).as_posix())
        ]

    def scan_text(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "tests" in path.relative_to(ROOT).parts:
            for probe in SYNTHETIC_PATH_PROBES:
                text = text.replace(probe, "[synthetic-path]")
        return text

    def test_release_tree_ignore_rules_exclude_documented_local_artifacts(self):
        probes = (
            "local-webui.env",
            "preview.local.env",
            ".venv/bin/python",
            "venv/bin/python",
            "node_modules/package/index.js",
            ".coverage",
            ".mypy_cache/cache.json",
            ".ruff_cache/cache.json",
            "qa-output/report.json",
            "reports/report.json",
            "second-brain-demo.png",
            ".env",
            ".env.production",
        )
        candidate_files = set(self.candidate_files())

        for path in self.release_tree_files():
            relative = path.relative_to(ROOT).as_posix()
            with self.subTest(candidate_path=relative):
                self.assertEqual(path in candidate_files, not self.is_ignored_by_rules(relative))
        for probe in probes:
            with self.subTest(ignored_probe=probe):
                self.assertTrue(self.is_ignored_by_rules(probe), probe)

    def test_release_tree_has_no_private_topology_or_secret_shapes(self):
        for path in self.candidate_files():
            relative = str(path.relative_to(ROOT))
            text = self.scan_text(path)
            self.assertIsNone(PRIVATE_HOME_PATH.search(text), relative)
            self.assertIsNone(LOCAL_SERVICE_URL.search(text), relative)
            self.assertIsNone(SECRET_SHAPE.search(text), relative)
            self.assertIsNone(URL_CREDENTIALS.search(text), relative)

    def test_demo_fixture_is_normalized_synthetic_data(self):
        path = ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        normalized = normalize_snapshot(payload)

        self.assertEqual(normalized["schema"], SNAPSHOT_SCHEMA)
        self.assertEqual(normalized["revision"], "synthetic-demo-v1")
        self.assertGreaterEqual(len(normalized["nodes"]), 10)
        self.assertEqual(
            [node["id"] for node in normalized["nodes"]],
            sorted(node["id"] for node in normalized["nodes"]),
        )
        self.assertTrue(all("demo" not in node["id"] for node in normalized["nodes"]))

    def test_published_fixture_assets_reject_identity_paths_and_credentials(self):
        """Published fixtures stay synthetic without censoring owner-view runtime data."""
        fixture_assets = sorted((ROOT / "tests" / "fixtures").rglob("*"))
        fixture_assets = [path for path in fixture_assets if path.is_file()]
        synthetic_identities = re.compile(r"\b(?:Owner" + r" Example|Customer" + r" Example)\b")
        absolute_paths = re.compile(
            r"(?i)(?:file:(?:" + "/" + r"/)?[" + ESCAPED_PATH_BACKSLASH + r"/]+|"
            + ESCAPED_PATH_BACKSLASH * 2 + r"[^" + ESCAPED_PATH_BACKSLASH + r"\s]+" + ESCAPED_PATH_BACKSLASH
            + r"|(?<![\w:])[A-Z]:[" + ESCAPED_PATH_BACKSLASH + r"/]|(?<![\w/])/"
            + r"(?:root|home|Users)" + "/)"
        )
        credentials = re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|token|client[_-]?secret|secret|password|authorization|cookie)\s*[:=]\s*(?!\[redacted\]|<|\$|\{)[^\s,;]+|\bbearer\s+[^\s,;]+|://[^/\s:@]+:[^@\s/]+@"
        )

        self.assertTrue(fixture_assets, "published fixture assets are required")
        for path in fixture_assets:
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = str(path.relative_to(ROOT))
            self.assertIsNone(synthetic_identities.search(text), relative)
            self.assertIsNone(absolute_paths.search(text), relative)
            self.assertIsNone(credentials.search(text), relative)

    def test_qa_report_sanitizes_nested_keys_paths_headers_and_url_credentials(self):
        username = "demo-user"
        login_value = "credential-value"
        credential_separator = ":"
        userinfo_separator = "@"
        query_key = "to" + "ken"
        url = (
            "https" + "://"
            + username
            + credential_separator
            + login_value
            + userinfo_separator
            + "example.test/path?" + query_key + "=query-value&ok=1"
        )
        payload = {
            "console_errors": [
                "failed at " + SYNTHETIC_HOME + "/note.md",
                "failed at " + SYNTHETIC_FILE_URI + "/note.md",
                "failed at " + SYNTHETIC_UNC + PATH_BACKSLASH + "note.md",
                "Authorization: *** " + login_value,
                url,
            ],
            "access_token": "mapping-value",
            "nested": {"cookie": "cookie-value", "path": SYNTHETIC_HOME + "/note.md"},
            "activeArea": PLACEHOLDER_PERSON,
            PLACEHOLDER_PERSON + " " + SYNTHETIC_HOME: "visible",
            "console_warning": "Rendered title for " + PLACEHOLDER_CLIENT,
            "screenshot": SYNTHETIC_WINDOWS + PATH_BACKSLASH + "private capture.png",
        }

        serialized = json.dumps(sanitize_report_payload(payload), ensure_ascii=False)

        for forbidden in (
            username,
            "demo-host",
            login_value,
            "demo-user:credential-value",
            "query-value",
            "mapping-value",
            "cookie-value",
            PLACEHOLDER_PERSON,
            PLACEHOLDER_CLIENT,
            "Demo" + " Example",
            "private capture",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_package_documents_exist(self):
        for relative in (
            "LICENSE",
            "README.md",
            "CONTRIBUTING.md",
            "docs/architecture.md",
            "docs/qa.md",
            "docs/upstream-data-boundary.md",
            "docs/upstream-slices.md",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
