from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import qa_clean_install as qa


SHA = "a" * 40
PLUGIN = "hermes-osb-panel"


class Process:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.waits = []

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return 0

    def kill(self):
        self.killed = True


class Harness:
    def __init__(self, *, mutate=None, fail_command=None):
        self.calls = []
        self.process = Process()
        self.installed = False
        self.mutate = mutate or {}
        self.fail_command = fail_command
        self.popen_env = None

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
        self.popen_env = kwargs["env"]
        return self.process

    def fetch(self, url, timeout):
        self.calls.append(("GET", url))
        if url.endswith("/api/dashboard/plugins"):
            value = {"plugins": [{"id": PLUGIN, "enabled": True, "status": "active"}]}
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
            sleep=lambda _seconds: None, port_picker=lambda: 43123,
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

    def test_source_never_contains_dashboard_stop(self):
        source = Path(qa.__file__).read_text(encoding="utf-8")
        forbidden = "dashboard" + " --" + "stop"
        self.assertNotIn(forbidden, source)


class CleanInstallTests(unittest.TestCase):
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
        for key in qa.SENSITIVE_ENV_KEYS:
            self.assertNotIn(key, harness.popen_env)
        self.assertNotIn("SOME_AUTH_TOKEN", harness.popen_env)
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
        for command in ("plugins show", "plugins doctor", "qa_dashboard_cdp.py"):
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
        with self.assertRaisesRegex(qa.QAFailure, "exited"):
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


if __name__ == "__main__":
    unittest.main()
