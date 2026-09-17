#!/usr/bin/env python3
"""Fail closed unless a link-free extracted tree matches archive paths."""

from __future__ import annotations

import argparse
import io
import os
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

try:
    from scripts.archive_safety import preflight_tar_bytes
except ModuleNotFoundError:  # direct execution: python scripts/check_archive_parity.py
    from archive_safety import preflight_tar_bytes


DEFAULT_MAX_ENTRIES = 100_000
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_NAME_BYTES = 4096
_ERROR = "error: archive/extraction path parity failed\n"


def _normalized_member_name(name: str, *, is_directory: bool) -> str:
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


def _bounded_names(names: Iterable[str], max_entries: int) -> frozenset[str]:
    if type(max_entries) is not int or max_entries < 1:
        raise ValueError("invalid entry bound")
    result: set[str] = set()
    count = 0
    for name in names:
        count += 1
        if count > max_entries:
            raise ValueError("entry bound exceeded")
        if name in result:
            raise ValueError("duplicate path")
        result.add(name)
    return frozenset(result)


def _archive_names(archive: Path, max_entries: int) -> frozenset[str]:
    with archive.open("rb") as source:
        payload = source.read(MAX_ARCHIVE_BYTES + 1)
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive byte bound exceeded")
    preflight_tar_bytes(payload, max_archive_bytes=MAX_ARCHIVE_BYTES)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as handle:
        def names() -> Iterable[str]:
            for member in handle:
                if member.type not in (tarfile.DIRTYPE, tarfile.REGTYPE):
                    raise ValueError("special archive entries are not permitted")
                if member.sparse is not None:
                    raise ValueError("sparse archive entries are not permitted")
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise ValueError("archive member byte bound exceeded")
                yield _normalized_member_name(
                    member.name,
                    is_directory=member.type == tarfile.DIRTYPE,
                )

        return _bounded_names(names(), max_entries)


def validate_archive(archive: Path, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
    """Reject unsafe names, duplicate paths, links, and excessive fanout."""
    _archive_names(Path(archive), max_entries)


def _tree_name_iter(root: Path) -> Iterable[str]:
    stack: list[tuple[Path, PurePosixPath | None]] = [(root, None)]
    while stack:
        directory, relative_directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise ValueError("extracted links are not permitted")
                metadata = entry.stat(follow_symlinks=False)
                is_directory = stat.S_ISDIR(metadata.st_mode)
                if not (is_directory or stat.S_ISREG(metadata.st_mode)):
                    raise ValueError("special extracted entries are not permitted")
                if not is_directory and metadata.st_nlink != 1:
                    raise ValueError("extracted hard links are not permitted")
                relative = PurePosixPath(entry.name) if relative_directory is None else relative_directory / entry.name
                yield relative.as_posix()
                if is_directory:
                    stack.append((Path(entry.path), relative))


def _tree_names(root: Path, max_entries: int) -> frozenset[str]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("invalid extraction root")
    return _bounded_names(_tree_name_iter(root), max_entries)


def check_parity(archive: Path, root: Path, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
    """Check exact path-set parity and reject every symbolic or hard link."""
    if _archive_names(Path(archive), max_entries) != _tree_names(Path(root), max_entries):
        raise ValueError("archive/extraction path mismatch")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument(
        "--root",
        type=Path,
        help="also compare this extracted root with the validated archive",
    )
    parser.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        validate_archive(args.archive, max_entries=args.max_entries)
        if args.root is not None:
            check_parity(args.archive, args.root, max_entries=args.max_entries)
        return 0
    except (OSError, tarfile.TarError, ValueError):
        sys.stderr.write(_ERROR)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
