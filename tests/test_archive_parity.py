from __future__ import annotations

import io
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.check_archive_parity import check_parity


class ArchiveParityTests(unittest.TestCase):
    def _write_archive(self, archive: Path) -> None:
        with tarfile.open(archive, "w") as handle:
            directory = tarfile.TarInfo("nested/")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            handle.addfile(directory)

            payload = b"candidate bytes\n"
            regular = tarfile.TarInfo("nested/file.txt")
            regular.size = len(payload)
            regular.mode = 0o644
            handle.addfile(regular, io.BytesIO(payload))

            symlink = tarfile.TarInfo("linked-directory")
            symlink.type = tarfile.SYMTYPE
            symlink.linkname = "../outside"
            symlink.mode = 0o777
            handle.addfile(symlink)

    def test_accepts_exact_tree_without_following_symlinked_directory(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            archive = base / "candidate.tar"
            root = base / "root"
            outside = base / "outside"
            (root / "nested").mkdir(parents=True)
            outside.mkdir()
            (root / "nested" / "file.txt").write_bytes(b"candidate bytes\n")
            (outside / "must-not-be-seen.txt").write_text("outside", encoding="utf-8")
            (root / "linked-directory").symlink_to(outside, target_is_directory=True)
            self._write_archive(archive)

            check_parity(archive, root)

    def test_rejects_extra_extracted_path(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            archive = base / "candidate.tar"
            root = base / "root"
            (root / "nested").mkdir(parents=True)
            (root / "nested" / "file.txt").write_bytes(b"candidate bytes\n")
            (root / "linked-directory").symlink_to("../outside", target_is_directory=True)
            (root / "extra.txt").write_text("extra", encoding="utf-8")
            self._write_archive(archive)

            with self.assertRaises(ValueError):
                check_parity(archive, root)

    def test_entry_bound_fails_closed(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            archive = base / "candidate.tar"
            root = base / "root"
            root.mkdir()
            self._write_archive(archive)

            with self.assertRaises(ValueError):
                check_parity(archive, root, max_entries=2)


if __name__ == "__main__":
    unittest.main()
