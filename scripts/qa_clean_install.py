#!/usr/bin/env python3
"""Isolated, fail-closed clean-install QA for hermes-osb-panel."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

PLUGIN_ID = "hermes-osb-panel"
ROOT = Path(__file__).resolve().parents[1]
MAX_RESPONSE_BYTES = 1_000_000
COMMAND_TIMEOUT = 120.0
TOTAL_TIMEOUT = 180.0
CLEANUP_TIMEOUT = 10.0
DASHBOARD_READY_TIMEOUT = 15.0
DASHBOARD_POLL_INTERVAL = 0.1
MAX_TRANSIENT_FETCH_FAILURES = 50
MAX_PORT_ATTEMPTS = 3
SAFE_PATH = "/usr/bin:/bin"
_REPO_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})\Z")
_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_SOCKET_INODE_RE = re.compile(r"socket:\[([1-9][0-9]*)\]\Z")


class QAFailure(RuntimeError):
    """A sanitized contract failure safe to print."""


@dataclass(frozen=True)
class OwnedProcess:
    """A process group created by this harness with setsid(2)."""

    process: Any
    pgid: int


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def github_repo(value: str) -> str:
    if not _REPO_RE.fullmatch(value) or ".." in value.split("/"):
        raise argparse.ArgumentTypeError("repository must be a GitHub owner/repo slug")
    return value


def commit_sha(value: str) -> str:
    if not _SHA_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("--ref must be a lowercase 40-character hexadecimal SHA")
    return value


def _safe_env(source: Mapping[str, str], home: Path, hermes_home: Path) -> dict[str, str]:
    """Build a fixed allowlist; source is deliberately never copied."""
    # HOME roots all profile state in the disposable tree. The fixed locale
    # makes CLI/JSON decoding deterministic; the fixed system PATH permits
    # required git/node helpers without inheriting operator-controlled entries.
    return {
        "HOME": str(home),
        "HERMES_HOME": str(hermes_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": SAFE_PATH,
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _resolve_executable(value: str, source: Mapping[str, str], which: Callable[..., str | None] = shutil.which) -> str:
    found = which(value, path=source.get("PATH", ""))
    if not found:
        raise QAFailure("required executable was not found")
    resolved = str(Path(found).resolve())
    if not Path(resolved).is_absolute():
        raise QAFailure("required executable did not resolve absolutely")
    return resolved


def _resolve_browser(source: Mapping[str, str], which: Callable[..., str | None] = shutil.which) -> str:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = which(name, path=source.get("PATH", ""))
        if found:
            return str(Path(found).resolve())
    raise QAFailure("Chromium was not found; refusing to install a dependency")


def _remaining(deadline: float, monotonic: Callable[[], float]) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise QAFailure("clean-install QA deadline exceeded")
    return remaining


def _bounded_run(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run without allowing child output into terminal or unbounded memory."""
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout, tempfile.TemporaryFile(
        mode="w+t", encoding="utf-8"
    ) as stderr:
        completed = subprocess.run(
            list(command), stdout=stdout, stderr=stderr, text=True,
            timeout=kwargs.pop("timeout", COMMAND_TIMEOUT), **kwargs,
        )
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(command, completed.returncode, stdout.read(65536), stderr.read(65536))


def _validate_loopback_url(url: str, expected_port: int) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise QAFailure("dashboard URL is invalid") from exc
    if (
        parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or port != expected_port
        or parsed.username is not None or parsed.password is not None
    ):
        raise QAFailure("dashboard request escaped the owned loopback endpoint")


def _fetch_json(url: str, timeout: float, expected_port: int) -> Any:
    _validate_loopback_url(url, expected_port)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.getcode()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if status < 200 or status >= 300:
                raise QAFailure("dashboard returned a non-success status")
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise QAFailure("dashboard returned a non-JSON content type")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise QAFailure("dashboard redirect was refused") from exc
        raise QAFailure("dashboard returned a non-success status") from exc
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


def _parse_proc_stat(text: str, expected_pid: int) -> tuple[int, int]:
    """Return (process group, session) without trusting spaces in comm."""
    close = text.rfind(")")
    open_ = text.find("(")
    if open_ <= 0 or close <= open_ or not text[close + 1 :].startswith(" "):
        raise ValueError("invalid proc stat")
    if int(text[:open_].strip()) != expected_pid:
        raise ValueError("proc stat pid mismatch")
    fields = text[close + 2 :].split()
    if len(fields) < 4 or len(fields[0]) != 1:
        raise ValueError("incomplete proc stat")
    return int(fields[2]), int(fields[3])


def _listening_loopback_inodes(text: str, port: int, *, ipv6: bool) -> set[int]:
    lines = text.splitlines()
    if not lines or "local_address" not in lines[0] or "inode" not in lines[0]:
        raise ValueError("invalid proc net header")
    expected_addresses = (
        {"00000000000000000000000001000000", "0000000000000000FFFF00000100007F"}
        if ipv6 else {"0100007F"}
    )
    result: set[int] = set()
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 10 or ":" not in fields[1]:
            raise ValueError("invalid proc net row")
        address, port_hex = fields[1].rsplit(":", 1)
        expected_address_length = 32 if ipv6 else 8
        hexadecimal = "0123456789ABCDEFabcdef"
        if (
            len(address) != expected_address_length or len(port_hex) != 4 or len(fields[3]) != 2
            or any(char not in hexadecimal for char in address + port_hex + fields[3])
            or not fields[9].isdecimal()
        ):
            raise ValueError("invalid proc net address")
        local_port = int(port_hex, 16)
        inode = int(fields[9])
        if fields[3] == "0A" and address.upper() in expected_addresses and local_port == port:
            if inode <= 0:
                raise ValueError("invalid listening inode")
            result.add(inode)
    return result


def _listener_is_owned(
    owned: OwnedProcess, port: int, *, proc_root: Path = Path("/proc"), platform: str = sys.platform,
) -> bool:
    """Prove a loopback LISTEN socket belongs to the owned session, or fail closed."""
    if platform != "linux" or owned.process.poll() is not None or owned.pgid <= 0:
        return False
    try:
        members: list[Path] = []
        root_seen = False
        for entry in proc_root.iterdir():
            if not entry.name.isdecimal() or not entry.is_dir():
                continue
            pid = int(entry.name)
            try:
                pgid, session = _parse_proc_stat((entry / "stat").read_text(encoding="ascii"), pid)
            except FileNotFoundError:
                continue
            if pgid == owned.pgid and session == owned.pgid:
                members.append(entry)
                root_seen = root_seen or pid == owned.pgid
        if not root_seen or not members:
            return False

        owned_inodes: set[int] = set()
        for member in members:
            for descriptor in (member / "fd").iterdir():
                try:
                    target = os.readlink(descriptor)
                except FileNotFoundError:
                    continue
                match = _SOCKET_INODE_RE.fullmatch(target)
                if match:
                    owned_inodes.add(int(match.group(1)))

        listeners = _listening_loopback_inodes(
            (proc_root / "net" / "tcp").read_text(encoding="ascii"), port, ipv6=False,
        )
        listeners.update(
            _listening_loopback_inodes(
                (proc_root / "net" / "tcp6").read_text(encoding="ascii"), port, ipv6=True,
            )
        )
        return bool(owned_inodes & listeners)
    except (OSError, UnicodeError, ValueError):
        return False


def _has_owned_listener(probe: Callable[[], bool]) -> bool:
    try:
        return probe() is True
    except Exception:
        return False


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


def _cli_plugin_active(payload: Any) -> bool:
    item = _plugin_entry(payload)
    if not item:
        return False
    status = str(item.get("status", item.get("state", ""))).casefold()
    return item.get("enabled") is True or item.get("active") is True or status in {"active", "enabled", "loaded"}


def _dashboard_plugin_present(payload: Any) -> bool:
    return isinstance(payload, list) and any(
        isinstance(item, dict) and item.get("name") == PLUGIN_ID for item in payload
    )


def _run_checked(
    runner: Callable[..., subprocess.CompletedProcess[str]], command: Sequence[str], env: Mapping[str, str],
    deadline: float, monotonic: Callable[[], float],
) -> subprocess.CompletedProcess[str]:
    timeout = min(COMMAND_TIMEOUT, _remaining(deadline, monotonic))
    try:
        result = runner(list(command), env=dict(env), cwd=str(ROOT), timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QAFailure("command could not be executed") from exc
    if result.returncode != 0:
        raise QAFailure("command failed")
    return result


def _list_plugins(
    runner: Callable[..., subprocess.CompletedProcess[str]], hermes: str, env: Mapping[str, str],
    deadline: float, monotonic: Callable[[], float],
) -> Any:
    result = _run_checked(runner, [hermes, "plugins", "list", "--json"], env, deadline, monotonic)
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise QAFailure("plugin list returned invalid JSON") from exc


def _wait_json(
    fetch_json: Callable[[str, float], Any], url: str, process: Any,
    sleep: Callable[[float], None], condition: Callable[[Any], bool], *, deadline: float | None = None,
    timeout: float = DASHBOARD_READY_TIMEOUT, max_failures: int = MAX_TRANSIENT_FETCH_FAILURES,
    monotonic: Callable[[], float] = time.monotonic,
    listener_owned: Callable[[], bool],
) -> Any:
    if deadline is None:
        deadline = monotonic() + timeout
    failures = 0
    while monotonic() < deadline:
        if process.poll() is not None:
            raise QAFailure("dashboard exited before becoming ready")
        remaining = _remaining(deadline, monotonic)
        if _has_owned_listener(listener_owned):
            try:
                payload = fetch_json(url, min(2.0, remaining))
            except QAFailure:
                raise
            except Exception:
                failures += 1
                if failures >= max_failures:
                    raise QAFailure("dashboard endpoint was repeatedly unavailable")
            else:
                if process.poll() is not None:
                    raise QAFailure("dashboard exited during identity verification")
                if _has_owned_listener(listener_owned) and condition(payload):
                    return payload
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(DASHBOARD_POLL_INTERVAL, remaining))
    raise QAFailure("dashboard did not become ready")


def _spawn_owned(popen: Callable[..., Any], command: Sequence[str], **kwargs: Any) -> OwnedProcess:
    process = popen(list(command), start_new_session=True, **kwargs)
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        raise QAFailure("owned process identity was unavailable")
    return OwnedProcess(process, pid)


def _stop_owned(
    owned: OwnedProcess | None, deadline: float, monotonic: Callable[[], float],
    killpg: Callable[[int, int], None] = os.killpg,
) -> None:
    if owned is None or owned.process.poll() is not None:
        return
    killpg(owned.pgid, signal.SIGTERM)
    try:
        owned.process.wait(timeout=min(2.0, _remaining(deadline, monotonic)))
        return
    except subprocess.TimeoutExpired:
        pass
    except QAFailure:
        pass
    if owned.process.poll() is None:
        killpg(owned.pgid, signal.SIGKILL)
        remaining = max(0.0, deadline - monotonic())
        try:
            owned.process.wait(timeout=min(2.0, remaining))
        except subprocess.TimeoutExpired as exc:
            raise QAFailure("owned process group did not exit before cleanup deadline") from exc


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
    if not isinstance(provider, dict) or provider.get("available") is not False or provider.get("mode") != "disabled":
        raise QAFailure("health provider did not fail closed")


def _require_alive(owned: OwnedProcess) -> None:
    if owned.process.poll() is not None:
        raise QAFailure("owned dashboard exited during verification")


def _fetch_owned_json(
    fetch_json: Callable[[str, float], Any], url: str, timeout: float,
    owned: OwnedProcess, port: int, listener_owned: Callable[[OwnedProcess, int], bool],
) -> Any:
    _require_alive(owned)
    if not _has_owned_listener(lambda: listener_owned(owned, port)):
        raise QAFailure("dashboard listener ownership could not be proven")
    payload = fetch_json(url, timeout)
    _require_alive(owned)
    if not _has_owned_listener(lambda: listener_owned(owned, port)):
        raise QAFailure("dashboard listener ownership changed during verification")
    return payload


def run_clean_install(
    repository: str, ref: str, *, hermes: str = "hermes", browser: str | None = None,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _bounded_run,
    popen: Callable[..., Any] = subprocess.Popen,
    fetch_json: Callable[[str, float], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep, port_picker: Callable[[], int] = _free_port,
    monotonic: Callable[[], float] = time.monotonic, which: Callable[..., str | None] = shutil.which,
    killpg: Callable[[int, int], None] = os.killpg, timeout: float = TOTAL_TIMEOUT,
    listener_owned: Callable[[OwnedProcess, int], bool] = _listener_is_owned,
) -> dict[str, Any]:
    repository = github_repo(repository)
    ref = commit_sha(ref)
    source_env = os.environ if environ is None else environ
    deadline = monotonic() + timeout
    operation_deadline = deadline - min(CLEANUP_TIMEOUT, timeout / 4)
    hermes_executable = _resolve_executable(hermes, source_env, which)
    python_executable = str(Path(sys.executable).resolve())
    browser_executable = _resolve_executable(browser, source_env, which) if browser else _resolve_browser(source_env, which)
    installed = False
    dashboard: OwnedProcess | None = None
    cdp: OwnedProcess | None = None
    primary_error: Exception | None = None
    cancellation: KeyboardInterrupt | SystemExit | None = None
    cleanup_errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="hermes-osb-clean-install-") as temporary:
        temp_root = Path(temporary).resolve()
        try:
            temp_root.relative_to(ROOT.resolve())
        except ValueError:
            pass
        else:
            raise QAFailure("temporary QA root must be outside the repository")
        home, hermes_home = temp_root / "home", temp_root / "hermes-home"
        cdp_output = temp_root / "cdp-output"
        home.mkdir(mode=0o700)
        hermes_home.mkdir(mode=0o700)
        env = _safe_env(source_env, home, hermes_home)
        base_url = ""
        active_fetch: Callable[[str, float], Any] | None = None

        try:
            _run_checked(runner, [hermes_executable, "plugins", "install", repository, "--ref", ref, "--enable"], env, operation_deadline, monotonic)
            installed = True
            if not _cli_plugin_active(_list_plugins(runner, hermes_executable, env, operation_deadline, monotonic)):
                raise QAFailure("installed plugin is not active")
            _run_checked(runner, [hermes_executable, "plugins", "show", PLUGIN_ID], env, operation_deadline, monotonic)
            _run_checked(runner, [hermes_executable, "plugins", "doctor", PLUGIN_ID, "--ci"], env, operation_deadline, monotonic)

            last_start_error: QAFailure | None = None
            for _attempt in range(MAX_PORT_ATTEMPTS):
                port = port_picker()
                base_url = f"http://127.0.0.1:{port}"
                active_fetch = fetch_json or (lambda url, request_timeout, expected=port: _fetch_json(url, request_timeout, expected))
                dashboard = _spawn_owned(
                    popen,
                    [hermes_executable, "dashboard", "--host", "127.0.0.1", "--port", str(port), "--no-open", "--skip-build"],
                    env=env, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                try:
                    ready_deadline = min(operation_deadline, monotonic() + DASHBOARD_READY_TIMEOUT)
                    _wait_json(active_fetch, base_url + "/api/dashboard/plugins", dashboard.process, sleep,
                               _dashboard_plugin_present, deadline=ready_deadline, monotonic=monotonic,
                               listener_owned=lambda owned=dashboard, selected=port: listener_owned(owned, selected))
                    _require_alive(dashboard)
                    break
                except QAFailure as exc:
                    last_start_error = exc
                    retry_cleanup = min(operation_deadline, monotonic() + 2.0)
                    _stop_owned(dashboard, retry_cleanup, monotonic, killpg)
                    dashboard = None
                    if monotonic() >= operation_deadline:
                        raise
            else:
                raise QAFailure("dashboard failed to claim an owned loopback port") from last_start_error

            assert dashboard is not None and active_fetch is not None
            health = _fetch_owned_json(
                active_fetch, base_url + f"/api/plugins/{PLUGIN_ID}/health",
                min(5.0, _remaining(operation_deadline, monotonic)), dashboard, port, listener_owned,
            )
            _assert_health(health)
            snapshot = _fetch_owned_json(
                active_fetch, base_url + f"/api/plugins/{PLUGIN_ID}/snapshot",
                min(5.0, _remaining(operation_deadline, monotonic)), dashboard, port, listener_owned,
            )
            _assert_snapshot(snapshot)

            if not _has_owned_listener(lambda: listener_owned(dashboard, port)):
                raise QAFailure("dashboard listener ownership could not be proven before CDP QA")

            cdp = _spawn_owned(
                popen,
                [python_executable, str(ROOT / "scripts" / "qa_dashboard_cdp.py"),
                 "--chromium", browser_executable, "--url", base_url + "/second-brain",
                 "--fixture", str(ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"),
                 "--output", str(cdp_output)],
                env=env, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                returncode = cdp.process.wait(timeout=min(COMMAND_TIMEOUT, _remaining(operation_deadline, monotonic)))
            except subprocess.TimeoutExpired as exc:
                raise QAFailure("CDP QA exceeded the clean-install deadline") from exc
            if returncode != 0:
                raise QAFailure("CDP QA failed")
            _require_alive(dashboard)
            if not _has_owned_listener(lambda: listener_owned(dashboard, port)):
                raise QAFailure("dashboard listener ownership changed during CDP QA")
            cdp = None
        except (KeyboardInterrupt, SystemExit) as exc:
            cancellation = exc
        except Exception as exc:
            primary_error = exc
        finally:
            cleanup_deadline = min(deadline, monotonic() + CLEANUP_TIMEOUT)
            for label, owned in (("owned CDP process", cdp), ("owned dashboard process", dashboard)):
                try:
                    _stop_owned(owned, cleanup_deadline, monotonic, killpg)
                except Exception:
                    cleanup_errors.append(label)
            if installed:
                for action in ("disable", "remove"):
                    try:
                        _run_checked(runner, [hermes_executable, "plugins", action, PLUGIN_ID], env, cleanup_deadline, monotonic)
                    except Exception:
                        cleanup_errors.append(f"plugin {action}")
                try:
                    if _plugin_entry(_list_plugins(runner, hermes_executable, env, cleanup_deadline, monotonic)) is not None:
                        cleanup_errors.append("plugin absence confirmation")
                except Exception:
                    cleanup_errors.append("plugin absence confirmation")

        if cancellation is not None:
            raise cancellation
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
    parser.add_argument("--browser", help="Chromium executable (resolved before environment isolation)")
    args = parser.parse_args(argv)
    try:
        result = run_clean_install(args.repository, args.ref, hermes=args.hermes, browser=args.browser)
    except QAFailure as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
