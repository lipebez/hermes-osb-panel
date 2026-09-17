from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "dashboard" / "plugin_api.py"


def _candidate_bytecode_artifacts():
    return frozenset(ROOT.rglob("__pycache__/*.pyc"))


def _load_plugin_api():
    spec = importlib.util.spec_from_file_location("plugin_api", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PluginApiSnapshotTests(unittest.TestCase):
    def test_dashboard_host_can_import_plugin_api_as_a_standalone_module(self):
        probe = """
import importlib.util
import sys
spec = importlib.util.spec_from_file_location('host_loaded_osb_panel', sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert hasattr(module, 'router')
assert callable(module.normalize_snapshot)
print('HOST_IMPORT_OK')
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-I", "-c", probe, str(MODULE_PATH)],
            cwd=ROOT.parent,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "HOST_IMPORT_OK")

    def test_host_import_subprocess_leaves_no_bytecode_in_candidate(self):
        before = _candidate_bytecode_artifacts()
        completed = subprocess.run(
            [sys.executable, "-B", "-I", "-c", "import sys; print('BYTECODE_GUARD_OK')"],
            cwd=ROOT.parent,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
            capture_output=True,
            text=True,
        )
        after = _candidate_bytecode_artifacts()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "BYTECODE_GUARD_OK")
        self.assertEqual(after - before, frozenset())

    def test_bytecode_guard_ignores_preexisting_artifact(self):
        cache_dir = ROOT / "tests" / "__pycache__"
        cache_existed = cache_dir.exists()
        artifact = cache_dir / "preexisting-regression.pyc"
        cache_dir.mkdir(exist_ok=True)
        artifact.write_bytes(b"preexisting regression fixture")
        try:
            self.test_host_import_subprocess_leaves_no_bytecode_in_candidate()
        finally:
            artifact.unlink(missing_ok=True)
            if not cache_existed:
                cache_dir.rmdir()

    def test_default_snapshot_is_disabled_and_discloses_no_local_data(self):
        mod = _load_plugin_api()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "private-vault-should-not-appear"
            brain = vault / "Brain"
            brain.mkdir(parents=True)
            note_title = "Private note title should not appear"
            note_preview = "Private note preview should not appear"
            (brain / "active.md").write_text(
                f"# {note_title}\n\n{note_preview}\n", encoding="utf-8"
            )
            config = root / "private-osb-config.yaml"
            config.write_text(
                f"vault: {vault}\nobsidian_vault_id: private-vault-id-should-not-appear\n",
                encoding="utf-8",
            )

            snapshot = mod.build_snapshot(vault=vault, config_path=config)

        payload = json.dumps(snapshot, sort_keys=True)
        self.assertFalse(snapshot["provider"]["available"])
        self.assertEqual(snapshot["provider"]["mode"], "disabled")
        self.assertEqual(snapshot["nodes"], [])
        self.assertEqual(snapshot["edges"], [])
        self.assertEqual(snapshot["activity"]["active_preview"], "")
        self.assertEqual(snapshot["activity"]["recent"], [])
        self.assertEqual(snapshot["activity"]["timeline"], [])
        endpoint_snapshot = mod.snapshot()
        self.assertFalse(endpoint_snapshot["provider"]["available"])
        self.assertEqual(endpoint_snapshot["provider"]["mode"], "disabled")
        for forbidden in (
            vault.name,
            note_title,
            note_preview,
            "private-vault-id-should-not-appear",
            ".hermes/memories",
        ):
            self.assertNotIn(forbidden, payload)

    def test_direct_markdown_opt_in_is_strict_and_fail_closed(self):
        mod = _load_plugin_api()
        env_name = mod.DIRECT_MARKDOWN_OPT_IN_ENV
        original = os.environ.get(env_name)
        true_values = ("1", "true", "yes", "on", " TRUE ", "\tYeS\n", " On ")
        false_values = ("0", "false", "no", "", "   ", "arbitrary text", "enabled", "2")

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                vault = root / "private-vault-should-not-appear"
                brain = vault / "Brain"
                brain.mkdir(parents=True)
                title = "Owner-only title becomes available"
                preview = "Owner-only preview becomes available"
                url_credential = "URL_CREDENTIAL_VALUE"
                bearer_value = "BEARER_VALUE_SHOULD_NOT_LEAK"
                secret_value = "SECRET_VALUE_SHOULD_NOT_LEAK"
                vault_id = "private-vault-id-should-not-appear"
                config = root / "private-osb-config.yaml"
                scheme = "https" + "://"
                config.write_text(
                    "\n".join(
                        (
                            f"vault: {vault}",
                            f"obsidian_vault_id: {vault_id}",
                            f"service_url: {scheme}owner:{url_credential}@example.invalid/api",
                            f"authorization: Bearer {bearer_value}",
                            f"secret: {secret_value}",
                            "memory_root: /owner/private/hermes/memories",
                        )
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (brain / "active.md").write_text(
                    "\n".join(
                        (
                            f"# {title}",
                            preview,
                            f"Authorization: Bearer {bearer_value}",
                            f"secret: {secret_value}",
                            f"{scheme}owner:{url_credential}@example.invalid/private",
                        )
                    )
                    + "\n",
                    encoding="utf-8",
                )

                for value in true_values:
                    with self.subTest(enabled=value):
                        os.environ[env_name] = value
                        snapshot = mod.build_snapshot(vault=vault, config_path=config)
                        payload = json.dumps(snapshot, sort_keys=True)
                        self.assertTrue(snapshot["provider"]["available"])
                        self.assertEqual(snapshot["provider"]["mode"], "direct-markdown-prototype")
                        self.assertIn(title, payload)
                        for forbidden in (
                            str(vault),
                            str(config),
                            vault.name,
                            config.name,
                            vault_id,
                            url_credential,
                            bearer_value,
                            secret_value,
                            "memory_root",
                        ):
                            self.assertNotIn(forbidden, payload)

                for value in false_values:
                    with self.subTest(disabled=value):
                        os.environ[env_name] = value
                        snapshot = mod.build_snapshot(vault=vault, config_path=config)
                        self.assertFalse(snapshot["provider"]["available"])
                        self.assertEqual(snapshot["provider"]["mode"], "disabled")
                        self.assertEqual(snapshot["nodes"], [])

                os.environ.pop(env_name, None)
                snapshot = mod.build_snapshot(vault=vault, config_path=config)
                self.assertFalse(snapshot["provider"]["available"])
                self.assertEqual(snapshot["provider"]["mode"], "disabled")

                for value in ("0", "true"):
                    with self.subTest(explicit_reader_precedence=value):
                        os.environ[env_name] = value
                        disabled = mod.build_snapshot(
                            vault=vault,
                            config_path=config,
                            reader=mod.UnavailableSnapshotReader(),
                        )
                        direct = mod.build_snapshot(
                            vault=vault,
                            config_path=config,
                            reader=mod.DirectMarkdownPrototypeReader(
                                vault=vault, config_path=config
                            ),
                        )
                        self.assertFalse(disabled["provider"]["available"])
                        self.assertTrue(direct["provider"]["available"])
        finally:
            if original is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = original

    def test_vault_resolution_requires_config_or_explicit_override(self):
        mod = _load_plugin_api()
        with tempfile.TemporaryDirectory() as tmp:
            missing_config = Path(tmp) / "missing-config.yaml"
            self.assertIsNone(mod.resolve_vault(config_path=missing_config))

            vault = Path(tmp) / "configured-vault"
            vault.mkdir()
            config = Path(tmp) / "config.yaml"
            config.write_text(f"vault: {vault}\nobsidian_vault_id: portable-demo\n", encoding="utf-8")
            snapshot = mod.build_snapshot(
                reader=mod.DirectMarkdownPrototypeReader(config_path=config)
            )

        self.assertFalse(snapshot["provider"]["available"])
        self.assertNotIn("obsidian_vault_id", snapshot["provider"])
        self.assertNotIn("portable-demo", json.dumps(snapshot, sort_keys=True))
        self.assertNotIn(str(vault), repr(snapshot))

    def test_build_snapshot_counts_and_previews_brain_files(self):
        mod = _load_plugin_api()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            brain = vault / "Brain"
            (brain / "preferences").mkdir(parents=True)
            (brain / "inbox").mkdir(parents=True)
            (brain / "retired").mkdir(parents=True)
            (brain / "log").mkdir(parents=True)
            (brain / "active.md").write_text("# Active Brain Preferences\n\nKind: active\n", encoding="utf-8")
            (brain / "_BRAIN.md").write_text("# Brain operating manual\n\nUse notes carefully.\n", encoding="utf-8")
            (brain / "preferences" / "pref-clean.md").write_text("# Pref clean\n\nstatus: active\n", encoding="utf-8")
            (brain / "inbox" / "sig-request.md").write_text("# Signal request\n", encoding="utf-8")
            (brain / "retired" / "ret-old.md").write_text("# Retired pref\n", encoding="utf-8")
            (brain / "log" / "2026-06-06.md").write_text(
                "# Brain log — 2026 06 06\n\n## 10:00:00Z — note\n- text: Test note\n- agent: dashboard-test\n",
                encoding="utf-8",
            )

            snapshot = mod.build_snapshot(
                reader=mod.DirectMarkdownPrototypeReader(vault=vault)
            )

            self.assertTrue(snapshot["provider"]["available"])
            self.assertNotIn("vault", snapshot["provider"])
            self.assertNotIn(str(vault), repr(snapshot))
            self.assertEqual(snapshot["counts"]["preferences"], 1)
            self.assertEqual(snapshot["counts"]["inbox"], 1)
            self.assertEqual(snapshot["counts"]["retired"], 1)
            self.assertEqual(snapshot["counts"]["logs"], 1)
            self.assertEqual(snapshot["counts"]["brain_nodes"], 6)
            self.assertEqual(snapshot["counts"]["vault_notes"], 0)
            self.assertIn("Active Brain Preferences", snapshot["active_preview"])
            self.assertEqual(len(snapshot["recent_logs"]), 1)
            self.assertEqual(snapshot["recent_logs"][0]["kind"], "note")
            self.assertTrue(snapshot["graph"]["nodes"])
    def test_graph_contains_obsidian_style_nodes_and_wikilink_edges(self):
        mod = _load_plugin_api()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            brain = vault / "Brain"
            brain.mkdir()
            (brain / "active.md").write_text("# Active Brain Preferences\n\n[[Brain — operating manual]]\n", encoding="utf-8")
            (brain / "_BRAIN.md").write_text("# Brain — operating manual\n\nUse notes carefully.\n", encoding="utf-8")

            snapshot = mod.build_snapshot(
                reader=mod.DirectMarkdownPrototypeReader(vault=vault)
            )

            ids = [node["id"] for node in snapshot["graph"]["nodes"]]
            self.assertIn("Brain/active.md", ids)
            self.assertIn("Brain/_BRAIN.md", ids)

            positions = {node["id"]: (node["x"], node["y"]) for node in snapshot["graph"]["nodes"]}
            self.assertNotEqual(positions["Brain/active.md"], positions["Brain/_BRAIN.md"])

            self.assertTrue(snapshot["graph"]["edges"])
            self.assertEqual(snapshot["graph"]["edges"][0]["kind"], "wikilink")

    def test_vault_notes_appear_in_snapshot_and_graph(self):
        mod = _load_plugin_api()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            brain = vault / "Brain"
            brain.mkdir()
            (brain / "active.md").write_text("# Active Brain Preferences\n", encoding="utf-8")
            projects = vault / "02 Projetos"
            projects.mkdir()
            (projects / "Project Atlas.md").write_text("# Project Atlas\n\nPrimary project.\n", encoding="utf-8")
            runbooks = vault / "04 Runbooks"
            runbooks.mkdir()
            (runbooks / "WhatsApp Bridge.md").write_text("# WhatsApp Bridge\n\nEnvio e mídia.\n", encoding="utf-8")

            snapshot = mod.build_snapshot(
                reader=mod.DirectMarkdownPrototypeReader(vault=vault)
            )
            ids = [node["id"] for node in snapshot["graph"]["nodes"]]
            self.assertIn("02 Projetos/Project Atlas.md", ids)
            self.assertIn("04 Runbooks/WhatsApp Bridge.md", ids)
            self.assertEqual(snapshot["counts"]["brain_nodes"], 1)
            self.assertEqual(snapshot["counts"]["vault_notes"], 2)
            self.assertEqual(len(snapshot["vault_notes"]), 2)
            self.assertEqual(snapshot["vault_notes"][0]["area"], "projects")

    def test_counts_compact_hermes_memory_entries_without_content(self):
        mod = _load_plugin_api()
        text = "A primeira memória.\n§\nA segunda memória.\n\ncom detalhe\n§\n\n"
        self.assertEqual(mod._count_markdown_memory_entries(text), 2)

    def test_missing_vault_fails_soft(self):
        mod = _load_plugin_api()
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = mod.build_snapshot(
                reader=mod.DirectMarkdownPrototypeReader(vault=Path(tmp) / "missing")
            )
            self.assertFalse(snapshot["provider"]["available"])
            self.assertEqual(snapshot["counts"]["brain_nodes"], 0)
            self.assertEqual(snapshot["counts"]["vault_notes"], 0)
            self.assertEqual(snapshot["graph"]["nodes"], [])

    def test_graph_intelligence_broken_links_freshness_and_revision(self):
        mod = _load_plugin_api()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            brain = vault / "Brain"
            brain.mkdir()
            (brain / "active.md").write_text("# Active\n", encoding="utf-8")
            projects = vault / "02 Projetos"
            projects.mkdir()
            hub = projects / "Hub.md"
            hub.write_text("# Hub\n[[A]] [[B]] [[C]] [[Missing target]]\n", encoding="utf-8")
            for name in ("A", "B", "C", "Orphan"):
                (projects / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")

            first = mod.build_snapshot(
                reader=mod.DirectMarkdownPrototypeReader(vault=vault)
            )
            second = mod.build_snapshot(
                reader=mod.DirectMarkdownPrototypeReader(vault=vault)
            )
            by_id = {node["id"]: node for node in first["graph"]["nodes"]}
            hub_id = "02 Projetos/Hub.md"
            orphan_id = "02 Projetos/Orphan.md"

            self.assertEqual(first["revision"], second["revision"])
            self.assertGreaterEqual(by_id[hub_id]["degree"], 3)
            self.assertGreater(by_id[hub_id]["size_bytes"], 0)
            self.assertTrue(by_id[hub_id]["modified_at"].endswith("+00:00"))
            self.assertIn(hub_id, first["graph_summary"]["hub_ids"])
            self.assertIn(orphan_id, first["graph_summary"]["orphan_ids"])
            self.assertIn({"source": hub_id, "target": "Missing target"}, first["broken_links"])

            hub.write_text("# Hub changed\n[[A]] [[B]] [[C]] [[Missing target]]\n", encoding="utf-8")
            changed = mod.build_snapshot(
                reader=mod.DirectMarkdownPrototypeReader(vault=vault)
            )
            self.assertNotEqual(first["revision"], changed["revision"])

    def test_snapshot_reports_caps_and_combines_timeline_events_deterministically(self):
        mod = _load_plugin_api()
        setattr(mod, "MAX_GRAPH_FILES", 3)
        setattr(mod, "MAX_VAULT_FILES", 2)
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            brain = vault / "Brain"
            (brain / "log").mkdir(parents=True)
            (brain / "active.md").write_text("# Active\n", encoding="utf-8")
            (brain / "a.md").write_text("# A\n", encoding="utf-8")
            (brain / "b.md").write_text("# B\n", encoding="utf-8")
            (brain / "c.md").write_text("# C\n", encoding="utf-8")
            log = brain / "log" / "2026-06-06.md"
            log.write_text("# Log\n\n## 10:00:00Z — note\n- text: Logged fact\n- agent: dashboard-test\n", encoding="utf-8")
            projects = vault / "02 Projetos"
            projects.mkdir()
            for index in range(3):
                note = projects / f"P{index}.md"
                note.write_text(f"# P{index}\n", encoding="utf-8")
                os.utime(note, (1_800_000_000 + index, 1_800_000_000 + index))

            snapshot = mod.build_snapshot(
                reader=mod.DirectMarkdownPrototypeReader(vault=vault)
            )
            self.assertEqual(snapshot["limits"]["brain_files"]["total"], 5)
            self.assertEqual(snapshot["limits"]["brain_files"]["shown"], 3)
            self.assertTrue(snapshot["limits"]["brain_files"]["truncated"])
            self.assertEqual(snapshot["limits"]["vault_files"]["total"], 3)
            self.assertEqual(snapshot["limits"]["vault_files"]["shown"], 2)
            self.assertTrue(snapshot["timeline_events"])
            stamps = [event["timestamp"] for event in snapshot["timeline_events"] if event.get("timestamp")]
            self.assertEqual(stamps, sorted(stamps, reverse=True))
            self.assertTrue(any(event["kind"] == "note" for event in snapshot["timeline_events"]))
            self.assertTrue(any(event["kind"] == "modified" for event in snapshot["timeline_events"]))


if __name__ == "__main__":
    unittest.main()
