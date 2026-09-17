from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import qa_clean_install as qa


SHA = "a" * 40
PLUGIN = "hermes-osb-panel"


def uses_dashboard_stop(command):
    argv = tuple(command)
    return any(argv[index : index + 2] == ("dashboard", "--stop") for index in range(len(argv) - 1))


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Process:
    next_pid = 41000

    def __init__(self, returncode=0):
        self.pid = Process.next_pid
        Process.next_pid += 1
        self.returncode = returncode
        self.alive = True
        self.terminated = False
        self.killed = False
        self.waits = []

    def poll(self):
        return None if self.alive else self.returncode

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.alive = False
        return self.returncode


class Harness:
    def __init__(self, *, mutate=None, fail_command=None):
        self.calls = []
        self.process = Process()
        self.processes = [self.process]
        self.installed = False
        self.mutate = mutate or {}
        self.fail_command = fail_command
        self.popen_env = None
        self.clock = Clock()

    def run(self, command, **kwargs):
        self.calls.append(tuple(command))
        action = " ".join(command)
        if self.fail_command and self.fail_command in action:
            return subprocess.CompletedProcess(command, 2, "", "private output")
        if command[1:3] == ["plugins", "install"]:
            self.installed = True
        elif command[1:3] == ["plugins", "remove"]:
            self.installed = False
        if command[1:4] == ["plugins", "list", "--json"]:
            payload = {"plugins": ([{"id": PLUGIN, "enabled": True, "status": "active"}] if self.installed else [])}
            if "list" in self.mutate:
                payload = self.mutate["list"](payload, self.installed)
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    def popen(self, command, **kwargs):
        self.calls.append(tuple(command))
        self.assert_session = kwargs.get("start_new_session")
        if "dashboard" in command:
            self.popen_env = kwargs["env"]
            return self.process
        process = Process(returncode=2 if self.fail_command == "qa_dashboard_cdp.py" else 0)
        self.processes.append(process)
        return process

    def killpg(self, pgid, sig):
        process = next(item for item in self.processes if item.pid == pgid)
        if sig == qa.signal.SIGTERM:
            process.terminated = True
        elif sig == qa.signal.SIGKILL:
            process.killed = True

    def fetch(self, url, timeout):
        self.calls.append(("GET", url))
        if url.endswith("/api/dashboard/plugins"):
            value = [
                {
                    "name": PLUGIN,
                    "label": "Open Second Brain",
                    "description": "Read-only visual companion for Open Second Brain snapshots.",
                    "icon": "BrainCircuit",
                    "version": "3.1.0",
                    "tab": {"path": "/second-brain", "position": "after:plugins"},
                    "slots": [],
                    "entry": "dist/index.js",
                    "css": "dist/style.css?v=3.1.0",
                    "has_api": True,
                    "source": "user",
                }
            ]
            key = "plugins"
        elif url.endswith("/health"):
            value = {"ok": False, "provider": {"available": False, "mode": "disabled"}}
            key = "health"
        else:
            value = {"provider": {"available": False, "mode": "disabled"}, "graph": {"nodes": [], "edges": []}}
            key = "snapshot"
        mutation = self.mutate.get(key)
        if mutation:
            value = mutation(value)
        return value

    def execute(self):
        env = dict(os.environ)
        env.update({
            "HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN": "true",
            "OPEN_SECOND_BRAIN_CONFIG": "/private/config",
            "O2B_CONFIG": "/private/config2",
            "HERMES_DASHBOARD_SESSION_TOKEN": "secret",
            "HERMES_WEBUI_PASSWORD": "secret",
            "SOME_AUTH_TOKEN": "secret",
        })
        return qa.run_clean_install(
            "owner/repo", SHA, hermes="hermes", environ=env,
            runner=self.run, popen=self.popen, fetch_json=self.fetch,
            sleep=self.clock.sleep, port_picker=lambda: 43123,
            monotonic=self.clock.monotonic, killpg=self.killpg,
            listener_owned=lambda owned, port: True,
            which=lambda value, path="": (
                "/opt/hermes/bin/hermes" if value == "hermes" else "/usr/bin/chromium"
            ),
        )


class ValidationTests(unittest.TestCase):
    def test_repo_and_ref_are_strict(self):
        self.assertEqual(qa.github_repo("Owner-1/repo.name"), "Owner-1/repo.name")
        self.assertEqual(qa.commit_sha(SHA), SHA)
        for value in ("repo", "a/b/c", "../repo", "owner/repo;bad"):
            with self.subTest(value=value), self.assertRaises(Exception):
                qa.github_repo(value)
        for value in ("A" * 40, "a" * 39, "g" * 40, "main"):
            with self.subTest(value=value), self.assertRaises(Exception):
                qa.commit_sha(value)

    def test_recorded_subprocess_argv_never_stops_a_shared_dashboard(self):
        harness = Harness()
        harness.execute()
        commands = [call for call in harness.calls if call[:1] != ("GET",)]
        self.assertFalse(any(uses_dashboard_stop(command) for command in commands))
        self.assertTrue(uses_dashboard_stop(("hermes", "dashboard", "--stop")))


class CleanInstallTests(unittest.TestCase):
    def assert_waits_for_dashboard_manifest(self, responses):
        clock = Clock()
        responses = iter(responses)
        calls = []

        def fetch(url, timeout):
            calls.append((url, timeout))
            return next(responses)

        result = qa._wait_json(
            fetch,
            "http:" + "//" + "127.0.0.1:43123/plugins",
            Process(),
            clock.sleep,
            qa._dashboard_plugin_present,
            timeout=1.0, monotonic=clock.monotonic, listener_owned=lambda: True,
        )

        self.assertTrue(qa._dashboard_plugin_present(result))
        self.assertEqual(len(calls), 2)

    def test_wait_json_polls_from_empty_until_manifest_is_present(self):
        self.assert_waits_for_dashboard_manifest([[], [{"name": PLUGIN}]])

    def test_wait_json_polls_past_other_plugin_until_target_manifest_is_present(self):
        self.assert_waits_for_dashboard_manifest(
            [[{"name": "another-dashboard-plugin"}], [{"name": PLUGIN}]]
        )

    def test_wait_json_times_out_when_target_manifest_is_absent(self):
        clock = Clock()
        calls = []

        def fetch(url, timeout):
            calls.append((url, timeout))
            return [{"name": "another-dashboard-plugin", "route": "/another"}]

        with self.assertRaisesRegex(qa.QAFailure, "did not become ready"):
            qa._wait_json(
                fetch,
                "http:" + "//" + "127.0.0.1:43123/plugins",
                Process(),
                clock.sleep,
                qa._dashboard_plugin_present,
                timeout=0.25, monotonic=clock.monotonic, listener_owned=lambda: True,
            )
        self.assertEqual(len(calls), 3)

    def test_dashboard_manifest_presence_does_not_require_cli_state_fields(self):
        manifest = {
            "name": PLUGIN,
            "label": "Open Second Brain",
            "description": "Read-only visual companion for Open Second Brain snapshots.",
            "icon": "BrainCircuit",
            "version": "3.1.0",
            "tab": {"path": "/second-brain", "position": "after:plugins"},
            "slots": [],
            "entry": "dist/index.js",
            "css": "dist/style.css?v=3.1.0",
            "has_api": True,
            "source": "user",
        }

        self.assertTrue(qa._dashboard_plugin_present([manifest]))

    def test_dashboard_manifest_presence_ignores_contradictory_synthetic_state(self):
        for state in (
            {"enabled": False},
            {"active": False},
            {"status": "inactive"},
            {"state": "disabled"},
        ):
            with self.subTest(state=state):
                self.assertTrue(qa._dashboard_plugin_present([{"name": PLUGIN, **state}]))

    def test_cli_activation_and_dashboard_manifest_predicates_are_separate(self):
        cli_payload = {"plugins": [{"id": PLUGIN, "enabled": False, "status": "inactive"}]}

        self.assertFalse(qa._cli_plugin_active(cli_payload))
        self.assertFalse(qa._dashboard_plugin_present(cli_payload))

    def test_wait_json_limits_transient_fetch_failures(self):
        clock = Clock()
        calls = []

        def fetch(url, timeout):
            calls.append((url, timeout))
            raise OSError("not ready")

        with self.assertRaisesRegex(qa.QAFailure, "repeatedly unavailable"):
            qa._wait_json(
                fetch,
                "http:" + "//" + "127.0.0.1:43123/plugins",
                Process(),
                clock.sleep,
                qa._dashboard_plugin_present,
                timeout=10.0, max_failures=3, monotonic=clock.monotonic,
                listener_owned=lambda: True,
            )
        self.assertEqual(len(calls), 3)

    def test_order_is_deterministic_and_environment_is_sanitized(self):
        harness = Harness()
        result = harness.execute()
        self.assertEqual(result, {"passed": True, "plugin": PLUGIN})
        actions = [" ".join(call) for call in harness.calls]
        expected = [
            "plugins install owner/repo --ref", "plugins list --json", "plugins show", "plugins doctor",
            "dashboard --host 127.0.0.1 --port 43123 --no-open --skip-build",
            "/api/dashboard/plugins", "/health", "/snapshot", "qa_dashboard_cdp.py",
            "plugins disable", "plugins remove", "plugins list --json",
        ]
        cursor = 0
        for needle in expected:
            cursor = next(i + 1 for i, action in enumerate(actions[cursor:], cursor) if needle in action)
        self.assertTrue(harness.process.terminated)
        self.assertEqual(
            set(harness.popen_env),
            {"HOME", "HERMES_HOME", "LANG", "LC_ALL", "PATH", "PYTHONDONTWRITEBYTECODE"},
        )
        self.assertEqual(harness.popen_env["PATH"], "/usr/bin:/bin")
        self.assertNotEqual(harness.popen_env["HOME"], os.environ.get("HOME"))
        self.assertTrue(Path(harness.popen_env["HOME"]).is_absolute())
        self.assertFalse(Path(harness.popen_env["HOME"]).exists())

    def test_contract_failures_still_stop_and_remove(self):
        cases = {
            "inactive": {"plugins": lambda value: {"plugins": [{"id": PLUGIN, "enabled": False}]}},
            "health_shape": {"health": lambda value: []},
            "health_available": {"health": lambda value: {"provider": {"available": True, "mode": "disabled"}}},
            "health_mode": {"health": lambda value: {"provider": {"available": False, "mode": "fixture"}}},
            "provider_available": {"snapshot": lambda value: {**value, "provider": {"available": True, "mode": "disabled"}}},
            "provider_mode": {"snapshot": lambda value: {**value, "provider": {"available": False, "mode": "fixture"}}},
            "nodes": {"snapshot": lambda value: {**value, "graph": {"nodes": [{}], "edges": []}}},
            "edges": {"snapshot": lambda value: {**value, "graph": {"nodes": [], "edges": [{}]}}},
        }
        for name, mutations in cases.items():
            with self.subTest(name=name):
                harness = Harness(mutate=mutations)
                with self.assertRaises(qa.QAFailure):
                    harness.execute()
                self.assertTrue(harness.process.terminated)
                self.assertFalse(harness.installed)

    def test_command_failures_are_bounded_and_cleanup_after_install(self):
        for command in ("plugins show", "plugins doctor"):
            with self.subTest(command=command):
                harness = Harness(fail_command=command)
                with self.assertRaisesRegex(qa.QAFailure, "command failed") as caught:
                    harness.execute()
                self.assertNotIn("private output", str(caught.exception))
                self.assertFalse(harness.installed)

    def test_failed_install_does_not_run_destructive_cleanup(self):
        harness = Harness(fail_command="plugins install")
        with self.assertRaises(qa.QAFailure):
            harness.execute()
        actions = [" ".join(call) for call in harness.calls]
        self.assertFalse(any("plugins remove" in action for action in actions))

    def test_invalid_list_and_early_dashboard_exit_fail_closed(self):
        invalid = Harness(mutate={"list": lambda payload, installed: []})
        with self.assertRaisesRegex(qa.QAFailure, "not active"):
            invalid.execute()
        self.assertFalse(invalid.installed)

        exited = Harness()
        exited.process.poll = lambda: 3
        with self.assertRaisesRegex(qa.QAFailure, "owned loopback port"):
            exited.execute()
        self.assertFalse(exited.installed)

    def test_remove_must_be_confirmed(self):
        def keep_plugin(payload, installed):
            return {"plugins": [{"id": PLUGIN, "enabled": True}]}
        harness = Harness(mutate={"list": keep_plugin})
        with self.assertRaisesRegex(qa.QAFailure, "cleanup"):
            harness.execute()
        self.assertTrue(harness.process.terminated)

    def test_owned_process_is_killed_only_after_timeout(self):
        harness = Harness()
        waits = 0
        def wait(timeout=None):
            nonlocal waits
            waits += 1
            if waits == 1:
                raise subprocess.TimeoutExpired("dashboard", timeout or 0)
            return 0
        harness.process.wait = wait
        harness.execute()
        self.assertTrue(harness.process.terminated)
        self.assertTrue(harness.process.killed)


class HardeningRegressionTests(unittest.TestCase):
    def test_environment_is_a_minimal_allowlist_despite_canaries(self):
        canaries = {
            "PATH": "/tmp/evil", "OPENAI_API_KEY": "x", "AWS_PROFILE": "x",
            "GOOGLE_APPLICATION_CREDENTIALS": "x", "SSH_AUTH_SOCK": "x",
            "GIT_CONFIG_GLOBAL": "/tmp/x", "LD_PRELOAD": "/tmp/x.so",
            "DYLD_INSERT_LIBRARIES": "/tmp/x", "PYTHONPATH": "/tmp/x",
            "NODE_OPTIONS": "--require=/tmp/x", "HERMES_CONFIG": "/tmp/x",
            "HERMES_WEB_DIST": "/tmp/x", "HTTP_PROXY": "http://proxy",
            "BROWSER": "/tmp/x", "HERMES_AUTH_TOKEN": "x", "SESSION": "x",
        }
        env = qa._safe_env(canaries, Path("/tmp/home"), Path("/tmp/hermes"))
        self.assertEqual(
            env,
            {"HOME": "/tmp/home", "HERMES_HOME": "/tmp/hermes", "LANG": "C.UTF-8",
             "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )

    class Response:
        def __init__(
            self, payload, *, content_type="application/json", status=200, version=11,
            will_close=False, after_read=None,
        ):
            self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.headers = {"Content-Type": content_type}
            self.status = status
            self.version = version
            self.will_close = will_close
            self.after_read = after_read
            self.limits = []
        def read(self, limit):
            self.limits.append(limit)
            if self.after_read:
                self.after_read()
            return self.body

    class Connection:
        def __init__(self, responses):
            self.responses = list(responses)
            self.sock = None
            self.socket = object()
            self.connects = 0
            self.requests = []
        def connect(self):
            self.connects += 1
            self.sock = self.socket
        def request(self, method, path, *, headers):
            self.requests.append((method, path, headers))
        def getresponse(self):
            return self.responses.pop(0)
        def close(self):
            self.sock = None

    def transaction(self, responses, **kwargs):
        connection = self.Connection(responses)
        result = qa._fetch_json_transaction(
            "http:" + "//" + "127.0.0.1:43123/target", 1.5, 43123, "a" * 32,
            connection_factory=lambda host, port, timeout: connection, **kwargs,
        )
        return result, connection

    def test_http_pins_status_and_target_to_one_http11_connection(self):
        status = self.Response({"install_id": "a" * 32})
        target = self.Response({"ok": True})
        result, connection = self.transaction([status, target])
        self.assertEqual(result, {"ok": True})
        self.assertEqual(connection.connects, 1)
        self.assertEqual([request[1] for request in connection.requests], ["/api/status", "/target"])
        self.assertEqual(status.limits, [qa.MAX_STATUS_RESPONSE_BYTES + 1])
        self.assertEqual(target.limits, [qa.MAX_RESPONSE_BYTES + 1])

    def test_http_rejects_missing_or_wrong_install_id(self):
        for payload in ({}, {"install_id": "b" * 32}):
            with self.subTest(payload=payload), self.assertRaisesRegex(qa.QAFailure, "identity"):
                self.transaction([self.Response(payload), self.Response({"ok": True})])

    def test_http_close_after_status_fails_without_reconnect(self):
        connection = self.Connection([])
        status = self.Response({"install_id": "a" * 32}, after_read=lambda: setattr(connection, "sock", None))
        connection.responses = [status, self.Response({"ok": True})]
        with self.assertRaisesRegex(qa.QAFailure, "connection"):
            qa._fetch_json_transaction(
                "http:" + "//" + "127.0.0.1:43123/target", 1, 43123, "a" * 32,
                connection_factory=lambda host, port, timeout: connection,
            )
        self.assertEqual(connection.connects, 1)
        self.assertEqual([item[1] for item in connection.requests], ["/api/status"])

    def test_http_rejects_socket_or_connection_swap(self):
        for replacement in (object(), None):
            connection = self.Connection([])
            status = self.Response(
                {"install_id": "a" * 32}, after_read=lambda value=replacement: setattr(connection, "sock", value)
            )
            connection.responses = [status, self.Response({"ok": True})]
            with self.subTest(replacement=replacement), self.assertRaisesRegex(qa.QAFailure, "connection"):
                qa._fetch_json_transaction(
                    "http:" + "//" + "127.0.0.1:43123/target", 1, 43123, "a" * 32,
                    connection_factory=lambda host, port, timeout: connection,
                )

    def test_http_enforces_status_and_target_limits_and_json_contract(self):
        cases = (
            ([self.Response(b"{" + b"x" * qa.MAX_STATUS_RESPONSE_BYTES), self.Response({})], "size"),
            ([self.Response({"install_id": "a" * 32}), self.Response(b"{" + b"x" * qa.MAX_RESPONSE_BYTES)], "size"),
            ([self.Response({"install_id": "a" * 32}, content_type="text/html"), self.Response({})], "non-JSON"),
            ([self.Response({"install_id": "a" * 32}), self.Response({}, content_type="text/html")], "non-JSON"),
            ([self.Response({}, status=302), self.Response({})], "non-success"),
            ([self.Response({"install_id": "a" * 32}), self.Response({}, status=503)], "non-success"),
            ([self.Response({"install_id": "a" * 32}, version=10), self.Response({})], "HTTP/1.1"),
            ([self.Response({"install_id": "a" * 32}, will_close=True), self.Response({})], "persistent"),
        )
        for responses, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(qa.QAFailure, message):
                self.transaction(responses)

    def test_install_id_is_random_valid_and_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            hermes_home = Path(temporary)
            first = qa._create_install_id(hermes_home)
            path = hermes_home / "install_id"
            self.assertRegex(first, r"^[0-9a-f]{32}$")
            self.assertEqual(path.read_text(encoding="ascii"), first)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            path.unlink()
            self.assertNotEqual(first, qa._create_install_id(hermes_home))

    def test_retryable_identity_failure_restarts_the_complete_transaction(self):
        clock = Clock()
        attempts = []
        payload = [{"name": PLUGIN}]
        def fetch(url, timeout):
            attempts.append((url, timeout))
            if len(attempts) == 1:
                raise qa._RetryableFetchFailure("dashboard install identity did not match")
            return payload
        result = qa._wait_json(
            fetch, "http:" + "//" + "127.0.0.1:43123/plugins", Process(), clock.sleep,
            qa._dashboard_plugin_present, timeout=1, monotonic=clock.monotonic,
            listener_owned=lambda: True,
        )
        self.assertIs(result, payload)
        self.assertEqual(len(attempts), 2)

    def test_pinned_connection_refuses_a_second_connect_without_network(self):
        connection = qa._PinnedHTTPConnection("127.0.0.1", 43123)
        connection._pin_connect_attempted = True
        with self.assertRaises(qa.http.client.CannotSendRequest):
            connection.connect()

    def test_http_rejects_every_destination_except_expected_ipv4_loopback(self):
        bad = (
            "https:" + "//" + "127.0.0.1:43123/x", "http:" + "//" + "localhost:43123/x",
            "http:" + "//" + "127.0.0.1:43124/x", "http:" + "//" + "user@127.0.0.1:43123/x",
            "http://example.test:43123/x",
        )
        factory = mock.Mock()
        for url in bad:
            with self.subTest(url=url), self.assertRaisesRegex(qa.QAFailure, "owned loopback"):
                qa._fetch_json_transaction(url, 1, 43123, "a" * 32, connection_factory=factory)
        factory.assert_not_called()

    def test_stop_signals_only_the_recorded_owned_group(self):
        process = Process()
        signals = []
        qa._stop_owned(qa.OwnedProcess(process, process.pid), 5, lambda: 0, lambda pgid, sig: signals.append((pgid, sig)))
        self.assertEqual(signals, [(process.pid, qa.signal.SIGTERM)])
        with self.assertRaises(AttributeError):
            qa._stop_owned(Process(), 5, lambda: 0, lambda pgid, sig: None)  # type: ignore[arg-type]

    def test_global_deadline_bounds_commands_and_preserves_cancellation(self):
        clock = Clock()
        seen = []
        def runner(command, **kwargs):
            seen.append(kwargs["timeout"])
            clock.now += 2
            return subprocess.CompletedProcess(command, 0, "", "")
        qa._run_checked(runner, ["/bin/true"], {}, 3, clock.monotonic)
        self.assertEqual(seen, [3])
        with self.assertRaisesRegex(qa.QAFailure, "deadline"):
            qa._run_checked(runner, ["/bin/true"], {}, 1, clock.monotonic)

        harness = Harness()
        original = harness.run
        def interrupt(command, **kwargs):
            if command[1:3] == ["plugins", "doctor"]:
                raise KeyboardInterrupt()
            return original(command, **kwargs)
        harness.run = interrupt
        with self.assertRaises(KeyboardInterrupt):
            harness.execute()
        self.assertFalse(harness.installed)

    def test_cdp_is_owned_and_cleaned_after_timeout(self):
        harness = Harness()
        original = harness.popen
        def popen(command, **kwargs):
            process = original(command, **kwargs)
            if any(str(item).endswith("qa_dashboard_cdp.py") for item in command):
                process.wait = lambda timeout=None: (_ for _ in ()).throw(subprocess.TimeoutExpired("cdp", timeout))
            return process
        harness.popen = popen
        with self.assertRaises(qa.QAFailure):
            harness.execute()
        cdp = harness.processes[-1]
        self.assertTrue(cdp.terminated)
        self.assertTrue(cdp.killed)
        self.assertTrue(harness.assert_session)

    def test_port_retry_accepts_only_the_live_new_process(self):
        harness = Harness()
        first = harness.process
        first.alive = False
        second = Process()
        harness.processes.append(second)
        ports = iter((43123, 43124))
        dashboard_starts = 0
        original = harness.popen
        def popen(command, **kwargs):
            nonlocal dashboard_starts
            if "dashboard" in command:
                harness.calls.append(tuple(command))
                harness.popen_env = kwargs["env"]
                dashboard_starts += 1
                return first if dashboard_starts == 1 else second
            return original(command, **kwargs)
        harness.popen = popen
        result = qa.run_clean_install(
            "owner/repo", SHA, environ={"PATH": "/tools"}, runner=harness.run,
            popen=harness.popen, fetch_json=harness.fetch, sleep=harness.clock.sleep,
            port_picker=lambda: next(ports), monotonic=harness.clock.monotonic,
            killpg=harness.killpg,
            listener_owned=lambda owned, port: True,
            which=lambda value, path="": "/opt/hermes" if value == "hermes" else "/opt/chromium",
        )
        self.assertTrue(result["passed"])
        starts = [call for call in harness.calls if "dashboard" in call]
        self.assertEqual(len(starts), 2)
        self.assertIn("43123", starts[0])
        self.assertIn("43124", starts[1])
        self.assertTrue(second.terminated)

    def test_wait_json_never_fetches_from_a_foreign_listener(self):
        clock = Clock()
        calls = []
        ownership = iter((False, False, False))
        with self.assertRaisesRegex(qa.QAFailure, "did not become ready"):
            qa._wait_json(
                lambda url, timeout: calls.append(url) or [{"name": PLUGIN}],
                "http:" + "//" + "127.0.0.1:43123/plugins", Process(), clock.sleep,
                qa._dashboard_plugin_present, timeout=0.25,
                monotonic=clock.monotonic,
                listener_owned=lambda: next(ownership, False),
            )
        self.assertEqual(calls, [])

    def test_wait_json_discards_json_if_listener_ownership_changes(self):
        clock = Clock()
        checks = iter((True, False, False, False))
        calls = []
        with self.assertRaisesRegex(qa.QAFailure, "did not become ready"):
            qa._wait_json(
                lambda url, timeout: calls.append(url) or [{"name": PLUGIN}],
                "http:" + "//" + "127.0.0.1:43123/plugins", Process(), clock.sleep,
                qa._dashboard_plugin_present, timeout=0.25,
                monotonic=clock.monotonic,
                listener_owned=lambda: next(checks, False),
            )
        self.assertEqual(len(calls), 1)

    def test_listener_owned_accepts_descendant_in_owned_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            (proc / "net").mkdir()
            (proc / "41000" / "fd").mkdir(parents=True)
            (proc / "41001" / "fd").mkdir(parents=True)
            (proc / "41000" / "stat").write_text(
                "41000 (dashboard) S 1 41000 41000 0\n", encoding="ascii"
            )
            (proc / "41001" / "stat").write_text(
                "41001 (worker) S 41000 41000 41000 0\n", encoding="ascii"
            )
            (proc / "41001" / "fd" / "7").symlink_to("socket:[98765]")
            (proc / "net" / "tcp").write_text(
                "  sl  local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
                "   0: 0100007F:A873 00000000:0000 0A 0:0 00:0 0 1000 0 98765\n",
                encoding="ascii",
            )
            (proc / "net" / "tcp6").write_text(
                "  sl  local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n",
                encoding="ascii",
            )
            owned = qa.OwnedProcess(Process(), 41000)
            self.assertTrue(qa._listener_is_owned(owned, 43123, proc_root=proc, platform="linux"))

    def test_listener_owned_rejects_foreign_dead_and_unprovable_cases(self):
        def build(proc: Path, *, pgid="42000", tcp_inode="98765", malformed=False):
            (proc / "net").mkdir()
            (proc / "42000" / "fd").mkdir(parents=True)
            (proc / "42000" / "stat").write_text(
                "not a stat\n" if malformed else f"42000 (server) S 1 {pgid} {pgid} 0\n",
                encoding="ascii",
            )
            (proc / "42000" / "fd" / "7").symlink_to("socket:[98765]")
            header = "  sl  local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
            (proc / "net" / "tcp").write_text(
                header + f"   0: 0100007F:A873 00000000:0000 0A 0:0 00:0 0 1000 0 {tcp_inode}\n",
                encoding="ascii",
            )
            (proc / "net" / "tcp6").write_text(header, encoding="ascii")

        for case in ("foreign", "dead", "missing", "malformed", "non_linux"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                proc = Path(temporary)
                if case != "missing":
                    build(proc, pgid="42000" if case == "foreign" else "41000", malformed=case == "malformed")
                process = Process()
                if case == "dead":
                    process.alive = False
                owned = qa.OwnedProcess(process, 41000)
                self.assertFalse(
                    qa._listener_is_owned(
                        owned, 43123, proc_root=proc,
                        platform="darwin" if case == "non_linux" else "linux",
                    )
                )

    def test_ci_runs_discovery_once_without_duplicate_focused_step(self):
        ci = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertEqual(ci.count("unittest discover"), 1)
        self.assertNotIn("unittest tests.test_qa_clean_install", ci)


if __name__ == "__main__":
    unittest.main()
