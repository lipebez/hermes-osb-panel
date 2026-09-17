from __future__ import annotations

import io
import subprocess
import tarfile
import unittest
from pathlib import Path

from scripts.check_public_release import (
    ALLOWED_BINARY_ARCHIVE_PATHS,
    format_findings,
    scan_archive_bytes,
)


def archive_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


def candidate_tree_entries(repository_root: Path) -> dict[str, bytes]:
    """Return files that could enter a release, excluding Git-ignored local state."""
    if (repository_root / ".git").exists():
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=repository_root,
            check=True,
            capture_output=True,
        )
        relative_paths = (
            Path(raw.decode("utf-8"))
            for raw in completed.stdout.split(b"\0")
            if raw
        )
    else:
        relative_paths = (
            path.relative_to(repository_root)
            for path in repository_root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(repository_root).parts
        )

    return {
        path.as_posix(): (repository_root / path).read_bytes()
        for path in relative_paths
        if (repository_root / path).is_file()
    }


class PublicReleaseArchiveScannerTests(unittest.TestCase):
    def categories_for(self, entries: dict[str, bytes]) -> set[tuple[str, str]]:
        return {
            (finding.filename, finding.category)
            for finding in scan_archive_bytes(archive_bytes(entries))
        }

    def test_current_candidate_tree_archive_has_no_findings(self):
        """Exercise the scanner against tracked and non-ignored candidate bytes."""
        repository_root = Path(__file__).resolve().parents[1]
        entries = candidate_tree_entries(repository_root)

        self.assertEqual(scan_archive_bytes(archive_bytes(entries)), [])

    def test_candidate_tree_excludes_git_ignored_local_artifacts(self):
        repository_root = Path(__file__).resolve().parents[1]
        ignored_artifact = repository_root / ".hermes" / "candidate-tree-regression.txt"
        ignored_artifact.parent.mkdir(parents=True, exist_ok=True)
        ignored_artifact.write_text("/" + "root/private-local-path\n", encoding="utf-8")
        try:
            entries = candidate_tree_entries(repository_root)
        finally:
            ignored_artifact.unlink(missing_ok=True)

        self.assertNotIn(".hermes/candidate-tree-regression.txt", entries)

    def test_detects_environment_artifacts_by_archive_filename(self):
        findings = self.categories_for(
            {"config/local.env": b"MODE=development\n", ".env": b"MODE=development\n"}
        )

        self.assertIn(("config/local.env", "environment artifact"), findings)
        self.assertIn((".env", "environment artifact"), findings)

    def test_detects_unallowlisted_binary_media_by_extension_and_content_class(self):
        findings = self.categories_for({"assets/preview.png": b"\x89PNG\r\n\x1a\n\x00\xff"})

        self.assertEqual(findings, {("assets/preview.png", "binary media")})

    def test_valid_utf8_replacement_character_is_not_binary(self):
        replacement = bytes((0xEF, 0xBF, 0xBD))
        findings = self.categories_for({"docs/utf8.txt": b"valid " + replacement + b" text"})

        self.assertEqual(findings, set())

    def test_allows_only_the_named_neutral_binary_fixture(self):
        fixture_path = "tests/fixtures/release_scanner_neutral.png"
        findings = self.categories_for({fixture_path: b"\x89PNG\r\n\x1a\n\x00\xff"})

        self.assertEqual(ALLOWED_BINARY_ARCHIVE_PATHS, frozenset({fixture_path}))
        self.assertEqual(findings, set())

    def test_detects_qa_reports_and_screenshots_by_archive_filename(self):
        findings = self.categories_for(
            {
                "qa-output/release-report.json": b"{}",
                "artifacts/release-screenshot.png": b"plain report marker",
            }
        )

        self.assertIn(("qa-output/release-report.json", "QA report/screenshot"), findings)
        self.assertIn(("artifacts/release-screenshot.png", "QA report/screenshot"), findings)

    def test_detects_private_home_paths_without_returning_the_content(self):
        private_path = "/" + "home/example-user/private-note.md"
        findings = scan_archive_bytes(archive_bytes({"notes/example.txt": private_path.encode()}))

        self.assertEqual([(finding.filename, finding.category) for finding in findings], [
            ("notes/example.txt", "private home path")
        ])
        self.assertNotIn(private_path, format_findings(findings))

    def test_detects_placeholder_person_markers(self):
        marker = "Owner" + " Example"
        findings = self.categories_for({"docs/notes.txt": ("reviewer: " + marker + "\n").encode()})

        self.assertEqual(findings, {("docs/notes.txt", "identity marker")})

    def test_detects_credential_assignment_and_token_shapes_without_leaking_values(self):
        token = "synthetic_credential_value_1234567890"
        content = ("access" + "_token=" + token + "\n").encode()
        findings = scan_archive_bytes(archive_bytes({"config/settings.txt": content}))
        rendered = format_findings(findings)

        self.assertIn(("config/settings.txt", "credential assignment/token shape"), {
            (finding.filename, finding.category) for finding in findings
        })
        self.assertNotIn(token, rendered)

    def test_detects_url_userinfo_and_query_secrets_without_leaking_values(self):
        userinfo_value = "synthetic-userinfo-value"
        query_value = "synthetic-query-value"
        scheme = "https" + "://"
        query_key = "to" + "ken"
        content = (
            scheme + "user:" + userinfo_value + "@example.invalid/service\n"
            + scheme + "example.invalid/service?" + query_key + "=" + query_value + "&page=1\n"
        ).encode()
        findings = scan_archive_bytes(archive_bytes({"docs/links.txt": content}))
        rendered = format_findings(findings)

        self.assertEqual(
            {(finding.filename, finding.category) for finding in findings},
            {("docs/links.txt", "URL credential/query secret")},
        )
        self.assertNotIn(userinfo_value, rendered)
        self.assertNotIn(query_value, rendered)

    def test_safe_text_archive_has_no_findings(self):
        findings = scan_archive_bytes(
            archive_bytes({"README.md": b"Portable, read-only dashboard companion.\n"})
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
