#!/usr/bin/env python3
"""Isolated, fail-closed clean-install QA for hermes-osb-panel.

The harness uses disposable HOME/HERMES_HOME directories, owns exactly one
Dashboard child process, and never reads or mutates an existing Hermes profile.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

PLUGIN_ID = "hermes-osb-panel"
ROOT = Path(__file__).resolve().parents[1]
MAX_RESPONSE_BYTES = 1_000_000
COMMAND_TIMEOUT = 120
DASHBOARD_READY_TIMEOUT = 15.0
DASHBOARD_POLL_INTERVAL = 0.1
MAX_TRANSIENT_FETCH_FAILURES = 50
SENSITIVE_ENV_KEYS = frozenset(
    {
        "HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN",
        "OPEN_SECOND_BRAIN_CONFIG",
        "O2B_CONFIG",
        "HERMES_DASHBOARD_SESSION_TOKEN",
        "HERMES_WEBUI_PASSWORD",
        "HERMES_WEBUI_ENV_FILE",
        "HERMES_SESSION_TOKEN",
        "HERMES_AUTH_TOKEN",
    }
)
_SECRET_ENV_PARTS = ("AUTH", "TOKEN", "SESSION", "PASSWORD", "PASSWD", "COOKIE", "SECRET")
_REPO_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})\Z")
_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


class QAFailure(RuntimeError):
    """A sanitized contract failure safe to print."""


def github_repo(value: str) -> str:
    if not _REPO_RE.fullmatch(value) or ".." in value.split("/"):
        raise argparse.ArgumentTypeError("repository must be a GitHub owner/repo slug")
    return value


def commit_sha(value: str) -> str:
    if not _SHA_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("--ref must be a lowercase 40-character hexadecimal SHA")
    return value


def _safe_env(source: Mapping[str, str], home: Path, hermes_home: Path) -> dict[str, str]:
    result = {
        key: value
        for key, value in source.items()
        if key not in SENSITIVE_ENV_KEYS and not any(part in key.upper() for part in _SECRET_ENV_PARTS)
    }
    result.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(hermes_home),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return result


def _bounded_run(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run without allowing child output into terminal or unbounded memory."""
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout, tempfile.TemporaryFile(
        mode="w+t", encoding="utf-8"
    ) as stderr:
        completed = subprocess.run(
            list(command),
            stdout=stdout,
            stderr=stderr,
            text=True,
            timeout=kwargs.pop("timeout", COMMAND_TIMEOUT),
            **kwargs,
        )
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(command, completed.returncode, stdout.read(65536), stderr.read(65536))


def _fetch_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise QAFailure("dashboard response exceeded the QA size limit")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QAFailure("dashboard returned invalid JSON") from exc


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("plugins", "items", "data"):
            if key in payload:
                return _entries(payload[key])
    return []


def _plugin_entry(payload: Any) -> dict[str, Any] | None:
    for item in _entries(payload):
        if item.get("id") == PLUGIN_ID or item.get("name") == PLUGIN_ID:
            return item
    return None


def _active(payload: Any) -> bool:
    item = _plugin_entry(payload)
    if not item:
        return False
    status = str(item.get("status", item.get("state", ""))).casefold()
    return item.get("enabled") is True or item.get("active") is True or status in {"active", "enabled", "loaded"}


def _run_checked(
    runner: Callable[..., subprocess.CompletedProcess[str]], command: Sequence[str], env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(list(command), env=dict(env), cwd=str(ROOT), timeout=COMMAND_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QAFailure("command could not be executed") from exc
    if result.returncode != 0:
        raise QAFailure("command failed")
    return result


def _list_plugins(
    runner: Callable[..., subprocess.CompletedProcess[str]], hermes: str, env: Mapping[str, str]
) -> Any:
    result = _run_checked(runner, [hermes, "plugins", "list", "--json"], env)
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise QAFailure("plugin list returned invalid JSON") from exc


def _wait_json(
    fetch_json: Callable[[str, float], Any],
    url: str,
    process: Any,
    sleep: Callable[[float], None],
    condition: Callable[[Any], bool],
    *,
    timeout: float = DASHBOARD_READY_TIMEOUT,
    max_failures: int = MAX_TRANSIENT_FETCH_FAILURES,
    monotonic: Callable[[], float] | None = None,
) -> Any:
    clock = time.monotonic if monotonic is None else monotonic
    deadline = clock() + timeout
    failures = 0
    while clock() < deadline:
        if process.poll() is not None:
            raise QAFailure("dashboard exited before becoming ready")
        remaining = deadline - clock()
        try:
            payload = fetch_json(url, min(2.0, remaining))
        except QAFailure:
            raise
        except Exception:
            failures += 1
            if failures >= max_failures:
                raise QAFailure("dashboard endpoint was repeatedly unavailable")
        else:
            if condition(payload):
                return payload
        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(DASHBOARD_POLL_INTERVAL, remaining))
    raise QAFailure("dashboard did not become ready")


def _stop_owned(process: Any) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _assert_snapshot(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise QAFailure("snapshot contract failed")
    provider = payload.get("provider")
    graph = payload.get("graph")
    if not isinstance(provider, dict) or provider.get("available") is not False or provider.get("mode") != "disabled":
        raise QAFailure("snapshot provider did not fail closed")
    if not isinstance(graph, dict) or graph.get("nodes") != [] or graph.get("edges") != []:
        raise QAFailure("snapshot graph did not fail closed")


def _assert_health(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise QAFailure("plugin health contract failed")
    provider = payload.get("provider")
    if (
        not isinstance(provider, dict)
        or provider.get("available") is not False
        or provider.get("mode") != "disabled"
    ):
        raise QAFailure("health provider did not fail closed")


def run_clean_install(
    repository: str,
    ref: str,
    *,
    hermes: str = "hermes",
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _bounded_run,
    popen: Callable[..., Any] = subprocess.Popen,
    fetch_json: Callable[[str, float], Any] = _fetch_json,
    sleep: Callable[[float], None] = time.sleep,
    port_picker: Callable[[], int] = _free_port,
) -> dict[str, Any]:
    repository = github_repo(repository)
    ref = commit_sha(ref)
    source_env = os.environ if environ is None else environ
    installed = False
    dashboard = None
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="hermes-osb-clean-install-") as temporary:
        temp_root = Path(temporary).resolve()
        try:
            temp_root.relative_to(ROOT.resolve())
        except ValueError:
            pass
        else:
            raise QAFailure("temporary QA root must be outside the repository")
        home = temp_root / "home"
        hermes_home = temp_root / "hermes-home"
        cdp_output = temp_root / "cdp-output"
        home.mkdir(mode=0o700)
        hermes_home.mkdir(mode=0o700)
        env = _safe_env(source_env, home, hermes_home)
        port = port_picker()
        base_url = f"http://127.0.0.1:{port}"

        try:
            _run_checked(runner, [hermes, "plugins", "install", repository, "--ref", ref, "--enable"], env)
            installed = True
            if not _active(_list_plugins(runner, hermes, env)):
                raise QAFailure("installed plugin is not active")
            _run_checked(runner, [hermes, "plugins", "show", PLUGIN_ID], env)
            _run_checked(runner, [hermes, "plugins", "doctor", PLUGIN_ID, "--ci"], env)
            dashboard = popen(
                [
                    hermes, "dashboard", "--host", "127.0.0.1", "--port", str(port),
                    "--no-open", "--skip-build",
                ],
                env=env,
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            _wait_json(fetch_json, base_url + "/api/dashboard/plugins", dashboard, sleep, _active)
            health = fetch_json(base_url + f"/api/plugins/{PLUGIN_ID}/health", 5.0)
            _assert_health(health)
            snapshot = fetch_json(base_url + f"/api/plugins/{PLUGIN_ID}/snapshot", 5.0)
            _assert_snapshot(snapshot)
            _run_checked(
                runner,
                [
                    sys.executable, str(ROOT / "scripts" / "qa_dashboard_cdp.py"),
                    "--url", base_url + "/second-brain",
                    "--fixture", str(ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"),
                    "--output", str(cdp_output),
                ],
                env,
            )
        except BaseException as exc:
            primary_error = exc
        finally:
            try:
                _stop_owned(dashboard)
            except Exception:
                cleanup_errors.append("owned dashboard process")
            if installed:
                for action in ("disable", "remove"):
                    try:
                        _run_checked(runner, [hermes, "plugins", action, PLUGIN_ID], env)
                    except Exception:
                        cleanup_errors.append(f"plugin {action}")
                try:
                    if _plugin_entry(_list_plugins(runner, hermes, env)) is not None:
                        cleanup_errors.append("plugin absence confirmation")
                except Exception:
                    cleanup_errors.append("plugin absence confirmation")

        if cleanup_errors:
            raise QAFailure("cleanup failed: " + ", ".join(cleanup_errors)) from primary_error
        if primary_error is not None:
            if isinstance(primary_error, QAFailure):
                raise primary_error
            raise QAFailure("clean-install QA failed") from primary_error
    return {"passed": True, "plugin": PLUGIN_ID}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=github_repo, help="GitHub owner/repo")
    parser.add_argument("--ref", required=True, type=commit_sha, help="immutable lowercase commit SHA")
    parser.add_argument("--hermes", default="hermes", help="Hermes CLI executable")
    args = parser.parse_args(argv)
    try:
        result = run_clean_install(args.repository, args.ref, hermes=args.hermes)
    except QAFailure as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
