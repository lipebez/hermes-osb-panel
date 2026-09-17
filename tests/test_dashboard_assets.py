from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from socketserver import BaseRequestHandler, ThreadingTCPServer
from pathlib import Path
from unittest.mock import patch

from scripts import qa_dashboard_cdp


ROOT = Path(__file__).resolve().parents[1]


class DashboardAssetTests(unittest.TestCase):
    def test_browser_proxy_forwards_only_exact_fixture_origin_without_dns(self):
        fixture = ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"
        with qa_dashboard_cdp.demo_server(fixture, b"js", b"css") as url:
            with qa_dashboard_cdp.browser_proxy(url) as (boundary, proxy_port):
                exact = url.rsplit("/", 1)[0] + "/assets/index.js"
                connection = qa_dashboard_cdp.http.client.HTTPConnection("127.0.0.1", proxy_port)
                connection.request("GET", exact, headers={"Host": url.split("/", 3)[2]})
                self.assertEqual(connection.getresponse().read(), b"js")
                connection.close()

                connection = qa_dashboard_cdp.http.client.HTTPConnection("127.0.0.1", proxy_port)
                connection.connect()
                with patch.object(qa_dashboard_cdp.socket, "getaddrinfo", side_effect=AssertionError("denial must not resolve")):
                    connection.request("GET", "http://example.invalid/nope", headers={"Host": "example.invalid"})
                    self.assertEqual(connection.getresponse().status, 403)
                    connection.close()
            self.assertTrue(boundary.blocked())

    def test_browser_proxy_rejects_non_exact_targets_and_sanitizes_denials(self):
        fixture_origin = "http:" + "//" + "127.0.0.1:8123"
        credential_target = "http:" + "//" + "user:" + "secret@127.0.0.1:8123/private?" + "token=secret#fragment"
        boundary = qa_dashboard_cdp.BrowserProxyBoundary(fixture_origin + "/second-brain")
        denied = (
            ("CONNECT", "127.0.0.1:8123", "127.0.0.1:8123", "connect_denied"),
            ("GET", "/relative", "127.0.0.1:8123", "origin_denied"),
            ("GET", "http:" + "//" + "localhost:8123/alias", "localhost:8123", "origin_denied"),
            ("GET", "http:" + "//" + "[::1]:8123/alias", "[::1]:8123", "origin_denied"),
            ("GET", "http:" + "//" + "127.0.0.1:8124/port", "127.0.0.1:8124", "origin_denied"),
            ("GET", "https:" + "//" + "127.0.0.1:8123/scheme", "127.0.0.1:8123", "origin_denied"),
            ("GET", fixture_origin + "/host", "localhost:8123", "host_denied"),
            ("GET", credential_target, "127.0.0.1:8123", "origin_denied"),
        )
        with patch.object(qa_dashboard_cdp.socket, "getaddrinfo", side_effect=AssertionError("denial must not resolve")) as resolver:
            for method, target, host, reason in denied:
                with self.subTest(target=target, host=host):
                    self.assertEqual(boundary.authorize(method, target, host), (False, reason))
                    boundary.record("proxy-denied", target, False, reason=reason)
        resolver.assert_not_called()
        serialized = json.dumps(boundary.blocked())
        self.assertNotIn("secret", serialized)
        self.assertNotIn("token=", serialized)
        self.assertNotIn("fragment", serialized)
        self.assertIn("/private", serialized)

    def test_chromium_global_proxy_flags_cover_loopback_and_non_proxy_udp(self):
        fixture_url = "http:" + "//" + "127.0.0.1:8123/second-brain"
        command = qa_dashboard_cdp.chromium_command(
            "/usr/bin/chromium", 9222, "/tmp/profile", fixture_url, 8118,
            redirect_browser_internals=True,
        )
        self.assertIn("--proxy-server=" + "http:" + "//" + "127.0.0.1:8118", command)
        self.assertIn("--proxy-bypass-list=<-loopback>", command)
        self.assertIn("--disable-quic", command)
        self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", command)
        self.assertIn("--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", command)
        self.assertNotIn("--no-proxy-server", command)

        disabled = next(item for item in command if item.startswith("--disable-features="))
        disabled_features = set(disabled.split("=", 1)[1].split(","))
        self.assertTrue({
            "NetworkTimeServiceQuerying",
            "SearchEnginePreconnector",
            "DefaultSearchEnginePrewarm",
            "PreconnectToSearch",
        }.issubset(disabled_features))
        origin = fixture_url.rsplit("/", 1)[0]
        for switch, path in qa_dashboard_cdp.BROWSER_INTERNAL_ENDPOINTS:
            self.assertIn(f"--{switch}={origin}{path}", command)
        self.assertEqual(
            qa_dashboard_cdp.BROWSER_INTERNAL_RESERVED_PATHS,
            {
                qa_dashboard_cdp.BROWSER_INTERNAL_PREFIX + "gcm-checkin",
                qa_dashboard_cdp.BROWSER_INTERNAL_PREFIX + "gcm-mcs",
            },
        )
        self.assertTrue(qa_dashboard_cdp.BROWSER_INTERNAL_RESERVED_PATHS.isdisjoint({
            "/", "/second-brain", "/assets/index.js", "/assets/style.css",
            "/api/plugins/hermes-osb-panel/snapshot",
        }))

        live_command = qa_dashboard_cdp.chromium_command(
            "/usr/bin/chromium", 9222, "/tmp/profile", fixture_url, 8118,
            redirect_browser_internals=False,
        )
        self.assertFalse(any(any(item.startswith(f"--{switch}=") for switch, _ in qa_dashboard_cdp.BROWSER_INTERNAL_ENDPOINTS) for item in live_command))

    def test_fixture_server_reserves_browser_internal_paths_without_asset_or_api_overlap(self):
        fixture = ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"
        request_log: list[str] = []
        with qa_dashboard_cdp.demo_server(fixture, b"js", b"css", request_log=request_log) as url:
            origin = url.rsplit("/", 1)[0]
            for path in sorted(qa_dashboard_cdp.BROWSER_INTERNAL_RESERVED_PATHS):
                request = urllib.request.Request(origin + path, method="GET")
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 204)
                    self.assertEqual(response.read(), b"")
            with self.assertRaises(urllib.error.HTTPError) as unknown:
                urllib.request.urlopen(origin + qa_dashboard_cdp.BROWSER_INTERNAL_PREFIX + "unknown")
            self.assertEqual(unknown.exception.code, 404)
            request = urllib.request.Request(
                origin + "/api/plugins/hermes-osb-panel/snapshot", data=b"", method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as api_post:
                urllib.request.urlopen(request)
            self.assertEqual(api_post.exception.code, 404)

        self.assertEqual(
            request_log[:-2],
            sorted(qa_dashboard_cdp.BROWSER_INTERNAL_RESERVED_PATHS),
        )
        self.assertEqual(request_log[-2:], [
            qa_dashboard_cdp.BROWSER_INTERNAL_PREFIX + "unknown",
            "/api/plugins/hermes-osb-panel/snapshot",
        ])

    def test_cleanup_preserves_first_baseexception_and_stops_process_and_servers(self):
        class DisableFailure(BaseException):
            pass

        disable_attempts = 0
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        servers = [ThreadingTCPServer(("127.0.0.1", 0), BaseRequestHandler) for _ in range(2)]
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
        for thread in threads:
            thread.start()

        def disable():
            nonlocal disable_attempts
            disable_attempts += 1
            raise DisableFailure("disable-first")

        def close():
            raise KeyboardInterrupt("close-second")

        def close_server(server, thread):
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        steps = [disable, close, lambda: qa_dashboard_cdp.stop_owned_group(process, process.pid)]
        steps.extend(lambda s=s, t=t: close_server(s, t) for s, t in zip(servers, threads))
        with self.assertRaisesRegex(DisableFailure, "disable-first"):
            qa_dashboard_cdp.run_cleanup_steps(None, steps)

        self.assertEqual(disable_attempts, 2)
        self.assertIsNotNone(process.poll())
        self.assertTrue(all(server.fileno() == -1 for server in servers))
        self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_cleanup_resumes_interrupted_real_process_stop_and_preserves_identity(self):
        class StopInterrupted(BaseException):
            pass

        cancellation = StopInterrupted("stop-first")
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        profile = tempfile.TemporaryDirectory(prefix="cleanup-profile-")
        profile_path = Path(profile.name)
        server = ThreadingTCPServer(("127.0.0.1", 0), BaseRequestHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        stop_attempts = 0
        later_steps: list[str] = []
        original_killpg = qa_dashboard_cdp.os.killpg
        interrupted = False

        def interrupted_stop():
            nonlocal stop_attempts
            stop_attempts += 1
            qa_dashboard_cdp.stop_owned_group(process, process.pid)

        def interrupt_killpg(pgid, sig):
            nonlocal interrupted
            if not interrupted and sig == 0:
                interrupted = True
                raise cancellation
            return original_killpg(pgid, sig)

        def close_profile():
            profile.cleanup()
            later_steps.append("profile")

        def close_server():
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3)
            later_steps.append("servers")

        try:
            with (
                patch.object(qa_dashboard_cdp.os, "killpg", side_effect=interrupt_killpg),
                self.assertRaises(StopInterrupted) as raised,
            ):
                qa_dashboard_cdp.run_cleanup_steps(
                    None,
                    [
                        interrupted_stop,
                        close_profile,
                        close_server,
                    ],
                )
        finally:
            if process.poll() is None:
                qa_dashboard_cdp.stop_owned_group(process, process.pid)
            if profile_path.exists():
                profile.cleanup()
            if server.fileno() != -1:
                server.shutdown()
                server.server_close()
            server_thread.join(timeout=3)

        self.assertIs(raised.exception, cancellation)
        self.assertEqual(stop_attempts, 2)
        self.assertIsNotNone(process.poll())
        self.assertEqual(later_steps, ["profile", "servers"])
        self.assertFalse(profile_path.exists())
        self.assertEqual(server.fileno(), -1)
        self.assertFalse(server_thread.is_alive())

    def test_demo_server_cleanup_resumes_interrupted_shutdown(self):
        class ShutdownInterrupted(BaseException):
            pass

        cancellation = ShutdownInterrupted("shutdown-first")
        original_server = qa_dashboard_cdp.ThreadingHTTPServer
        created = []
        shutdown_attempts = 0

        def interruptible_server(*args, **kwargs):
            nonlocal shutdown_attempts
            server = original_server(*args, **kwargs)
            original_shutdown = server.shutdown

            def shutdown():
                nonlocal shutdown_attempts
                shutdown_attempts += 1
                if shutdown_attempts == 1:
                    raise cancellation
                return original_shutdown()

            server.shutdown = shutdown
            created.append(server)
            return server

        fixture = ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"
        with patch.object(qa_dashboard_cdp, "ThreadingHTTPServer", side_effect=interruptible_server):
            context = qa_dashboard_cdp.demo_server(fixture, b"js", b"css")
            context.__enter__()
            with self.assertRaises(ShutdownInterrupted) as raised:
                context.__exit__(None, None, None)

        self.assertIs(raised.exception, cancellation)
        self.assertEqual(shutdown_attempts, 2)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].fileno(), -1)

    @unittest.skipUnless(hasattr(os, "memfd_create"), "Linux memfd required")
    def test_main_teardown_survives_cdp_baseexceptions_and_closes_real_resources(self):
        class DisableFailure(BaseException):
            pass

        class FakeCDP:
            events = []

            def __init__(self, url):
                self.url = url

            def call(self, method, params=None):
                return {}

            def enable_egress_boundary(self, url, require_fixture_origin=True):
                self.boundary = (url, require_fixture_origin)

            def disable_egress_boundary(self):
                raise DisableFailure("disable-first")

            def close(self):
                raise KeyboardInterrupt("close-second")

        fixture = ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"
        original_demo_server = qa_dashboard_cdp.demo_server
        original_browser_proxy = qa_dashboard_cdp.browser_proxy
        ports: list[int] = []

        @contextmanager
        def tracked_demo(*args, **kwargs):
            with original_demo_server(*args, **kwargs) as url:
                ports.append(int(url.split(":")[2].split("/")[0]))
                yield url

        @contextmanager
        def tracked_proxy(*args, **kwargs):
            with original_browser_proxy(*args, **kwargs) as value:
                ports.append(value[1])
                yield value

        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        js_fd = qa_dashboard_cdp.create_sealed_memfd("teardown-js", b"js")
        css_fd = qa_dashboard_cdp.create_sealed_memfd("teardown-css", b"css")
        captured_command: list[str] = []

        def return_process(command, **kwargs):
            captured_command.extend(command)
            self.assertTrue(kwargs["start_new_session"])
            return process

        try:
            with (
                tempfile.TemporaryDirectory() as output,
                patch.object(qa_dashboard_cdp, "demo_server", side_effect=tracked_demo),
                patch.object(qa_dashboard_cdp, "browser_proxy", side_effect=tracked_proxy),
                patch.object(qa_dashboard_cdp.subprocess, "Popen", side_effect=return_process),
                patch.object(qa_dashboard_cdp, "get_json", return_value=[{"type": "page", "webSocketDebuggerUrl": "ws://test"}]),
                patch.object(qa_dashboard_cdp, "CDP", FakeCDP),
                patch.object(qa_dashboard_cdp, "run_viewport", return_value={
                    "viewport": {"width": 390, "height": 844}, "checks": [],
                    "metrics": {}, "probes": {}, "console_errors": [], "screenshot": "shot.png",
                }),
            ):
                with self.assertRaisesRegex(DisableFailure, "disable-first"):
                    qa_dashboard_cdp.main([
                        "--url", "http:" + "//" + "127.0.0.1:1/second-brain", "--output", output,
                        "--fixture", str(fixture), "--asset-js-fd", str(js_fd),
                        "--asset-css-fd", str(css_fd), "--chromium", "/bin/true",
                        "--viewport", "390x844",
                    ])
        finally:
            if process.poll() is None:
                qa_dashboard_cdp.stop_owned_group(process, process.pid)

        profile_arg = next(item for item in captured_command if item.startswith("--user-data-dir="))
        self.assertFalse(Path(profile_arg.split("=", 1)[1]).exists())
        self.assertIsNotNone(process.poll())
        self.assertEqual(len(ports), 2)
        for port in ports:
            with self.subTest(port=port), qa_dashboard_cdp.socket.socket() as probe:
                self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)

    def test_cdp_egress_boundary_allows_only_exact_fixture_origin(self):
        ipv4 = "127.0.0.1"
        local_name = "localhost"
        ipv6 = "::1"
        boundary = qa_dashboard_cdp.EgressBoundary(f"http://{ipv4}:8123/second-brain")

        self.assertTrue(boundary.allows(f"http://{ipv4}:8123/assets/index.js"))
        self.assertTrue(boundary.allows("data:text/plain,fixture"))
        self.assertTrue(boundary.allows(f"blob:http://{ipv4}:8123/id"))
        for url in (
            f"http://{ipv4}:8124/",
            f"http://{local_name}:8123/",
            f"http://[{ipv6}]:8123/",
            f"https://{ipv4}:8123/",
            f"ws://{ipv4}:8123/",
            f"wss://{ipv4}:8123/",
            "file:///etc/passwd",
            "chrome-extension://fixture/page.html",
            "http://example.invalid/",
        ):
            with self.subTest(url=url):
                self.assertFalse(boundary.allows(url))

    @unittest.skipUnless(
        hasattr(os, "memfd_create")
        and shutil.which("chromium")
        and importlib.util.find_spec("websocket") is not None,
        "Linux Chromium and websocket-client required",
    )
    def test_real_clean_chromium_fixture_has_no_browser_global_denials(self):
        chromium = shutil.which("chromium")
        assert chromium is not None
        fixture = ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"
        installed_js = (ROOT / "dashboard" / "dist" / "index.js").read_bytes()
        installed_css = (ROOT / "dashboard" / "dist" / "style.css").read_bytes()
        js_fd = qa_dashboard_cdp.create_sealed_memfd("clean-egress-js", installed_js)
        css_fd = qa_dashboard_cdp.create_sealed_memfd("clean-egress-css", installed_css)
        request_log: list[str] = []
        proxy_records: list[dict[str, object]] = []
        original_demo_server = qa_dashboard_cdp.demo_server
        original_browser_proxy = qa_dashboard_cdp.browser_proxy

        @contextmanager
        def probing_server(path, javascript, stylesheet):
            with original_demo_server(path, javascript, stylesheet, request_log=request_log) as url:
                yield url

        @contextmanager
        def probing_proxy(url, require_fixture_origin=True):
            with original_browser_proxy(url, require_fixture_origin=require_fixture_origin) as value:
                boundary, _ = value
                yield value
                proxy_records.extend(boundary.snapshot())

        def network_probe(cdp, url, output, width, height, browser_boundary):
            cdp.call("Page.navigate", {"url": url})
            qa_dashboard_cdp.wait_for(cdp, "document.readyState === 'complete'")
            time.sleep(3)
            cdp.drain()
            blocked = browser_boundary.blocked()
            return {
                "viewport": {"width": width, "height": height}, "metrics": {}, "probes": {},
                "checks": [{"name": "browser_global_egress_boundary", "passed": not blocked, "detail": blocked}],
                "console_errors": [], "browser_network": browser_boundary.snapshot(), "screenshot": "",
            }

        with (
            tempfile.TemporaryDirectory() as output,
            patch.object(qa_dashboard_cdp, "demo_server", side_effect=probing_server),
            patch.object(qa_dashboard_cdp, "browser_proxy", side_effect=probing_proxy),
            patch.object(qa_dashboard_cdp, "run_viewport", side_effect=network_probe),
        ):
            result = qa_dashboard_cdp.main([
                "--url", "http:" + "//" + "127.0.0.1:1/second-brain",
                "--output", output,
                "--fixture", str(fixture),
                "--asset-js-fd", str(js_fd),
                "--asset-css-fd", str(css_fd),
                "--chromium", chromium,
                "--viewport", "390x844",
            ])
            report = json.loads((Path(output) / "report.json").read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertTrue(report["passed"])
        self.assertEqual([item for item in proxy_records if not item["allowed"]], [])
        internal_requests = [path for path in request_log if path.startswith(qa_dashboard_cdp.BROWSER_INTERNAL_PREFIX)]
        self.assertTrue(internal_requests)
        self.assertTrue(set(internal_requests).issubset(qa_dashboard_cdp.BROWSER_INTERNAL_RESERVED_PATHS))

    @unittest.skipUnless(
        hasattr(os, "memfd_create")
        and shutil.which("chromium")
        and importlib.util.find_spec("websocket") is not None,
        "Linux Chromium and websocket-client required",
    )
    def test_real_chromium_blocks_and_fails_all_fixture_egress_attempts(self):
        class CanaryHandler(BaseRequestHandler):
            def handle(inner_self):
                canary_hits.append(inner_self.request.recv(80))

        canary_hits: list[bytes] = []
        canary = ThreadingTCPServer(("127.0.0.1", 0), CanaryHandler)
        canary_thread = threading.Thread(target=canary.serve_forever, daemon=True)
        canary_thread.start()
        port = canary.server_address[1]
        attacks = f"""
window.addEventListener('load', () => setTimeout(() => {{
  for (const url of [
    'http://example.invalid/internet',
    'http://127.0.0.1:{port}/other-port',
    'http://localhost:{port}/localhost-alias',
    'http://[::1]:{port}/ipv6-alias',
    'https://127.0.0.1:{port}/https-scheme',
    '/__qa_redirect'
  ]) fetch(url).catch(() => {{}});
  for (const url of ['ws://127.0.0.1:{port}/ws', 'wss://127.0.0.1:{port}/wss']) {{
    try {{ const socket = new WebSocket(url); socket.onerror = () => {{}}; }} catch (_) {{}}
  }}
  navigator.serviceWorker.register('/__qa_service_worker.js').catch(() => {{}});
  try {{ window.open('http://127.0.0.1:{port}/popup-target', '_blank'); }} catch (_) {{}}
}}, 400));
""".encode()
        service_worker_script = f"""
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil((async () => {{
  await self.clients.claim();
  try {{ await fetch('http://127.0.0.1:{port}/service-worker-target'); }} catch (_) {{}}
  try {{ await fetch('/__qa_redirect'); }} catch (_) {{}}
}})()));
""".encode()
        installed_js = (ROOT / "dashboard" / "dist" / "index.js").read_bytes() + attacks
        installed_css = (ROOT / "dashboard" / "dist" / "style.css").read_bytes()
        fixture = ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"
        request_log: list[str] = []
        original_demo_server = qa_dashboard_cdp.demo_server
        original_browser_proxy = qa_dashboard_cdp.browser_proxy
        proxy_records: list[dict[str, object]] = []

        @contextmanager
        def probing_server(path, javascript, stylesheet):
            with original_demo_server(
                path,
                javascript,
                stylesheet,
                redirect_target=f"http://127.0.0.1:{port}/redirect-target",
                request_log=request_log,
                service_worker_script=service_worker_script,
            ) as url:
                yield url

        @contextmanager
        def probing_proxy(url, require_fixture_origin=True):
            with original_browser_proxy(url, require_fixture_origin=require_fixture_origin) as value:
                boundary, _ = value
                yield value
                proxy_records.extend(boundary.snapshot())

        js_fd = qa_dashboard_cdp.create_sealed_memfd("egress-js", installed_js)
        css_fd = qa_dashboard_cdp.create_sealed_memfd("egress-css", installed_css)

        def network_probe(cdp, url, output, width, height, browser_boundary):
            cdp.call("Page.navigate", {"url": url})
            qa_dashboard_cdp.wait_for(cdp, "document.readyState === 'complete'")
            time.sleep(3)
            cdp.drain()
            blocked = browser_boundary.blocked()
            return {
                "viewport": {"width": width, "height": height}, "metrics": {}, "probes": {},
                "checks": [{"name": "browser_global_egress_boundary", "passed": not blocked, "detail": blocked}],
                "console_errors": [], "browser_network": browser_boundary.snapshot(), "screenshot": "",
            }

        try:
            with (
                tempfile.TemporaryDirectory() as output,
                patch.object(qa_dashboard_cdp, "demo_server", side_effect=probing_server),
                patch.object(qa_dashboard_cdp, "browser_proxy", side_effect=probing_proxy),
                patch.object(qa_dashboard_cdp, "run_viewport", side_effect=network_probe),
            ):
                result = qa_dashboard_cdp.main([
                    "--url", f"http://{'127.0.0.1'}:1/second-brain",
                    "--output", output,
                    "--fixture", str(fixture),
                    "--asset-js-fd", str(js_fd),
                    "--asset-css-fd", str(css_fd),
                    "--chromium", shutil.which("chromium"),
                    "--viewport", "390x844",
                ])
                report = json.loads((Path(output) / "report.json").read_text(encoding="utf-8"))
        finally:
            canary.shutdown()
            canary.server_close()
            canary_thread.join(timeout=3)

        self.assertEqual(result, 1)
        self.assertIn("390x844:browser_global_egress_boundary", report["failures"])
        self.assertEqual(canary_hits, [])
        self.assertIn("/assets/index.js", request_log)
        self.assertIn("/api/plugins/hermes-osb-panel/snapshot", request_log)
        self.assertIn("/__qa_redirect", request_log)
        denied = [item for item in proxy_records if not item["allowed"]]
        denied_urls = " ".join(item["url"] for item in denied)
        self.assertIn("popup-target", denied_urls)
        self.assertIn("service-worker-target", denied_urls)
        self.assertIn("redirect-target", denied_urls)

    def test_fixture_server_serves_only_explicit_installed_asset_bytes(self):
        fixture = ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"
        checkout_js = (ROOT / "dashboard" / "dist" / "index.js").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary) / "installed" / "dist"
            assets.mkdir(parents=True)
            installed_js = b"window.__B2A_INSTALLED_ASSET__ = true;"
            installed_css = b".installed-b2a-canary{display:block}"
            self.assertNotEqual(installed_js, checkout_js)
            (assets / "index.js").write_bytes(installed_js)
            (assets / "style.css").write_bytes(installed_css)
            with qa_dashboard_cdp.demo_server(fixture, installed_js, installed_css) as url:
                origin = url.rsplit("/", 1)[0]
                self.assertEqual(urllib.request.urlopen(origin + "/assets/index.js").read(), installed_js)
                self.assertEqual(urllib.request.urlopen(origin + "/assets/style.css").read(), installed_css)

    def test_fixture_server_keeps_validated_bytes_after_path_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"
            path = Path(temporary) / "index.js"
            path.write_bytes(b"validated")
            validated = path.read_bytes()
            path.write_bytes(b"replacement")
            with qa_dashboard_cdp.demo_server(fixture, validated, b"css") as url:
                origin = url.rsplit("/", 1)[0]
                self.assertEqual(urllib.request.urlopen(origin + "/assets/index.js").read(), b"validated")

    @unittest.skipUnless(hasattr(os, "memfd_create"), "Linux memfd required")
    def test_asset_memfd_is_sealed_and_read_once(self):
        fd = qa_dashboard_cdp.create_sealed_memfd("test-asset", b"validated")
        self.assertEqual(qa_dashboard_cdp.read_sealed_memfd(fd), b"validated")
        with self.assertRaises(OSError):
            os.fstat(fd)
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
        self.assertIn(f"Source version: `{version}`", readme)

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

    def test_graph_controls_are_discoverable_and_focus_modes_explain_counts_in_english(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        self.assertIn("Fit all nodes", js)
        self.assertIn("Focus selected note and direct connections", js)
        self.assertNotIn("Enquadrar todos os nós", js)
        self.assertNotIn("Focar a nota e conexões diretas", js)
        self.assertNotIn('\"data-graph-control\": \"overview\"', js)
        self.assertNotIn('}, \"FA\")', js)
        self.assertNotIn('}, \"FS\")', js)
        self.assertNotIn('}, \"OV\")', js)
        self.assertIn("focusNeighborhood", js)
        self.assertIn('\"data-focus-count\"', js)
        self.assertIn("direct neighbors", js)
        self.assertIn("Expand graph", js)
        self.assertIn("Reset panels", js)

    def test_ui_and_vault_area_labels_are_english(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        for label in (
            'preference:"Preference"',
            'signal:"Signal"',
            'note:"Note"',
            'projects:"Projects"',
            'clients:"Clients"',
            'decisions:"Decisions"',
            'references:"References"',
            'other:"Other"',
            "Search notes and commands",
            "Vault explorer",
            "Deterministic summary",
        ):
            self.assertIn(label, js)

        for legacy_label in (
            'preference:"Preferência"',
            'note:"Nota"',
            'projects:"Projetos"',
            'decisions:"Decisões"',
            'references:"Referências"',
            'other:"Outros"',
        ):
            self.assertNotIn(legacy_label, js)

    def test_inspector_copy_actions_use_icons_instead_of_abbreviations(self):
        js = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
        self.assertIn('uiIcon("copy",13)', js)
        self.assertIn('uiIcon("link",13)', js)
        self.assertIn('title:"Copy path"', js)
        self.assertIn('title:"Copy wikilink"', js)
        self.assertIn('"aria-label":"Copy note path"', js)
        self.assertIn('"aria-label":"Copy wikilink"', js)
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

    def test_cdp_harness_allows_only_strict_loopback_urls(self):
        http = "http" + "://"
        https = "https" + "://"
        accepted = (
            http + "127.0.0.1" + ":8123/second-brain",
            https + "localhost" + "/second-brain?" + "mode=qa",
            http + "[" + "::1" + "]:9222/second-brain",
        )
        rejected = (
            "ftp" + "://" + "localhost/second-brain",
            http + "dashboard.example.test/second-brain",
            http + "127.0.0.1.evil.test/second-brain",
            http + "user" + "@localhost/second-brain",
            https + "user" + ":" + "password" + "@127.0.0.1/second-brain",
            "http:///second-brain",
            http + "localhost:not-a-port/second-brain",
        )

        for url in accepted:
            self.assertEqual(qa_dashboard_cdp.parse_loopback_url(url), url)
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaises(argparse.ArgumentTypeError):
                    qa_dashboard_cdp.parse_loopback_url(url)

    def test_cdp_harness_rejects_external_url_before_output_or_credentials(self):
        output = ROOT / "qa-cdp-rejected-output-must-not-exist"
        credential_names = {
            "HERMES_WEBUI_PASSWORD",
            "HERMES_WEBUI_ENV_FILE",
            "HERMES_DASHBOARD_SESSION_TOKEN",
        }
        credential_reads: list[str] = []
        original_environ_get = qa_dashboard_cdp.os.environ.get

        def reject_credential_read(key: str, default: str | None = None) -> str | None:
            if key in credential_names:
                credential_reads.append(key)
                raise AssertionError("credentials must not be read")
            return original_environ_get(key, default)

        self.assertFalse(output.exists())
        with (
            patch.object(sys, "argv", ["qa_dashboard_cdp.py", "--url", "https://external.example.test/second-brain", "--output", str(output)]),
            patch.object(Path, "mkdir", side_effect=AssertionError("output must not be created")) as mkdir,
            patch.object(qa_dashboard_cdp.shutil, "which", side_effect=AssertionError("Chromium discovery must not run")) as chromium_which,
            patch.object(qa_dashboard_cdp, "local_auth_cookie", side_effect=AssertionError("local auth must not run")) as local_auth,
            patch.object(qa_dashboard_cdp.os.environ, "get", side_effect=reject_credential_read),
            patch.object(qa_dashboard_cdp.subprocess, "Popen", side_effect=AssertionError("Chromium must not start")) as chromium_start,
        ):
            with self.assertRaises(SystemExit) as failure:
                qa_dashboard_cdp.main()

        self.assertEqual(failure.exception.code, 2)
        self.assertFalse(output.exists())
        self.assertEqual(credential_reads, [])
        mkdir.assert_not_called()
        chromium_which.assert_not_called()
        local_auth.assert_not_called()
        chromium_start.assert_not_called()

    def test_cdp_harness_stops_only_its_recorded_chromium_group(self):
        class ExitedLeader:
            pid = 43210

            @staticmethod
            def poll():
                return 0

            @staticmethod
            def wait(timeout=None):
                return 0

        process = ExitedLeader()
        group_exists = True
        signals = []

        def killpg(pgid, sig):
            nonlocal group_exists
            self.assertEqual(pgid, process.pid)
            if sig == 0:
                if not group_exists:
                    raise ProcessLookupError()
                return
            signals.append(sig)
            if sig == qa_dashboard_cdp.signal.SIGKILL:
                group_exists = False

        with (
            patch.object(qa_dashboard_cdp.os, "getpgrp", return_value=999),
            patch.object(qa_dashboard_cdp.os, "killpg", side_effect=killpg),
        ):
            qa_dashboard_cdp.stop_owned_group(process, process.pid, timeout=0)

        self.assertEqual(signals, [qa_dashboard_cdp.signal.SIGTERM, qa_dashboard_cdp.signal.SIGKILL])
        with (
            patch.object(qa_dashboard_cdp.os, "getpgrp", return_value=999),
            patch.object(qa_dashboard_cdp.os, "killpg") as foreign_kill,
            self.assertRaisesRegex(RuntimeError, "refusing"),
        ):
            qa_dashboard_cdp.stop_owned_group(process, process.pid + 1)
        foreign_kill.assert_not_called()

    def test_cdp_main_switches_real_popen_topology_only_for_internal_flag(self):
        class Chromium:
            pid = 43210
            alive = True
            def poll(self):
                return None if self.alive else 0
            def terminate(self):
                self.alive = False
            def kill(self):
                self.alive = False
            def wait(self, timeout=None):
                self.alive = False
                return 0

        class FakeCDP:
            events = []
            def __init__(self, url):
                self.url = url
            def call(self, method, params=None):
                return {}
            def enable_egress_boundary(self, url, require_fixture_origin=True):
                self.boundary = (url, require_fixture_origin)
            def disable_egress_boundary(self):
                self.disabled = True
            def close(self):
                pass

        for inherit in (False, True):
            with self.subTest(inherit=inherit), tempfile.TemporaryDirectory() as temporary:
                process = Chromium()
                argv = ["--url", "http:" + "//" + "127.0.0.1:8123/second-brain", "--output", temporary,
                        "--chromium", "/bin/true", "--viewport", "390x844"]
                if inherit:
                    argv.append("--inherit-runner-process-group")
                result = {"viewport": {"width": 390, "height": 844}, "checks": [],
                          "metrics": {}, "probes": {}, "console_errors": [], "screenshot": "shot.png"}
                with (
                    patch.object(qa_dashboard_cdp.subprocess, "Popen", return_value=process) as popen,
                    patch.object(qa_dashboard_cdp, "free_port", return_value=9222),
                    patch.object(qa_dashboard_cdp, "get_json", return_value=[{"type": "page", "webSocketDebuggerUrl": "ws://test"}]),
                    patch.object(qa_dashboard_cdp, "CDP", FakeCDP),
                    patch.object(qa_dashboard_cdp, "local_auth_cookie", return_value=None),
                    patch.object(qa_dashboard_cdp, "run_viewport", return_value=result),
                    patch.object(qa_dashboard_cdp, "stop_owned_group") as stop_group,
                ):
                    self.assertEqual(qa_dashboard_cdp.main(argv), 0)
                self.assertEqual(popen.call_args.kwargs["start_new_session"], not inherit)
                if inherit:
                    stop_group.assert_not_called()
                else:
                    stop_group.assert_called_once_with(process, process.pid)

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
