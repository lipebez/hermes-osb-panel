#!/usr/bin/env python3
"""Fail-closed scanner for the exact payload of ``git archive HEAD``.

The pure functions are intentionally archive-only so their tests never require a
Git repository. The command-line entry point is the only Git integration and
will not inspect an uncommitted working tree as a substitute for a release.
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


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


def scan_archive_bytes(payload: bytes) -> list[Finding]:
    """Return filename/category findings from a Git archive tar payload only."""
    findings: list[Finding] = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive:
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            content = extracted.read()
            findings.extend(
                Finding(member.name, category)
                for category in _categories_for_file(member.name, content)
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


def _git_archive_head() -> bytes | None:
    result = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout if result.returncode == 0 else None


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
            payload = args.archive.read_bytes()
        except OSError:
            print("public release scanner: could not read supplied archive.", file=sys.stderr)
            return 2
        try:
            rendered = format_findings(scan_archive_bytes(payload))
        except (OSError, tarfile.TarError):
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

    rendered = format_findings(scan_archive_bytes(payload))
    if rendered:
        print(rendered)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
