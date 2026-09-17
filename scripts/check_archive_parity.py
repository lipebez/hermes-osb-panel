#!/usr/bin/env python3
"""Fail closed unless an extracted tree has exactly the archive's paths."""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


DEFAULT_MAX_ENTRIES = 100_000
_ERROR = "error: archive/extraction path parity failed\n"


def _normalized_member_name(name: str) -> str:
    stripped = name.rstrip("/")
    path = PurePosixPath(stripped)
    if not stripped or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
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
    with tarfile.open(archive, mode="r:*") as handle:
        return _bounded_names(
            (_normalized_member_name(member.name) for member in handle),
            max_entries,
        )


def _tree_name_iter(root: Path) -> Iterable[str]:
    stack: list[tuple[Path, PurePosixPath | None]] = [(root, None)]
    while stack:
        directory, relative_directory = stack.pop()
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: os.fsencode(entry.name), reverse=True)
        for entry in ordered:
            relative = PurePosixPath(entry.name) if relative_directory is None else relative_directory / entry.name
            yield relative.as_posix()
            if entry.is_dir(follow_symlinks=False):
                stack.append((Path(entry.path), relative))


def _tree_names(root: Path, max_entries: int) -> frozenset[str]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("invalid extraction root")
    return _bounded_names(_tree_name_iter(root), max_entries)


def check_parity(archive: Path, root: Path, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
    """Check exact path-set parity without following extraction symlinks."""
    if _archive_names(Path(archive), max_entries) != _tree_names(Path(root), max_entries):
        raise ValueError("archive/extraction path mismatch")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        check_parity(args.archive, args.root, max_entries=args.max_entries)
        return 0
    except (OSError, tarfile.TarError, ValueError):
        sys.stderr.write(_ERROR)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
