from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, cast
from unittest import mock

from scripts.check_public_release import (
    ALLOWED_BINARY_ARCHIVE_PATHS,
    MAX_ARCHIVE_BYTES,
    MAX_MEMBER_BYTES,
    _git_archive_head,
    format_findings,
    main,
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


def corrupt_first_header(payload: bytes, start: int, stop: int, value: bytes) -> bytes:
    assert len(value) == stop - start
    corrupted = bytearray(payload)
    corrupted[start:stop] = value
    return bytes(corrupted)


def sparse_pax_fixture() -> bytes:
    """Build the exact dangerous shape: per-file PAX sparse map then a file."""
    def record(key: str, value: str) -> bytes:
        body = f"{key}={value}\n".encode()
        length = len(body) + 2
        while True:
            candidate = f"{length} ".encode() + body
            if len(candidate) == length:
                return candidate
            length = len(candidate)

    def header(name: str, size: int, typeflag: bytes) -> bytes:
        block = bytearray(512)
        block[:len(name)] = name.encode()
        block[100:108] = b"0000644\0"
        block[108:116] = b"0000000\0"
        block[116:124] = b"0000000\0"
        block[124:136] = f"{size:011o}\0".encode()
        block[136:148] = b"00000000000\0"
        block[148:156] = b"        "
        block[156:157] = typeflag
        block[257:263] = b"ustar\0"
        block[263:265] = b"00"
        block[148:156] = f"{sum(block):06o}\0 ".encode()
        return bytes(block)

    pax = record("GNU.sparse.map", "0,1") + record("GNU.sparse.size", "1")
    padded_pax = pax + b"\0" * (-len(pax) % 512)
    data = b"x" + b"\0" * 511
    return (
        header("PaxHeaders/sparse.bin", len(pax), b"x")
        + padded_pax
        + header("sparse.bin", 1, b"0")
        + data
        + b"\0" * 1024
    )


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

    def test_current_git_archive_with_global_comment_is_accepted(self):
        repository_root = Path(__file__).resolve().parents[1]
        payload = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"],
            cwd=repository_root,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout

        self.assertEqual(scan_archive_bytes(payload), [])

    def test_rejects_base256_and_non_octal_bytes_in_every_numeric_header_field(self):
        payload = archive_bytes({"safe.txt": b"safe\n"})
        fields = {
            "mode": (100, 108),
            "uid": (108, 116),
            "gid": (116, 124),
            "size": (124, 136),
            "mtime": (136, 148),
            "checksum": (148, 156),
            "devmajor": (329, 337),
            "devminor": (337, 345),
        }
        for label, (start, stop) in fields.items():
            width = stop - start
            for invalid in (b"\x80" + b"0" * (width - 1), b"8" + b"0" * (width - 1)):
                with self.subTest(field=label, invalid=invalid[:1]), mock.patch(
                    "scripts.check_public_release.tarfile.open",
                    side_effect=AssertionError("tarfile.open must not be reached"),
                ) as opened:
                    with self.assertRaises(ValueError):
                        scan_archive_bytes(corrupt_first_header(payload, start, stop, invalid))
                opened.assert_not_called()

    def test_rejects_nul_typeflag_before_tarfile_open(self):
        payload = corrupt_first_header(archive_bytes({"safe.txt": b"safe\n"}), 156, 157, b"\0")
        with mock.patch(
            "scripts.check_public_release.tarfile.open",
            side_effect=AssertionError("tarfile.open must not be reached"),
        ) as opened:
            with self.assertRaises(ValueError):
                scan_archive_bytes(payload)
        opened.assert_not_called()

    def test_sparse_pax_is_rejected_before_tarfile_open(self):
        payload = sparse_pax_fixture()
        with mock.patch(
            "scripts.check_public_release.tarfile.open",
            side_effect=AssertionError("tarfile.open must not be reached"),
        ) as opened:
            with self.assertRaises(ValueError):
                scan_archive_bytes(payload)
        opened.assert_not_called()

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

    def test_cli_can_scan_an_existing_exact_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "candidate.tar"
            archive.write_bytes(archive_bytes({"README.md": b"safe release bytes\n"}))
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                result = main(["--archive", str(archive)])

            self.assertEqual(result, 0)
            self.assertEqual(stderr.getvalue(), "")

    def test_pure_scanner_rejects_archive_and_member_bounds(self):
        with self.assertRaises(ValueError):
            scan_archive_bytes(b"x" * (MAX_ARCHIVE_BYTES + 1))

        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            member = tarfile.TarInfo("large.txt")
            member.size = MAX_MEMBER_BYTES + 1
            archive.addfile(member)
        with self.assertRaises(ValueError):
            scan_archive_bytes(output.getvalue())

    def test_scanner_rejects_sparse_contiguous_and_raw_unsafe_names(self):
        cases = [
            ("sparse", "safe.txt", tarfile.GNUTYPE_SPARSE),
            ("contiguous", "safe.txt", tarfile.CONTTYPE),
        ] + [(f"path-{index}", name, tarfile.REGTYPE) for index, name in enumerate(
            (".", "./", "./ok", "a//b", "a/./b", "..", "/absolute")
        )]
        for label, name, entry_type in cases:
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w") as archive:
                member = tarfile.TarInfo(name)
                member.type = entry_type
                archive.addfile(member)
            payload = output.getvalue()
            with self.subTest(label=label), self.assertRaises(ValueError):
                scan_archive_bytes(payload)
            with tempfile.TemporaryDirectory() as temporary:
                archive_path = Path(temporary) / "candidate.tar"
                archive_path.write_bytes(payload)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["--archive", str(archive_path)]), 2)

    def test_cli_rejects_oversized_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "oversized.tar"
            with archive.open("wb") as handle:
                handle.truncate(MAX_ARCHIVE_BYTES + 1)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--archive", str(archive)]), 2)


class GitArchiveProcessTests(unittest.TestCase):
    def _run_child(self, source: str, **kwargs: object) -> tuple[bytes | None, subprocess.Popen[bytes], float]:
        real_popen = subprocess.Popen
        children: list[subprocess.Popen[bytes]] = []

        def replacement(_command: object, **options: Any) -> subprocess.Popen[bytes]:
            child = cast(subprocess.Popen[bytes], real_popen([sys.executable, "-c", source], **options))
            children.append(child)
            return child

        started = time.monotonic()
        with mock.patch("scripts.check_public_release.subprocess.Popen", side_effect=replacement):
            result = _git_archive_head(**kwargs)
        return result, children[0], time.monotonic() - started

    def assert_process_group_absent(self, pgid: int) -> None:
        with self.assertRaises(ProcessLookupError):
            os.killpg(pgid, 0)

    @staticmethod
    def _descendant_source(output: bytes) -> str:
        return (
            "import os,signal,subprocess,sys,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "subprocess.Popen([sys.executable, '-c', 'import signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
            "signal.signal(signal.SIGTERM, signal.SIG_DFL); "
            f"os.write(1, {output!r}); time.sleep(60)"
        )

    def test_short_process_returns_incrementally_read_payload(self):
        result, child, elapsed = self._run_child("import os; os.write(1, b'archive')", timeout_seconds=1.0)
        self.assertEqual(result, b"archive")
        self.assertIsNotNone(child.poll())
        self.assertLess(elapsed, 1.0)

    def test_trapped_process_is_bounded_and_cleaned_up(self):
        result, child, elapsed = self._run_child(
            self._descendant_source(b"partial"),
            timeout_seconds=0.15,
            cleanup_grace_seconds=0.1,
        )
        self.assertIsNone(result)
        self.assertIsNotNone(child.poll())
        self.assert_process_group_absent(child.pid)
        self.assertLess(elapsed, 1.0)

    def test_oversize_process_is_bounded_and_cleaned_up(self):
        result, child, elapsed = self._run_child(
            self._descendant_source(b"x" * 2048),
            timeout_seconds=1.0,
            cleanup_grace_seconds=0.1,
            max_archive_bytes=1024,
        )
        self.assertIsNone(result)
        self.assertIsNotNone(child.poll())
        self.assert_process_group_absent(child.pid)
        self.assertLess(elapsed, 1.0)

    def test_selector_baseexception_after_popen_is_preserved_and_cleans_up(self):
        real_popen = subprocess.Popen
        children: list[subprocess.Popen[bytes]] = []
        marker = BaseException("selector setup failed")

        def replacement(_command: object, **options: Any) -> subprocess.Popen[bytes]:
            child = cast(subprocess.Popen[bytes], real_popen(
                [sys.executable, "-c", self._descendant_source(b"ready")], **options
            ))
            children.append(child)
            return child

        caught: BaseException | None = None
        with (
            mock.patch("scripts.check_public_release.subprocess.Popen", side_effect=replacement),
            mock.patch("scripts.check_public_release.selectors.DefaultSelector", side_effect=marker),
        ):
            try:
                _git_archive_head(cleanup_grace_seconds=0.1)
            except BaseException as error:
                caught = error

        self.assertIs(caught, marker)
        child = children[0]
        self.assertIsNotNone(child.stdout)
        stdout = child.stdout
        assert stdout is not None
        self.assertTrue(stdout.closed)
        self.assertIsNotNone(child.poll())
        self.assert_process_group_absent(child.pid)


if __name__ == "__main__":
    unittest.main()
