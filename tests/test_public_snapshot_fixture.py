from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from dashboard.plugin_api import build_snapshot
from dashboard.snapshot_contract import SNAPSHOT_SCHEMA, FixtureSnapshotReader, normalize_snapshot


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FIXTURE = ROOT / "tests" / "fixtures" / "public_snapshot_v1.json"


class PublicSnapshotFixtureTests(unittest.TestCase):
    def test_public_fixture_is_a_complete_normalized_snapshot(self):
        payload = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
        normalized = normalize_snapshot(payload)

        self.assertEqual(payload, normalized)
        self.assertEqual(payload["schema"], SNAPSHOT_SCHEMA)
        self.assertGreaterEqual(len(payload["nodes"]), 8)
        self.assertGreaterEqual(len(payload["edges"]), 8)
        self.assertTrue(payload["summaries"]["counts"])
        self.assertTrue(payload["limits"])
        self.assertTrue(payload["activity"]["timeline"])
        self.assertTrue(payload["metrics"])

        node_ids = {node["id"] for node in payload["nodes"]}
        self.assertTrue(all(edge["source"] in node_ids and edge["target"] in node_ids for edge in payload["edges"]))

    def test_public_fixture_builds_without_vault_access(self):
        with mock.patch.object(Path, "rglob", side_effect=AssertionError("vault access")):
            snapshot = build_snapshot(reader=FixtureSnapshotReader(PUBLIC_FIXTURE))

        self.assertEqual(snapshot["schema"], SNAPSHOT_SCHEMA)
        self.assertEqual(snapshot["provider"]["mode"], "fixture")
        self.assertTrue(snapshot["graph"]["nodes"])

    def test_no_speculative_production_adapter_exists(self):
        self.assertFalse((ROOT / "dashboard" / "osb_public_adapter.py").exists())
        plugin_source = (ROOT / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", plugin_source)
        self.assertNotIn("graph-export", plugin_source)


if __name__ == "__main__":
    unittest.main()
