#!/usr/bin/env python3
"""Build a deterministic, sanitized operator-supplied release summary.

All evidence is supplied explicitly. This module does not inspect Git, the
repository, environment variables, host state, QA artifacts, or raw logs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import NoReturn, Sequence


_NUMERIC_IDENTIFIER = r"(?:0|[1-9][0-9]*)"
_PRERELEASE_IDENTIFIER = rf"(?:{_NUMERIC_IDENTIFIER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_VERSION = re.compile(
    rf"{_NUMERIC_IDENTIFIER}\.{_NUMERIC_IDENTIFIER}\.{_NUMERIC_IDENTIFIER}"
    rf"(?:-{_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CLI_ERROR = "error: unable to create release evidence\n"


def _validated_version(field: str, value: object) -> str:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def _validated_sha(field: str, value: object) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def _validated_sha256(field: str, value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def _validated_bool(field: str, value: object) -> bool:
    if type(value) is not bool:
        raise ValueError(f"invalid {field}")
    return value


def _validated_count(field: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid {field}")
    return value


def build_evidence(
    *,
    project_version: object,
    candidate_sha: object,
    candidate_archive_sha256: object,
    tested_hermes_version: object,
    tested_hermes_ref: object,
    tested_osb_version: object,
    tested_osb_ref: object,
    static_gate_passed: object,
    unit_gate_passed: object,
    archive_gate_passed: object,
    doctor_gate_passed: object,
    tests_run: object,
    tests_passed: object,
    tests_failed: object,
) -> dict[str, object]:
    """Return the fixed release-evidence schema from safe scalar inputs only."""
    project = _validated_version("project_version", project_version)
    candidate = _validated_sha("candidate_sha", candidate_sha)
    archive_sha256 = _validated_sha256("candidate_archive_sha256", candidate_archive_sha256)
    hermes_version = _validated_version("tested_hermes_version", tested_hermes_version)
    hermes_ref = _validated_sha("tested_hermes_ref", tested_hermes_ref)
    osb_version = _validated_version("tested_osb_version", tested_osb_version)
    osb_ref = _validated_sha("tested_osb_ref", tested_osb_ref)
    static_passed = _validated_bool("static_gate_passed", static_gate_passed)
    unit_passed = _validated_bool("unit_gate_passed", unit_gate_passed)
    archive_passed = _validated_bool("archive_gate_passed", archive_gate_passed)
    doctor_passed = _validated_bool("doctor_gate_passed", doctor_gate_passed)
    run = _validated_count("tests_run", tests_run)
    passed = _validated_count("tests_passed", tests_passed)
    failed = _validated_count("tests_failed", tests_failed)

    if run == 0 or passed + failed != run:
        raise ValueError("inconsistent test counts")
    if unit_passed != (failed == 0):
        raise ValueError("unit gate contradicts test counts")
    if not all((static_passed, unit_passed, archive_passed, doctor_passed)):
        raise ValueError("release gate did not pass")

    return {
        "candidate_archive_sha256": archive_sha256,
        "candidate_sha": candidate,
        "doctor_gate_passed": doctor_passed,
        "evidence_kind": "sanitized_operator_summary.v1",
        "gates": {
            "archive_passed": archive_passed,
            "static_passed": static_passed,
            "unit_passed": unit_passed,
        },
        "project_version": project,
        "tested_hermes": {"ref": hermes_ref, "version": hermes_version},
        "tested_osb": {"ref": osb_ref, "version": osb_version},
        "tests": {"failed": failed, "passed": passed, "run": run},
    }


def encode_evidence(evidence: dict[str, object]) -> str:
    """Encode evidence as stable UTF-8-compatible JSON with one final newline."""
    return json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("expected 'true' or 'false'")


def _output_path(value: str) -> tuple[Path, str]:
    output = Path(value)
    if not output.is_absolute() or output.name in ("", ".", ".."):
        raise ValueError("output must be an absolute path below /tmp")

    try:
        temporary_root = Path("/tmp").resolve(strict=True)
        resolved_parent = output.parent.resolve(strict=True)
        resolved_parent.relative_to(temporary_root)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("output must be an absolute path below /tmp") from None

    if resolved_parent == temporary_root and output.name in ("", ".", ".."):
        raise ValueError("output must be an absolute path below /tmp")
    return resolved_parent, output.name


def _open_parent(parent: Path) -> int:
    """Open a validated parent without following mutable path symlinks."""
    temporary_root = Path("/tmp").resolve(strict=True)
    relative = parent.relative_to(temporary_root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory_fd = os.open(temporary_root, flags)
    try:
        for component in relative.parts:
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def _publish_atomic(output: tuple[Path, str], content: str) -> None:
    parent, destination = output
    directory_fd = _open_parent(parent)
    temporary_name: str | None = None
    temporary_fd: int | None = None
    try:
        try:
            destination_stat = os.stat(destination, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            destination_stat = None
        if destination_stat is not None and stat.S_ISLNK(destination_stat.st_mode):
            raise ValueError("output must not be a symlink")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        for _ in range(128):
            candidate = f".release-evidence-{secrets.token_hex(16)}.tmp"
            try:
                temporary_fd = os.open(candidate, flags, 0o600, dir_fd=directory_fd)
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        else:
            raise OSError("unable to allocate temporary output")

        data = content.encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(temporary_fd, data[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None
        os.replace(
            temporary_name,
            destination,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = None
        os.fsync(directory_fd)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


class _BoundedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("invalid command line")


def _parser() -> argparse.ArgumentParser:
    parser = _BoundedArgumentParser(description=__doc__)
    parser.add_argument("--project-version", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--candidate-archive-sha256", required=True)
    parser.add_argument("--tested-hermes-version", required=True)
    parser.add_argument("--tested-hermes-ref", required=True)
    parser.add_argument("--tested-osb-version", required=True)
    parser.add_argument("--tested-osb-ref", required=True)
    parser.add_argument("--static-gate-passed", required=True, type=_boolean)
    parser.add_argument("--unit-gate-passed", required=True, type=_boolean)
    parser.add_argument("--archive-gate-passed", required=True, type=_boolean)
    parser.add_argument("--doctor-gate-passed", required=True, type=_boolean)
    parser.add_argument("--tests-run", required=True, type=int)
    parser.add_argument("--tests-passed", required=True, type=int)
    parser.add_argument("--tests-failed", required=True, type=int)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        evidence = build_evidence(
            project_version=args.project_version,
            candidate_sha=args.candidate_sha,
            candidate_archive_sha256=args.candidate_archive_sha256,
            tested_hermes_version=args.tested_hermes_version,
            tested_hermes_ref=args.tested_hermes_ref,
            tested_osb_version=args.tested_osb_version,
            tested_osb_ref=args.tested_osb_ref,
            static_gate_passed=args.static_gate_passed,
            unit_gate_passed=args.unit_gate_passed,
            archive_gate_passed=args.archive_gate_passed,
            doctor_gate_passed=args.doctor_gate_passed,
            tests_run=args.tests_run,
            tests_passed=args.tests_passed,
            tests_failed=args.tests_failed,
        )
        output = _output_path(args.output)
        _publish_atomic(output, encode_evidence(evidence))
        return 0
    except (ValueError, OSError, RuntimeError):
        sys.stderr.write(_CLI_ERROR)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
