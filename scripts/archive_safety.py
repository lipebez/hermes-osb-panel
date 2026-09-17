#!/usr/bin/env python3
"""Raw, bounded tar preflight shared by release archive consumers."""

from __future__ import annotations

import re


BLOCK_SIZE = 512
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
_ALLOWED_TYPES = frozenset((b"0", b"5"))
_GIT_GLOBAL_NAME = b"pax_global_header"
_GIT_COMMENT = re.compile(rb"52 comment=[0-9a-f]{40}\n\Z")
_CHECKSUM = re.compile(rb"(?:[0-7]{6}\0 |[0-7]{6}  |[0-7]{7}\0)\Z")

_NUMERIC_FIELDS = (
    ("mode", 100, 108),
    ("uid", 108, 116),
    ("gid", 116, 124),
    ("size", 124, 136),
    ("mtime", 136, 148),
    ("devmajor", 329, 337),
    ("devminor", 337, 345),
)


def _octal_field(field: bytes, label: str) -> int:
    """Parse a padded POSIX octal field without accepting base-256."""
    if field and field[0] & 0x80:
        raise ValueError(f"unsafe tar {label}")
    if re.fullmatch(rb"[\0 ]*", field):
        return 0
    match = re.fullmatch(rb" *([0-7]+)(?:\0[\0 ]*| *)", field)
    if match is None:
        raise ValueError(f"unsafe tar {label}")
    return int(match.group(1), 8)


def _validate_numeric_fields(header: bytes) -> int:
    values = {
        label: _octal_field(header[start:stop], label)
        for label, start, stop in _NUMERIC_FIELDS
    }
    if _CHECKSUM.fullmatch(header[148:156]) is None:
        raise ValueError("unsafe tar checksum")
    return values["size"]


def preflight_tar_bytes(payload: bytes, *, max_archive_bytes: int = MAX_ARCHIVE_BYTES) -> None:
    """Reject all tar extensions except Git's exact global commit comment."""
    if not isinstance(payload, bytes) or len(payload) > max_archive_bytes:
        raise ValueError("archive byte bound exceeded")
    if not payload or len(payload) % BLOCK_SIZE:
        raise ValueError("truncated tar archive")

    offset = 0
    saw_entry = False
    saw_global = False
    while offset + BLOCK_SIZE <= len(payload):
        header = payload[offset : offset + BLOCK_SIZE]
        if not any(header):
            if len(payload) - offset < 2 * BLOCK_SIZE or any(payload[offset:]):
                raise ValueError("data after tar terminator")
            return

        typeflag = header[156:157]
        size = _validate_numeric_fields(header)
        data_start = offset + BLOCK_SIZE
        padded_size = ((size + BLOCK_SIZE - 1) // BLOCK_SIZE) * BLOCK_SIZE
        next_offset = data_start + padded_size
        if next_offset > len(payload):
            raise ValueError("truncated tar member")

        if typeflag == b"g":
            name_field = header[:100]
            prefix = header[345:500].rstrip(b"\0")
            record = payload[data_start : data_start + size]
            if (
                saw_entry
                or saw_global
                or name_field.rstrip(b"\0") != _GIT_GLOBAL_NAME
                or prefix
                or _GIT_COMMENT.fullmatch(record) is None
            ):
                raise ValueError("unsafe global pax header")
            saw_global = True
        elif typeflag in _ALLOWED_TYPES:
            saw_entry = True
        else:
            # Reject PAX x, GNU long names/links/sparse, links, devices, and
            # every other extension before tarfile can interpret its payload.
            raise ValueError("extended tar header is not permitted")

        offset = next_offset

    raise ValueError("tar terminator missing")
