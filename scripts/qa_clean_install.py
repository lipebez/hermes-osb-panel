#!/usr/bin/env python3
"""Isolated, fail-closed clean-install QA for hermes-osb-panel."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

PLUGIN_ID = "hermes-osb-panel"
ROOT = Path(__file__).resolve().parents[1]
MAX_RESPONSE_BYTES = 1_000_000
MAX_STATUS_RESPONSE_BYTES = 64_000
MAX_HEADER_BYTES = 64_000
MAX_STATUS_LINE_BYTES = 8_192
COMMAND_TIMEOUT = 120.0
TOTAL_TIMEOUT = 180.0
CLEANUP_TIMEOUT = 10.0
DASHBOARD_READY_TIMEOUT = 15.0
DASHBOARD_POLL_INTERVAL = 0.1
MAX_TRANSIENT_FETCH_FAILURES = 50
MAX_PORT_ATTEMPTS = 3
SAFE_PATH = "/usr/bin:/bin"
F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
REQUIRED_MEMFD_SEALS = 0x01 | 0x02 | 0x04 | 0x08
_REPO_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})\Z")
_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_SOCKET_INODE_RE = re.compile(r"socket:\[([1-9][0-9]*)\]\Z")
_GENERATED_AT_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{6})?\+00:00\Z"
)

_DISABLED_COUNTS = {
    "preferences": 0,
    "inbox": 0,
    "retired": 0,
    "logs": 0,
    "brain_nodes": 0,
    "vault_notes": 0,
    "hermes_memory": 0,
    "hermes_user": 0,
    "hermes_notes": 0,
}
_DISABLED_PROVIDER = {
    "name": "open-second-brain",
    "available": False,
    "health": "unavailable",
    "semantic": "disabled",
    "mode": "disabled",
}
_EMPTY_GRAPH_SUMMARY = {"hub_ids": [], "orphan_ids": [], "broken_count": 0}
_EMPTY_GRAPH_REVISION = "f5f601586348141c"


class QAFailure(RuntimeError):
    """A sanitized contract failure safe to print."""


class _RetryableFetchFailure(QAFailure):
    """A failed pinned transaction which may be retried from status."""


@dataclass(frozen=True)
class OwnedProcess:
    """A process group created by this harness with setsid(2)."""

    process: Any
    pgid: int
    harness_pgid: int | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _validate_loopback_url(url: str, expected_port: int) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise QAFailure("dashboard URL is invalid") from exc
    if (
        parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or port != expected_port
        or parsed.username is not None or parsed.password is not None or parsed.fragment
    ):
        raise QAFailure("dashboard request escaped the owned loopback endpoint")
    path = parsed.path or "/"
    return path + (("?" + parsed.query) if parsed.query else "")


def _wait_socket(sock: Any, event: int, deadline: float, monotonic: Callable[[], float],
                 selector_factory: Callable[[], Any]) -> None:
    remaining = _remaining(deadline, monotonic)
    with selector_factory() as selector:
        selector.register(sock, event)
        if not selector.select(remaining):
            raise QAFailure("clean-install QA deadline exceeded")
    _remaining(deadline, monotonic)


def _send_all(sock: Any, payload: bytes, deadline: float, monotonic: Callable[[], float],
              selector_factory: Callable[[], Any]) -> None:
    view = memoryview(payload)
    while view:
        _wait_socket(sock, selectors.EVENT_WRITE, deadline, monotonic, selector_factory)
        try:
            sent = sock.send(view)
        except BlockingIOError:
            continue
        if sent <= 0:
            raise _RetryableFetchFailure("dashboard connection failed")
        view = view[sent:]


def _recv_some(sock: Any, deadline: float, monotonic: Callable[[], float],
               selector_factory: Callable[[], Any]) -> bytes:
    while True:
        _wait_socket(sock, selectors.EVENT_READ, deadline, monotonic, selector_factory)
        try:
            return sock.recv(65536)
        except BlockingIOError:
            continue


def _read_pinned_json(sock: Any, path: str, limit: int, deadline: float,
                      monotonic: Callable[[], float], selector_factory: Callable[[], Any]) -> Any:
    request = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: application/json\r\n"
               "Connection: keep-alive\r\n\r\n").encode("ascii")
    _send_all(sock, request, deadline, monotonic, selector_factory)
    received = bytearray()
    marker = b"\r\n\r\n"
    while marker not in received:
        chunk = _recv_some(sock, deadline, monotonic, selector_factory)
        if not chunk:
            raise _RetryableFetchFailure("dashboard response ended before its headers")
        received.extend(chunk)
        if marker not in received and len(received) > MAX_HEADER_BYTES:
            raise _RetryableFetchFailure("dashboard response headers exceeded the QA size limit")
    raw_headers, body = bytes(received).split(marker, 1)
    if len(raw_headers) + len(marker) > MAX_HEADER_BYTES:
        raise _RetryableFetchFailure("dashboard response headers exceeded the QA size limit")
    try:
        lines = raw_headers.decode("iso-8859-1").split("\r\n")
        if len(lines[0].encode("iso-8859-1")) > MAX_STATUS_LINE_BYTES:
            raise ValueError("status line too large")
        version, status_text, _reason = lines[0].split(" ", 2)
        if not re.fullmatch(r"[0-9]{3}", status_text):
            raise ValueError("invalid status code")
        status = int(status_text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _RetryableFetchFailure("dashboard returned an invalid HTTP status") from exc
    if version != "HTTP/1.1":
        raise _RetryableFetchFailure("dashboard did not preserve HTTP/1.1")
    if status < 200 or status >= 300:
        raise _RetryableFetchFailure("dashboard returned a non-success status")
    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        if not line or ":" not in line or line[0] in " \t":
            raise _RetryableFetchFailure("dashboard returned invalid HTTP headers")
        name, value = line.split(":", 1)
        headers.setdefault(name.strip().lower(), []).append(value.strip())
    if "transfer-encoding" in headers:
        raise _RetryableFetchFailure("dashboard transfer encoding is not permitted")
    lengths = headers.get("content-length", [])
    if len(lengths) != 1 or re.fullmatch(r"[0-9]+", lengths[0]) is None:
        raise _RetryableFetchFailure("dashboard response requires Content-Length")
    length = int(lengths[0])
    if length > limit:
        raise _RetryableFetchFailure("dashboard response exceeded the QA size limit")
    content_types = headers.get("content-type", [])
    content_type = content_types[0].split(";", 1)[0].strip().lower() if len(content_types) == 1 else ""
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise _RetryableFetchFailure("dashboard returned a non-JSON content type")
    if any(
        "close" in {token.strip() for token in value.lower().split(",")}
        for value in headers.get("connection", [])
    ):
        raise _RetryableFetchFailure("dashboard connection was not persistent")
    if len(body) > length:
        raise _RetryableFetchFailure("dashboard returned bytes beyond Content-Length")
    body_bytes = bytearray(body)
    while len(body_bytes) < length:
        chunk = _recv_some(sock, deadline, monotonic, selector_factory)
        if not chunk:
            raise _RetryableFetchFailure("dashboard response ended before Content-Length")
        body_bytes.extend(chunk)
        if len(body_bytes) > length:
            raise _RetryableFetchFailure("dashboard returned bytes beyond Content-Length")
    try:
        return json.loads(bytes(body_bytes).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _RetryableFetchFailure("dashboard returned invalid JSON") from exc


def _fetch_json_transaction(
    url: str, timeout: float, expected_port: int, expected_install_id: str,
    connection_factory: Callable[..., Any] = socket.create_connection,
    monotonic: Callable[[], float] = time.monotonic,
    selector_factory: Callable[[], Any] = selectors.DefaultSelector,
) -> Any:
    """Authenticate status and fetch a target over one pinned direct socket."""
    target = _validate_loopback_url(url, expected_port)
    if not re.fullmatch(r"[0-9a-f]{32}", expected_install_id):
        raise QAFailure("expected dashboard identity is invalid")
    deadline = monotonic() + timeout
    connection = None
    try:
        connection = connection_factory(("127.0.0.1", expected_port), _remaining(deadline, monotonic))
        connection.setblocking(False)
        status = _read_pinned_json(
            connection, "/api/status", MAX_STATUS_RESPONSE_BYTES, deadline, monotonic, selector_factory,
        )
        if not isinstance(status, dict) or status.get("install_id") != expected_install_id:
            raise _RetryableFetchFailure("dashboard install identity did not match")
        return _read_pinned_json(connection, target, MAX_RESPONSE_BYTES, deadline, monotonic, selector_factory)
    except _RetryableFetchFailure:
        raise
    except OSError as exc:
        raise _RetryableFetchFailure("dashboard connection failed") from exc
    finally:
        if connection is not None:
            connection.close()


def _create_install_id(hermes_home: Path) -> str:
    """Create the private per-run dashboard identity without exposing it."""
    install_id = secrets.token_hex(16)
    path = hermes_home / "install_id"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, install_id.encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return install_id


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


def _check_opened_fd(descriptor: int, *, directory: bool) -> os.stat_result:
    status = os.fstat(descriptor)
    mode = status.st_mode
    expected_type = stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)
    if not expected_type:
        raise QAFailure("installed plugin path had an unexpected type")
    if status.st_uid != os.geteuid() or mode & 0o022:
        raise QAFailure("installed plugin path had unsafe permissions")
    if directory:
        if mode & 0o500 != 0o500:
            raise QAFailure("installed plugin directory was not owner-readable and searchable")
    elif status.st_nlink != 1 or mode & 0o7000 or not mode & stat.S_IRUSR:
        raise QAFailure("installed plugin file had unsafe identity or permissions")
    return status


def _read_fd(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65536, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise QAFailure("installed plugin file exceeded the QA size limit")


def _read_installed_assets(
    hermes_home: Path, repository: str, ref: str, *,
    _after_open: Callable[[str], None] | None = None,
) -> dict[str, bytes]:
    """Read the install once through an O_NOFOLLOW descriptor tree."""
    after_open = _after_open or (lambda _label: None)
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    opened: list[int] = []

    def open_component(parent: int | None, name: str | Path, label: str, *, directory: bool) -> int:
        flags = directory_flags if directory else file_flags
        try:
            descriptor = os.open(name, flags) if parent is None else os.open(name, flags, dir_fd=parent)
            opened.append(descriptor)
            _check_opened_fd(descriptor, directory=directory)
            after_open(label)
            return descriptor
        except QAFailure:
            raise
        except OSError as exc:
            raise QAFailure("installed plugin path was missing, linked, or unsafe") from exc

    try:
        home_fd = open_component(None, hermes_home, "HERMES_HOME", directory=True)
        plugins_fd = open_component(home_fd, "plugins", "plugins", directory=True)
        metadata_fd = open_component(
            plugins_fd, ".install-metadata.json", "plugins/.install-metadata.json", directory=False,
        )
        try:
            metadata = json.loads(_read_fd(metadata_fd, MAX_STATUS_RESPONSE_BYTES).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise QAFailure("plugin install metadata was invalid") from exc
        record = metadata.get(PLUGIN_ID) if isinstance(metadata, dict) else None
        if record != {
            "pinned": True, "revision": ref, "source": f"https://github.com/{repository}.git",
        }:
            raise QAFailure("plugin install metadata did not match the requested pin")

        plugin_fd = open_component(plugins_fd, PLUGIN_ID, f"plugins/{PLUGIN_ID}", directory=True)
        plugin_manifest_fd = open_component(
            plugin_fd, "plugin.yaml", f"plugins/{PLUGIN_ID}/plugin.yaml", directory=False,
        )
        try:
            manifest_text = _read_fd(plugin_manifest_fd, MAX_STATUS_RESPONSE_BYTES).decode("utf-8")
        except UnicodeError as exc:
            raise QAFailure("installed plugin manifest was invalid") from exc
        names = re.findall(r"(?m)^name:\s*['\"]?([^'\"\s#]+)['\"]?\s*(?:#.*)?$", manifest_text)
        if names != [PLUGIN_ID]:
            raise QAFailure("installed plugin manifest identity did not match")

        dashboard_fd = open_component(
            plugin_fd, "dashboard", f"plugins/{PLUGIN_ID}/dashboard", directory=True,
        )
        dashboard_manifest_fd = open_component(
            dashboard_fd, "manifest.json", f"plugins/{PLUGIN_ID}/dashboard/manifest.json", directory=False,
        )
        try:
            dashboard = json.loads(_read_fd(dashboard_manifest_fd, MAX_STATUS_RESPONSE_BYTES).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise QAFailure("installed dashboard manifest was invalid") from exc
        css = dashboard.get("css") if isinstance(dashboard, dict) else None
        if (
            not isinstance(dashboard, dict) or dashboard.get("name") != PLUGIN_ID
            or dashboard.get("entry") != "dist/index.js"
            or not isinstance(css, str) or css.split("?", 1)[0] != "dist/style.css"
        ):
            raise QAFailure("installed dashboard manifest assets were unexpected")

        dist_label = f"plugins/{PLUGIN_ID}/dashboard/dist"
        dist_fd = open_component(dashboard_fd, "dist", dist_label, directory=True)
        assets: dict[str, bytes] = {}
        for name in ("index.js", "style.css"):
            asset_fd = open_component(dist_fd, name, f"{dist_label}/{name}", directory=False)
            assets[name] = _read_fd(asset_fd, MAX_RESPONSE_BYTES)
        return assets
    finally:
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _git_output(
    arguments: Sequence[str], *, max_bytes: int = MAX_RESPONSE_BYTES,
    expected_size: int | None = None, deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> bytes:
    git = shutil.which("git", path=SAFE_PATH)
    if not git:
        raise QAFailure("git was unavailable for commit blob verification")
    if max_bytes < 0 or expected_size is not None and not 0 <= expected_size <= max_bytes:
        raise QAFailure("requested Git output size was invalid")
    operation_deadline = min(
        deadline if deadline is not None else float("inf"), monotonic() + COMMAND_TIMEOUT,
    )
    _remaining(operation_deadline, monotonic)
    owned: OwnedProcess | None = None
    stream = None
    failure: BaseException | None = None
    data = bytearray()
    limit = expected_size if expected_size is not None else max_bytes
    try:
        owned = _spawn_owned(
            subprocess.Popen, [git, "-C", str(ROOT), *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={
                "PATH": SAFE_PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_SYSTEM": "/dev/null",
            },
        )
        stream = owned.process.stdout
        if stream is None:
            raise QAFailure("requested Git object could not be verified")
        descriptor = stream.fileno()
        os.set_blocking(descriptor, False)
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ)
            while True:
                if not selector.select(_remaining(operation_deadline, monotonic)):
                    raise QAFailure("clean-install QA deadline exceeded")
                try:
                    chunk = os.read(descriptor, min(65536, limit + 1 - len(data)))
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    if expected_size is not None:
                        raise QAFailure("requested Git blob size did not match its declaration")
                    raise QAFailure("requested Git output exceeded the QA size limit")
        try:
            returncode = owned.process.wait(timeout=_remaining(operation_deadline, monotonic))
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise QAFailure("requested Git object could not be verified") from exc
        if returncode != 0:
            raise QAFailure("requested Git object could not be verified")
        if expected_size is not None and len(data) != expected_size:
            raise QAFailure("requested Git blob size did not match its declaration")
        return bytes(data)
    except (OSError, subprocess.SubprocessError) as exc:
        failure = exc
        raise QAFailure("requested Git object could not be verified") from exc
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_error: BaseException | None = None
        if stream is not None:
            try:
                stream.close()
            except BaseException as exc:
                cleanup_error = exc
        try:
            _stop_owned(owned, time.monotonic() + CLEANUP_TIMEOUT, time.monotonic)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if failure is None and cleanup_error is not None:
            raise cleanup_error


def _trusted_git_assets(
    ref: str, *, deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, bytes]:
    operation_deadline = deadline if deadline is not None else monotonic() + COMMAND_TIMEOUT
    commit = _git_output(
        ["rev-parse", "--verify", f"{ref}^{{commit}}"],
        deadline=operation_deadline, monotonic=monotonic,
    )
    try:
        commit_identity = commit.decode("ascii", "strict").strip()
    except UnicodeError as exc:
        raise QAFailure("requested Git commit identity did not match") from exc
    if commit_identity != ref:
        raise QAFailure("requested Git commit identity did not match")
    assets: dict[str, bytes] = {}
    for name in ("index.js", "style.css"):
        object_spec = f"{ref}:dashboard/dist/{name}"
        if _git_output(
            ["cat-file", "-t", object_spec], deadline=operation_deadline, monotonic=monotonic,
        ) != b"blob\n":
            raise QAFailure("requested dashboard Git object was not a blob")
        size_field = _git_output(
            ["cat-file", "-s", object_spec], max_bytes=32,
            deadline=operation_deadline, monotonic=monotonic,
        )
        if not re.fullmatch(rb"(?:0|[1-9][0-9]*)\n", size_field):
            raise QAFailure("requested Git blob size was invalid")
        size = int(size_field[:-1])
        if size > MAX_RESPONSE_BYTES:
            raise QAFailure("requested Git blob exceeded the QA size limit")
        assets[name] = _git_output(
            ["cat-file", "blob", object_spec], expected_size=size,
            deadline=operation_deadline, monotonic=monotonic,
        )
    return assets


def _validated_installed_assets(
    hermes_home: Path, repository: str, ref: str, *, deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, bytes]:
    installed = _read_installed_assets(hermes_home, repository, ref)
    trusted = _trusted_git_assets(ref, deadline=deadline, monotonic=monotonic)
    if installed != trusted:
        raise QAFailure("installed dashboard asset did not match the requested Git blob")
    return installed


def _create_sealed_memfd(name: str, payload: bytes) -> int:
    descriptor = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short memfd write")
            view = view[written:]
        fcntl.fcntl(descriptor, F_ADD_SEALS, REQUIRED_MEMFD_SEALS)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


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
            except _RetryableFetchFailure:
                failures += 1
                if failures >= max_failures:
                    raise QAFailure("dashboard endpoint was repeatedly unavailable")
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


def _spawn_owned(popen: Callable[..., Any], command: Sequence[str], *,
                 getpgrp: Callable[[], int] = os.getpgrp, **kwargs: Any) -> OwnedProcess:
    harness_pgid = getpgrp()
    if not isinstance(harness_pgid, int) or harness_pgid <= 0:
        raise QAFailure("harness process group identity was unavailable")
    process = popen(list(command), start_new_session=True, **kwargs)
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        raise QAFailure("owned process identity was unavailable")
    owned = OwnedProcess(process, pid, harness_pgid)
    if owned.pgid == harness_pgid:
        raise QAFailure("owned process group identity was unsafe")
    return owned


def _group_exists(pgid: int, killpg: Callable[[int, int], None]) -> bool:
    try:
        killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_group_gone(pgid: int, process: Any, deadline: float, monotonic: Callable[[], float],
                     sleep: Callable[[float], None], killpg: Callable[[int, int], None]) -> bool:
    while monotonic() < deadline:
        process.poll()  # Reap the owned leader; descendants are still checked by PGID.
        if not _group_exists(pgid, killpg):
            return True
        sleep(min(0.05, max(0.0, deadline - monotonic())))
    process.poll()
    return not _group_exists(pgid, killpg)


def _stop_owned(
    owned: OwnedProcess | None, deadline: float, monotonic: Callable[[], float],
    killpg: Callable[[int, int], None] = os.killpg,
    sleep: Callable[[float], None] = time.sleep,
    getpgrp: Callable[[], int] = os.getpgrp,
) -> None:
    if owned is None:
        return
    if owned.pgid <= 0 or getattr(owned.process, "pid", None) != owned.pgid or owned.pgid == getpgrp() or (
        owned.harness_pgid is not None and owned.pgid == owned.harness_pgid
    ):
        raise QAFailure("refusing to signal the harness process group")
    if not _group_exists(owned.pgid, killpg):
        return
    try:
        killpg(owned.pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    term_deadline = min(deadline, monotonic() + 2.0)
    if not _wait_group_gone(owned.pgid, owned.process, term_deadline, monotonic, sleep, killpg):
        try:
            killpg(owned.pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        if not _wait_group_gone(owned.pgid, owned.process, deadline, monotonic, sleep, killpg):
            raise QAFailure("owned process group did not exit before cleanup deadline")
    if owned.process.poll() is not None:
        try:
            owned.process.wait(timeout=0)
        except (subprocess.TimeoutExpired, ChildProcessError):
            pass

def _strict_contract_equal(actual: Any, expected: Any) -> bool:
    """Compare JSON values without treating booleans as integer counts."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _strict_contract_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _strict_contract_equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _disabled_snapshot_contract() -> dict[str, Any]:
    return {
        "schema": "open-second-brain.dashboard.snapshot.v1",
        "revision": _EMPTY_GRAPH_REVISION,
        "provider": dict(_DISABLED_PROVIDER),
        "nodes": [],
        "edges": [],
        "summaries": {
            "counts": dict(_DISABLED_COUNTS),
            "graph": {key: list(value) if isinstance(value, list) else value for key, value in _EMPTY_GRAPH_SUMMARY.items()},
        },
        "limits": {
            key: {"shown": 0, "total": 0, "truncated": False}
            for key in ("brain_files", "vault_files", "artifacts", "vault_notes")
        },
        "activity": {"active_preview": "", "recent": [], "timeline": []},
        "metrics": {},
        "capabilities": {"graph_3d": True},
        "counts": dict(_DISABLED_COUNTS),
        "graph": {"nodes": [], "edges": []},
        "graph_summary": {
            key: list(value) if isinstance(value, list) else value for key, value in _EMPTY_GRAPH_SUMMARY.items()
        },
        "broken_links": [],
        "active_preview": "",
        "recent_logs": [],
        "timeline_events": [],
        "artifacts": [],
        "vault_notes": [],
    }


def _assert_snapshot(
    payload: Any, *, generated_after: datetime, generated_before: datetime,
) -> None:
    if not isinstance(payload, dict) or set(payload) != {"generated_at", *_disabled_snapshot_contract()}:
        raise QAFailure("snapshot contract failed")
    generated_at = payload.get("generated_at")
    bounds = (generated_after, generated_before)
    if any(
        type(bound) is not datetime or bound.tzinfo is None or bound.utcoffset() is None
        or bound.utcoffset() != timezone.utc.utcoffset(bound)
        for bound in bounds
    ) or generated_before < generated_after:
        raise QAFailure("snapshot validation window was invalid")
    if type(generated_at) is not str or _GENERATED_AT_RE.fullmatch(generated_at) is None:
        raise QAFailure("snapshot contract failed")
    try:
        parsed = datetime.fromisoformat(generated_at)
    except ValueError as exc:
        raise QAFailure("snapshot contract failed") from exc
    if not generated_after <= parsed <= generated_before:
        raise QAFailure("snapshot contract failed")
    stable_payload = {key: value for key, value in payload.items() if key != "generated_at"}
    if not _strict_contract_equal(stable_payload, _disabled_snapshot_contract()):
        raise QAFailure("snapshot did not fail closed")


def _assert_health(payload: Any) -> None:
    expected = {
        "ok": False,
        "provider": dict(_DISABLED_PROVIDER),
        "counts": dict(_DISABLED_COUNTS),
    }
    if not isinstance(payload, dict) or not _strict_contract_equal(payload, expected):
        raise QAFailure("plugin health contract failed")


def _require_alive(owned: OwnedProcess) -> None:
    if owned.process.poll() is not None:
        raise QAFailure("owned dashboard exited during verification")


def _fetch_owned_json(
    fetch_json: Callable[[str, float], Any], url: str, deadline: float,
    owned: OwnedProcess, port: int, listener_owned: Callable[[OwnedProcess, int], bool],
    sleep: Callable[[float], None], monotonic: Callable[[], float],
) -> Any:
    while monotonic() < deadline:
        _require_alive(owned)
        if _has_owned_listener(lambda: listener_owned(owned, port)):
            try:
                payload = fetch_json(url, min(2.0, _remaining(deadline, monotonic)))
            except _RetryableFetchFailure:
                payload = None
            except QAFailure:
                raise
            except Exception:
                payload = None
            else:
                _require_alive(owned)
                if _has_owned_listener(lambda: listener_owned(owned, port)):
                    return payload
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(DASHBOARD_POLL_INTERVAL, remaining))
    raise QAFailure("dashboard response identity could not be verified")


def run_clean_install(
    repository: str, ref: str, *, hermes: str = "hermes", browser: str | None = None,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _bounded_run,
    popen: Callable[..., Any] = subprocess.Popen,
    fetch_json: Callable[[str, float], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep, port_picker: Callable[[], int] = _free_port,
    monotonic: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], datetime] = _utc_now,
    which: Callable[..., str | None] = shutil.which,
    killpg: Callable[[int, int], None] = os.killpg, timeout: float = TOTAL_TIMEOUT,
    listener_owned: Callable[[OwnedProcess, int], bool] = _listener_is_owned,
    getpgrp: Callable[[], int] = os.getpgrp,
    asset_loader: Callable[[Path, str, str], dict[str, bytes]] | None = None,
) -> dict[str, Any]:
    if sys.platform != "linux":
        raise QAFailure("the real clean-install harness is Linux-only")
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
    asset_fds: list[int] = []
    first_error: BaseException | None = None
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
        install_id = _create_install_id(hermes_home)
        env = _safe_env(source_env, home, hermes_home)
        base_url = ""
        active_fetch: Callable[[str, float], Any] | None = None

        try:
            _run_checked(runner, [hermes_executable, "plugins", "install", repository, "--ref", ref, "--enable"], env, operation_deadline, monotonic)
            installed = True
            if not _cli_plugin_active(_list_plugins(runner, hermes_executable, env, operation_deadline, monotonic)):
                raise QAFailure("installed plugin is not active")
            _run_checked(runner, [hermes_executable, "plugins", "show", PLUGIN_ID], env, operation_deadline, monotonic)
            if asset_loader is None:
                assets = _validated_installed_assets(
                    hermes_home, repository, ref, deadline=operation_deadline, monotonic=monotonic,
                )
            else:
                assets = asset_loader(hermes_home, repository, ref)
            if set(assets) != {"index.js", "style.css"} or not all(
                isinstance(value, bytes) for value in assets.values()
            ):
                raise QAFailure("validated dashboard assets were invalid")
            _run_checked(runner, [hermes_executable, "plugins", "doctor", PLUGIN_ID, "--ci"], env, operation_deadline, monotonic)

            last_start_error: QAFailure | None = None
            for _attempt in range(MAX_PORT_ATTEMPTS):
                port = port_picker()
                base_url = f"http://127.0.0.1:{port}"
                active_fetch = fetch_json or (
                    lambda url, request_timeout, expected=port, identity=install_id:
                    _fetch_json_transaction(url, request_timeout, expected, identity)
                )
                dashboard = _spawn_owned(
                    popen,
                    [hermes_executable, "dashboard", "--host", "127.0.0.1", "--port", str(port), "--no-open", "--skip-build"],
                    env=env, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    getpgrp=getpgrp,
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
                    _stop_owned(dashboard, retry_cleanup, monotonic, killpg, sleep, getpgrp)
                    dashboard = None
                    if monotonic() >= operation_deadline:
                        raise
            else:
                raise QAFailure("dashboard failed to claim an owned loopback port") from last_start_error

            assert dashboard is not None and active_fetch is not None
            health = _fetch_owned_json(
                active_fetch, base_url + f"/api/plugins/{PLUGIN_ID}/health",
                min(operation_deadline, monotonic() + 5.0), dashboard, port, listener_owned,
                sleep, monotonic,
            )
            _assert_health(health)
            snapshot_deadline = min(operation_deadline, monotonic() + 5.0)
            snapshot_window_start = wall_clock()
            snapshot = _fetch_owned_json(
                active_fetch, base_url + f"/api/plugins/{PLUGIN_ID}/snapshot",
                snapshot_deadline, dashboard, port, listener_owned,
                sleep, monotonic,
            )
            _remaining(snapshot_deadline, monotonic)
            snapshot_window_end = wall_clock()
            _assert_snapshot(
                snapshot,
                generated_after=snapshot_window_start,
                generated_before=snapshot_window_end,
            )
            _remaining(snapshot_deadline, monotonic)

            try:
                asset_fds.append(_create_sealed_memfd("hermes-osb-index-js", assets["index.js"]))
                asset_fds.append(_create_sealed_memfd("hermes-osb-style-css", assets["style.css"]))
                cdp = _spawn_owned(
                    popen,
                    [python_executable, str(ROOT / "scripts" / "qa_dashboard_cdp.py"),
                     "--chromium", browser_executable, "--url", base_url + "/second-brain",
                     "--fixture", str(ROOT / "tests" / "fixtures" / "demo_snapshot_v1.json"),
                     "--asset-js-fd", str(asset_fds[0]), "--asset-css-fd", str(asset_fds[1]),
                     "--output", str(cdp_output), "--inherit-runner-process-group"],
                    env=env, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    pass_fds=tuple(asset_fds), getpgrp=getpgrp,
                )
            finally:
                for descriptor in asset_fds:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                asset_fds = []
            try:
                returncode = cdp.process.wait(timeout=min(COMMAND_TIMEOUT, _remaining(operation_deadline, monotonic)))
            except subprocess.TimeoutExpired as exc:
                raise QAFailure("CDP QA exceeded the clean-install deadline") from exc
            if returncode != 0:
                raise QAFailure("CDP QA failed")
            _require_alive(dashboard)
            cdp = None
        except BaseException as exc:
            first_error = exc
        finally:
            for descriptor in asset_fds:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            asset_fds = []
            try:
                cleanup_deadline = min(deadline, monotonic() + CLEANUP_TIMEOUT)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                cleanup_deadline = deadline

            def cleanup_step(label: str, operation: Callable[[], Any]) -> None:
                nonlocal first_error
                interrupted = False
                while True:
                    try:
                        operation()
                        if interrupted:
                            cleanup_errors.append(label + " interrupted")
                        return
                    except BaseException as exc:
                        if first_error is None:
                            first_error = exc
                        if isinstance(exc, Exception):
                            cleanup_errors.append(label)
                            return
                        try:
                            expired = monotonic() >= cleanup_deadline
                        except BaseException as clock_exc:
                            if first_error is None:
                                first_error = clock_exc
                            expired = True
                        if interrupted or expired:
                            cleanup_errors.append(label)
                            return
                        interrupted = True

            for label, owned in (("owned CDP process", cdp), ("owned dashboard process", dashboard)):
                cleanup_step(
                    label,
                    lambda selected=owned: _stop_owned(
                        selected, cleanup_deadline, monotonic, killpg, sleep, getpgrp
                    ),
                )
            if installed:
                for action in ("disable", "remove"):
                    cleanup_step(
                        f"plugin {action}",
                        lambda selected=action: _run_checked(
                            runner, [hermes_executable, "plugins", selected, PLUGIN_ID],
                            env, cleanup_deadline, monotonic,
                        ),
                    )

                def confirm_absent() -> None:
                    if _plugin_entry(_list_plugins(
                        runner, hermes_executable, env, cleanup_deadline, monotonic
                    )) is not None:
                        raise QAFailure("plugin remained installed")

                cleanup_step("plugin absence confirmation", confirm_absent)

        if first_error is not None:
            if isinstance(first_error, QAFailure):
                raise first_error
            if not isinstance(first_error, Exception):
                raise first_error
            raise QAFailure("clean-install QA failed") from first_error
        if cleanup_errors:
            raise QAFailure("cleanup failed: " + ", ".join(cleanup_errors))
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
