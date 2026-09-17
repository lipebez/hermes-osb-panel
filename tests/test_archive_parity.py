from __future__ import annotations

import io
import os
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.check_archive_parity import check_parity, main, validate_archive


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

    def test_accepts_exact_link_free_tree(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            archive = base / "candidate.tar"
            root = base / "root"
            (root / "nested").mkdir(parents=True)
            (root / "nested" / "file.txt").write_bytes(b"candidate bytes\n")
            self._write_archive(archive)

            check_parity(archive, root)

    def test_rejects_extra_extracted_path(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            archive = base / "candidate.tar"
            root = base / "root"
            (root / "nested").mkdir(parents=True)
            (root / "nested" / "file.txt").write_bytes(b"candidate bytes\n")
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

    def test_rejects_archive_symbolic_and_hard_links(self):
        for link_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(link_type=link_type), TemporaryDirectory() as temp_dir:
                base = Path(temp_dir)
                archive = base / "candidate.tar"
                root = base / "root"
                root.mkdir()
                link = tarfile.TarInfo("escape")
                link.type = link_type
                link.linkname = "../outside"
                with tarfile.open(archive, "w") as handle:
                    handle.addfile(link)
                (root / "escape").symlink_to("../outside")

                with self.assertRaises(ValueError):
                    check_parity(archive, root)
                with self.assertRaises(ValueError):
                    validate_archive(archive)
                self.assertEqual(main(["--archive", str(archive)]), 2)

    def test_rejects_extracted_symlink_even_when_archive_name_matches(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            archive = base / "candidate.tar"
            root = base / "root"
            root.mkdir()
            payload = b"ordinary file\n"
            info = tarfile.TarInfo("entry")
            info.size = len(payload)
            with tarfile.open(archive, "w") as handle:
                handle.addfile(info, io.BytesIO(payload))
            (root / "entry").symlink_to("../outside")

            with self.assertRaises(ValueError):
                check_parity(archive, root)

    def test_rejects_extracted_hardlink_and_special_entry(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            archive = base / "candidate.tar"
            root = base / "root"
            outside = base / "outside.txt"
            root.mkdir()
            outside.write_bytes(b"candidate bytes\n")
            self._write_archive(archive)
            (root / "nested").mkdir()
            os.link(outside, root / "nested" / "file.txt")
            with self.assertRaises(ValueError):
                check_parity(archive, root)

            (root / "nested" / "file.txt").unlink()
            os.mkfifo(root / "nested" / "file.txt")
            with self.assertRaises(ValueError):
                check_parity(archive, root)

    def test_rejects_duplicate_and_special_archive_entries(self):
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for name, members in (
                (
                    "duplicate.tar",
                    [tarfile.TarInfo("package/file.txt"), tarfile.TarInfo("package/file.txt")],
                ),
                (
                    "fifo.tar",
                    [tarfile.TarInfo("package/pipe")],
                ),
            ):
                archive = temp / name
                members[0].type = tarfile.REGTYPE if name == "duplicate.tar" else tarfile.FIFOTYPE
                if len(members) > 1:
                    members[1].type = tarfile.REGTYPE
                with tarfile.open(archive, "w") as handle:
                    for member in members:
                        member.size = 0
                        handle.addfile(member)
                with self.assertRaises(ValueError):
                    validate_archive(archive)

    def test_validation_only_accepts_link_free_archive_before_extraction(self):
        with TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "candidate.tar"
            self._write_archive(archive)

            validate_archive(archive)
            self.assertEqual(main(["--archive", str(archive)]), 0)


if __name__ == "__main__":
    unittest.main()
