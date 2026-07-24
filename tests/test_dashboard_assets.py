from __future__ import annotations

import json
import re
import subprocess
import tomllib
import unittest
from pathlib import Path

from scripts import qa_dashboard_cdp


ROOT = Path(__file__).resolve().parents[1]


class DashboardAssetTests(unittest.TestCase):
    def test_release_version_is_consistent_across_metadata_assets_and_docs(self):
        manifest = json.loads((ROOT / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
        plugin_text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        plugin_version = re.search(r'(?m)^version:\s*["\']?([^"\'\s]+)', plugin_text)

        self.assertIsNotNone(plugin_version)
        assert plugin_version is not None
        version = plugin_version.group(1)
        self.assertEqual(manifest["version"], version)
        self.assertEqual(project["version"], version)
        self.assertEqual(manifest["css"], f"dist/style.css?v={version}")
        self.assertIn(f"Current release: `{version}`", readme)

    def test_behavioral_cdp_harness_covers_product_invariants(self):
        qa = (ROOT / "scripts" / "qa_dashboard_cdp.py").read_text(encoding="utf-8")
        for invariant in (
            "canvas_painted",
            "note_title_22px",
            "no_console_errors",
            "live_search",
            "filter_reset",
            "cross_area_navigation",
            "mobile_modes",
            "product_features",
            "layout_controls",
            "rendered_typography",
            "host_header_scope",
            "url_object_auth_scope",
            "host_main_no_vertical_overflow",
            "app_fits_host_main",
            "app_fits_visible_root",
            "internal_scroll_end_reachable",
            "graph_3d",
        ):
            self.assertIn(invariant, qa)
        self.assertIn("Page.captureScreenshot", qa)
        self.assertIn("report.json", qa)

    def test_manifest_registers_second_brain_dashboard(self):
        manifest = json.loads((ROOT / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "hermes-osb-panel")
        self.assertEqual(manifest["label"], "Open Second Brain")
        self.assertEqual(manifest["tab"]["path"], "/second-brain")
        self.assertEqual(manifest["entry"], "dist/index.js")
        self.assertEqual(manifest["version"], "3.1.0")
        self.assertEqual(manifest["css"], "dist/style.css?v=3.1.0")
        self.assertEqual(manifest["api"], "plugin_api.py")

    def test_frontend_uses_sdk_api_and_registers_plugin(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        self.assertIn("__HERMES_PLUGIN_SDK__", js)
        self.assertIn("/api/plugins/hermes-osb-panel/snapshot", js)
        self.assertIn('REG.register("hermes-osb-panel"', js)
        self.assertIn("fbsdk", js)
        self.assertIn("GraphCanvas", js)
        self.assertIn("SecondBrainDashboard", js)
        self.assertIn("graphCanvas", js)
        self.assertIn("simulate", js)
        self.assertIn("requestAnimationFrame", js)
        self.assertIn("graph-controls", js)
        self.assertIn("backlinks-section", js)
        self.assertIn("note-pane", js)
        self.assertIn("status-bar", js)
        # v2.1 features
        self.assertIn("areaColor", js)
        self.assertIn("nodeColor", js)
        self.assertIn("cleanLabel", js)
        self.assertIn("BottomPanel", js)
        self.assertIn("folder-children", js)
        self.assertIn("expandedS", js)
        # v3 mini-runtime lifecycle and browser input semantics
        self.assertIn("runCleanups", js)
        self.assertIn("flushEffects", js)
        self.assertIn("effect cleanup", js)
        self.assertIn('eventName==="change"', js)
        self.assertIn('eventName="input"', js)

    def test_dashboard_styles_do_not_request_external_fonts_or_assets(self):
        css = (ROOT / "dashboard" / "dist" / "style.css").read_text(encoding="utf-8")

        self.assertNotRegex(css, re.compile(r"@import", re.IGNORECASE))
        self.assertNotRegex(css, re.compile(r"https?://", re.IGNORECASE))
        self.assertNotRegex(css, re.compile(r"url\(\s*['\"]?\s*(?:(?:https?:)?//)", re.IGNORECASE))
        self.assertNotRegex(css, re.compile(r"@font-face", re.IGNORECASE))
        self.assertIn("ui-sans-serif", css)
        self.assertIn("ui-monospace", css)

    def test_styles_include_obsidian_and_vault_surfaces(self):
        css = (ROOT / "dashboard" / "dist" / "style.css").read_text(encoding="utf-8")
        self.assertIn("#0d1117", css)
        self.assertIn("#161b22", css)
        self.assertIn("ui-monospace", css)
        self.assertIn("var(--accent-purple)", css)
        self.assertIn("var(--accent-blue)", css)
        self.assertIn(".graph-pane", css)
        self.assertIn(".note-pane", css)
        self.assertIn(".sidebar", css)
        self.assertIn(".status-bar", css)
        self.assertIn(".graph-controls", css)
        self.assertIn(".backlink-item", css)
        self.assertIn("backdrop-filter", css)
        self.assertIn("@media", css)
        # v2.1 features
        self.assertIn("--area-projects", css)
        self.assertIn("--area-runbooks", css)
        self.assertIn("--area-decisions", css)
        self.assertIn(".bottom-panel", css)
        self.assertIn(".bp-tabs", css)
        self.assertIn(".bp-tab", css)
        self.assertIn(".folder-children", css)
        self.assertIn(".folder-arrow", css)

    def test_plugin_styles_are_isolated_from_the_host_dashboard(self):
        css = (ROOT / "dashboard" / "dist" / "style.css").read_text(encoding="utf-8")

        # The Hermes Dashboard loads every plugin stylesheet in the host
        # document. Global resets and generic component selectors therefore
        # corrupt Sessions, Kanban and every other dashboard page.
        for unsafe in (
            r"(?m)^\s*:root\s*\{",
            r"(?m)^\s*\*\s*\{",
            r"(?m)^\s*body(?:\s|,|\{)",
            r"(?m)^\s*header\s*\{",
            r"(?m)^\s*\.sidebar\s*\{",
            r"(?m)^\s*\.status-bar\s*\{",
            r"(?m)^\s*button\s*[,\{]",
        ):
            self.assertIsNone(re.search(unsafe, css), unsafe)

        self.assertIn(".osb-app, .osb-app *, .osb-app *::before, .osb-app *::after", css)
        self.assertIn(".osb-app header", css)
        self.assertIn(".osb-app .sidebar", css)
        self.assertIn(".osb-app .status-bar", css)

        host_header_rule = 'body:has(.osb-app) header[role="banner"]:has(~ main .osb-app){display:none!important}'
        self.assertIn(host_header_rule, css)
        self.assertEqual(css.count('header[role="banner"]'), 1)

    def test_layout_controls_use_compound_root_state_selectors(self):
        css = (ROOT / "dashboard" / "dist" / "style.css").read_text(encoding="utf-8")
        for selector in (
            ".osb-app.explorer-collapsed .sidebar",
            ".osb-app.inspector-collapsed .note-pane",
            ".osb-app.activity-collapsed .bottom-panel",
            ".osb-app.graph-maximized .sidebar",
        ):
            self.assertIn(selector, css)

        for broken_selector in (
            ".osb-app .explorer-collapsed .sidebar",
            ".osb-app .inspector-collapsed .note-pane",
            ".osb-app .activity-collapsed .bottom-panel",
            ".osb-app .graph-maximized .sidebar",
        ):
            self.assertNotIn(broken_selector, css)
        self.assertIn(".osb-app .views-drawer.open{display:block!important}", css)

    def test_graph_controls_are_discoverable_and_focus_modes_explain_counts(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        self.assertIn("Enquadrar todos os nós", js)
        self.assertIn("Focar a nota e conexões diretas", js)
        self.assertNotIn('\"data-graph-control\": \"overview\"', js)
        self.assertNotIn('}, \"FA\")', js)
        self.assertNotIn('}, \"FS\")', js)
        self.assertNotIn('}, \"OV\")', js)
        self.assertIn("focusNeighborhood", js)
        self.assertIn('\"data-focus-count\"', js)
        self.assertIn("vizinhos diretos", js)
        self.assertIn("Expandir grafo", js)
        self.assertIn("Redefinir painéis", js)

    def test_inspector_copy_actions_use_icons_instead_of_abbreviations(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        self.assertIn('uiIcon("copy",13)', js)
        self.assertIn('uiIcon("link",13)', js)
        self.assertIn('title:"Copiar caminho"', js)
        self.assertIn('title:"Copiar wikilink"', js)
        self.assertIn('"aria-label":"Copiar caminho da nota"', js)
        self.assertIn('"aria-label":"Copiar wikilink"', js)
        self.assertNotIn('},"CP")', js)
        self.assertNotIn('},"WL")', js)

    def test_graph_preserves_2d_and_offers_an_interactive_dependency_free_3d_mode(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "dist" / "style.css").read_text(encoding="utf-8")
        self.assertIn("function GraphCanvas3D(props)", js)
        self.assertIn('id:"graphCanvas3D"', js)
        self.assertIn('"data-graph-dimension":"3d"', js)
        self.assertIn('"data-graph-dimension":"2d"', js)
        self.assertIn('mode:"3d"', js)
        self.assertIn('mode:"2d"', js)
        self.assertIn(".osb-app .dimension-switch", css)
        self.assertIn(".osb-app #graphCanvas3D", css)
        self.assertNotIn("three.min.js", js.lower())
        self.assertNotIn("three.js", js.lower())

    def test_3d_has_safe_bounds_reduced_motion_and_lifecycle_instrumentation(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        for invariant in (
            "safePadding",
            "currentSceneRadius",
            "reducedMotion",
            "activeRafs",
            "activeListenerSets",
            "__OSB_GRAPH_DIAGNOSTICS__",
            '"aria-pressed":!reducedMotion',
            'capabilities.graph_3d',
        ):
            self.assertIn(invariant, js)
        self.assertIn("if(reducedMotion)return", js)
        self.assertIn("graph3dEnabled", js)

    def test_graph_labels_share_measured_safe_bounds_and_mobile_cdp_gate(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        qa = (ROOT / "scripts" / "qa_dashboard_cdp.py").read_text(encoding="utf-8")

        # One helper must drive both renderers; label geometry sent to CDP is
        # deliberately content-free so a public report cannot leak note names.
        self.assertIn("function labelSafeBounds(", js)
        self.assertGreaterEqual(js.count("labelSafeBounds("), 3)
        self.assertIn("ctx.measureText", js)
        self.assertIn("labelBounds", js)
        self.assertIn("labelSafeBounds", js)
        self.assertIn("label_bounds_mobile_safe", qa)
        self.assertIn("label_bounds_fit", qa)
        self.assertTrue(qa_dashboard_cdp.label_bounds_fit({
            "viewport": [390, 300], "labelSafePadding": 24,
            "labelBounds": [{"left": 24, "top": 30, "right": 366, "bottom": 70}],
        }))
        self.assertFalse(qa_dashboard_cdp.label_bounds_fit({
            "viewport": [390, 300], "labelSafePadding": 24,
            "labelBounds": [{"left": 23.4, "top": 30, "right": 366, "bottom": 70}],
        }))

    def test_cdp_harness_probes_reduced_motion_full_3d_controls_and_sanitizes_report(self):
        qa = (ROOT / "scripts" / "qa_dashboard_cdp.py").read_text(encoding="utf-8")
        for invariant in (
            "Emulation.setEmulatedMedia",
            "prefers-reduced-motion",
            "fit-selection",
            "fit-all",
            "activeRafs",
            "activeListenerSets",
            "sanitize_report",
            "safePadding",
        ):
            self.assertIn(invariant, qa)
        self.assertIn("state_before,state_after,state_zoom,state_fit_all", qa)
        self.assertIn("requestAnimationFrame(()=>requestAnimationFrame(resolve))", qa)

    def test_cdp_harness_accepts_dashboard_session_header_without_logging_it(self):
        qa = (ROOT / "scripts" / "qa_dashboard_cdp.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN"', qa)
        self.assertIn("X-Hermes-Session-Token", qa)
        self.assertIn("Page.addScriptToEvaluateOnNewDocument", qa)
        self.assertIn("no_cross_origin_auth", qa)
        self.assertIn("foreign_auth_requests", qa)
        self.assertNotIn("Network.setExtraHTTPHeaders", qa)
        self.assertIn("input instanceof Request ? input.url : String(input)", qa)
        self.assertIn("fetch(new URL(target.href))", qa)
        self.assertNotIn("print(session_token", qa)
        self.assertNotIn('"session_token":', qa)

    def test_session_fetch_wrapper_scopes_url_objects_to_the_page_origin(self):
        token = "unit-" + "session-value"
        source = qa_dashboard_cdp.session_fetch_wrapper_source(token)
        node_probe = f"""
global.window = global;
global.location = new URL('https://app.example.test/second-brain');
class TestHeaders {{
  constructor(base) {{ this.values = new Map(base && base.values ? base.values : []); }}
  set(name, value) {{ this.values.set(String(name).toLowerCase(), value); }}
  get(name) {{ return this.values.get(String(name).toLowerCase()); }}
}}
class TestRequest {{
  constructor(url) {{ this.url = String(url); this.headers = new TestHeaders(); }}
}}
global.Headers = TestHeaders;
global.Request = TestRequest;
const calls = [];
window.fetch = function(input, init) {{
  calls.push({{
    url: String(input instanceof TestRequest ? input.url : input),
    token: Boolean(init && init.headers && init.headers.get('X-Hermes-Session-Token'))
  }});
  return Promise.resolve({{ok: true}});
}};
eval({json.dumps(source)});
Promise.resolve()
  .then(() => window.fetch(new URL('https://foreign.example.test/data')))
  .then(() => window.fetch(new URL('https://app.example.test/api/plugins/demo')))
  .then(() => process.stdout.write(JSON.stringify(calls)));
"""
        result = subprocess.run(
            ["node", "-e", node_probe],
            check=True,
            capture_output=True,
            text=True,
        )
        calls = json.loads(result.stdout)

        self.assertEqual(calls[0], {"url": "https://foreign.example.test/data", "token": False})
        self.assertEqual(calls[1]["url"], "https://app.example.test/api/plugins/demo")
        self.assertTrue(calls[1]["token"])

    def test_reusable_sources_have_no_local_identity_or_vault_defaults(self):
        paths = [
            ROOT / "dashboard" / "plugin_api.py",
            ROOT / "dashboard" / "snapshot_contract.py",
            ROOT / "dashboard" / "dist" / "index.js",
            ROOT / "dashboard" / "manifest.json",
            ROOT / "README.md",
            ROOT / "plugin.yaml",
        ]
        forbidden = (
            "/" + "home" + "/demo/Vault",
            "demo-vault",
            "Owner" + " Example",
            "example.test",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for value in forbidden:
            self.assertNotIn(value, combined)
        self.assertNotIn('"pt-BR"', combined)

    def test_obsidian_action_requires_explicit_safe_vault_identifier(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        self.assertIn("provider.obsidian_vault_id", js)
        self.assertIn("function obsidianLink", js)
        self.assertNotIn("obsidian://open?vault=demo-vault", js)

    def test_open_second_brain_brand_fills_the_host_main_without_page_scroll(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "dist" / "style.css").read_text(encoding="utf-8")
        self.assertIn('"OPEN_SECOND_BRAIN"', js)
        self.assertNotIn('"SECOND_BRAIN"', js)
        self.assertIn("body:has(.osb-app) main:has(.osb-app){overflow-y:hidden!important}", css)
        self.assertIn("body:has(.osb-app) main:has(.osb-app)>div:has(.osb-app)>div:has(.osb-app)", css)
        self.assertIn("body:has(.osb-app){display:grid!important;grid-template-rows:minmax(0,1fr)!important}", css)
        self.assertIn("body:has(.osb-app) #root{height:auto!important;min-height:0!important", css)
        self.assertIn("body:has(.osb-app) #root>div:has(.osb-app){height:100%!important;max-height:100%!important", css)
        self.assertIn("padding-bottom:0!important", css)
        self.assertIn("#osbStandaloneRoot{height:100dvh", css)
        self.assertIn(".osb-app{height:auto;min-height:0;flex:1 1 auto}", css)
        self.assertIn(".osb-app.mode-vault .sidebar{display:block!important;position:absolute;inset:0;width:100%;height:auto", css)
        self.assertIn(".osb-app.mode-activity .bottom-panel{display:flex!important;height:100%;max-height:100%", css)

    def test_rendered_markdown_headings_override_host_scale_compactly(self):
        css = (ROOT / "dashboard" / "dist" / "style.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.osb-app \.markdown-rendered h1\{[^}]*font-size:15px!important")
        self.assertRegex(css, r"\.osb-app \.markdown-rendered h2\{[^}]*font-size:13px!important")
        self.assertRegex(css, r"\.osb-app \.markdown-rendered h3,[^{]+\{[^}]*font-size:12px!important")
        self.assertRegex(css, r"\.osb-app \.note-title\{[^}]*font-weight:590!important")


if __name__ == "__main__":
    unittest.main()
