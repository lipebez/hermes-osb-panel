from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from scripts.build_release_evidence import build_evidence, encode_evidence, main


SHA = "0123456789abcdef0123456789abcdef01234567"
HERMES_SHA = "89abcdef0123456789abcdef0123456789abcdef"
OSB_SHA = "fedcba9876543210fedcba9876543210fedcba98"
ARCHIVE_SHA256 = "a" * 64


def safe_inputs() -> dict[str, object]:
    return {
        "project_version": "3.1.0",
        "candidate_sha": SHA,
        "candidate_archive_sha256": ARCHIVE_SHA256,
        "tested_hermes_version": "0.21.3",
        "tested_hermes_ref": HERMES_SHA,
        "tested_osb_version": "1.4.2",
        "tested_osb_ref": OSB_SHA,
        "static_gate_passed": True,
        "unit_gate_passed": True,
        "archive_gate_passed": True,
        "doctor_gate_passed": True,
        "tests_run": 72,
        "tests_passed": 72,
        "tests_failed": 0,
    }


def cli_args(output: Path) -> list[str]:
    return [
        "--project-version", "3.1.0",
        "--candidate-sha", SHA,
        "--candidate-archive-sha256", ARCHIVE_SHA256,
        "--tested-hermes-version", "0.21.3",
        "--tested-hermes-ref", HERMES_SHA,
        "--tested-osb-version", "1.4.2",
        "--tested-osb-ref", OSB_SHA,
        "--static-gate-passed", "true",
        "--unit-gate-passed", "true",
        "--archive-gate-passed", "true",
        "--doctor-gate-passed", "true",
        "--tests-run", "72",
        "--tests-passed", "72",
        "--tests-failed", "0",
        "--output", str(output),
    ]


class ReleaseEvidenceTests(unittest.TestCase):
    def test_builds_the_exact_safe_summary(self):
        evidence = build_evidence(**safe_inputs())

        self.assertEqual(
            evidence,
            {
                "candidate_sha": SHA,
                "candidate_archive_sha256": ARCHIVE_SHA256,
                "doctor_gate_passed": True,
                "evidence_kind": "sanitized_operator_summary.v1",
                "gates": {
                    "archive_passed": True,
                    "static_passed": True,
                    "unit_passed": True,
                },
                "project_version": "3.1.0",
                "tested_hermes": {"ref": HERMES_SHA, "version": "0.21.3"},
                "tested_osb": {"ref": OSB_SHA, "version": "1.4.2"},
                "tests": {"failed": 0, "passed": 72, "run": 72},
            },
        )

    def test_encoding_is_canonical_and_deterministic(self):
        first = encode_evidence(build_evidence(**safe_inputs()))
        second = encode_evidence(build_evidence(**dict(reversed(list(safe_inputs().items())))))

        self.assertEqual(first, second)
        self.assertEqual(first, json.dumps(json.loads(first), indent=2, sort_keys=True) + "\n")

    def test_rejects_non_sha_candidate_and_refs_without_echoing_input(self):
        cases = {
            "candidate_sha": "main",
            "tested_hermes_ref": "release-branch",
            "tested_osb_ref": "local-checkout",
        }
        for field, unsafe in cases.items():
            with self.subTest(field=field):
                values = safe_inputs()
                values[field] = unsafe
                with self.assertRaises(ValueError) as caught:
                    build_evidence(**values)
                self.assertNotIn(unsafe, str(caught.exception))

    def test_rejects_malformed_archive_digest(self):
        for digest in ("a" * 63, "a" * 65, "A" * 64, "not-a-digest"):
            with self.subTest(digest=digest):
                values = safe_inputs()
                values["candidate_archive_sha256"] = digest
                with self.assertRaises(ValueError):
                    build_evidence(**values)

    def test_requires_every_release_gate_to_be_literal_true(self):
        for field in (
            "static_gate_passed",
            "unit_gate_passed",
            "archive_gate_passed",
            "doctor_gate_passed",
        ):
            for value in (False, 1, "true", None):
                with self.subTest(field=field, value=value):
                    values = safe_inputs()
                    values[field] = value
                    with self.assertRaises(ValueError):
                        build_evidence(**values)

    def test_summary_identifies_itself_as_operator_supplied_not_attestation(self):
        evidence = build_evidence(**safe_inputs())
        self.assertEqual(evidence["evidence_kind"], "sanitized_operator_summary.v1")

    def test_false_static_or_archive_gate_is_rejected(self):
        for field in ("static_gate_passed", "archive_gate_passed"):
            with self.subTest(field=field):
                values = safe_inputs()
                values[field] = False
                with self.assertRaises(ValueError):
                    build_evidence(**values)

    def test_rejects_private_or_artifact_shaped_text(self):
        private_home = "/" + "home/private-user/project"
        scheme = "https" + "://"
        userinfo = "person" + ":" + "credential" + "@"
        url_with_userinfo = scheme + userinfo + "example.invalid/repo"
        credential = "to" + "ken=synthetic-secret-value"
        screenshot = "release-" + "screenshot.png"
        raw_log = "tests passed\ntraceback: private detail"
        vault_identifier = "vault" + "-private-identifier"
        username = "private" + "-username"
        unsafe_values = (
            private_home,
            url_with_userinfo,
            credential,
            screenshot,
            raw_log,
            vault_identifier,
            username,
        )

        for unsafe in unsafe_values:
            with self.subTest(unsafe=unsafe):
                values = safe_inputs()
                values["tested_hermes_version"] = unsafe
                with self.assertRaises(ValueError) as caught:
                    build_evidence(**values)
                self.assertNotIn(unsafe, str(caught.exception))

    def test_accepts_semver_2_prerelease_and_build_boundaries(self):
        versions = (
            "0.0.0",
            "1.2.3-alpha",
            "1.2.3-alpha.1",
            "1.2.3-0.3.7",
            "1.2.3-x.7.z.92",
            "1.2.3-x-y-z.--",
            "1.2.3+001",
            "1.2.3-beta+exp.sha.5114f85",
        )

        for version in versions:
            with self.subTest(version=version):
                values = safe_inputs()
                values["project_version"] = version
                self.assertEqual(build_evidence(**values)["project_version"], version)

    def test_rejects_non_semver_and_zero_padded_numeric_prerelease_identifiers(self):
        versions = (
            "01.2.3",
            "1.02.3",
            "1.2.03",
            "1.2.3-01",
            "1.2.3-alpha.01",
            "1.2.3-",
            "1.2.3-alpha.",
            "1.2.3+build.",
            "v1.2.3",
            "1.2.3 alpha",
        )

        for version in versions:
            with self.subTest(version=version):
                values = safe_inputs()
                values["project_version"] = version
                with self.assertRaises(ValueError):
                    build_evidence(**values)

    def test_rejects_inconsistent_or_boolean_test_counts(self):
        for field, value in (("tests_run", True), ("tests_passed", -1), ("tests_failed", 73)):
            with self.subTest(field=field, value=value):
                values = safe_inputs()
                values[field] = value
                with self.assertRaises(ValueError):
                    build_evidence(**values)

        inconsistent = safe_inputs()
        inconsistent["tests_passed"] = 71
        with self.assertRaises(ValueError):
            build_evidence(**inconsistent)

    def test_requires_gate_booleans_and_consistent_unit_result(self):
        values = safe_inputs()
        values["static_gate_passed"] = 1
        with self.assertRaises(ValueError):
            build_evidence(**values)

        values = safe_inputs()
        values["unit_gate_passed"] = False
        with self.assertRaises(ValueError):
            build_evidence(**values)

    def test_cli_writes_only_an_explicit_tmp_output(self):
        with TemporaryDirectory(dir="/tmp") as temp_dir:
            first = Path(temp_dir) / "first.json"
            second = Path(temp_dir) / "second.json"

            self.assertEqual(main(cli_args(first)), 0)
            self.assertEqual(main(cli_args(second)), 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_cli_rejects_existing_output_symlink_before_write(self):
        with TemporaryDirectory(dir="/tmp") as temp_dir:
            directory = Path(temp_dir)
            target = directory / "target.json"
            target.write_text("unchanged", encoding="utf-8")
            output = directory / "output.json"
            output.symlink_to(target)

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(main(cli_args(output)), 2)

            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(stderr.getvalue(), "error: unable to create release evidence\n")

    def test_cli_rejects_symlinked_parent_that_escapes_tmp_before_write(self):
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory(dir="/tmp") as temp_dir, TemporaryDirectory(dir=repository_root) as outside_dir:
            linked_parent = Path(temp_dir) / "linked-parent"
            linked_parent.symlink_to(outside_dir, target_is_directory=True)
            escaped_output = Path(outside_dir) / "evidence.json"

            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(cli_args(linked_parent / "evidence.json")), 2)

            self.assertFalse(escaped_output.exists())

    def test_cli_rejects_output_outside_tmp(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = main([
                "--project-version", "3.1.0",
                "--candidate-sha", SHA,
                "--candidate-archive-sha256", ARCHIVE_SHA256,
                "--tested-hermes-version", "0.21.3",
                "--tested-hermes-ref", HERMES_SHA,
                "--tested-osb-version", "1.4.2",
                "--tested-osb-ref", OSB_SHA,
                "--static-gate-passed", "true",
                "--unit-gate-passed", "true",
                "--archive-gate-passed", "true",
                "--doctor-gate-passed", "true",
                "--tests-run", "72",
                "--tests-passed", "72",
                "--tests-failed", "0",
                "--output", "release-evidence.json",
            ])
        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "error: unable to create release evidence\n")
        self.assertNotIn(str(Path.cwd()), stderr.getvalue())

    def test_atomic_publish_replaces_hard_link_without_mutating_other_target(self):
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory(dir="/tmp") as temp_dir, TemporaryDirectory(dir=repository_root) as outside_dir:
            outside = Path(outside_dir) / "outside.json"
            outside.write_text("outside unchanged", encoding="utf-8")
            output = Path(temp_dir) / "evidence.json"
            os.link(outside, output)

            self.assertEqual(main(cli_args(output)), 0)

            self.assertEqual(outside.read_text(encoding="utf-8"), "outside unchanged")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), build_evidence(**safe_inputs()))
            self.assertNotEqual(output.stat().st_ino, outside.stat().st_ino)

    def test_destination_symlink_race_is_harmless(self):
        repository_root = Path(__file__).resolve().parents[1]
        real_replace = os.replace
        with TemporaryDirectory(dir="/tmp") as temp_dir, TemporaryDirectory(dir=repository_root) as outside_dir:
            outside = Path(outside_dir) / "outside.json"
            outside.write_text("outside unchanged", encoding="utf-8")
            output = Path(temp_dir) / "evidence.json"

            def insert_symlink_then_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
                os.symlink(outside, dst, dir_fd=dst_dir_fd)
                return real_replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

            with mock.patch("scripts.build_release_evidence.os.replace", side_effect=insert_symlink_then_replace):
                self.assertEqual(main(cli_args(output)), 0)

            self.assertEqual(outside.read_text(encoding="utf-8"), "outside unchanged")
            self.assertFalse(output.is_symlink())
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), build_evidence(**safe_inputs()))

    def test_atomic_write_interruption_preserves_final_and_cleans_temp(self):
        real_write = os.write
        writes = 0
        with TemporaryDirectory(dir="/tmp") as temp_dir:
            directory = Path(temp_dir)
            output = directory / "evidence.json"
            original = '{"complete": true}\n'
            output.write_text(original, encoding="utf-8")
            stderr = io.StringIO()

            def interrupt_after_partial_write(fd, data):
                nonlocal writes
                writes += 1
                if writes == 1:
                    return real_write(fd, data[:10])
                raise InterruptedError("private path /secret")

            with mock.patch("scripts.build_release_evidence.os.write", side_effect=interrupt_after_partial_write):
                with redirect_stderr(stderr):
                    self.assertEqual(main(cli_args(output)), 2)

            self.assertEqual(output.read_text(encoding="utf-8"), original)
            self.assertEqual(list(directory.iterdir()), [output])
            self.assertEqual(stderr.getvalue(), "error: unable to create release evidence\n")
            self.assertNotIn("private", stderr.getvalue())

    def test_cli_validation_error_is_bounded_and_non_reflective(self):
        with TemporaryDirectory(dir="/tmp") as temp_dir:
            secret_value = "/absolute/private/source/value"
            args = cli_args(Path(temp_dir) / "evidence.json")
            args[args.index("--project-version") + 1] = secret_value
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                self.assertEqual(main(args), 2)

            self.assertEqual(stderr.getvalue(), "error: unable to create release evidence\n")
            self.assertNotIn(secret_value, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
