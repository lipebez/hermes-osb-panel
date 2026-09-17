#!/usr/bin/env python3
"""Raw, bounded tar preflight shared by release archive consumers."""

from __future__ import annotations

import re


BLOCK_SIZE = 512
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
_ALLOWED_TYPES = frozenset((b"\0", b"0", b"5"))
_GIT_GLOBAL_NAME = b"pax_global_header"
_GIT_COMMENT = re.compile(rb"52 comment=[0-9a-f]{40}\n\Z")


def _octal_size(field: bytes) -> int:
    """Parse a POSIX tar size field, rejecting base-256 and malformed forms."""
    if len(field) != 12 or field[0] & 0x80:
        raise ValueError("unsafe tar size")
    nul = field.find(b"\0")
    if nul >= 0:
        if any(byte not in (0, 32) for byte in field[nul:]):
            raise ValueError("unsafe tar size")
        field = field[:nul]
    digits = field.strip(b" ")
    if not digits or any(byte < ord("0") or byte > ord("7") for byte in digits):
        raise ValueError("unsafe tar size")
    return int(digits, 8)


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
        size = _octal_size(header[124:136])
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
