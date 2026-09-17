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
        if sig == 0:
            if not process.alive:
                raise ProcessLookupError()
        elif sig == qa.signal.SIGTERM:
            process.terminated = True
            process.alive = False
        elif sig == qa.signal.SIGKILL:
            process.killed = True
            process.alive = False

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
            getpgrp=lambda: 999,
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
        with self.assertRaisesRegex(qa.QAFailure, "remained installed"):
            harness.execute()
        self.assertTrue(harness.process.terminated)

    def test_owned_process_group_is_terminated(self):
        harness = Harness()
        harness.execute()
        self.assertTrue(harness.process.terminated)
        self.assertFalse(harness.process.killed)


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

    @staticmethod
    def wire(payload, **headers):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        fields = {"Content-Type": "application/json", "Content-Length": str(len(body)), **headers}
        return ("HTTP/1.1 200 OK\r\n" + "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n").encode() + body

    class FakeSocket:
        def __init__(self, chunks):
            self.chunks = list(chunks)
            self.sent = []
            self.closed = False
        def setblocking(self, value):
            self.blocking = value
        def send(self, payload):
            self.sent.append(bytes(payload))
            return len(payload)
        def recv(self, limit):
            return self.chunks.pop(0) if self.chunks else b""
        def close(self):
            self.closed = True

    class FakeSelector:
        def __init__(self, clock, delays):
            self.clock = clock
            self.delays = delays
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def register(self, sock, event): pass
        def select(self, timeout):
            delay = self.delays.pop(0) if self.delays else 0
            self.clock.now += delay
            return [] if delay > timeout else [(object(), object())]

    def transaction(self, chunks, *, timeout=1.5, delays=None):
        clock = Clock()
        sock = self.FakeSocket(chunks)
        delays = list(delays or [])
        result = qa._fetch_json_transaction(
            "http:" + "//" + "127.0.0.1:43123/target", timeout, 43123, "a" * 32,
            connection_factory=lambda address, connect_timeout: sock,
            monotonic=clock.monotonic,
            selector_factory=lambda: self.FakeSelector(clock, delays),
        )
        return result, sock, clock

    def test_http_pins_status_and_target_to_one_socket(self):
        result, sock, _ = self.transaction([
            self.wire({"install_id": "a" * 32}), self.wire({"ok": True}),
        ])
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(sock.sent), 2)
        self.assertIn(b"GET /api/status HTTP/1.1", sock.sent[0])
        self.assertIn(b"GET /target HTTP/1.1", sock.sent[1])
        self.assertTrue(sock.closed)

    def test_http_absolute_deadline_rejects_slow_drip_headers_and_body(self):
        status = self.wire({"install_id": "a" * 32})
        target = self.wire({"ok": True})
        for chunks in ([status[:1], status[1:2], status[2:]], [status, target[:-2], target[-2:-1], target[-1:]]):
            with self.subTest(chunks=len(chunks)), self.assertRaisesRegex(qa.QAFailure, "deadline"):
                self.transaction(chunks, timeout=.5, delays=[0, .2, .2, .2, .2])

    def test_http_rejects_missing_short_long_and_chunked_bodies(self):
        status = self.wire({"install_id": "a" * 32})
        cases = {
            "requires Content-Length": b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}",
            "before Content-Length": b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 3\r\n\r\n{}",
            "beyond Content-Length": b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}x",
            "transfer encoding": b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}",
        }
        for message, response in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(qa.QAFailure, message):
                self.transaction([status, response])

    def test_http_enforces_complete_header_and_status_line_limits(self):
        oversized_header = (
            b"HTTP/1.1 200 OK\r\nX-Fill: "
            + b"x" * qa.MAX_HEADER_BYTES
            + b"\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
        )
        oversized_status = (
            b"HTTP/1.1 200 " + b"x" * qa.MAX_STATUS_LINE_BYTES
            + b"\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
        )
        for response, message in (
            (oversized_header, "headers exceeded"),
            (oversized_status, "invalid HTTP status"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(qa.QAFailure, message):
                self.transaction([response])

    def test_http_rejects_non_ascii_content_length_and_spaced_close(self):
        status = self.wire({"install_id": "a" * 32})
        cases = (
            (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: \xb2\r\n\r\n{}", "Content-Length"),
            (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: keep-alive, close\r\n\r\n{}", "persistent"),
        )
        for response, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(qa.QAFailure, message):
                self.transaction([status, response])

    def test_http_rejects_protocol_status_type_size_and_identity(self):
        status = self.wire({"install_id": "a" * 32})
        cases = [
            (self.wire({}), "identity"),
            (b"HTTP/1.1 302 Found\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}", "non-success"),
            (b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}", "HTTP/1.1"),
            (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 2\r\n\r\n{}", "non-JSON"),
            (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1000001\r\n\r\n", "size"),
        ]
        for response, message in cases:
            chunks = [response] if message == "identity" else [status, response]
            with self.subTest(message=message), self.assertRaisesRegex(qa.QAFailure, message):
                self.transaction(chunks)

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

    def test_retryable_transaction_is_not_restarted_without_budget(self):
        clock = Clock()
        attempts = []
        def fetch(url, timeout):
            attempts.append((url, timeout))
            clock.now += timeout
            raise qa._RetryableFetchFailure("dashboard connection failed")
        with self.assertRaisesRegex(qa.QAFailure, "did not become ready"):
            qa._wait_json(
                fetch, "http:" + "//" + "127.0.0.1:43123/plugins", Process(), clock.sleep,
                qa._dashboard_plugin_present, timeout=1, monotonic=clock.monotonic,
                listener_owned=lambda: True,
            )
        self.assertEqual(len(attempts), 1)


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

    def test_stop_signals_group_after_leader_death_and_handles_missing_group(self):
        process = Process()
        process.alive = False
        signals = []
        exists = True
        def killpg(pgid, sig):
            nonlocal exists
            if sig == 0 and not exists:
                raise ProcessLookupError()
            signals.append((pgid, sig))
            if sig == qa.signal.SIGTERM:
                exists = False
        qa._stop_owned(qa.OwnedProcess(process, process.pid, 999), 5, lambda: 0,
                       killpg, lambda seconds: None, lambda: 999)
        self.assertIn((process.pid, qa.signal.SIGTERM), signals)

        signals.clear()
        qa._stop_owned(qa.OwnedProcess(process, process.pid, 999), 5, lambda: 0,
                       lambda pgid, sig: (_ for _ in ()).throw(ProcessLookupError()),
                       lambda seconds: None, lambda: 999)
        self.assertEqual(signals, [])
        with self.assertRaises(AttributeError):
            qa._stop_owned(Process(), 5, lambda: 0, lambda pgid, sig: None)  # type: ignore[arg-type]

        foreign = Process()
        with self.assertRaisesRegex(qa.QAFailure, "refusing"):
            qa._stop_owned(qa.OwnedProcess(foreign, foreign.pid + 1, 999), 5, lambda: 0,
                           lambda pgid, sig: self.fail("must not signal a foreign group"),
                           lambda seconds: None, lambda: 999)

    def test_stop_kills_group_when_descendant_ignores_term(self):
        clock = Clock()
        process = Process()
        process.alive = False
        exists = True
        signals = []
        def killpg(pgid, sig):
            nonlocal exists
            if sig == 0:
                if not exists:
                    raise ProcessLookupError()
                return
            signals.append(sig)
            if sig == qa.signal.SIGKILL:
                exists = False
        qa._stop_owned(qa.OwnedProcess(process, process.pid, 999), 5, clock.monotonic,
                       killpg, clock.sleep, lambda: 999)
        self.assertEqual(signals, [qa.signal.SIGTERM, qa.signal.SIGKILL])

    def test_spawn_records_group_even_if_leader_dies_immediately(self):
        clock = Clock()
        process = Process()
        process.alive = False
        exists = True
        signals = []
        owned = qa._spawn_owned(lambda command, **kwargs: process, ["worker"], getpgrp=lambda: 999)

        def killpg(pgid, sig):
            nonlocal exists
            if sig == 0:
                if not exists:
                    raise ProcessLookupError()
                return
            signals.append(sig)
            if sig == qa.signal.SIGKILL:
                exists = False

        qa._stop_owned(owned, 5, clock.monotonic, killpg, clock.sleep, lambda: 999)
        self.assertEqual(owned.pgid, process.pid)
        self.assertEqual(signals, [qa.signal.SIGTERM, qa.signal.SIGKILL])

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

    def test_cleanup_cancellation_at_each_stage_does_not_skip_later_stages(self):
        for stage, cancellation_type in (("stop", KeyboardInterrupt), ("disable", SystemExit),
                                         ("remove", KeyboardInterrupt), ("list", SystemExit)):
            with self.subTest(stage=stage):
                harness = Harness()
                original_run = harness.run
                original_killpg = harness.killpg
                fired = False
                list_calls = 0

                def run(command, **kwargs):
                    nonlocal fired, list_calls
                    action = command[2] if len(command) > 2 and command[1] == "plugins" else ""
                    if action == "list":
                        list_calls += 1
                    should_interrupt = (
                        stage == "list" and action == "list" and list_calls >= 2
                    ) or (stage != "list" and action == stage)
                    if not fired and should_interrupt:
                        fired = True
                        raise cancellation_type()
                    return original_run(command, **kwargs)

                def killpg(pgid, sig):
                    nonlocal fired
                    if stage == "stop" and not fired and sig == 0:
                        fired = True
                        raise cancellation_type()
                    return original_killpg(pgid, sig)

                harness.run = run
                harness.killpg = killpg
                with self.assertRaises(cancellation_type):
                    harness.execute()
                actions = [" ".join(call) for call in harness.calls]
                self.assertTrue(any("plugins disable" in action for action in actions))
                self.assertTrue(any("plugins remove" in action for action in actions))
                self.assertGreaterEqual(sum("plugins list --json" in action for action in actions), 2)
                self.assertFalse(harness.installed)

    def test_cleanup_survives_interrupt_on_first_finally_clock_read(self):
        class Cancel(BaseException):
            pass

        harness = Harness()
        original_popen = harness.popen
        armed = False
        interrupted = False
        cancellation = Cancel()

        def popen(command, **kwargs):
            process = original_popen(command, **kwargs)
            if any(str(item).endswith("qa_dashboard_cdp.py") for item in command):
                original_wait = process.wait
                def wait(timeout=None):
                    nonlocal armed
                    result = original_wait(timeout)
                    armed = True
                    return result
                process.wait = wait
            return process

        original_monotonic = harness.clock.monotonic
        def monotonic():
            nonlocal interrupted
            if armed and not interrupted:
                interrupted = True
                raise cancellation
            return original_monotonic()

        harness.popen = popen
        harness.clock.monotonic = monotonic
        with self.assertRaises(Cancel) as raised:
            harness.execute()
        self.assertIs(raised.exception, cancellation)
        actions = [" ".join(call) for call in harness.calls]
        self.assertTrue(any("plugins disable" in action for action in actions))
        self.assertTrue(any("plugins remove" in action for action in actions))
        self.assertGreaterEqual(sum("plugins list --json" in action for action in actions), 2)
        self.assertTrue(harness.process.terminated)
        self.assertFalse(harness.installed)

    def test_cleanup_resumes_remove_after_base_exception_and_preserves_identity(self):
        class Cancel(BaseException):
            pass

        harness = Harness()
        original_run = harness.run
        cancellation = Cancel()
        fired = False
        list_calls = 0
        remove_completed = False
        confirmation_list_calls = 0

        def run(command, **kwargs):
            nonlocal fired, list_calls, remove_completed, confirmation_list_calls
            action = command[2] if len(command) > 2 and command[1] == "plugins" else ""
            if action == "list":
                list_calls += 1
                if remove_completed:
                    confirmation_list_calls += 1
            if action == "remove" and not fired:
                fired = True
                raise cancellation
            result = original_run(command, **kwargs)
            if action == "remove":
                remove_completed = True
            return result

        harness.run = run
        with self.assertRaises(Cancel) as raised:
            harness.execute()
        self.assertIs(raised.exception, cancellation)
        actions = [" ".join(call) for call in harness.calls]
        self.assertTrue(any("plugins disable" in action for action in actions))
        self.assertGreaterEqual(sum("plugins remove" in action for action in actions), 1)
        self.assertGreaterEqual(list_calls, 2)
        self.assertEqual(confirmation_list_calls, 1)
        self.assertTrue(harness.process.terminated)
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
        self.assertFalse(cdp.killed)
        self.assertTrue(harness.assert_session)
        cdp_argv = next(call for call in harness.calls if any(str(item).endswith("qa_dashboard_cdp.py") for item in call))
        self.assertIn("--inherit-runner-process-group", cdp_argv)

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
            getpgrp=lambda: 999,
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
