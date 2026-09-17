from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.build_release_evidence import build_evidence, encode_evidence, main


SHA = "0123456789abcdef0123456789abcdef01234567"
HERMES_SHA = "89abcdef0123456789abcdef0123456789abcdef"
OSB_SHA = "fedcba9876543210fedcba9876543210fedcba98"


def safe_inputs() -> dict[str, object]:
    return {
        "project_version": "3.1.0",
        "candidate_sha": SHA,
        "tested_hermes_version": "0.21.3",
        "tested_hermes_ref": HERMES_SHA,
        "tested_osb_version": "1.4.2",
        "tested_osb_ref": OSB_SHA,
        "static_gate_passed": True,
        "unit_gate_passed": True,
        "archive_gate_passed": True,
        "tests_run": 72,
        "tests_passed": 72,
        "tests_failed": 0,
    }


class ReleaseEvidenceTests(unittest.TestCase):
    def test_builds_the_exact_safe_summary(self):
        evidence = build_evidence(**safe_inputs())

        self.assertEqual(
            evidence,
            {
                "candidate_sha": SHA,
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
            common = [
                "--project-version", "3.1.0",
                "--candidate-sha", SHA,
                "--tested-hermes-version", "0.21.3",
                "--tested-hermes-ref", HERMES_SHA,
                "--tested-osb-version", "1.4.2",
                "--tested-osb-ref", OSB_SHA,
                "--static-gate-passed", "true",
                "--unit-gate-passed", "true",
                "--archive-gate-passed", "true",
                "--tests-run", "72",
                "--tests-passed", "72",
                "--tests-failed", "0",
            ]

            self.assertEqual(main([*common, "--output", str(first)]), 0)
            self.assertEqual(main([*common, "--output", str(second)]), 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_cli_rejects_output_outside_tmp(self):
        with self.assertRaises(ValueError) as caught:
            main([
                "--project-version", "3.1.0",
                "--candidate-sha", SHA,
                "--tested-hermes-version", "0.21.3",
                "--tested-hermes-ref", HERMES_SHA,
                "--tested-osb-version", "1.4.2",
                "--tested-osb-ref", OSB_SHA,
                "--static-gate-passed", "true",
                "--unit-gate-passed", "true",
                "--archive-gate-passed", "true",
                "--tests-run", "72",
                "--tests-passed", "72",
                "--tests-failed", "0",
                "--output", "release-evidence.json",
            ])
        self.assertEqual(str(caught.exception), "output must be an absolute path below /tmp")


if __name__ == "__main__":
    unittest.main()
