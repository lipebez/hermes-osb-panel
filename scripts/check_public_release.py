#!/usr/bin/env python3
"""Fail-closed scanner for the exact payload of ``git archive HEAD``.

The pure functions are intentionally archive-only so their tests never require a
Git repository. The command-line entry point is the only Git integration and
will not inspect an uncommitted working tree as a substitute for a release.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import selectors
import signal
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

try:
    from scripts.archive_safety import preflight_tar_bytes
except ModuleNotFoundError:  # direct execution: python scripts/check_public_release.py
    from archive_safety import preflight_tar_bytes


MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_NAME_BYTES = 4096
MAX_ENTRIES = 100_000
GIT_ARCHIVE_TIMEOUT_SECONDS = 30.0
PROCESS_CLEANUP_GRACE_SECONDS = 0.25
ALLOWED_BINARY_ARCHIVE_PATHS = frozenset({"tests/fixtures/release_scanner_neutral.png"})
BINARY_MEDIA_SUFFIXES = frozenset(
    {
        ".avif",
        ".bmp",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".svg",
        ".webm",
        ".webp",
    }
)

ENVIRONMENT_ARTIFACT = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\.[^/]+)?|[^/]+\.local\.env|local(?:-[^/]+)?\.env)$"
)
QA_REPORT_OR_SCREENSHOT = re.compile(
    r"(?i)(?:^|/)(?:qa[-_]?output|reports?)(?:/|$)|(?:report|screenshot|screen[-_]?shot|capture)[^/]*\.(?:json|jpe?g|png|webp)$"
)
PRIVATE_HOME_PATH_LITERAL = re.escape(chr(92))
PRIVATE_HOME_PATH = re.compile(
    r"(?i)(?:file:" + "/" + "/)?/" + r"(?:root|home|users)" + "/"
    + r"|[A-Z]:" + PRIVATE_HOME_PATH_LITERAL + r"Users" + PRIVATE_HOME_PATH_LITERAL
    + r"|" + PRIVATE_HOME_PATH_LITERAL * 2 + r"[^" + PRIVATE_HOME_PATH_LITERAL + r"\s]+" + PRIVATE_HOME_PATH_LITERAL
)
IDENTITY_MARKER = re.compile(
    r"(?i)\b(?:owner|customer|demo|example|test)\s+(?:example|user|customer|owner)\b|\bsynthetic[-_ ](?:identity|owner|user)\b"
)
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?im)(?<![?&])\b(?:api[-_]?key|access[-_]?token|auth(?:orization)?|client[-_]?secret|credential|password|secret|token)\b\s*[:=]\s*(?!\[(?:redacted|masked)\])[A-Za-z0-9_./+=-]{8,}"
)
TOKEN_SHAPE = re.compile(
    r"\b(?:sk|ghp|gho|github_pat)_[A-Za-z0-9_-]{16,}\b|\b[A-Za-z0-9]{24,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
URL_CREDENTIAL_OR_QUERY_SECRET = re.compile(
    r"(?i)://[^/\s:@]+:[^@\s/]+@|[?&](?:api[-_]?key|access[-_]?token|auth|client[-_]?secret|credential|password|secret|token)=[^&#\s]+"
)


@dataclass(frozen=True, order=True)
class Finding:
    filename: str
    category: str


def _is_binary_content(content: bytes) -> bool:
    """Classify bytes deliberately instead of treating every media suffix as text."""
    sample = content[:8192]
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _is_binary_media(filename: str, content: bytes) -> bool:
    suffix = PurePosixPath(filename).suffix.lower()
    return suffix in BINARY_MEDIA_SUFFIXES or _is_binary_content(content)


def _categories_for_file(filename: str, content: bytes) -> tuple[str, ...]:
    categories: list[str] = []
    if ENVIRONMENT_ARTIFACT.search(filename):
        categories.append("environment artifact")
    if QA_REPORT_OR_SCREENSHOT.search(filename):
        categories.append("QA report/screenshot")
    if filename not in ALLOWED_BINARY_ARCHIVE_PATHS and _is_binary_media(filename, content):
        categories.append("binary media")

    text = content.decode("utf-8", errors="replace")
    if PRIVATE_HOME_PATH.search(text):
        categories.append("private home path")
    if IDENTITY_MARKER.search(text):
        categories.append("identity marker")
    if CREDENTIAL_ASSIGNMENT.search(text) or TOKEN_SHAPE.search(text):
        categories.append("credential assignment/token shape")
    if URL_CREDENTIAL_OR_QUERY_SECRET.search(text):
        categories.append("URL credential/query secret")
    return tuple(categories)


def _safe_member_name(name: str, *, is_directory: bool) -> str:
    try:
        name_size = len(name.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError("unsafe archive member") from error
    if not name or name_size > MAX_NAME_BYTES or name.startswith("/"):
        raise ValueError("unsafe archive member")
    raw = name[:-1] if is_directory and name.endswith("/") else name
    if not raw or any(part in ("", ".", "..") for part in raw.split("/")):
        raise ValueError("unsafe archive member")
    path = PurePosixPath(raw)
    if path.is_absolute():
        raise ValueError("unsafe archive member")
    return path.as_posix()


def scan_archive_bytes(payload: bytes) -> list[Finding]:
    """Return filename/category findings from a Git archive tar payload only."""
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive byte bound exceeded")
    preflight_tar_bytes(payload, max_archive_bytes=MAX_ARCHIVE_BYTES)
    findings: list[Finding] = []
    names: set[str] = set()
    count = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive:
            count += 1
            if count > MAX_ENTRIES:
                raise ValueError("entry bound exceeded")
            if member.type not in (tarfile.DIRTYPE, tarfile.REGTYPE):
                raise ValueError("special archive entry")
            if member.sparse is not None:
                raise ValueError("sparse archive entry")
            if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                raise ValueError("archive member byte bound exceeded")
            filename = _safe_member_name(
                member.name,
                is_directory=member.type == tarfile.DIRTYPE,
            )
            if filename in names:
                raise ValueError("duplicate archive path")
            names.add(filename)
            if member.type == tarfile.DIRTYPE:
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("unreadable archive member")
            content = extracted.read(MAX_MEMBER_BYTES + 1)
            if len(content) != member.size or len(content) > MAX_MEMBER_BYTES:
                raise ValueError("archive member byte bound exceeded")
            findings.extend(
                Finding(filename, category)
                for category in _categories_for_file(filename, content)
            )
    return findings


def format_findings(findings: Iterable[Finding]) -> str:
    """Render only safe filename/category labels; never include matched content."""
    return "\n".join(f"{finding.filename}: {finding.category}" for finding in findings)


def _head_exists() -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _bounded_wait(process: subprocess.Popen[bytes], timeout: float) -> int | None:
    try:
        return process.wait(timeout=max(timeout, 0.001))
    except subprocess.TimeoutExpired:
        return None


def _signal_process_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        pass


def _wait_process_group_absent(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(timeout, 0.001)
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def _git_archive_head(
    *,
    timeout_seconds: float = GIT_ARCHIVE_TIMEOUT_SECONDS,
    cleanup_grace_seconds: float = PROCESS_CLEANUP_GRACE_SECONDS,
    max_archive_bytes: int = MAX_ARCHIVE_BYTES,
) -> bytes | None:
    """Read git archive incrementally with a hard deadline and bounded cleanup."""
    process = subprocess.Popen(
        ["git", "archive", "--format=tar", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pgid = process.pid
    stdout = None
    selector = None
    failure: BaseException | None = None
    failure_traceback = None
    result: bytes | None = None
    try:
        assert process.stdout is not None
        stdout = process.stdout
        selector = selectors.DefaultSelector()
        deadline = time.monotonic() + timeout_seconds
        descriptor = stdout.fileno()
        os.set_blocking(descriptor, False)
        selector.register(descriptor, selectors.EVENT_READ)
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not selector.select(min(remaining, 0.1)):
                continue
            chunk = os.read(descriptor, min(65_536, max_archive_bytes + 1 - total))
            if not chunk:
                returncode = _bounded_wait(process, max(0.0, deadline - time.monotonic()))
                if returncode == 0:
                    result = b"".join(chunks)
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_archive_bytes:
                break
    except BaseException as error:
        failure = error
        failure_traceback = error.__traceback__
    finally:
        if selector is not None:
            try:
                selector.close()
            except BaseException as error:
                if failure is None:
                    failure, failure_traceback = error, error.__traceback__
        if stdout is None:
            stdout = process.stdout
        if stdout is not None:
            try:
                stdout.close()
            except BaseException as error:
                if failure is None:
                    failure, failure_traceback = error, error.__traceback__
        try:
            _signal_process_group(pgid, signal.SIGTERM)
        except BaseException as error:
            if failure is None:
                failure, failure_traceback = error, error.__traceback__
        try:
            _bounded_wait(process, cleanup_grace_seconds)
        except BaseException as error:
            if failure is None:
                failure, failure_traceback = error, error.__traceback__
        group_absent = False
        try:
            group_absent = _wait_process_group_absent(pgid, cleanup_grace_seconds)
        except BaseException as error:
            if failure is None:
                failure, failure_traceback = error, error.__traceback__
        if not group_absent:
            try:
                _signal_process_group(pgid, signal.SIGKILL)
            except BaseException as error:
                if failure is None:
                    failure, failure_traceback = error, error.__traceback__
            try:
                _wait_process_group_absent(pgid, cleanup_grace_seconds)
            except BaseException as error:
                if failure is None:
                    failure, failure_traceback = error, error.__traceback__
        try:
            _bounded_wait(process, cleanup_grace_seconds)
        except BaseException as error:
            if failure is None:
                failure, failure_traceback = error, error.__traceback__
    if failure is not None:
        raise failure.with_traceback(failure_traceback)
    return result


def _read_archive(path: Path) -> bytes:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("archive byte bound exceeded")
    with path.open("rb") as handle:
        payload = handle.read(MAX_ARCHIVE_BYTES + 1)
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive byte bound exceeded")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan a committed public-release archive")
    parser.add_argument(
        "--archive",
        type=Path,
        help="scan this prebuilt git-archive tar instead of creating git archive HEAD",
    )
    args = parser.parse_args(argv)

    if args.archive is not None:
        try:
            payload = _read_archive(args.archive)
        except (OSError, ValueError):
            print("public release scanner: could not read supplied archive.", file=sys.stderr)
            return 2
        try:
            rendered = format_findings(scan_archive_bytes(payload))
        except (OSError, tarfile.TarError, ValueError):
            print("public release scanner: supplied archive is not a readable tar.", file=sys.stderr)
            return 2
        if rendered:
            print(rendered)
            return 1
        return 0

    if not _head_exists():
        print(
            "public release scanner: no HEAD commit; git archive HEAD requires a committed release candidate.",
            file=sys.stderr,
        )
        return 2

    payload = _git_archive_head()
    if payload is None:
        print("public release scanner: git archive HEAD failed.", file=sys.stderr)
        return 2

    try:
        rendered = format_findings(scan_archive_bytes(payload))
    except (OSError, tarfile.TarError, ValueError):
        print("public release scanner: git archive HEAD is not a readable bounded tar.", file=sys.stderr)
        return 2
    if rendered:
        print(rendered)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
