from __future__ import annotations

import json
import re
import subprocess
import tempfile
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

    def test_readme_lists_ordered_shell_specific_direct_reader_commands(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        expected_sequences = (
            (
                "### Windows PowerShell",
                "$env:HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN = 'true'",
                "hermes dashboard --stop",
                "hermes dashboard --no-open",
            ),
            (
                "Linux/macOS (Bash/Zsh)",
                "export HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN=true",
                "hermes dashboard --stop",
                "hermes dashboard --no-open",
            ),
        )
        for heading, *commands in expected_sequences:
            with self.subTest(platform=heading):
                start = readme.index(heading)
                positions = [readme.index(command, start) for command in commands]
                self.assertEqual(positions, sorted(positions))

    def test_windows_readonly_recovery_targets_hidden_files_not_directories(self):
        local_app_data = "$env:LOCAL" + "APPDATA"
        path_separator = chr(92)
        expected = (
            f"$pluginDir = Join-Path {local_app_data} 'hermes{path_separator}plugins{path_separator}hermes-osb-panel'",
            "Get-ChildItem -LiteralPath $pluginDir -Recurse -File -Force | ForEach-Object { $_.IsReadOnly = $false }",
            "hermes plugins remove hermes-osb-panel",
        )
        headings = {"README.md": "### Windows ReadOnly removal recovery", "docs/qa.md": "## Windows disable/remove recovery"}
        for relative, heading in headings.items():
            with self.subTest(document=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                start = text.index(heading)
                positions = [text.index(command, start) for command in expected]
                self.assertEqual(positions, sorted(positions))

    def test_release_qa_is_fail_fast_and_binds_detached_worktree_and_one_exact_archive(self):
        qa = (ROOT / "docs" / "qa.md").read_text(encoding="utf-8")

        self.assertIn("set -euo pipefail", qa)
        self.assertIn('QA_PARENT="$(dirname "$REPO_ROOT")"', qa)
        self.assertIn('mktemp -d "$QA_PARENT/.hermes-osb-panel-release.XXXXXX"', qa)
        self.assertIn('git -C "$REPO_ROOT" worktree add --detach "$QA_ROOT" "$CANDIDATE_SHA"', qa)
        self.assertIn('git -C "$REPO_ROOT" worktree remove --force "$QA_ROOT"', qa)
        self.assertNotIn('git -C "$REPO_ROOT" worktree prune', qa)
        self.assertIn('set +e', qa)
        self.assertIn('cleanup_status=0', qa)
        self.assertIn('[[ ! -e "$QA_CONTAINER" ]] || cleanup_status=1', qa)
        self.assertIn('[[ ! -e "$EVIDENCE_TMP_ROOT" ]] || cleanup_status=1', qa)
        self.assertIn('status=$?', qa)
        self.assertIn('exit "$status"', qa)
        self.assertLess(qa.index("trap cleanup EXIT"), qa.index("EVIDENCE_TMP_ROOT=\"$(mktemp"))
        self.assertLess(qa.index("trap cleanup EXIT"), qa.index("QA_CONTAINER=\"$(mktemp"))

        candidate = qa.index('CANDIDATE_SHA="$(git rev-parse HEAD)"')
        worktree = qa.index('worktree add --detach "$QA_ROOT" "$CANDIDATE_SHA"', candidate)
        archive = qa.index('git archive --format=tar "$CANDIDATE_SHA"', worktree)
        digest = qa.index('CANDIDATE_ARCHIVE_SHA256="$(sha256sum', archive)
        readonly = qa.index('chmod 0444 "$CANDIDATE_ARCHIVE"', digest)
        node = qa.index('cd "$QA_ROOT" && node --check dashboard/dist/index.js', readonly)
        tracked_python = qa.index("git -C \"$QA_ROOT\" ls-files -z -- '*.py'", node)
        ast_gate = qa.index('python3 -B - "$QA_ROOT" "${TRACKED_PYTHON[@]}"', tracked_python)
        suite = qa.index("unittest discover", ast_gate)
        scanner = qa.index('check_public_release.py" --archive "$CANDIDATE_ARCHIVE"', suite)
        extraction = qa.index('tar -xf "$CANDIDATE_ARCHIVE" -C "$ARCHIVE_ROOT"', scanner)
        parity_before = qa.index("\nassert_archive_parity\n", extraction)
        doctor = qa.index('plugins doctor "$ARCHIVE_ROOT" --ci', parity_before)
        parity_after = qa.index("\nassert_archive_parity\n", doctor)
        evidence = qa.index("build_release_evidence.py", parity_after)
        self.assertEqual(
            [candidate, worktree, archive, digest, readonly, node, tracked_python, ast_gate, suite, scanner, extraction, parity_before, doctor, parity_after, evidence],
            sorted([candidate, worktree, archive, digest, readonly, node, tracked_python, ast_gate, suite, scanner, extraction, parity_before, doctor, parity_after, evidence]),
        )

        self.assertEqual(qa.count('git archive --format=tar "$CANDIDATE_SHA"'), 1)
        self.assertGreaterEqual(qa.count("\nassert_candidate_worktree\n"), 6)
        for command in (
            'git -C "$QA_ROOT" rev-parse HEAD',
            'git -C "$QA_ROOT" status --porcelain=v1 --untracked-files=normal',
            'git -C "$QA_ROOT" diff --quiet "$CANDIDATE_SHA" --',
            'git -C "$QA_ROOT" diff --cached --quiet',
        ):
            self.assertIn(command, qa)
        self.assertGreaterEqual(qa.count("\nassert_archive_digest\n"), 7)
        self.assertIn('test "$(stat -c \'%a\' "$CANDIDATE_ARCHIVE")" = 444', qa)
        self.assertIn('test "$(stat -c \'%h\' "$CANDIDATE_ARCHIVE")" = 1', qa)
        self.assertIn('test "$(stat -c \'%s\' "$CANDIDATE_ARCHIVE")" -le "$MAX_ARCHIVE_BYTES"', qa)
        self.assertIn('tar --compare --file "$CANDIDATE_ARCHIVE" --directory "$ARCHIVE_ROOT"', qa)
        self.assertIn('check_archive_parity.py" --archive "$CANDIDATE_ARCHIVE" --root "$ARCHIVE_ROOT"', qa)
        validation = qa.index('check_archive_parity.py" \\\n  --archive "$CANDIDATE_ARCHIVE"')
        extraction = qa.index('tar -xf "$CANDIDATE_ARCHIVE" -C "$ARCHIVE_ROOT"')
        self.assertLess(validation, extraction)
        self.assertIn("sanitized_operator_summary.v1", qa)
        self.assertIn("not a signature or independent attestation", qa)
        self.assertIn('--candidate-archive-sha256 "$CANDIDATE_ARCHIVE_SHA256"', qa)
        self.assertIn('--doctor-gate-passed "$DOCTOR_GATE_PASSED"', qa)
        self.assertIn('DOCTOR_GATE_PASSED=true', qa)
        self.assertIn('TESTS_FAILED=0  # reached only after the suite pipeline succeeded', qa)
        self.assertIn('--tests-failed "$TESTS_FAILED"', qa)
        self.assertNotIn("--tests-failed 0", qa)
        self.assertIn(r'^Ran (\d+) tests?$', qa)
        self.assertNotIn("matches[-1]", qa)

    def test_release_qa_trap_cleans_directory_when_first_mktemp_returns_error(self):
        qa = (ROOT / "docs" / "qa.md").read_text(encoding="utf-8")
        segment = qa[qa.index('QA_CONTAINER=""'):qa.index('QA_ROOT="$QA_CONTAINER/candidate-worktree"')]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_mktemp = fake_bin / "mktemp"
            fake_mktemp.write_text(
                "#!/bin/bash\npath=\"${2%.XXXXXX}.probe\"\nmkdir -p -- \"$path\"\nprintf '%s\\n' \"$path\"\nexit 9\n",
                encoding="utf-8",
            )
            fake_mktemp.chmod(0o755)
            probe = root / ".hermes-osb-panel-release.probe"
            script = f'set -euo pipefail\nREPO_ROOT={str(ROOT)!r}\nQA_PARENT={str(root)!r}\n' + segment
            result = subprocess.run(
                ["bash", "-c", script],
                env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 9, result.stderr)
            self.assertFalse(probe.exists())

    def test_readme_uses_opt_in_install_doctor_enable_sequence(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        commands = (
            "plugins install lipebez/hermes-osb-panel",
            "--no-enable",
            "plugins list --user --json",
            "plugins doctor hermes-osb-panel --ci",
            "plugins enable hermes-osb-panel --no-allow-tool-override",
            "dashboard --no-open",
        )
        positions = [readme.index(command) for command in commands]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("Current release", readme)

    def test_ci_actions_are_pinned_to_full_commit_shas(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        uses = re.findall(r"(?m)^\s*uses:\s*(actions/(?:checkout|setup-python))@([^\s#]+)", ci)

        self.assertEqual(len(uses), 3)
        for action, revision in uses:
            with self.subTest(action=action):
                self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_docs_describe_redirect_and_release_state_without_stale_claims(self):
        qa = (ROOT / "docs" / "qa.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertIn("forwards same-origin 3xx responses", qa)
        self.assertIn("subsequent destination", qa)
        self.assertNotIn("The proxy rejects CONNECT, non-exact origins, redirects", qa)
        for text in (qa, readme, security, changelog):
            self.assertNotIn("documentation freeze", text)
            self.assertNotIn("no `v3.1.0` tag", text)
        self.assertNotIn("Current release", readme)


if __name__ == "__main__":
    unittest.main()
