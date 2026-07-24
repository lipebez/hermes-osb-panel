from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard.snapshot_contract import (
    SNAPSHOT_SCHEMA,
    FixtureSnapshotReader,
    normalize_snapshot,
    sanitize_text,
)
from dashboard.plugin_api import build_snapshot


SYNTHETIC_HOME = "/" + "home" + "/demo/Vault"
PATH_BACKSLASH = chr(92)
SYNTHETIC_FILE_URI = "file:" + "/" + "/" + "home" + "/demo/Vault"
SYNTHETIC_WINDOWS = "C:" + PATH_BACKSLASH + "Users" + PATH_BACKSLASH + "Demo" + " Example" + PATH_BACKSLASH + "Vault"
SYNTHETIC_UNC = PATH_BACKSLASH * 2 + "demo-host" + PATH_BACKSLASH + "share" + PATH_BACKSLASH + "Vault"
SYNTHETIC_MEMORY_UNC = PATH_BACKSLASH * 2 + "demo-host" + PATH_BACKSLASH + "share" + PATH_BACKSLASH + "memory"


class SnapshotContractTests(unittest.TestCase):
    def fixture_payload(self) -> dict:
        return {
            "generated_at": "2026-01-02T03:04:05+00:00",
            "revision": "fixture-rev",
            "provider": {
                "name": "open-second-brain",
                "available": True,
                "semantic": "indexed",
                "obsidian_vault_id": "demo-vault",
            },
            "graph": {
                "nodes": [
                    {
                        "id": "notes/zeta.md",
                        "label": "Zeta",
                        "kind": "note",
                        "layer": "vault",
                        "area": "references",
                        "preview": "Safe preview",
                        "modified_at": "2026-01-02T03:04:05+00:00",
                    },
                    {
                        "id": "Brain/active.md",
                        "label": "Active context",
                        "kind": "active",
                        "layer": "brain",
                        "area": "brain",
                        "preview": "to" + "ken=should-not-leak " + SYNTHETIC_HOME + "/private.md demo-vault",
                    },
                ],
                "edges": [
                    {"source": "notes/zeta.md", "target": "Brain/active.md", "kind": "wikilink"}
                ],
            },
            "counts": {"brain_nodes": 1, "vault_notes": 1},
            "graph_summary": {"hub_ids": [], "orphan_ids": [], "broken_count": 0},
            "timeline_events": [],
            "recent_logs": [],
            "artifacts": [],
            "vault_notes": [],
            "limits": {},
        }

    def test_fixture_reader_builds_snapshot_without_filesystem_access(self):
        reader = FixtureSnapshotReader(self.fixture_payload())
        with mock.patch.object(Path, "rglob", side_effect=AssertionError("filesystem access")):
            snapshot = build_snapshot(reader=reader)

        self.assertEqual(snapshot["schema"], SNAPSHOT_SCHEMA)
        self.assertEqual(len(snapshot["graph"]["nodes"]), 2)
        self.assertEqual(snapshot["provider"]["mode"], "fixture")

    def test_contract_redacts_paths_vault_identifier_and_secret_values(self):
        snapshot = normalize_snapshot(self.fixture_payload(), forbidden_values=("demo-vault",))
        serialized = json.dumps(snapshot, ensure_ascii=False)

        self.assertNotIn(SYNTHETIC_HOME, serialized)
        self.assertNotIn("demo-vault", serialized)
        self.assertNotIn("should-not-leak", serialized)
        self.assertIn("[redacted-path]", serialized)

    def test_generic_normalization_never_exports_obsidian_vault_identifier(self):
        vault_id = "RAW_VAULT_IDENTIFIER"
        payload = {
            "provider": {
                "name": "open-second-brain",
                "available": True,
                "obsidian_vault_id": vault_id,
            }
        }

        snapshot = normalize_snapshot(payload)
        serialized = json.dumps(snapshot, ensure_ascii=False)

        self.assertNotIn(vault_id, serialized)
        self.assertNotIn("obsidian_vault_id", snapshot["provider"])

    def test_text_sanitizer_redacts_single_segment_windows_and_bearer_values(self):
        cases = (
            ("/secret", "secret"),
            (SYNTHETIC_HOME + "/private note.md", "private note"),
            ("//demo-host/share/Vault/note.md", "demo-host"),
            (SYNTHETIC_WINDOWS + PATH_BACKSLASH + "note.md", "Demo" + " Example"),
            (SYNTHETIC_WINDOWS + PATH_BACKSLASH + "private note.md", "private note"),
            (SYNTHETIC_FILE_URI + "/note.md", "demo"),
            (SYNTHETIC_UNC + PATH_BACKSLASH + "note.md", "demo-host"),
            ("Bearer example-value", "example-value"),
            ("Authorization: Bearer " + "example-value", "example-value"),
            ("Authorization: Basic " + "example-value", "example-value"),
        )
        for value, forbidden in cases:
            with self.subTest(value=value):
                sanitized = sanitize_text(value)
                self.assertNotIn(forbidden, sanitized)
                self.assertNotEqual(sanitized, value)

    def test_nested_secret_keys_are_redacted_and_external_ids_are_hashed(self):
        payload = self.fixture_payload()
        payload["metrics"] = {
            "access_token": "metric-value",
            "x-api-key": "alias-api-value",
            "customCookie": "alias-cookie-value",
            "customAuthorization": "alias-auth-value",
            "nested": {"client_secret": "nested-value"},
        }
        payload["graph"]["nodes"] = [
            {"id": SYNTHETIC_FILE_URI + "/a.md", "metadata": {"api_key": "metadata-value"}},
            {"id": SYNTHETIC_UNC + PATH_BACKSLASH + "b.md", "metadata": {"cookie": "cookie-value"}},
        ]

        snapshot = normalize_snapshot(payload)
        serialized = json.dumps(snapshot, ensure_ascii=False)

        for forbidden in (
            "metric-value",
            "alias-api-value",
            "alias-cookie-value",
            "alias-auth-value",
            "nested-value",
            "metadata-value",
            "cookie-value",
            "demo",
            "demo-host",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertTrue(all(node["id"].startswith("external-") for node in snapshot["graph"]["nodes"]))

    def test_credential_and_private_key_aliases_redact_nested_list_values_across_surfaces(self):
        leaked_values = (
            "METRIC_CREDENTIAL_VALUE",
            "METRIC_CREDENTIALS_NESTED_VALUE",
            "METRIC_PRIVATE_KEY_VALUE",
            "METRIC_LIST_CREDENTIAL_VALUE",
            "METRIC_LIST_CREDENTIALS_VALUE",
            "METRIC_LIST_PRIVATEKEY_VALUE",
            "NODE_PRIVATE_KEY_VALUE",
            "ARTIFACT_CREDENTIALS_VALUE",
        )
        payload = {
            "graph": {
                "nodes": [
                    {
                        "id": "notes/safe.md",
                        "metadata": {"private_key": "NODE_PRIVATE_KEY_VALUE"},
                    }
                ],
                "edges": [],
            },
            "metrics": {
                "credential": "METRIC_CREDENTIAL_VALUE",
                "credentials": {"nested": "METRIC_CREDENTIALS_NESTED_VALUE"},
                "private_key": "METRIC_PRIVATE_KEY_VALUE",
                "nested": [
                    {"credential": "METRIC_LIST_CREDENTIAL_VALUE"},
                    {"credentials": {"nested": "METRIC_LIST_CREDENTIALS_VALUE"}},
                    {"privatekey": "METRIC_LIST_PRIVATEKEY_VALUE"},
                ],
            },
            "artifacts": [{"credentials": {"nested": "ARTIFACT_CREDENTIALS_VALUE"}}],
        }

        snapshot = normalize_snapshot(payload)
        serialized = json.dumps(snapshot, ensure_ascii=False)

        for leaked_value in leaked_values:
            self.assertNotIn(leaked_value, serialized)

    def test_every_exported_surface_redacts_recursive_topology_and_credentials(self):
        """All public projections must remove provider topology and credentials."""
        vault_id = "configured-vault-id-should-not-survive"
        secrets = (
            "NODE_BEARER_SECRET",
            "ARTIFACT_TOKEN_SECRET",
            "NOTE_QUERY_SECRET",
            "LOG_USERINFO_SECRET",
            "TIMELINE_COOKIE_SECRET",
            "METRIC_CLIENT_SECRET",
            "HEALTH_BEARER_SECRET",
        )
        url_user = "demo-user"
        query_key = "to" + "ken"
        hostile_url = (
            "https" + "://"
            + url_user
            + ":"
            + "LOG_USERINFO_SECRET"
            + "@"
            + "example.test/private?" + query_key + "=NOTE_QUERY_SECRET&client_" + "se" + "cret=METRIC_CLIENT_SECRET"
        )
        topology = {
            "path": "/private/node.md",
            "vaultPath": SYNTHETIC_WINDOWS + PATH_BACKSLASH + "node.md",
            "nested": [
                {"config-path": "file:" + "/" + "/private/config.yaml"},
                {"memoryRoot": SYNTHETIC_MEMORY_UNC},
                {"access_token": "ARTIFACT_TOKEN_SECRET"},
            ],
        }
        payload = {
            "provider": {
                "name": "open-second-brain",
                "available": True,
                "health": "Bearer HEALTH_BEARER_SECRET at /private/health",
                "obsidian_vault_id": vault_id,
            },
            "graph": {
                "nodes": [
                    {
                        "id": "notes/safe.md",
                        "label": "Owner-visible local label",
                        "metadata": dict(topology, authorization="Bearer NODE_BEARER_SECRET"),
                    }
                ],
                "edges": [],
            },
            "artifacts": [dict(topology, preview=hostile_url)],
            "vault_notes": [dict(topology, preview="Bearer NODE_BEARER_SECRET")],
            "recent_logs": [dict(topology, text=hostile_url)],
            "timeline_events": [dict(topology, text="Cookie: TIMELINE_COOKIE_SECRET")],
            "metrics": dict(topology, endpoint=hostile_url),
        }

        snapshot = normalize_snapshot(payload, forbidden_values=(vault_id,))
        serialized = json.dumps(snapshot, ensure_ascii=False)

        for forbidden in (
            *secrets,
            vault_id,
            url_user,
            "/private/",
            "C:" + PATH_BACKSLASH + "Users" + PATH_BACKSLASH + "Demo" + " Example",
            "demo-host" + PATH_BACKSLASH + "share" + PATH_BACKSLASH + "memory",
        ):
            self.assertNotIn(forbidden, serialized)
        for topology_key in ("path", "vaultPath", "config-path", "memoryRoot"):
            self.assertNotIn(f'"{topology_key}"', serialized)
        self.assertEqual(snapshot["nodes"][0]["label"], "Owner-visible local label")
        self.assertEqual(snapshot["provider"]["health"], "[redacted-path]")

    def test_recursively_strips_topology_alias_keys_inside_lists(self):
        payload = {
            "metrics": {
                "path": "/private/root",
                "vault": "configured-vault-id",
                "config_path": "/private/config.yaml",
                "memory_root": "/private/memory",
                "vault_path": "/private/vault",
                "source_path": "/private/source",
                "obsidianVault": "configured-vault-id",
                "nested": [
                    {
                        "Path": "/private/list-path",
                        "Vault Path": "/private/list-vault",
                        "config-path": "/private/list-config",
                        "memoryRoot": "/private/list-memory",
                        "source_path": "/private/list-source",
                    }
                ],
            }
        }

        serialized = json.dumps(
            normalize_snapshot(payload, forbidden_values=("configured-vault-id",)),
            ensure_ascii=False,
        )

        for forbidden in (
            "configured-vault-id",
            "/private/",
            '"path"',
            '"vault"',
            '"config_path"',
            '"memory_root"',
            '"vault_path"',
            '"source_path"',
            '"obsidianVault"',
            '"Vault Path"',
            '"memoryRoot"',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_missing_optional_metrics_and_graph_fields_fail_soft(self):
        snapshot = normalize_snapshot(
            {
                "provider": {"name": "open-second-brain", "available": False},
                "generated_at": "2026-01-02T03:04:05+00:00",
            }
        )

        self.assertEqual(snapshot["schema"], SNAPSHOT_SCHEMA)
        self.assertEqual(snapshot["graph"], {"nodes": [], "edges": []})
        self.assertEqual(snapshot["metrics"], {})
        self.assertEqual(snapshot["activity"]["timeline"], [])

    def test_nodes_edges_and_collections_are_sorted_deterministically(self):
        payload = self.fixture_payload()
        payload["graph"]["edges"].extend(
            [
                {"source": "Brain/active.md", "target": "notes/zeta.md", "kind": "surface"},
                {"source": "Brain/active.md", "target": "notes/zeta.md", "kind": "wikilink"},
            ]
        )
        first = normalize_snapshot(payload)
        second = normalize_snapshot(payload)

        self.assertEqual(first, second)
        self.assertEqual(
            [node["id"] for node in first["graph"]["nodes"]],
            ["Brain/active.md", "notes/zeta.md"],
        )
        self.assertEqual(
            [(edge["source"], edge["target"], edge["kind"]) for edge in first["graph"]["edges"]],
            sorted((edge["source"], edge["target"], edge["kind"]) for edge in first["graph"]["edges"]),
        )

    def test_fixture_reader_accepts_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(self.fixture_payload()), encoding="utf-8")
            snapshot = build_snapshot(reader=FixtureSnapshotReader(path))

        self.assertEqual(snapshot["revision"], "fixture-rev")


if __name__ == "__main__":
    unittest.main()
